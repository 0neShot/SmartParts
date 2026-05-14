"""
Altium BOM Parser  (v3 -- Unified Smart Parser & Metadata Extractor)
=====================================================================

Merges two parsing concepts into a single ``BomParser`` class:

Concept A -- Dynamic Header Sniffing
    Parse the header row and dynamically identify target columns using
    case-insensitive aliases.  Supports both "clean" Altium exports
    (dedicated MPN column) and "combined" legacy formats where
    Manufacturer + MPN share one field.

Concept B -- Metadata Interception
    As rows are iterated, any row with an empty MPN is checked for
    board-level metadata (assembly name, description, revision) before
    being discarded.  This captures the "bare PCB" or "project header"
    row that Altium exports as the first data line of the BOM.

Pipeline
--------
1. ``BomParser(headers)`` -- sniff columns, build the internal map.
2. ``parser.parse_all(raw_rows)`` -- iterate every row:
   a. Extract MPN; if empty, intercept metadata then skip.
   b. If combined format, split manufacturer/MPN via prefix heuristic.
   c. Sanitise and normalise the row.
3. Access ``parser.bom_items`` (clean component list) and
   ``parser.assembly_metadata`` (dict with name/description/revision).

Both are returned together for the frontend to pre-fill the
"Create Assembly" UI inputs.
"""

import logging
import re
from dataclasses import dataclass, field
from typing import Tuple, Optional, Dict, List

logger = logging.getLogger('inventree_smart_parts.batch.altium')

# ---------------------------------------------------------------------------
#  Column alias tables  (first match wins, checked case-insensitively)
# ---------------------------------------------------------------------------

# MPN aliases in two tiers:
#   CLEAN    -> value is already a bare MPN, no extraction needed
#   COMBINED -> value is "Manufacturer MPN[, ]", extraction required
_MPN_ALIASES_CLEAN = [
    'manufacturer part number 1',
    'manufacturer part number',
    'mpn',
    'mfr part number',
    'mfr part no',
    'mfr pn',
]

_MPN_ALIASES_COMBINED = [
    'part number',
    'partnumber',
    'part no',
    'component',
]

_QTY_ALIASES = [
    'quantity',
    'qty',
    'menge',
    'anzahl',
    'count',
]

_MFR_ALIASES = [
    'manufacturer 1',
    'manufacturer',
    'hersteller',
    'mfr',
    'make',
]

_DESCRIPTION_ALIASES = [
    'description',
    'beschreibung',
    'comment',
    'notes',
]

_NAME_ALIASES = [
    'name',
    'value',
    'component value',
]

_DESIGNATOR_ALIASES = [
    'designator',
    'reference',
    'ref',
    'refdes',
]

_REVISION_ALIASES = [
    'revision id',
    'revision',
    'rev',
    'version',
    'ver',
]

# Values Altium inserts automatically that carry no real information
_JUNK_VALUES = {
    'unknown server',
    'unknown',
    'n/a',
    'na',
    '-',
    '',
}


# ---------------------------------------------------------------------------
#  Regex helpers
# ---------------------------------------------------------------------------
_TRAIL_RE = re.compile(r'[\s,\'"]+$')
_LEAD_RE  = re.compile(r'^[\s,\'"]+')


def _clean(value: str) -> str:
    """Strip leading/trailing whitespace, commas, and quotes."""
    return _TRAIL_RE.sub('', _LEAD_RE.sub('', (value or '').strip())).strip()


# ---------------------------------------------------------------------------
#  Known manufacturer prefixes  (sorted longest-first for greedy matching)
# ---------------------------------------------------------------------------
_KNOWN_MANUFACTURERS: List[str] = [
    "Analog Devices", "AVX Corporation", "Broadcom", "Bourns", "CUI Devices",
    "Diodes Incorporated", "EPCOS", "Infineon Technologies",
    "International Rectifier", "Kemet", "Littelfuse", "Microchip Technology",
    "Molex", "Murata Manufacturing", "Murata", "NXP Semiconductors", "NXP",
    "ON Semiconductor", "Panasonic", "Renesas Electronics", "Renesas",
    "Rohm Semiconductor", "Rohm", "Samsung Electro-Mechanics", "Samsung",
    "Semtech", "STMicroelectronics", "Taiyo Yuden", "TDK Corporation", "TDK",
    "Texas Instruments", "TE Connectivity", "Vishay", "Würth Elektronik",
    "Wurth Elektronik", "Wurth", "Yageo", "Amphenol", "Hirose",
    "JAE Electronics", "JST", "Kyocera AVX", "Kyocera", "LCSC",
    "Maxim Integrated", "Maxim", "Microchip", "Nexperia", "onsemi", "Osram",
    "ROHM", "SAMSUNG", "Skyworks", "Susumu", "Toshiba", "Vishay Dale",
    "Vishay Intertechnology", "Winbond", "Alps", "CTS", "Eaton", "IXYS",
    "KEMET", "Knowles", "Linear Technology", "MCC", "Marvell", "NEXPERIA",
    "Nichicon", "Nippon Chemi-Con", "Phoenix Contact", "SEMTECH", "Tyco",
    "Unitrode", "Vicor", "Weidmuller",
]

_KNOWN_MANUFACTURERS_SORTED = sorted(_KNOWN_MANUFACTURERS, key=len, reverse=True)


# ---------------------------------------------------------------------------
#  Column map (internal)
# ---------------------------------------------------------------------------

def _first_match(lower_map: Dict[str, str], aliases: List[str]) -> Optional[str]:
    """Return the original header for the first matching alias, or None."""
    for alias in aliases:
        if alias in lower_map:
            return lower_map[alias]
    return None


@dataclass
class _ColumnMap:
    """Resolved mapping from raw CSV headers to canonical field names."""
    mpn_col:   Optional[str] = None
    mpn_clean: bool = False       # True = bare MPN, False = combined format
    mfr_col:   Optional[str] = None
    qty_col:   Optional[str] = None
    desc_col:  Optional[str] = None
    name_col:  Optional[str] = None
    desig_col: Optional[str] = None
    rev_col:   Optional[str] = None

    @property
    def found_mpn(self) -> bool:
        return self.mpn_col is not None

    def describe(self) -> str:
        mode = "clean" if self.mpn_clean else "combined"
        return (
            f"mpn='{self.mpn_col}'({mode}), mfr='{self.mfr_col}', "
            f"qty='{self.qty_col}', desc='{self.desc_col}', "
            f"name='{self.name_col}', rev='{self.rev_col}'"
        )


def _build_column_map(headers: List[str]) -> _ColumnMap:
    """Sniff headers and return a resolved column map."""
    lower_map: Dict[str, str] = {h.lower().strip(): h for h in headers}
    cm = _ColumnMap()

    # Try clean MPN aliases first (bare MPN column)
    for alias in _MPN_ALIASES_CLEAN:
        if alias in lower_map:
            cm.mpn_col   = lower_map[alias]
            cm.mpn_clean = True
            break

    # Fallback to combined aliases
    if not cm.mpn_col:
        for alias in _MPN_ALIASES_COMBINED:
            if alias in lower_map:
                cm.mpn_col   = lower_map[alias]
                cm.mpn_clean = False
                break

    cm.mfr_col   = _first_match(lower_map, _MFR_ALIASES)
    cm.qty_col   = _first_match(lower_map, _QTY_ALIASES)
    cm.desc_col  = _first_match(lower_map, _DESCRIPTION_ALIASES)
    cm.name_col  = _first_match(lower_map, _NAME_ALIASES)
    cm.desig_col = _first_match(lower_map, _DESIGNATOR_ALIASES)
    cm.rev_col   = _first_match(lower_map, _REVISION_ALIASES)

    return cm


# ═══════════════════════════════════════════════════════════════════════════
#  BomParser -- Unified public API
# ═══════════════════════════════════════════════════════════════════════════

class BomParser:
    """
    Unified Altium BOM parser with dynamic header sniffing and metadata
    extraction from MPN-less rows.

    Usage::

        parser = BomParser(headers)          # sniff columns
        parser.parse_all(raw_rows)           # process every row

        clean_items = parser.bom_items       # [{mpn, manufacturer, ...}]
        meta        = parser.assembly_metadata  # {name, description, revision}

    The parser is also usable row-by-row::

        parser = BomParser(headers)
        for row in raw_rows:
            item = parser.parse_row(row)     # returns dict or None
    """

    def __init__(self, headers: List[str]):
        self._col_map = _build_column_map(headers)
        self._bom_items: List[Dict[str, str]] = []
        self._metadata: Dict[str, str] = {
            'name': '',
            'description': '',
            'revision': '',
        }
        self._metadata_complete = False

        logger.debug(f"BomParser initialised: {self._col_map.describe()}")

    # -- Read-only properties ------------------------------------------------

    @property
    def column_map(self) -> _ColumnMap:
        """The resolved column mapping (read-only inspection)."""
        return self._col_map

    @property
    def bom_items(self) -> List[Dict[str, str]]:
        """Clean BOM component rows (no metadata / empty-MPN rows)."""
        return self._bom_items

    @property
    def assembly_metadata(self) -> Dict[str, str]:
        """Extracted board-level metadata: name, description, revision."""
        return dict(self._metadata)

    @property
    def found_mpn_column(self) -> bool:
        """True if at least one MPN alias was found in the headers."""
        return self._col_map.found_mpn

    # -- Bulk parse ----------------------------------------------------------

    def parse_all(
        self, raw_rows: List[Dict[str, str]],
    ) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
        """
        Process all raw CSV rows in a single pass.

        Returns:
            Tuple of (bom_items, assembly_metadata) for convenience.
            Also populates ``self.bom_items`` and ``self.assembly_metadata``.
        """
        self._bom_items.clear()
        self._metadata = {'name': '', 'description': '', 'revision': ''}
        self._metadata_complete = False

        for row in raw_rows:
            item = self.parse_row(row)
            if item is not None:
                self._bom_items.append(item)

        return self._bom_items, self._metadata

    # -- Single-row parse (the core pipeline) --------------------------------

    def parse_row(self, row: Dict[str, str]) -> Optional[Dict[str, str]]:
        """
        Process a single raw CSV row through the full pipeline.

        Pipeline:
        1. Read the raw MPN value from the sniffed column.
        2. If MPN is empty -> try to intercept metadata, then return None.
        3. If combined format -> split manufacturer/MPN via prefix heuristic.
        4. Sanitise, extract quantity, build normalised dict.

        Returns:
            Normalised row dict, or None if the row has no valid MPN.
        """
        cm = self._col_map

        def _get(col: Optional[str]) -> str:
            if col is None:
                return ''
            return _clean(row.get(col, '') or '')

        raw_pn_value = _get(cm.mpn_col)

        # -- Step 1: Determine MPN and manufacturer --------------------------
        if cm.mpn_clean:
            # Format B: MPN is already isolated
            mpn = _clean(raw_pn_value)
            manufacturer = _get(cm.mfr_col)
        else:
            # Format A: extract manufacturer from combined field
            manufacturer, mpn = extract_manufacturer_and_mpn(raw_pn_value)
            # Prefer an explicit manufacturer column over the extracted one
            mfr_from_col = _get(cm.mfr_col)
            if mfr_from_col and not manufacturer:
                manufacturer = mfr_from_col

        # -- Step 2: Metadata interception (empty MPN) -----------------------
        if not mpn:
            self._try_intercept_metadata(row)
            return None  # Drop this row from the BOM items

        # -- Step 3: Description, quantity, designator -----------------------
        description = _get(cm.desc_col)
        if not description:
            description = _get(cm.name_col)

        qty_str = _get(cm.qty_col)
        quantity = 1
        if qty_str:
            try:
                quantity = max(1, int(float(qty_str)))
            except (ValueError, TypeError):
                quantity = 1

        return {
            'mpn':             mpn,
            'manufacturer':    manufacturer,
            'description':     description,
            'quantity':        str(quantity),
            'designator':      _get(cm.desig_col),
            'raw_part_number': raw_pn_value,
        }

    # -- Private: metadata interception --------------------------------------

    def _try_intercept_metadata(self, row: Dict[str, str]) -> None:
        """
        Check an MPN-less row for board-level metadata fields.

        Only the *first* non-junk value found for each field is kept.
        Once all three fields (name, description, revision) are populated,
        further MPN-less rows are ignored for metadata purposes.
        """
        if self._metadata_complete:
            return

        cm = self._col_map

        def _col_val(col: Optional[str]) -> str:
            if not col:
                return ''
            raw = _clean(row.get(col, '') or '')
            return '' if raw.lower() in _JUNK_VALUES else raw

        name     = _col_val(cm.name_col)
        desc     = _col_val(cm.desc_col)
        revision = _col_val(cm.rev_col)

        if name and not self._metadata['name']:
            self._metadata['name'] = name
        if desc and not self._metadata['description']:
            self._metadata['description'] = desc
        if revision and not self._metadata['revision']:
            self._metadata['revision'] = revision

        # Check if we've found all three
        if all(self._metadata.values()):
            self._metadata_complete = True

        if any([name, desc, revision]):
            logger.debug(
                f"[BomParser] Metadata intercepted from MPN-less row: "
                f"name='{name}', desc='{desc}', rev='{revision}'"
            )


# ═══════════════════════════════════════════════════════════════════════════
#  Standalone helpers (used by combined-format extraction)
# ═══════════════════════════════════════════════════════════════════════════

def extract_manufacturer_and_mpn(raw_pn: str) -> Tuple[str, str]:
    """
    Split a combined Altium "Part Number" field into (manufacturer, mpn).

    Strategy: try to match a known manufacturer prefix (greedy, longest
    first).  If none matches, the entire value is treated as a bare MPN.

    Examples::

        "Murata GRM32ER61C476KE15L, " -> ("Murata", "GRM32ER61C476KE15L")
        "TDK CGA4C2C0G1H472J060AA"   -> ("TDK",    "CGA4C2C0G1H472J060AA")
        "STM32F103C8T6"               -> ("",       "STM32F103C8T6")
    """
    cleaned = _clean(raw_pn)
    if not cleaned:
        return ('', '')

    cleaned_lower = cleaned.lower()
    for mfr in _KNOWN_MANUFACTURERS_SORTED:
        if cleaned_lower.startswith(mfr.lower()):
            remainder = cleaned[len(mfr):].strip()
            mpn = _clean(remainder)
            if mpn and len(mpn) >= 3:
                return (mfr, mpn)

    # No known prefix -> the whole thing is the MPN
    return ('', cleaned)


# ═══════════════════════════════════════════════════════════════════════════
#  Backward-compatible module-level wrappers
# ═══════════════════════════════════════════════════════════════════════════
#
#  These preserve the existing API used by importer.py so nothing breaks.
#  New code should prefer the BomParser class directly.
#

# Re-export AltiumColumnMap as an alias for the internal _ColumnMap
AltiumColumnMap = _ColumnMap


def is_altium_bom(headers: List[str]) -> bool:
    """
    Heuristic: does this header row look like an Altium BOM?

    Returns True if at least one MPN alias OR the 'Designator' column is found.
    """
    lower_headers = [h.lower().strip() for h in headers]
    all_mpn_aliases = _MPN_ALIASES_CLEAN + _MPN_ALIASES_COMBINED
    has_mpn_col    = any(a in lower_headers for a in all_mpn_aliases)
    has_designator = 'designator' in lower_headers
    return has_mpn_col or has_designator


def build_column_map(headers: List[str]) -> _ColumnMap:
    """Build and return the column map for a set of headers."""
    cm = _build_column_map(headers)
    logger.debug(f"Altium column map: {cm.describe()}")
    return cm


def parse_altium_row(row: Dict[str, str], col_map: _ColumnMap) -> Dict[str, str]:
    """
    Backward-compatible single-row parser.

    Converts a raw Altium BOM row into the normalised Smart Parts row format.
    Returns ``{'mpn': ''}`` if the row has no usable MPN.

    .. note::
        This wrapper does NOT perform metadata interception.  For full
        pipeline behaviour, use ``BomParser`` directly.
    """
    parser = BomParser.__new__(BomParser)
    parser._col_map = col_map
    parser._metadata = {'name': '', 'description': '', 'revision': ''}
    parser._metadata_complete = True  # Disable interception in compat mode
    parser._bom_items = []

    result = parser.parse_row(row)
    return result if result else {'mpn': ''}


def extract_assembly_metadata(
    raw_rows: List[Dict[str, str]],
    col_map: _ColumnMap,
) -> Dict[str, str]:
    """
    Backward-compatible metadata extractor.

    Scans all raw CSV rows and returns ``{name, description, revision}``
    from MPN-less rows.

    .. note::
        Prefer ``BomParser.parse_all()`` which does both extraction and
        metadata interception in a single pass.
    """
    parser = BomParser.__new__(BomParser)
    parser._col_map = col_map
    parser._metadata = {'name': '', 'description': '', 'revision': ''}
    parser._metadata_complete = False
    parser._bom_items = []

    for row in raw_rows:
        parser.parse_row(row)

    return parser.assembly_metadata
"""
Unified BOM Parser -- altium_parser.py (v3)
"""
