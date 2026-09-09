"""
Parameter Normalizer
====================
Intelligent normalization of electronic component parameters.
Handles unit parsing, prefix conversion, and format standardization
so that values like "10uF", "10 microfarad", and "0.00001F" all
become "10 µF".
"""

import re
import logging
from typing import Optional, Tuple
from dataclasses import dataclass

logger = logging.getLogger("inventree_smart_parts.services.normalizer")


# ═══════════════════════════════════════════════════════════════════
#  SI Prefix System
# ═══════════════════════════════════════════════════════════════════


@dataclass
class SIPrefix:
    symbol: str
    name: str
    exponent: int  # power of 10


SI_PREFIXES = [
    SIPrefix("p", "pico", -12),
    SIPrefix("n", "nano", -9),
    SIPrefix("µ", "micro", -6),
    SIPrefix("m", "milli", -3),
    SIPrefix("", "", 0),
    SIPrefix("k", "kilo", 3),
    SIPrefix("M", "mega", 6),
    SIPrefix("G", "giga", 9),
    SIPrefix("T", "tera", 12),
]

# Lookup: symbol/alias → SIPrefix
_PREFIX_MAP = {}
for _p in SI_PREFIXES:
    if _p.symbol:
        _PREFIX_MAP[_p.symbol] = _p
    if _p.name:
        _PREFIX_MAP[_p.name] = _p
        _PREFIX_MAP[_p.name + "s"] = _p  # plurals (e.g. "microfarads")
# Common aliases
_PREFIX_MAP["u"] = _PREFIX_MAP["µ"]  # ASCII u for micro
_PREFIX_MAP["μ"] = _PREFIX_MAP["µ"]  # Greek mu
_PREFIX_MAP["micro"] = _PREFIX_MAP["µ"]


# ═══════════════════════════════════════════════════════════════════
#  Unit Definitions
# ═══════════════════════════════════════════════════════════════════


@dataclass
class UnitDef:
    """Defines a base unit with its canonical symbol and recognized aliases."""

    symbol: str  # Canonical: "F", "Ω", "V", ...
    category: str  # "capacitance", "resistance", ...
    aliases: tuple  # Alternative spellings
    preferred_prefixes: tuple  # Preferred SI prefix exponents for display


UNITS = [
    UnitDef("F", "capacitance", ("farad", "farads", "fd"), (-12, -9, -6)),
    UnitDef("Ω", "resistance", ("ohm", "ohms", "r"), (0, 3, 6)),
    UnitDef("H", "inductance", ("henry", "henrys", "henries"), (-9, -6, -3)),
    UnitDef("V", "voltage", ("volt", "volts"), (-3, 0, 3)),
    UnitDef("A", "current", ("amp", "amps", "ampere", "amperes"), (-9, -6, -3, 0)),
    UnitDef("W", "power", ("watt", "watts"), (-6, -3, 0, 3)),
    UnitDef("Hz", "frequency", ("hertz",), (0, 3, 6, 9)),
    UnitDef("°C", "temperature", ("c", "celsius", "deg c", "degc"), (0,)),
    UnitDef("°F", "temperature", ("fahrenheit", "deg f", "degf"), (0,)),
    UnitDef("K", "temperature", ("kelvin",), (0,)),
    UnitDef("s", "time", ("sec", "second", "seconds"), (-9, -6, -3, 0)),
    UnitDef("m", "length", ("meter", "meters", "metre"), (-3, 0)),
    UnitDef("%", "percentage", ("percent", "pct"), (0,)),
    UnitDef("dB", "decibel", ("decibel", "decibels"), (0,)),
    UnitDef("ppm", "concentration", ("ppm",), (0,)),
]

# Build lookup: alias → UnitDef
_UNIT_MAP = {}
for _u in UNITS:
    _UNIT_MAP[_u.symbol.lower()] = _u
    for _a in _u.aliases:
        _UNIT_MAP[_a.lower()] = _u
# Ω aliases
_UNIT_MAP["ω"] = _UNIT_MAP["ω"] if "ω" in _UNIT_MAP else _UNIT_MAP.get("ohm")


# ═══════════════════════════════════════════════════════════════════
#  Resistor / Capacitor Shorthand Parser  (e.g. "4k7", "2R2")
# ═══════════════════════════════════════════════════════════════════

_SHORTHAND_RE = re.compile(r"^(\d+)([RrKkMm])(\d+)$")
_SHORTHAND_UNIT = {
    "r": ("Ω", 0),
    "R": ("Ω", 0),
    "k": ("Ω", 3),
    "K": ("Ω", 3),
    "m": ("Ω", 6),
    "M": ("Ω", 6),  # In EE shorthand, M = Mega for resistance
}


def _parse_shorthand(raw: str) -> Optional[Tuple[float, str, int]]:
    """
    Parse EE shorthand like '4k7' → (4.7, 'Ω', 3) or '2R2' → (2.2, 'Ω', 0).
    Returns (numeric_value, base_unit_symbol, prefix_exponent) or None.
    """
    m = _SHORTHAND_RE.match(raw.strip())
    if not m:
        return None
    integer, letter, decimal = m.group(1), m.group(2), m.group(3)
    if letter.upper() not in ("R", "K", "M"):
        return None
    value = float(f"{integer}.{decimal}")
    unit_sym, exp = _SHORTHAND_UNIT[letter]
    return value, unit_sym, exp


# ═══════════════════════════════════════════════════════════════════
#  Main Parser
# ═══════════════════════════════════════════════════════════════════

# Regex to split "10.5kΩ", "100 nF", "4.7uF", "-40°C" etc.
_VALUE_UNIT_RE = re.compile(
    r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)"  # numeric value
    r"\s*"  # optional space
    r"(.+)?$"  # unit string (rest)
)

# Regex for pure scientific notation like "0.00001F"
_SCI_FULL_RE = re.compile(r"^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$")


def _find_unit_and_prefix(unit_str: str) -> Optional[Tuple[UnitDef, SIPrefix]]:
    """
    Parse a unit string like 'kΩ', 'uF', 'nH', 'mV', 'ohm', 'microfarad'.
    Returns (UnitDef, SIPrefix) or None.
    """
    if not unit_str:
        return None

    raw = unit_str.strip()

    # Direct match on full string (e.g. "ohm", "Hz", "°C", "dB", "%")
    u = _UNIT_MAP.get(raw.lower())
    if u:
        return u, SI_PREFIXES[4]  # no prefix

    # Try prefix + unit combinations
    # First try single-char prefix
    if len(raw) >= 2:
        prefix_char = raw[0]
        rest = raw[1:]

        # Handle "µF", "kΩ" etc.
        p = _PREFIX_MAP.get(prefix_char)
        u = _UNIT_MAP.get(rest.lower())
        if p and u:
            return u, p

    # Try word-form prefix: "microfarad", "kiloohm", "millivolt"
    raw_lower = raw.lower()
    for pfx in SI_PREFIXES:
        if pfx.name and raw_lower.startswith(pfx.name):
            remainder = raw_lower[len(pfx.name) :]
            u = _UNIT_MAP.get(remainder)
            if u:
                return u, pfx

    return None


def _choose_best_prefix(
    value: float, unit: UnitDef, current_exp: int
) -> Tuple[float, SIPrefix]:
    """
    Choose the best SI prefix so the displayed value is in [1, 1000).
    Prefers the unit's preferred_prefixes when possible.
    """
    # Absolute value in base units
    abs_base = abs(value) * (10.0**current_exp)
    if abs_base == 0:
        return 0.0, SI_PREFIXES[4]

    sign = 1 if value >= 0 else -1

    best_prefix = SI_PREFIXES[4]  # no prefix
    best_val = abs_base
    best_score = abs(_niceness(abs_base))

    for pfx in SI_PREFIXES:
        if pfx.exponent not in unit.preferred_prefixes and pfx.exponent != 0:
            continue
        candidate = abs_base / (10.0**pfx.exponent)
        score = _niceness(candidate)
        if score < best_score:
            best_score = score
            best_prefix = pfx
            best_val = candidate

    return sign * best_val, best_prefix


def _niceness(v: float) -> float:
    """Lower is better. Prefers values in [1, 1000)."""
    if v == 0:
        return 0
    import math

    log = math.log10(abs(v))
    # Ideal range: 0 ≤ log < 3  →  1 to 999
    if 0 <= log < 3:
        return 0
    return abs(log - 1.5)  # distance from the midpoint of [0, 3)


def _format_value(v: float) -> str:
    """Format a numeric value cleanly: drop trailing zeros."""
    if v == int(v) and abs(v) < 1e12:
        return str(int(v))
    # Up to 6 decimal places, strip trailing zeros
    formatted = f"{v:.6f}".rstrip("0").rstrip(".")
    return formatted


# ═══════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════


@dataclass
class NormalizedParam:
    """Result of normalizing a parameter."""

    name: str
    value: str
    unit: str
    normalized: bool = False  # True if normalization was applied


def normalize_parameter(name: str, value: str, unit: str = "") -> NormalizedParam:
    """
    Normalize a single parameter's value and unit.

    Examples:
        ("Capacitance", "10uF",   "")   → ("Capacitance", "10",  "µF")
        ("Capacitance", "0.00001","F")   → ("Capacitance", "10",  "µF")
        ("Resistance",  "4k7",   "")     → ("Resistance",  "4.7", "kΩ")
        ("Voltage",     "3.3",   "V")    → ("Voltage",     "3.3", "V")
        ("Color",       "Red",   "")     → ("Color",       "Red", "")  # unchanged
    """
    raw_value = str(value).strip()
    raw_unit = str(unit).strip()

    if not raw_value:
        return NormalizedParam(name=name, value=raw_value, unit=raw_unit)

    # ── 1. Try EE shorthand (4k7, 2R2) ──
    shorthand = _parse_shorthand(raw_value)
    if shorthand:
        num, base_sym, exp = shorthand
        u_def = _UNIT_MAP.get(base_sym.lower())
        if u_def:
            best_val, best_pfx = _choose_best_prefix(num, u_def, exp)
            return NormalizedParam(
                name=name,
                value=_format_value(best_val),
                unit=f"{best_pfx.symbol}{u_def.symbol}",
                normalized=True,
            )

    # ── 2. Combined value+unit in the value field (e.g. "10uF", "100nF") ──
    combined = raw_value + raw_unit  # try both
    for attempt in ([raw_value] if raw_unit else [raw_value]):
        m = _VALUE_UNIT_RE.match(attempt)
        if m and m.group(2):
            num_str, unit_str = m.group(1), m.group(2).strip()
            parsed = _find_unit_and_prefix(unit_str)
            if parsed:
                u_def, pfx = parsed
                num = float(num_str)
                best_val, best_pfx = _choose_best_prefix(num, u_def, pfx.exponent)
                return NormalizedParam(
                    name=name,
                    value=_format_value(best_val),
                    unit=f"{best_pfx.symbol}{u_def.symbol}",
                    normalized=True,
                )

    # ── 3. Separate value and unit fields (e.g. value="0.00001", unit="F") ──
    if raw_unit:
        parsed = _find_unit_and_prefix(raw_unit)
        if parsed:
            try:
                num = float(raw_value)
                u_def, pfx = parsed
                best_val, best_pfx = _choose_best_prefix(num, u_def, pfx.exponent)
                return NormalizedParam(
                    name=name,
                    value=_format_value(best_val),
                    unit=f"{best_pfx.symbol}{u_def.symbol}",
                    normalized=True,
                )
            except ValueError:
                pass

    # ── 4. No normalization possible – return as-is ──
    return NormalizedParam(name=name, value=raw_value, unit=raw_unit)


def normalize_parameter_list(parameters: list) -> list:
    """
    Normalize a list of parameter dicts [{'name':…, 'value':…, 'unit':…}, …].
    Returns a new list with normalized values.

    Parameters with empty or placeholder values (e.g. "-", "N/A", "unknown")
    are stripped out so they never reach the editor UI or the database.
    """
    from .part_creator import is_useless_value

    result = []
    for p in parameters:
        name = p.get("name", "")
        value = p.get("value", "")

        # Skip parameters without a name or with a useless value
        if not name or is_useless_value(value):
            continue

        n = normalize_parameter(
            name=name,
            value=value,
            unit=p.get("unit", ""),
        )
        result.append(
            {
                "name": n.name,
                "value": n.value,
                "unit": n.unit,
                "_normalized": n.normalized,
            }
        )
    return result


# ═══════════════════════════════════════════════════════════════════
#  Parameter Name Normalization & Self-Learning (Catch & Learn)
# ═══════════════════════════════════════════════════════════════════

_PUNCT_RE = re.compile(r"[-_()\[\]/\\.]")
_SPACES_RE = re.compile(r"\s+")


def sanitize_parameter_name(raw_name: str) -> str:
    """
    Sanitize a parameter name by replacing punctuation with a single space,
    collapsing multiple spaces, and returning the lowercased, stripped result.
    """
    if not raw_name:
        return ""
    # Replace punctuation with a space
    s = _PUNCT_RE.sub(" ", raw_name)
    # Collapse multiple spaces
    s = _SPACES_RE.sub(" ", s)
    return s.lower().strip()


PARAMETER_MAP = {
    # ── Resistance & Resistors ─────────────────────────────────────────
    "resistance": "Resistance",
    "resistance (ohms)": "Resistance",
    "resistance value": "Resistance",
    "resistance - value": "Resistance",
    "res": "Resistance",
    "resistor value": "Resistance",
    "nominal resistance": "Resistance",
    "resistance range": "Resistance",
    "tolerance": "Tolerance",
    "tolerance (%)": "Tolerance",
    "tol": "Tolerance",
    "tol (%)": "Tolerance",
    "resistance tolerance": "Resistance Tolerance",
    "resistance tolerance (%)": "Resistance Tolerance",
    "resistor tolerance": "Resistance Tolerance",
    "temperature coefficient": "Temperature Coefficient (TCR)",
    "temp coefficient": "Temperature Coefficient (TCR)",
    "temperature coefficient of resistance": "Temperature Coefficient (TCR)",
    "tcr": "Temperature Coefficient (TCR)",
    "temperature coefficient (ppm/c)": "Temperature Coefficient (TCR)",
    "temperature coefficient (ppm/°c)": "Temperature Coefficient (TCR)",
    "tempco": "Temperature Coefficient (TCR)",
    # ── Capacitance & Capacitors ───────────────────────────────────────
    "capacitance": "Capacitance",
    "capacitance - value": "Capacitance",
    "capacitance value": "Capacitance",
    "cap": "Capacitance",
    "capacitor value": "Capacitance",
    "nominal capacitance": "Capacitance",
    "capacitance tolerance": "Capacitance Tolerance",
    "capacitance tolerance (%)": "Capacitance Tolerance",
    "capacitor tolerance": "Capacitance Tolerance",
    "dielectric material": "Dielectric Material",
    "dielectric": "Dielectric Material",
    "dielectric characteristic": "Dielectric Material",
    "temperature coefficient (capacitor)": "Dielectric Material",
    "dielectric code": "Dielectric Material",
    "capacitor dielectric": "Dielectric Material",
    "dielectric type": "Dielectric Material",
    "equivalent series resistance": "Equivalent Series Resistance (ESR)",
    "esr": "Equivalent Series Resistance (ESR)",
    "equivalent series resistance (esr)": "Equivalent Series Resistance (ESR)",
    "esr (ohms)": "Equivalent Series Resistance (ESR)",
    "max esr": "Equivalent Series Resistance (ESR)",
    "ripple current": "Ripple Current",
    "ripple current (rms)": "Ripple Current",
    "max ripple current": "Ripple Current",
    "ripple current - max": "Ripple Current",
    "ripple current @ low frequency": "Ripple Current",
    "ripple current @ high frequency": "Ripple Current",
    "leakage current": "Leakage Current",
    "capacitor leakage current": "Leakage Current",
    "max leakage current": "Leakage Current",
    "leakage current - max": "Leakage Current",
    "dc leakage current": "Leakage Current",
    # ── Inductance & Magnetics ─────────────────────────────────────────
    "inductance": "Inductance",
    "inductance (henries)": "Inductance",
    "inductance value": "Inductance",
    "nominal inductance": "Inductance",
    "ind": "Inductance",
    "inductance tolerance": "Inductance Tolerance",
    "inductance tolerance (%)": "Inductance Tolerance",
    "inductor tolerance": "Inductance Tolerance",
    "q factor": "Q Factor",
    "q @ frequency": "Q Factor",
    "quality factor": "Q Factor",
    "q minimum": "Q Factor",
    "q min": "Q Factor",
    "self resonant frequency": "Self Resonant Frequency (SRF)",
    "srf": "Self Resonant Frequency (SRF)",
    "self-resonant frequency": "Self Resonant Frequency (SRF)",
    "resonant frequency": "Self Resonant Frequency (SRF)",
    "srf (min)": "Self Resonant Frequency (SRF)",
    "srf min": "Self Resonant Frequency (SRF)",
    "dc resistance": "DC Resistance (DCR)",
    "dcr": "DC Resistance (DCR)",
    "dc resistance (dcr)": "DC Resistance (DCR)",
    "dcr (ohms)": "DC Resistance (DCR)",
    "max dcr": "DC Resistance (DCR)",
    "dc resistance max": "DC Resistance (DCR)",
    "core material": "Core Material",
    "inductor core material": "Core Material",
    "core type": "Core Material",
    "saturation current": "Saturation Current (Isat)",
    "isat": "Saturation Current (Isat)",
    "saturation current (isat)": "Saturation Current (Isat)",
    "inductor saturation current": "Saturation Current (Isat)",
    "current - saturation (isat)": "Saturation Current (Isat)",
    "temperature rise current": "Temperature Rise Current (Itemp)",
    "itemp": "Temperature Rise Current (Itemp)",
    "rms current": "Temperature Rise Current (Itemp)",
    "current - temperature rise": "Temperature Rise Current (Itemp)",
    "rated current (temp rise)": "Temperature Rise Current (Itemp)",
    # ── Voltage Rating ─────────────────────────────────────────────────
    "voltage rated": "Voltage Rating",
    "voltage rating": "Voltage Rating",
    "voltage": "Voltage Rating",
    "voltage dc": "Voltage Rating",
    "voltage ac": "Voltage Rating",
    "rated voltage": "Voltage Rating",
    "max voltage": "Voltage Rating",
    "voltage working": "Voltage Rating",
    "voltage rating dc": "Voltage Rating",
    "voltage rating ac": "Voltage Rating",
    "output voltage nom": "Voltage Rating",
    "output voltage": "Voltage Rating",
    # ── Power Rating / Rated Power ─────────────────────────────────────
    "power watts": "Rated Power",
    "power (watts)": "Rated Power",
    "power - watts": "Rated Power",
    "power rating": "Rated Power",
    "power rating (watts)": "Rated Power",
    "power rating - watts": "Rated Power",
    "rated power": "Rated Power",
    "rated power (watts)": "Rated Power",
    "power": "Rated Power",
    "power dissipation": "Rated Power",
    "power dissipation (max)": "Rated Power",
    "max power dissipation": "Rated Power",
    "power - max": "Rated Power",
    "power max": "Rated Power",
    "power - rated": "Rated Power",
    "rated wattage": "Rated Power",
    "wattage": "Rated Power",
    # ── Mounting Type ──────────────────────────────────────────────────
    "mounting type": "Mounting Type",
    "mounting style": "Mounting Type",
    "ic mounting": "Mounting Type",
    "mount style": "Mounting Type",
    "mounting": "Mounting Type",
    "mount": "Mounting Type",
    # ── Basic Semiconductor Ratings (Diodes & Rectifiers) ──────────────
    "forward voltage": "Forward Voltage",
    "forward voltage (vf)": "Forward Voltage",
    "vf": "Forward Voltage",
    "forward voltage max": "Forward Voltage",
    "vf max": "Forward Voltage",
    "forward voltage (vf) (max)": "Forward Voltage",
    "voltage forward vf max @ if": "Forward Voltage",
    "voltage forward vf max if": "Forward Voltage",
    "voltage - forward (vf) (max) @ if": "Forward Voltage",
    "voltage - forward (vf) @ if": "Forward Voltage",
    "voltage forward vf @ if": "Forward Voltage",
    "voltage - forward (vf)": "Forward Voltage",
    "voltage - forward": "Forward Voltage",
    "voltage forward": "Forward Voltage",
    "voltage forward vf typ": "Forward Voltage",
    "durchlassspannung": "Forward Voltage",
    "flussspannung": "Forward Voltage",
    "test current": "Test Current",
    "current test": "Test Current",
    "test current (if)": "Test Current",
    "current test (if)": "Test Current",
    "luminous flux": "Luminous Flux",
    "lumens watt @ current test": "Luminous Flux",
    "luminous flux @ current temperature": "Luminous Flux",
    "flux": "Luminous Flux",
    "forward current": "Forward Current",
    "forward current (if)": "Forward Current",
    "forward current max": "Forward Current",
    "forward current (if) (max)": "Forward Current",
    "current - forward": "Forward Current",
    "current - forward (if)": "Forward Current",
    "current - forward (if) (max)": "Forward Current",
    "current forward": "Forward Current",
    "current - average rectified (io)": "Forward Current",
    "current average rectified io": "Forward Current",
    "average rectified current": "Forward Current",
    "average forward current": "Forward Current",
    "continuous forward current": "Forward Current",
    "if": "Forward Current",
    "io": "Forward Current",
    "durchlassstrom": "Forward Current",
    "breakdown voltage": "Breakdown Voltage",
    "breakdown voltage (vbr)": "Breakdown Voltage",
    "breakdown voltage (min)": "Breakdown Voltage",
    "voltage - breakdown": "Breakdown Voltage",
    "voltage - breakdown (min)": "Breakdown Voltage",
    "voltage breakdown min": "Breakdown Voltage",
    "voltage breakdown": "Breakdown Voltage",
    "voltage breakdown (vbr)": "Breakdown Voltage",
    "vbr": "Breakdown Voltage",
    "v(br)": "Breakdown Voltage",
    "zener voltage": "Breakdown Voltage",
    "voltage zener": "Breakdown Voltage",
    "voltage - zener": "Breakdown Voltage",
    "voltage - zener (nom)": "Breakdown Voltage",
    "voltage - zener (nom) (vz)": "Breakdown Voltage",
    "voltage zener nom vz": "Breakdown Voltage",
    "voltage zener nom": "Breakdown Voltage",
    "zener voltage (vz)": "Breakdown Voltage",
    "nominal zener voltage": "Breakdown Voltage",
    "zener voltage range": "Breakdown Voltage",
    "vz": "Breakdown Voltage",
    "durchbruchspannung": "Breakdown Voltage",
    "zener-spannung": "Breakdown Voltage",
    "zenerspannung": "Breakdown Voltage",
    "polarity": "Polarity",
    "diode polarity": "Polarity",
    "diode configuration": "Polarity",
    "reverse voltage": "Reverse Voltage",
    "dc reverse voltage": "Reverse Voltage",
    "vr": "Reverse Voltage",
    "reverse voltage max": "Reverse Voltage",
    "vr max": "Reverse Voltage",
    "dc blocking voltage": "Reverse Voltage",
    "reverse voltage (vr) (max)": "Reverse Voltage",
    "voltage dc reverse vr max": "Reverse Voltage",
    "voltage dc reverse vr": "Reverse Voltage",
    "reverse current": "Reverse Current",
    "reverse leakage current": "Reverse Current",
    "current reverse leakage @ vr": "Reverse Current",
    "current - reverse leakage @ vr": "Reverse Current",
    "current - reverse leakage": "Reverse Current",
    "current reverse leakage": "Reverse Current",
    "ir": "Reverse Current",
    "max reverse current": "Reverse Current",
    "reverse current max": "Reverse Current",
    "leakage current (reverse)": "Reverse Current",
    "reverse recovery time": "Reverse Recovery Time",
    "trr": "Reverse Recovery Time",
    "reverse recovery time (trr)": "Reverse Recovery Time",
    "recovery time (trr)": "Reverse Recovery Time",
    "reverse recovery speed": "Reverse Recovery Time",
    "recovery speed": "Reverse Recovery Time",
    "speed (recovery)": "Reverse Recovery Time",
    # ── Transistors (BJT & MOSFET) ─────────────────────────────────────
    "dc current gain": "Current Gain (hFE)",
    "hfe": "Current Gain (hFE)",
    "dc current gain (hfe)": "Current Gain (hFE)",
    "current gain": "Current Gain (hFE)",
    "hfe min": "Current Gain (hFE)",
    "collector emitter saturation voltage": "Collector-Emitter Saturation Voltage",
    "vce sat": "Collector-Emitter Saturation Voltage",
    "vce(sat)": "Collector-Emitter Saturation Voltage",
    "collector-emitter saturation voltage (max)": "Collector-Emitter Saturation Voltage",
    "vce saturation": "Collector-Emitter Saturation Voltage",
    "continuous collector current": "Continuous Collector Current",
    "collector current": "Continuous Collector Current",
    "ic": "Continuous Collector Current",
    "continuous collector current (ic)": "Continuous Collector Current",
    "max collector current": "Continuous Collector Current",
    "continuous drain current (id)": "Continuous Drain Current (Id)",
    "id": "Continuous Drain Current (Id)",
    "current - continuous drain (id) @ 25°c": "Continuous Drain Current (Id)",
    "continuous drain current": "Continuous Drain Current (Id)",
    "drain current": "Continuous Drain Current (Id)",
    "drain to source voltage (vdss)": "Drain to Source Voltage (Vdss)",
    "vdss": "Drain to Source Voltage (Vdss)",
    "voltage - drain source (vdss)": "Drain to Source Voltage (Vdss)",
    "drain-source breakdown voltage": "Drain to Source Voltage (Vdss)",
    "drain source voltage": "Drain to Source Voltage (Vdss)",
    "gate to source threshold voltage (vgs th)": "Gate to Source Threshold Voltage (Vgs th)",
    "gate to source threshold voltage": "Gate to Source Threshold Voltage (Vgs th)",
    "vgs th": "Gate to Source Threshold Voltage (Vgs th)",
    "vgs(th)": "Gate to Source Threshold Voltage (Vgs th)",
    "voltage - gate threshold (vgs th)": "Gate to Source Threshold Voltage (Vgs th)",
    "gate threshold voltage": "Gate to Source Threshold Voltage (Vgs th)",
    "on resistance (rds on)": "On Resistance (Rds On)",
    "rds on": "On Resistance (Rds On)",
    "rds(on)": "On Resistance (Rds On)",
    "rds(on) max": "On Resistance (Rds On)",
    "drain to source on resistance": "On Resistance (Rds On)",
    "rds on (max)": "On Resistance (Rds On)",
    "gate charge": "Gate Charge",
    "total gate charge": "Gate Charge",
    "qg": "Gate Charge",
    "gate charge (qg)": "Gate Charge",
    "total gate charge (qg)": "Gate Charge",
    "input capacitance": "Input Capacitance",
    "ciss": "Input Capacitance",
    "input capacitance (ciss)": "Input Capacitance",
    "capacitance - input": "Input Capacitance",
    "output capacitance": "Output Capacitance",
    "coss": "Output Capacitance",
    "output capacitance (coss)": "Output Capacitance",
    "capacitance - output": "Output Capacitance",
    "reverse recovery charge": "Reverse Recovery Charge (Qrr)",
    "qrr": "Reverse Recovery Charge (Qrr)",
    "reverse recovery charge (qrr)": "Reverse Recovery Charge (Qrr)",
    # ── Integrated Circuits (ICs) & Power ──────────────────────────────
    "supply voltage": "Supply Voltage",
    "voltage - supply": "Supply Voltage",
    "operating supply voltage": "Supply Voltage",
    "supply voltage range": "Supply Voltage",
    "voltage supply": "Supply Voltage",
    "power supply voltage": "Supply Voltage",
    "vcc": "Supply Voltage",
    "vdd": "Supply Voltage",
    # ── Min Input / Supply Voltage ─────────────────────────────────────
    "voltage supply span min": "Min Input Voltage",
    "voltage - supply span (min)": "Min Input Voltage",
    "voltage supply span (min)": "Min Input Voltage",
    "supply span min": "Min Input Voltage",
    "supply voltage span min": "Min Input Voltage",
    "supply voltage - min": "Min Input Voltage",
    "supply voltage min": "Min Input Voltage",
    "supply voltage (min)": "Min Input Voltage",
    "min supply voltage": "Min Input Voltage",
    "min input voltage": "Min Input Voltage",
    "input voltage min": "Min Input Voltage",
    "input voltage - min": "Min Input Voltage",
    "input voltage (min)": "Min Input Voltage",
    "voltage input min": "Min Input Voltage",
    "voltage - input (min)": "Min Input Voltage",
    "operating supply voltage min": "Min Input Voltage",
    "operating voltage min": "Min Input Voltage",
    # ── Max Input / Supply Voltage ─────────────────────────────────────
    "voltage supply span max": "Max Input Voltage",
    "voltage - supply span (max)": "Max Input Voltage",
    "voltage supply span (max)": "Max Input Voltage",
    "supply span max": "Max Input Voltage",
    "supply voltage span max": "Max Input Voltage",
    "supply voltage - max": "Max Input Voltage",
    "supply voltage max": "Max Input Voltage",
    "supply voltage (max)": "Max Input Voltage",
    "max supply voltage": "Max Input Voltage",
    "max input voltage": "Max Input Voltage",
    "input voltage max": "Max Input Voltage",
    "input voltage - max": "Max Input Voltage",
    "input voltage (max)": "Max Input Voltage",
    "voltage input max": "Max Input Voltage",
    "voltage - input (max)": "Max Input Voltage",
    "operating supply voltage max": "Max Input Voltage",
    "operating voltage max": "Max Input Voltage",
    "supply current": "Supply Current",
    "current - supply": "Supply Current",
    "operating supply current": "Supply Current",
    "supply current (max)": "Supply Current",
    "icc": "Supply Current",
    "idd": "Supply Current",
    "output current": "Output Current",
    "current - output": "Output Current",
    "output current max": "Output Current",
    "max output current": "Output Current",
    "continuous output current": "Output Current",
    "iout": "Output Current",
    "interface": "Interface",
    "connectivity": "Interface",
    "protocols": "Interface",
    "communication interface": "Interface",
    "memory size": "Memory Size",
    "memory depth": "Memory Size",
    "capacity": "Memory Size",
    "program memory size": "Memory Size",
    "ram size": "Memory Size",
    "flash memory size": "Memory Size",
    "memory type": "Memory Type",
    "non-volatile memory type": "Memory Type",
    "memory category": "Memory Type",
    "clock frequency": "Clock Frequency",
    "clock speed": "Clock Frequency",
    "max clock frequency": "Clock Frequency",
    "frequency - clock": "Clock Frequency",
    "oscillator frequency": "Clock Frequency",
    "number of pins": "Pin Count",
    "pin count": "Pin Count",
    "pins": "Pin Count",
    "number of positions": "Pin Count",
    "positions count": "Pin Count",
    "no. of pins": "Pin Count",
    "termination count": "Pin Count",
    "core processor": "Core Processor",
    "core size": "Core Processor",
    "core family": "Core Processor",
    "processor core": "Core Processor",
    "cpu": "Core Processor",
    "core width": "Core Width",
    "data bus width": "Core Width",
    "bit size": "Core Width",
    "core size (bits)": "Core Width",
    "adc / dac resolution": "ADC / DAC Resolution",
    "data converters": "ADC / DAC Resolution",
    "adc resolution": "ADC / DAC Resolution",
    "dac resolution": "ADC / DAC Resolution",
    "converter resolution": "ADC / DAC Resolution",
    "common mode rejection ratio": "Common Mode Rejection Ratio (CMRR)",
    "cmrr": "Common Mode Rejection Ratio (CMRR)",
    "common mode rejection ratio (cmrr)": "Common Mode Rejection Ratio (CMRR)",
    "slew rate": "Slew Rate",
    "slew rate (typ)": "Slew Rate",
    "slew rate max": "Slew Rate",
    "logic type": "Logic Type",
    "logic family": "Logic Type",
    "logic function": "Logic Type",
    "output type": "Output Type",
    "logic output type": "Output Type",
    "output configuration": "Output Type",
    "reference voltage": "Reference Voltage",
    "voltage reference": "Reference Voltage",
    "internal reference voltage": "Reference Voltage",
    # ── Bandwidth ──────────────────────────────────────────────────────
    "bandwidth": "Bandwidth",
    "3db bandwidth": "Bandwidth",
    "-3db bandwidth": "Bandwidth",
    "3 db bandwidth": "Bandwidth",
    "-3 db bandwidth": "Bandwidth",
    "bandwidth (-3db)": "Bandwidth",
    "bandwidth -3db": "Bandwidth",
    "3db bandbreite": "Bandwidth",
    "-3db bandbreite": "Bandwidth",
    "bandbreite": "Bandwidth",
    "gain bandwidth product": "Bandwidth",
    "gain bandwidth product (gbwp)": "Bandwidth",
    "gain bandwidth product - gbwp": "Bandwidth",
    "gain bandwidth": "Bandwidth",
    "gbw": "Bandwidth",
    "gbwp": "Bandwidth",
    "cutoff frequency": "Bandwidth",
    # ── Input Bias Current ─────────────────────────────────────────────
    "input bias current": "Input Bias Current",
    "input bias current (ib)": "Input Bias Current",
    "max input bias current": "Input Bias Current",
    "current input bias": "Input Bias Current",
    "current - input bias": "Input Bias Current",
    "current - input bias (max)": "Input Bias Current",
    "ib": "Input Bias Current",
    # ── Input Offset Voltage ───────────────────────────────────────────
    "input offset voltage": "Input Offset Voltage",
    "input offset voltage (vios)": "Input Offset Voltage",
    "input offset voltage (vos)": "Input Offset Voltage",
    "max input offset voltage": "Input Offset Voltage",
    "voltage input offset": "Input Offset Voltage",
    "voltage - input offset": "Input Offset Voltage",
    "voltage - input offset (vos)": "Input Offset Voltage",
    "voltage - input offset (vios)": "Input Offset Voltage",
    "voltage - input offset (max)": "Input Offset Voltage",
    "vos - input offset voltage": "Input Offset Voltage",
    "vios - input offset voltage": "Input Offset Voltage",
    "offset voltage": "Input Offset Voltage",
    "vos": "Input Offset Voltage",
    "vios": "Input Offset Voltage",
    # ── Number of Channels / Circuits ──────────────────────────────────
    "number of channels": "Number of Channels",
    "channels": "Number of Channels",
    "channel count": "Number of Channels",
    "number of circuits": "Number of Channels",
    "circuits": "Number of Channels",
    "circuit count": "Number of Channels",
    "num channels": "Number of Channels",
    "num circuits": "Number of Channels",
    "no. of channels": "Number of Channels",
    "no. of circuits": "Number of Channels",
    "number of outputs": "Number of Outputs",
    "outputs": "Number of Outputs",
    "output count": "Number of Outputs",
    # ── Electromechanical & Mechanical ─────────────────────────────────
    "switch configuration": "Circuit / Contact Form",
    "circuit": "Circuit / Contact Form",
    "poles and throws": "Circuit / Contact Form",
    "contact form": "Circuit / Contact Form",
    "switch circuit": "Circuit / Contact Form",
    "contact rating": "Contact Rating",
    "contact current rating": "Contact Rating",
    "contact rating @ voltage": "Contact Rating",
    "contact current rating (max)": "Contact Rating",
    "switch contact rating": "Contact Rating",
    "contact resistance": "Contact Resistance",
    "max contact resistance": "Contact Resistance",
    "switch contact resistance": "Contact Resistance",
    "insulation resistance": "Insulation Resistance",
    "insulation resistance (min)": "Insulation Resistance",
    "min insulation resistance": "Insulation Resistance",
    "dielectric strength": "Dielectric Strength",
    "dielectric voltage withstand": "Dielectric Strength",
    "voltage withstand": "Dielectric Strength",
    "actuator type": "Actuator Type",
    "actuator style": "Actuator Type",
    "actuator": "Actuator Type",
    "switch actuator": "Actuator Type",
    "illumination": "Illumination",
    "illumination type": "Illumination",
    "illumination voltage": "Illumination",
    "backlight": "Illumination",
    "illuminated": "Illumination",
    "coil voltage": "Coil Voltage",
    "relay coil voltage": "Coil Voltage",
    "coil voltage (dc)": "Coil Voltage",
    "coil voltage (ac)": "Coil Voltage",
    "coil resistance": "Coil Resistance",
    "relay coil resistance": "Coil Resistance",
    "coil resistance (ohms)": "Coil Resistance",
    "coil power": "Coil Power",
    "coil power consumption": "Coil Power",
    "coil power (watts)": "Coil Power",
    "contact material": "Contact Material",
    "relay contact material": "Contact Material",
    "contact plating": "Contact Material",
    "row count": "Row Count",
    "number of rows": "Row Count",
    "rows": "Row Count",
    "pitch": "Pitch",
    "pitch - mating": "Pitch",
    "contact pitch": "Pitch",
    "spacing": "Pitch",
    "gender / type": "Gender / Type",
    "gender": "Gender / Type",
    "connector gender": "Gender / Type",
    "plug / receptacle": "Gender / Type",
    "contact type": "Gender / Type",
    "mounting orientation": "Mounting Orientation",
    "mounting angle": "Mounting Orientation",
    "connector orientation": "Mounting Orientation",
    "right angle / vertical": "Mounting Orientation",
    "fan airflow": "Fan Airflow",
    "airflow": "Fan Airflow",
    "airflow (cfm)": "Fan Airflow",
    "max airflow": "Fan Airflow",
    "fan speed": "Fan Speed",
    "fan rated speed": "Fan Speed",
    "rated fan speed": "Fan Speed",
    "speed (rpm)": "Fan Speed",
    "rated speed (rpm)": "Fan Speed",
    "fan rotational speed": "Fan Speed",
    "fan static pressure": "Fan Static Pressure",
    "static pressure": "Fan Static Pressure",
    "static pressure (in h2o)": "Fan Static Pressure",
    "fan noise": "Fan Noise",
    "noise": "Fan Noise",
    "noise (dba)": "Fan Noise",
    "acoustic noise": "Fan Noise",
    "fan bearing type": "Fan Bearing Type",
    "bearing type": "Fan Bearing Type",
    "bearing": "Fan Bearing Type",
    "fan rated voltage": "Fan Rated Voltage",
    "rated voltage (fan)": "Fan Rated Voltage",
    # ── General & Environmental ───────────────────────────────────────
    "operating temperature": "Operating Temperature",
    "operating temp": "Operating Temperature",
    "operating temperature range": "Operating Temperature",
    "temp range": "Operating Temperature",
    "temperature range": "Operating Temperature",
    "min operating temperature": "Operating Temperature",
    "max operating temperature": "Operating Temperature Max",
    "operating temperature max": "Operating Temperature Max",
    "operating temp max": "Operating Temperature Max",
    "operating temperature - max": "Operating Temperature Max",
    "operating temperature min": "Operating Temperature Min",
    "operating temp min": "Operating Temperature Min",
    "operating temperature - min": "Operating Temperature Min",
    "min operating temperature": "Operating Temperature Min",
    "storage temperature": "Storage Temperature",
    "storage temp": "Storage Temperature",
    "storage temperature range": "Storage Temperature",
    "storage temperature max": "Storage Temperature",
    "storage temperature min": "Storage Temperature",
    "package / case": "Package Type",
    "package/case": "Package Type",
    "package": "Package Type",
    "case/package": "Package Type",
    "case": "Package Type",
    "casing": "Package Type",
    "packaging": "Package Type",
    "device package": "Package Type",
    "supplier device package": "Package Type",
    "supplier device packaging": "Package Type",
    "package type": "Package Type",
    "package-type": "Package Type",
    "package_type": "Package Type",
    "termination style": "Termination Style",
    "termination": "Termination Style",
    "termination type": "Termination Style",
    "contact termination": "Termination Style",
    "termination method": "Termination Style",
    "moisture sensitivity level": "Moisture Sensitivity Level (MSL)",
    "msl": "Moisture Sensitivity Level (MSL)",
    "moisture sensitivity level (msl)": "Moisture Sensitivity Level (MSL)",
    "rohs status": "RoHS Status",
    "rohs": "RoHS Status",
    "rohs compliant": "RoHS Status",
    "lead free status": "Lead-Free Status",
    "lead free": "Lead-Free Status",
    "pb free": "Lead-Free Status",
    "lead free status (rohs)": "Lead-Free Status",
    "halogen free status": "Halogen-Free Status",
    "halogen free": "Halogen-Free Status",
    "physical width": "Physical Width",
    "width": "Physical Width",
    "dimension width": "Physical Width",
    "package width": "Physical Width",
    "physical length": "Physical Length",
    "length": "Physical Length",
    "dimension length": "Physical Length",
    "package length": "Physical Length",
    "physical height": "Physical Height",
    "height": "Physical Height",
    "dimension height": "Physical Height",
    "package height": "Physical Height",
    "max height": "Physical Height",
    "weight": "Weight",
    "unit weight": "Weight",
    "device weight": "Weight",
    "weight (grams)": "Weight",
    "color": "Color",
    "led color": "Color",
    "colour": "Color",
    "material": "Material",
    "body material": "Material",
    "housing material": "Material",
    "mounting hole diameter": "Mounting Hole Diameter",
    "mounting hole": "Mounting Hole Diameter",
    "hole diameter": "Mounting Hole Diameter",
    "mounting hole size": "Mounting Hole Diameter",
}

# Pre-sanitize all keys in PARAMETER_MAP at startup to guarantee perfect lookup hits
PARAMETER_MAP = {sanitize_parameter_name(k): v for k, v in PARAMETER_MAP.items()}


def is_parameter_ignored(name: str, plugin=None) -> bool:
    """
    Check if a parameter raw name is explicitly ignored.
    """
    if not name:
        return False

    key = sanitize_parameter_name(name.strip())
    if plugin:
        try:
            learned_json = plugin.get_setting("LEARNED_PARAMETER_MAPPINGS") or "{}"
            import json

            learned = json.loads(learned_json)
            if isinstance(learned, dict):
                for k, v in learned.items():
                    if sanitize_parameter_name(k) == key:
                        if isinstance(v, dict) and v.get("is_ignored"):
                            return True
        except Exception as e:
            logger.warning(f"Error checking ignored parameter: {e}")

    return False


def normalize_parameter_name(name: str, plugin=None) -> str:
    """
    Standardize a parameter name to a canonical name using built-in PARAMETER_MAP
    and/or user-learned mappings from settings.

    If the parameter name is unknown, it's tracked in the 'TRACKED_UNKNOWN_PARAMETERS' setting.
    """
    if not name:
        return ""

    stripped = name.strip()
    key = sanitize_parameter_name(stripped)

    # 1. Check user-defined learned mappings first (case-insensitive key comparison)
    if plugin:
        try:
            learned_json = plugin.get_setting("LEARNED_PARAMETER_MAPPINGS") or "{}"
            import json

            learned = json.loads(learned_json)
            if isinstance(learned, dict):
                # Search case-insensitively using sanitized keys, supporting both strings and dicts
                learned_lower = {}
                for k, v in learned.items():
                    if not k or v is None:
                        continue
                    sanitized_k = sanitize_parameter_name(k)
                    if isinstance(v, dict):
                        if v.get("is_ignored"):
                            # Ignored parameter maps to empty or special token
                            learned_lower[sanitized_k] = ""
                        else:
                            learned_lower[sanitized_k] = str(
                                v.get("canonical_name", "")
                            ).strip()
                    else:
                        learned_lower[sanitized_k] = str(v).strip()

                if key in learned_lower:
                    return learned_lower[key]
        except Exception as e:
            logger.warning(f"Error loading learned parameter mappings: {e}")

    # 2. Check hardcoded mapping
    if key in PARAMETER_MAP:
        return PARAMETER_MAP[key]

    # 3. Parameter is unknown: track it if not ignored!
    if plugin and not is_parameter_ignored(stripped, plugin):
        try:
            unknowns_json = plugin.get_setting("TRACKED_UNKNOWN_PARAMETERS") or "{}"
            import json

            unknowns = json.loads(unknowns_json)
            if not isinstance(unknowns, dict):
                unknowns = {}

            # Increment frequency count
            unknowns[stripped] = unknowns.get(stripped, 0) + 1

            plugin.set_setting(
                "TRACKED_UNKNOWN_PARAMETERS", json.dumps(unknowns, ensure_ascii=False)
            )
        except Exception as e:
            logger.warning(f"Error saving tracked unknown parameter: {e}")

    return stripped


# ═══════════════════════════════════════════════════════════════════
#  Category Template Filter & Drop Auditing
# ═══════════════════════════════════════════════════════════════════


@dataclass
class CategoryFilterResult:
    """
    Result of filtering a parameter list against a category's template whitelist.

    Attributes:
        accepted_parameters: Normalized parameter dicts that matched a template
            in the category hierarchy and should be persisted.
        dropped_parameters: Audit list of parameters excluded because they have
            no matching template. Each entry is a dict with keys
            ``supplier_key``, ``value``, and ``reason``.
    """

    accepted_parameters: list
    dropped_parameters: list


def filter_parameters_by_category(
    normalized_params: list,
    resolved_templates: dict,
) -> CategoryFilterResult:
    """
    Filter a list of normalized parameter dicts against the category template
    whitelist, building an audit trail of every dropped entry.

    This function must be called **after** ``normalize_parameter_list()`` so
    that each dict already has the canonical InvenTree parameter name in its
    ``"name"`` key.

    The function deliberately does **not** touch the unmapped-key tracking
    workflow (``TRACKED_UNKNOWN_PARAMETERS``).  Parameters that never made it
    through the ``normalize_parameter_name`` step because they were unknown are
    not affected here.

    Args:
        normalized_params:
            List of dicts produced by ``normalize_parameter_list()``.
            Each dict has at minimum: ``"name"``, ``"value"``, ``"unit"``.
        resolved_templates:
            Mapping of ``template_name → PartCategoryParameterTemplate``
            returned by ``get_resolved_category_templates(category)``.
            Pass an **empty dict** to indicate "no templates defined anywhere
            in the hierarchy" — every parameter will then be dropped when
            ``LIMIT_PARAMETERS_TO_CATEGORY`` is True.

    Returns:
        :class:`CategoryFilterResult` with two lists:
        ``accepted_parameters`` and ``dropped_parameters``.
    """
    accepted: list = []
    dropped: list = []

    if not resolved_templates:
        # No category templates defined: drop all parameters with audit trail
        for param in normalized_params:
            name: str = param.get("name", "")
            value: str = param.get("value", "")
            dropped.append(
                {
                    "supplier_key": name,
                    "value": value,
                    "reason": "not_in_category_templates",
                }
            )
            logger.debug(
                "CategoryFilter: dropped '%s'='%s' — not in category templates",
                name,
                value,
            )
        return CategoryFilterResult(
            accepted_parameters=accepted,
            dropped_parameters=dropped,
        )

    # Optional learned mappings lookup without touching unknown tracking
    learned_mappings = {}
    try:
        from plugin.registry import registry
        plug = registry.get_plugin("smartparts")
        if plug:
            import json
            raw_json = plug.get_setting("LEARNED_PARAMETER_MAPPINGS") or "{}"
            data = json.loads(raw_json)
            if isinstance(data, dict):
                for k, v in data.items():
                    if k and v is not None:
                        val = v.get("canonical_name", "") if isinstance(v, dict) else str(v)
                        if val:
                            learned_mappings[sanitize_parameter_name(k)] = val.strip()
    except Exception:
        pass

    def _resolve_canonical_name(raw: str) -> str:
        s = sanitize_parameter_name(raw)
        if s in learned_mappings:
            return learned_mappings[s]
        if s in PARAMETER_MAP:
            return PARAMETER_MAP[s]
        return raw

    # Build fast template lookup mappings:
    # 1. Direct lowercase: "resistance" -> "Resistance"
    template_by_lower = {name.lower(): name for name in resolved_templates}
    # 2. Sanitized lowercase: "package case" -> "Package / Case"
    template_by_sanitized = {sanitize_parameter_name(name): name for name in resolved_templates}
    # 3. Canonical mapping of category templates:
    # E.g. if category template is "Power", and "Power" maps to "Rated Power",
    # then "rated power" -> "Power"
    template_by_canon = {}
    for name in resolved_templates:
        c = _resolve_canonical_name(name)
        if c:
            template_by_canon[c.lower()] = name
            template_by_canon[sanitize_parameter_name(c)] = name

    accepted_by_template = {}
    dropped: list = []

    for param in normalized_params:
        name: str = param.get("name", "")
        value: str = param.get("value", "")
        raw_name = str(name).strip()

        if not raw_name:
            continue

        matched_template_name = None
        match_priority = 99

        # Priority 1: Direct case-insensitive match against category templates
        raw_lower = raw_name.lower()
        if raw_lower in template_by_lower:
            matched_template_name = template_by_lower[raw_lower]
            match_priority = 1

        # Priority 2: Sanitized match (handles spacing / punctuation differences)
        if not matched_template_name:
            sanitized_name = sanitize_parameter_name(raw_name)
            if sanitized_name in template_by_sanitized:
                matched_template_name = template_by_sanitized[sanitized_name]
                match_priority = 2

        # Priority 3: Alias resolution via PARAMETER_MAP / learned mappings
        # (e.g. "power watts" -> "Rated Power")
        if not matched_template_name:
            canonical_name = _resolve_canonical_name(raw_name)
            canon_lower = canonical_name.lower()
            if canon_lower in template_by_lower:
                matched_template_name = template_by_lower[canon_lower]
                match_priority = 3
            elif sanitize_parameter_name(canonical_name) in template_by_sanitized:
                matched_template_name = template_by_sanitized[sanitize_parameter_name(canonical_name)]
                match_priority = 3
            elif canon_lower in template_by_canon:
                matched_template_name = template_by_canon[canon_lower]
                match_priority = 3
            elif sanitize_parameter_name(canonical_name) in template_by_canon:
                matched_template_name = template_by_canon[sanitize_parameter_name(canonical_name)]
                match_priority = 3

        if matched_template_name:
            param_copy = dict(param)
            param_copy["name"] = matched_template_name

            if matched_template_name in accepted_by_template:
                prev_priority, prev_param, prev_raw, prev_val = accepted_by_template[matched_template_name]
                if match_priority < prev_priority:
                    accepted_by_template[matched_template_name] = (match_priority, param_copy, raw_name, value)
                    dropped.append(
                        {
                            "supplier_key": prev_raw,
                            "value": prev_val,
                            "reason": "not_in_category_templates",
                        }
                    )
                else:
                    dropped.append(
                        {
                            "supplier_key": raw_name,
                            "value": value,
                            "reason": "not_in_category_templates",
                        }
                    )
            else:
                accepted_by_template[matched_template_name] = (match_priority, param_copy, raw_name, value)
        else:
            dropped.append(
                {
                    "supplier_key": raw_name,
                    "value": value,
                    "reason": "not_in_category_templates",
                }
            )
            logger.debug(
                "CategoryFilter: dropped '%s'='%s' — not in category templates",
                raw_name,
                value,
            )

    accepted = [item[1] for item in accepted_by_template.values()]

    logger.debug(
        "CategoryFilter: %d accepted, %d dropped (total %d)",
        len(accepted),
        len(dropped),
        len(normalized_params),
    )
    return CategoryFilterResult(
        accepted_parameters=accepted,
        dropped_parameters=dropped,
    )
