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

logger = logging.getLogger('inventree_smart_parts.services.normalizer')


# ═══════════════════════════════════════════════════════════════════
#  SI Prefix System
# ═══════════════════════════════════════════════════════════════════

@dataclass
class SIPrefix:
    symbol: str
    name: str
    exponent: int   # power of 10

SI_PREFIXES = [
    SIPrefix('p',  'pico',  -12),
    SIPrefix('n',  'nano',  -9),
    SIPrefix('µ',  'micro', -6),
    SIPrefix('m',  'milli', -3),
    SIPrefix('',   '',       0),
    SIPrefix('k',  'kilo',   3),
    SIPrefix('M',  'mega',   6),
    SIPrefix('G',  'giga',   9),
    SIPrefix('T',  'tera',  12),
]

# Lookup: symbol/alias → SIPrefix
_PREFIX_MAP = {}
for _p in SI_PREFIXES:
    if _p.symbol:
        _PREFIX_MAP[_p.symbol] = _p
    if _p.name:
        _PREFIX_MAP[_p.name] = _p
        _PREFIX_MAP[_p.name + 's'] = _p  # plurals (e.g. "microfarads")
# Common aliases
_PREFIX_MAP['u']     = _PREFIX_MAP['µ']       # ASCII u for micro
_PREFIX_MAP['μ']     = _PREFIX_MAP['µ']       # Greek mu
_PREFIX_MAP['micro'] = _PREFIX_MAP['µ']


# ═══════════════════════════════════════════════════════════════════
#  Unit Definitions
# ═══════════════════════════════════════════════════════════════════

@dataclass
class UnitDef:
    """Defines a base unit with its canonical symbol and recognized aliases."""
    symbol: str                # Canonical: "F", "Ω", "V", ...
    category: str              # "capacitance", "resistance", ...
    aliases: tuple             # Alternative spellings
    preferred_prefixes: tuple  # Preferred SI prefix exponents for display

UNITS = [
    UnitDef('F',  'capacitance',  ('farad', 'farads', 'fd'),          (-12, -9, -6)),
    UnitDef('Ω',  'resistance',   ('ohm', 'ohms', 'r'),              (0, 3, 6)),
    UnitDef('H',  'inductance',   ('henry', 'henrys', 'henries'),     (-9, -6, -3)),
    UnitDef('V',  'voltage',      ('volt', 'volts'),                  (-3, 0, 3)),
    UnitDef('A',  'current',      ('amp', 'amps', 'ampere', 'amperes'), (-9, -6, -3, 0)),
    UnitDef('W',  'power',        ('watt', 'watts'),                  (-6, -3, 0, 3)),
    UnitDef('Hz', 'frequency',    ('hertz',),                         (0, 3, 6, 9)),
    UnitDef('°C', 'temperature',  ('c', 'celsius', 'deg c', 'degc'),  (0,)),
    UnitDef('°F', 'temperature',  ('fahrenheit', 'deg f', 'degf'),    (0,)),
    UnitDef('K',  'temperature',  ('kelvin',),                        (0,)),
    UnitDef('s',  'time',         ('sec', 'second', 'seconds'),       (-9, -6, -3, 0)),
    UnitDef('m',  'length',       ('meter', 'meters', 'metre'),       (-3, 0)),
    UnitDef('%',  'percentage',   ('percent', 'pct'),                 (0,)),
    UnitDef('dB', 'decibel',      ('decibel', 'decibels'),            (0,)),
    UnitDef('ppm','concentration',('ppm',),                           (0,)),
]

# Build lookup: alias → UnitDef
_UNIT_MAP = {}
for _u in UNITS:
    _UNIT_MAP[_u.symbol.lower()] = _u
    for _a in _u.aliases:
        _UNIT_MAP[_a.lower()] = _u
# Ω aliases
_UNIT_MAP['ω'] = _UNIT_MAP['ω'] if 'ω' in _UNIT_MAP else _UNIT_MAP.get('ohm')


# ═══════════════════════════════════════════════════════════════════
#  Resistor / Capacitor Shorthand Parser  (e.g. "4k7", "2R2")
# ═══════════════════════════════════════════════════════════════════

_SHORTHAND_RE = re.compile(
    r'^(\d+)([RrKkMm])(\d+)$'
)
_SHORTHAND_UNIT = {
    'r': ('Ω', 0), 'R': ('Ω', 0),
    'k': ('Ω', 3), 'K': ('Ω', 3),
    'm': ('Ω', 6), 'M': ('Ω', 6),  # In EE shorthand, M = Mega for resistance
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
    if letter.upper() not in ('R', 'K', 'M'):
        return None
    value = float(f"{integer}.{decimal}")
    unit_sym, exp = _SHORTHAND_UNIT[letter]
    return value, unit_sym, exp


# ═══════════════════════════════════════════════════════════════════
#  Main Parser
# ═══════════════════════════════════════════════════════════════════

# Regex to split "10.5kΩ", "100 nF", "4.7uF", "-40°C" etc.
_VALUE_UNIT_RE = re.compile(
    r'^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)'   # numeric value
    r'\s*'                                        # optional space
    r'(.+)?$'                                     # unit string (rest)
)

# Regex for pure scientific notation like "0.00001F"
_SCI_FULL_RE = re.compile(
    r'^([+-]?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)$'
)


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
            remainder = raw_lower[len(pfx.name):]
            u = _UNIT_MAP.get(remainder)
            if u:
                return u, pfx

    return None


def _choose_best_prefix(value: float, unit: UnitDef, current_exp: int) -> Tuple[float, SIPrefix]:
    """
    Choose the best SI prefix so the displayed value is in [1, 1000).
    Prefers the unit's preferred_prefixes when possible.
    """
    # Absolute value in base units
    abs_base = abs(value) * (10.0 ** current_exp)
    if abs_base == 0:
        return 0.0, SI_PREFIXES[4]

    sign = 1 if value >= 0 else -1

    best_prefix = SI_PREFIXES[4]  # no prefix
    best_val = abs_base
    best_score = abs(_niceness(abs_base))

    for pfx in SI_PREFIXES:
        if pfx.exponent not in unit.preferred_prefixes and pfx.exponent != 0:
            continue
        candidate = abs_base / (10.0 ** pfx.exponent)
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
    formatted = f"{v:.6f}".rstrip('0').rstrip('.')
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


def normalize_parameter(name: str, value: str, unit: str = '') -> NormalizedParam:
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
        name = p.get('name', '')
        value = p.get('value', '')

        # Skip parameters without a name or with a useless value
        if not name or is_useless_value(value):
            continue

        n = normalize_parameter(
            name=name,
            value=value,
            unit=p.get('unit', ''),
        )
        result.append({
            'name': n.name,
            'value': n.value,
            'unit': n.unit,
            '_normalized': n.normalized,
        })
    return result
