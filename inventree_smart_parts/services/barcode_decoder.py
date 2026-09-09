"""
Barcode Decoder
===============
Server-side Python implementation of the three-stage DataMatrix and barcode
sanitization pipeline from scanner.js.

Stages:
  1. Strict ANSI/ISO DataMatrix with Group Separator (GS / \x1d) and substitutes
  2. GS-Stripped DataMatrix with strong-boundary regex algorithm
  2b. Whitespace-delimited Data Identifier regex scan
  3. Graceful fallback (bare MPN / clean alphanumeric string)
"""

import re
import logging
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

logger = logging.getLogger("inventree_smart_parts.services.barcode")

GS = "\x1d"  # ASCII 29 – Group Separator
RS = "\x1e"  # ASCII 30 – Record Separator
EOT = "\x04"  # ASCII 4  – End Of Transmission

# Alternative representations scanner wedges emit
GS_SUBSTITUTES = [
    (re.compile(r"\{GS\}", re.IGNORECASE), GS),
    (re.compile(r"\[GS\]", re.IGNORECASE), GS),
    (re.compile(r"\u241d"), GS),
    (re.compile(r"\|(?=[1-9A-Z])"), GS),
    (re.compile(r"~(?=[1-9A-Z])"), GS),
]

RS_SUBSTITUTES = [
    (re.compile(r"\{RS\}", re.IGNORECASE), RS),
    (re.compile(r"\[RS\]", re.IGNORECASE), RS),
    (re.compile(r"\u241e"), RS),
]

HAS_GS_RE = re.compile(
    r"[\x1d\u241d]|(?:\{GS\}|\[GS\]|~(?=[1-9A-Z])|\|(?=[1-9A-Z]))", re.IGNORECASE
)

# DI definition table: (prefix, resultKey, isStrong)
DI_TABLE = [
    ("30P", "supplierSku", True),
    ("QTY", "quantity", True),
    ("1K", "poNumber", True),
    ("1P", "mpn", True),
    ("1T", "batch", True),
    ("1S", None, True),
    ("2S", None, True),
    ("3S", None, True),
    ("4L", None, True),
    ("6D", None, True),
    ("9D", None, False),
    ("PN", "mpn", False),
    ("PO", "poNumber", False),
    ("QT", "quantity", False),
    ("BT", "batch", False),
    ("BX", None, False),
    ("DC", None, False),
    ("LT", "batch", False),
    ("RV", None, False),
    ("K", "poNumber", False),
    ("Q", "quantity", False),
    ("P", "mpn", False),
    ("V", None, False),
    ("S", None, False),
]

_STRONG_BOUNDARY_RE = re.compile(r"(?=30P|QTY|1[PTKSE]|2S|3S|4L|6D|Q(?=\d)|$)")


@dataclass
class BarcodeData:
    """Standardized barcode parse result."""

    mpn: str = ""
    quantity: Optional[int] = None
    batch: str = ""
    supplier_sku: str = ""
    po_number: str = ""
    raw: str = ""
    source: str = ""  # 'ansi', 'heuristic', 'regex', 'fallback', ''
    distributor: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        from dataclasses import asdict

        return asdict(self)


def _extract_fields(fields: List[str], result: BarcodeData):
    """Populate BarcodeData according to ANSI MH10.8.2 DI assignments."""
    for f in fields:
        t = f.strip()
        if not t:
            continue

        if t in ("[)>", "06", "05") or t.startswith("[)>"):
            continue

        # 1P -> MPN
        if len(t) > 2 and t.startswith("1P"):
            if not result.mpn:
                result.mpn = t[2:].strip()
            continue

        # 30P -> Supplier SKU
        if len(t) > 3 and t.startswith("30P"):
            if not result.supplier_sku:
                result.supplier_sku = t[3:].strip()
            continue

        # P -> Generic MPN / SKU
        if len(t) > 1 and t.startswith("P") and not t.startswith("PO"):
            val = t[1:].strip()
            if not result.mpn:
                result.mpn = val
            elif not result.supplier_sku:
                result.supplier_sku = val
            continue

        # Q -> Quantity
        if len(t) > 1 and t.startswith("Q") and t[1].isdigit():
            try:
                q = int(t[1:])
                if q > 0 and result.quantity is None:
                    result.quantity = q
            except ValueError:
                pass
            continue

        # 1T -> Lot / Batch
        if len(t) > 2 and t.startswith("1T"):
            if not result.batch:
                result.batch = t[2:].strip()
            continue

        # K -> PO
        if len(t) > 1 and t.startswith("K") and not t[1].isspace():
            if not result.po_number:
                result.po_number = t[1:].strip()
            continue


def _try_ansi_parse(raw: str, result: BarcodeData) -> bool:
    has_header = "[)>" in raw or raw.startswith("\x05\x06")
    has_gs = bool(HAS_GS_RE.search(raw))
    if not has_header and not has_gs:
        return False

    norm = raw
    for pattern, replacement in GS_SUBSTITUTES:
        norm = pattern.sub(replacement, norm)
    for pattern, replacement in RS_SUBSTITUTES:
        norm = pattern.sub(replacement, norm)
    norm = re.sub(r"\{EOT\}|\u2404", EOT, norm, flags=re.IGNORECASE)

    fields = [f.strip() for f in re.split(r"[\x1d\x1e\x04]", norm) if f.strip()]
    if len(fields) < 2:
        return False

    _extract_fields(fields, result)
    found = bool(result.mpn or result.quantity is not None or result.batch)
    if found:
        result.source = "ansi"
    return found


def _try_heuristic_parse(raw: str, result: BarcodeData) -> bool:
    m = re.match(r"^\[?\)?>?\s*\[\)>(?:05|06)", raw)
    if not m:
        return False

    body = raw[m.end() :]
    if not body:
        return False

    pos = 0
    match_count = 0
    fields = {}

    while pos < len(body):
        matched = False

        for prefix, key, is_strong in DI_TABLE:
            if len(body) - pos < len(prefix):
                continue
            if body[pos : pos + len(prefix)] != prefix:
                continue

            # Single-char DI validation
            if len(prefix) == 1:
                if prefix == "Q":
                    if pos + 1 >= len(body) or not body[pos + 1].isdigit():
                        continue
                elif prefix in ("K", "P"):
                    if pos > 0 and body[pos - 1].isalnum():
                        continue
                else:
                    continue

            # Weak 2-char DI validation
            if not is_strong and len(prefix) == 2:
                if pos > 0 and body[pos - 1].isalnum():
                    continue

            after = body[pos + len(prefix) :]
            value = ""

            if key == "quantity":
                digits = []
                for i, char in enumerate(after):
                    if not char.isdigit():
                        break
                    if i > 0 and _STRONG_BOUNDARY_RE.match(after[i:]):
                        break
                    (
                        digits.push(char)
                        if hasattr(digits, "push")
                        else digits.append(char)
                    )
                value = "".join(digits)
                if value and key not in fields:
                    try:
                        q = int(value)
                        if q > 0:
                            fields[key] = q
                    except ValueError:
                        pass
            else:
                bm = _STRONG_BOUNDARY_RE.search(after)
                if bm and bm.start() > 0:
                    value = after[: bm.start()]
                elif bm and bm.start() == 0:
                    value = ""
                else:
                    value = after

                if value and key and key not in fields:
                    fields[key] = value.strip()

            pos += len(prefix) + len(value)
            match_count += 1
            matched = True
            break

        if not matched:
            pos += 1

    if "mpn" in fields:
        result.mpn = fields["mpn"]
    if "quantity" in fields:
        result.quantity = fields["quantity"]
    if "batch" in fields:
        result.batch = fields["batch"]
    if "supplierSku" in fields:
        result.supplier_sku = fields["supplierSku"]
    if "poNumber" in fields:
        result.po_number = fields["poNumber"]

    found = bool(result.mpn or result.quantity is not None or result.batch)
    if found and match_count >= 2:
        result.source = "heuristic"
        return True
    return False


def _try_regex_parse(raw: str, result: BarcodeData) -> bool:
    if not re.search(r"(?:^|\s)(?:1P|30P|P|Q|1T|K)", raw):
        return False

    m1p = re.search(r"(?:^|\s)1P([^\s]+)", raw)
    if m1p and not result.mpn:
        result.mpn = m1p.group(1)

    m30p = re.search(r"(?:^|\s)30P([^\s]+)", raw)
    if m30p and not result.supplier_sku:
        result.supplier_sku = m30p.group(1)

    mp = re.search(r"(?:^|\s)P([^\s]+)", raw)
    if mp:
        if not result.mpn:
            result.mpn = mp.group(1)
        elif not result.supplier_sku:
            result.supplier_sku = mp.group(1)

    if result.quantity is None:
        mq = re.search(r"(?:^|\s)Q(\d+)", raw)
        if mq:
            try:
                result.quantity = int(mq.group(1))
            except ValueError:
                pass

    if not result.batch:
        m1t = re.search(r"(?:^|\s)1T([^\s]+)", raw)
        if m1t:
            result.batch = m1t.group(1)

    if not result.po_number:
        mk = re.search(r"(?:^|\s)K(\S+)", raw)
        if mk:
            result.po_number = mk.group(1)

    found = bool(result.mpn or result.quantity is not None or result.batch)
    if found:
        result.source = "regex"
    return found


def _fallback_parse(raw: str, result: BarcodeData):
    cleaned = re.sub(r"[\x00-\x1f\x7f]+", "", raw).strip()
    # Guard: Never treat InvenTree internal JSON barcodes or short codes as fallback MPNs
    if (cleaned.startswith("{") and cleaned.endswith("}")) or cleaned.startswith(
        "INV-"
    ):
        result.source = "native_internal"
        return
    if cleaned:
        result.mpn = cleaned
        result.source = "fallback"


def parse_barcode(raw: str) -> BarcodeData:
    """
    Parse a raw barcode string through the 3-stage pipeline.
    Always returns a BarcodeData instance.
    """
    result = BarcodeData(raw=raw or "")
    if not raw or not raw.strip():
        return result

    # Stage 1: ANSI DataMatrix
    if _try_ansi_parse(raw, result):
        return result

    # Stage 2: GS-stripped heuristic
    if _try_heuristic_parse(raw, result):
        return result

    # Stage 2b: Regex
    if _try_regex_parse(raw, result):
        return result

    # Stage 3: Fallback
    _fallback_parse(raw, result)
    return result
