"""
Category Mapper  (v3 --- Intelligent Matching Engine)
====================================================

Replaces the naive SequenceMatcher approach with a multi-signal scorer:

Signal 1 --- Token Set Overlap
    Tokenise both sides, expand synonyms, then compute a Dice-coefficient
    style overlap.  Order-independence means "Ceramic Capacitor 10uF" and
    "Capacitor, Ceramic" score identically.

Signal 2 --- Hierarchy Leaf Bias
    The right-most segment of a ">" path is the most specific.  Its tokens
    are given 3-- weight.  The second-to-last segment gets 1.5-- weight.

Signal 3 --- Synonym Expansion
    A user-configurable synonym table (stored as JSON in plugin settings)
    is applied before tokenisation.  Matching after expansion is treated
    as an exact token hit rather than a fuzzy one.

Signal 4 --- Prefix Bonus
    If the distributor leaf node starts with (or is a prefix of) the
    InvenTree leaf node, add a small bonus.

Final score formula (0-100):
    score = 70 * token_dice + 20 * leaf_dice + 10 * prefix_bonus

Threshold default: 45  (lower than before because scoring is tighter)
"""

import json
import logging
import re
from typing import Dict, List, Optional, Set, Tuple

logger = logging.getLogger('inventree_smart_parts.services.category')

# -----------------------------------------------------------------------------
#  Category cache
# -----------------------------------------------------------------------------
_category_cache_flat: Optional[List[Dict]] = None

# -----------------------------------------------------------------------------
#  Built-in synonym table  (always active, augmented by user synonyms)
# -----------------------------------------------------------------------------
_BUILTIN_SYNONYMS: Dict[str, str] = {
    # Capacitors
    'mlcc': 'ceramic capacitor',
    'electrolytic': 'electrolytic capacitor',
    'tantalum': 'tantalum capacitor',
    'film cap': 'film capacitor',
    'cap': 'capacitor',
    'caps': 'capacitors',
    # Resistors
    'res': 'resistor',
    'resistors': 'resistor',
    'trimmer': 'trimmer resistor',
    'potentiometer': 'resistor',
    'varistor': 'resistor',
    'thermistor': 'resistor',
    'ntc': 'thermistor',
    'ptc': 'thermistor',
    # Inductors / Magnetics
    'inductor': 'inductor',
    'inductors': 'inductor',
    'ferrite bead': 'ferrite',
    'choke': 'inductor',
    'transformer': 'transformer',
    # Semiconductors
    'ic': 'integrated circuit',
    'ics': 'integrated circuit',
    'mcu': 'microcontroller',
    'microcontrollers': 'microcontroller',
    'mpu': 'microprocessor',
    'fpga': 'programmable logic',
    'cpld': 'programmable logic',
    'dsp': 'digital signal processor',
    'opamp': 'op amp',
    'op-amp': 'op amp',
    'operational amplifier': 'op amp',
    'comparator': 'comparator',
    'voltage reference': 'voltage reference',
    'vreg': 'voltage regulator',
    'ldo': 'voltage regulator',
    'buck': 'dc dc converter',
    'boost': 'dc dc converter',
    'dcdc': 'dc dc converter',
    'dc dc': 'dc dc converter',
    'switching regulator': 'dc dc converter',
    'power management': 'power',
    'pmic': 'power management ic',
    # MOSFETs / Transistors
    'mosfet': 'transistor',
    'bjt': 'transistor',
    'jfet': 'transistor',
    'igbt': 'transistor',
    # Diodes
    'diode': 'diode',
    'led': 'led',
    'light emitting diode': 'led',
    'zener': 'zener diode',
    'schottky': 'schottky diode',
    'tvs': 'transient voltage suppressor',
    'esd': 'protection',
    # Connectors
    'connector': 'connector',
    'connectors': 'connector',
    'header': 'connector',
    'socket': 'connector',
    'terminal': 'connector',
    'terminals': 'connector',
    # Switches / Relays
    'switch': 'switch',
    'relay': 'relay',
    'pushbutton': 'switch',
    # Passives generic
    'passive': 'passives',
    'passives': 'passives',
    'discrete': 'discrete',
    # Crystals / Oscillators
    'crystal': 'crystal',
    'xtal': 'crystal',
    'oscillator': 'oscillator',
    'resonator': 'resonator',
    # Sensors
    'sensor': 'sensor',
    'sensors': 'sensor',
    'accelerometer': 'sensor',
    'gyroscope': 'sensor',
    'temperature sensor': 'sensor',
    'humidity sensor': 'sensor',
    'pressure sensor': 'sensor',
    # RF / Wireless
    'rf': 'rf',
    'antenna': 'antenna',
    'bluetooth': 'wireless',
    'wifi': 'wireless',
    'wi fi': 'wireless',
    'zigbee': 'wireless',
    'module': 'module',
    # Mechanical
    'fuse': 'fuse',
    'heatsink': 'thermal',
    'heat sink': 'thermal',
    'fan': 'cooling',
    # Memory
    'flash': 'memory',
    'eeprom': 'memory',
    'sram': 'memory',
    'dram': 'memory',
    'ram': 'memory',
    'rom': 'memory',
    # Interface
    'uart': 'interface',
    'spi': 'interface',
    'i2c': 'interface',
    'usb': 'interface',
    'can': 'interface',
    'ethernet': 'interface',
    # Displays
    'lcd': 'display',
    'oled': 'display',
    'display': 'display',
}

# -----------------------------------------------------------------------------
#  Noise words --- ignored during tokenisation
# -----------------------------------------------------------------------------
_STOPWORDS: Set[str] = {
    'and', 'or', 'the', 'a', 'an', 'of', 'for', 'in', 'to', 'with',
    'by', 'on', 'at', 'from', 'as', 'is', 'are', 'other', 'general',
    'misc', 'various', 'smd', 'smt', 'thru', 'hole', 'through', 'surface',
    'mount', 'package', 'type', 'series', 'standard',
}


# -----------------------------------------------------------------------------
#  Category cache
# -----------------------------------------------------------------------------

def get_category_tree() -> List[Dict]:
    """Fetch all InvenTree categories as a flat list with full paths."""
    global _category_cache_flat

    if _category_cache_flat is not None:
        return _category_cache_flat

    try:
        from part.models import PartCategory
        categories = PartCategory.objects.all()

        flat = []
        for cat in categories:
            path_parts = []
            current = cat
            while current is not None:
                path_parts.insert(0, current.name)
                current = current.parent

            flat.append({
                'id': cat.pk,
                'name': cat.name,
                'full_path': ' > '.join(path_parts),
                'path_parts': path_parts,
                'level': cat.level if hasattr(cat, 'level') else len(path_parts) - 1,
            })

        _category_cache_flat = flat
        logger.debug(f"Loaded {len(flat)} InvenTree categories")
        return flat

    except Exception as e:
        logger.error(f"Failed to load categories: {e}")
        return []


def clear_cache():
    """Clear the category cache (call after category changes)."""
    global _category_cache_flat
    _category_cache_flat = None


def get_all_categories_for_ui() -> List[Dict]:
    """Return all categories formatted for dropdown/select UI."""
    categories = get_category_tree()
    return [
        {'id': c['id'], 'name': c['full_path']}
        for c in sorted(categories, key=lambda x: x['full_path'])
    ]


# -----------------------------------------------------------------------------
#  Synonym handling
# -----------------------------------------------------------------------------

def parse_user_synonyms(json_str: str) -> Dict[str, str]:
    """
    Parse the user-supplied synonym JSON string into a normalised dict.

    Expected format (either of):
        {"MLCC": "Ceramic Capacitor", "MCU": "Microcontroller"}
        [{"from": "MLCC", "to": "Ceramic Capacitor"}, ...]

    Returns dict mapping lowercase source --- lowercase target.
    Invalid JSON is logged and silently ignored.
    """
    if not json_str or not json_str.strip():
        return {}
    try:
        raw = json.loads(json_str)
        if isinstance(raw, dict):
            return {k.lower().strip(): v.lower().strip()
                    for k, v in raw.items() if k and v}
        if isinstance(raw, list):
            result = {}
            for item in raw:
                if isinstance(item, dict) and item.get('from') and item.get('to'):
                    result[item['from'].lower().strip()] = item['to'].lower().strip()
            return result
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"[CategoryMapper] Could not parse user synonyms: {e}")
    return {}


def build_synonym_table(user_synonyms_json: str = '') -> Dict[str, str]:
    """Merge built-in synonyms with user-defined ones (user takes priority)."""
    table = dict(_BUILTIN_SYNONYMS)
    table.update(parse_user_synonyms(user_synonyms_json))
    return table


# -----------------------------------------------------------------------------
#  Tokenisation
# -----------------------------------------------------------------------------

_SEP_RE = re.compile(r'[>\\/|,_\-&+\s]+')

# Multi-stage noise strippers applied to distributor category strings
# before tokenisation. Each regex removes a class of noise.
_STRIP_PARENS_RE = re.compile(r'\([^)]*\)')                    # (ICs), (LDO), etc.
_STRIP_VALUES_RE = re.compile(                                  # component value specs
    r'\b\d+(?:\.\d+)?'                                          # number
    r'(?:pf|nf|uf|µf|mf|f'                                     # capacitance
    r'|ohm|kohm|mohm|ω|kω|mω'                                  # resistance
    r'|uh|mh|nh|h'                                              # inductance
    r'|v|kv|mv'                                                 # voltage
    r'|a|ma|µa|ua'                                              # current
    r'|mhz|khz|ghz|hz'                                          # frequency
    r'|w|mw|kw'                                                 # power
    r')\b',
    re.IGNORECASE,
)
_STRIP_PACKAGE_RE = re.compile(                                 # SMD package codes
    r'\b(?:0201|0402|0603|0805|1206|1210|1812|2010|2512'
    r'|sop\d*|soic\d*|qfp\d*|tqfp\d*|bga\d*|dfn\d*|qfn\d*'
    r'|dip\d*|pdip\d*|to-?\d+[a-z]*|sot-?\d+[a-z]*)\b',
    re.IGNORECASE,
)
_STRIP_LONE_NUMS_RE = re.compile(r'\b\d+\b')                   # standalone numbers
_STRIP_MULTI_SPACE_RE = re.compile(r'\s{2,}')                  # collapse whitespace


def _strip_category_noise(text: str) -> str:
    """
    Strip distributor-specific noise from a category string.

    Runs multiple passes to remove:
      1. Parenthetical abbreviations: (ICs), (LDO), (SMD)
      2. Component value specs: 10uF, 50V, 0.1ohm
      3. SMD package codes: 0805, SOIC8, QFN32
      4. Standalone numbers left over after stripping
      5. Excess whitespace
    """
    s = text
    s = _STRIP_PARENS_RE.sub(' ', s)
    s = _STRIP_VALUES_RE.sub(' ', s)
    s = _STRIP_PACKAGE_RE.sub(' ', s)
    s = _STRIP_LONE_NUMS_RE.sub(' ', s)
    s = _STRIP_MULTI_SPACE_RE.sub(' ', s)
    return s.strip()


def _normalise(text: str) -> str:
    """Lowercase and collapse separators into spaces."""
    return ' '.join(_SEP_RE.split(text.lower())).strip()


def _tokenise(text: str, synonyms: Dict[str, str]) -> List[str]:
    """
    Convert text to a de-duplicated token list with synonym expansion.

    Multi-word synonyms are matched greedily before single-word expansion,
    so "Ceramic Capacitor" --- "mlcc" works even when the key is multi-word.
    """
    norm = _normalise(text)

    # Greedy multi-word synonym replacement (longest key first)
    for src in sorted(synonyms, key=len, reverse=True):
        if ' ' in src and src in norm:
            norm = norm.replace(src, synonyms[src])

    words = norm.split()

    # Single-word synonym expansion
    expanded: List[str] = []
    for w in words:
        if w in synonyms:
            expanded.extend(synonyms[w].split())
        else:
            expanded.append(w)

    # Filter stopwords and very short tokens, de-duplicate preserving order
    seen: Set[str] = set()
    result: List[str] = []
    for w in expanded:
        if len(w) >= 2 and w not in _STOPWORDS and w not in seen:
            seen.add(w)
            result.append(w)

    return result


def _leaf_tokens(path_parts: List[str], synonyms: Dict[str, str]) -> List[str]:
    """
    Return a weighted token list that gives the leaf node 3-- weight
    and the second-to-last node 1.5-- weight.
    """
    tokens: List[str] = []
    n = len(path_parts)
    for i, part in enumerate(path_parts):
        pts = _tokenise(part, synonyms)
        depth = i - (n - 1)          # 0 for leaf, -1 for parent, etc.
        if depth == 0:
            tokens.extend(pts * 3)    # leaf --- 3-- weight
        elif depth == -1:
            tokens.extend(pts * 2)    # parent --- 2-- weight
        else:
            tokens.extend(pts)        # ancestors --- 1-- weight
    return tokens


def _dice(tokens_a: List[str], tokens_b: List[str]) -> float:
    """Sorensen-Dice coefficient between two token lists (0.0-1.0)."""
    if not tokens_a or not tokens_b:
        return 0.0
    set_a = set(tokens_a)
    set_b = set(tokens_b)
    intersection = set_a & set_b
    return (2.0 * len(intersection)) / (len(set_a) + len(set_b))



def fuzzy_match_category(
    distributor_category: str,
    threshold: int = 60,
    default_category_name: str = '',
    user_synonyms_json: str = '',
    learned_mappings_json: str = '',
) -> Tuple[Optional[int], str, int]:
    """
    Find the best matching InvenTree category for a distributor category string.

    Args:
        distributor_category:  Category string from distributor API
                               (e.g., "Semiconductors > Voltage Regulators")
        threshold:             Minimum confidence score (0-100) to accept.
        default_category_name: Fallback category name if no match found.
        user_synonyms_json:    JSON string of user-defined synonyms from settings.
        learned_mappings_json: JSON string of learned mappings from settings.
                               Keys are exact distributor category strings;
                               values are InvenTree category path strings.
                               A match here returns 100% confidence immediately.

    Returns:
        Tuple of (category_id, category_path, confidence_score)
        category_id is None if no match found and no default exists.
    """
    if not distributor_category:
        return _get_default_category(default_category_name)

    # Defensive coercion: threshold may arrive as None, str, or int
    try:
        threshold = int(threshold) if threshold is not None else 60
    except (ValueError, TypeError):
        threshold = 60

    # -- Step 0: Check Learned Mappings (absolute override) --
    if learned_mappings_json:
        try:
            learned: Dict[str, str] = json.loads(learned_mappings_json)
            if isinstance(learned, dict) and distributor_category in learned:
                target_path = learned[distributor_category]
                target_norm = _normalize_path(target_path)
                # Resolve to an actual category ID (separator-independent comparison)
                categories = get_category_tree()
                for cat in categories:
                    if _normalize_path(cat['full_path']) == target_norm or \
                       _normalize_path(cat['name']) == target_norm:
                        logger.info(
                            f"Category matched via Learned Mapping: "
                            f"'{distributor_category}' -> '{cat['full_path']}'"
                        )
                        return (cat['id'], cat['full_path'], 100)
                # Path stored but category no longer exists -- log and continue
                logger.warning(
                    f"Learned mapping for '{distributor_category}' points to "
                    f"'{target_path}' which was not found in InvenTree categories. "
                    f"Falling back to fuzzy match."
                )
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"Could not parse learned_mappings_json: {e}")

    categories = get_category_tree()
    if not categories:
        return _get_default_category(default_category_name)

    synonyms = build_synonym_table(user_synonyms_json)

    # Parse the distributor category into path segments and strip noise
    dist_parts_raw = [p.strip() for p in re.split(r'[>/|]', distributor_category) if p.strip()]
    dist_parts = [_strip_category_noise(p) for p in dist_parts_raw]
    dist_parts = [p for p in dist_parts if p]  # drop segments that became empty after stripping

    if not dist_parts:
        dist_parts = dist_parts_raw  # fallback: use original if stripping killed everything

    dist_tokens_flat   = _tokenise(' '.join(dist_parts), synonyms)
    dist_tokens_leafw  = _leaf_tokens(dist_parts, synonyms)
    dist_leaf          = dist_parts[-1] if dist_parts else distributor_category
    dist_leaf_tokens   = _tokenise(dist_leaf, synonyms)

    best_score = 0.0
    best_match = None

    for cat in categories:
        inv_parts       = cat['path_parts']
        inv_tokens_flat = _tokenise(' '.join(inv_parts), synonyms)
        inv_tokens_leafw = _leaf_tokens(inv_parts, synonyms)
        inv_leaf        = inv_parts[-1] if inv_parts else cat['name']
        inv_leaf_tokens = _tokenise(inv_leaf, synonyms)

        # -- Signal 1: whole-path token overlap (order-independent) ------
        sig_path = _dice(dist_tokens_flat, inv_tokens_flat)

        # -- Signal 2: leaf-weighted token overlap -----------------------
        sig_leaf_w = _dice(dist_tokens_leafw, inv_tokens_leafw)

        # -- Signal 3: leaf-only direct overlap --------------------------
        sig_leaf = _dice(dist_leaf_tokens, inv_leaf_tokens)

        # -- Signal 4: prefix / subset bonus -----------------------------
        #  "Ceramic Capacitors" IS a subset of the leaf tokens
        dl = set(dist_leaf_tokens)
        il = set(inv_leaf_tokens)
        prefix_bonus = 0.0
        if dl and il:
            if dl.issubset(il) or il.issubset(dl):
                prefix_bonus = 1.0
            elif dl & il:
                prefix_bonus = len(dl & il) / max(len(dl), len(il))

        # -- Combine signals ---------------------------------------------
        # 40% path, 30% leaf-weighted, 20% leaf-direct, 10% prefix bonus
        score = (
            0.40 * sig_path
          + 0.30 * sig_leaf_w
          + 0.20 * sig_leaf
          + 0.10 * prefix_bonus
        ) * 100.0

        if score > best_score:
            best_score = score
            best_match = cat

    if best_match and best_score >= threshold:
        logger.info(
            f"Category match: '{distributor_category}' --- "
            f"'{best_match['full_path']}' (score: {best_score:.1f})"
        )
        return (best_match['id'], best_match['full_path'], int(best_score))

    logger.info(
        f"No category match for '{distributor_category}' "
        f"(best: {best_score:.1f}, threshold: {threshold})"
    )
    return _get_default_category(default_category_name)


# -----------------------------------------------------------------------------
#  Helpers
# -----------------------------------------------------------------------------

def _get_default_category(name: str) -> Tuple[Optional[int], str, int]:
    """Look up the default/fallback category by name."""
    if not name:
        return (None, '', 0)

    categories = get_category_tree()
    for cat in categories:
        if cat['name'].lower() == name.lower():
            return (cat['id'], cat['full_path'], 50)

    return (None, name, 0)


def _normalize_path(path: str) -> str:
    """
    Normalize a category path string for separator-independent comparison.

    Collapses all separator variants ('/', ' > ', '|', '\\') to a single
    canonical separator '>' and lowercases the result.  This lets stored
    paths like 'Capacitors/Ceramic' match InvenTree full_paths like
    'Capacitors > Ceramic'.
    """
    # Replace all common separator forms with '>'
    normalized = re.sub(r'\s*[>/|\\]\s*', '>', path.strip())
    return normalized.lower()


def learn_category_mapping(
    distributor_category: str,
    chosen_category_path: str,
    plugin,
) -> bool:
    """
    Persist a manual category correction into LEARNED_CATEGORY_MAPPINGS.

    Args:
        distributor_category:  The raw distributor category string
                               (e.g. "Semiconductors > Voltage Regulators").
        chosen_category_path:  The InvenTree category path the user actually chose
                               (e.g. "Power > LDO Regulators").
        plugin:                The live InvenTree plugin instance with get/set_setting.

    Returns:
        True on success, False on error.
    """
    if not distributor_category or not chosen_category_path:
        return False
    try:
        current_json = plugin.get_setting('LEARNED_CATEGORY_MAPPINGS') or '{}'
        mappings: Dict[str, str] = json.loads(current_json)
        if not isinstance(mappings, dict):
            mappings = {}

        if mappings.get(distributor_category) == chosen_category_path:
            logger.debug(
                f"Learned mapping already correct: '{distributor_category}' --- '{chosen_category_path}'"
            )
            return True  # already up-to-date

        mappings[distributor_category] = chosen_category_path
        plugin.set_setting('LEARNED_CATEGORY_MAPPINGS', json.dumps(mappings, ensure_ascii=False))
        logger.info(
            f"Learned category mapping saved: '{distributor_category}' --- '{chosen_category_path}'"
        )
        return True
    except Exception as e:
        logger.error(f"Failed to save learned category mapping: {e}", exc_info=True)
        return False


# -----------------------------------------------------------------------------
#  Debug helper (call from a shell to tune thresholds)
# -----------------------------------------------------------------------------

def debug_match(distributor_category: str, top_n: int = 5,
                user_synonyms_json: str = '') -> List[Dict]:
    """
    Return the top-N scored candidates for a distributor category string.
    Useful for tuning thresholds from a Django shell.

    Usage:
        from inventree_smart_parts.services.category_mapper import debug_match
        debug_match("Semiconductors > Power > LDO Regulators")
    """
    categories = get_category_tree()
    synonyms = build_synonym_table(user_synonyms_json)
    dist_parts_raw = [p.strip() for p in re.split(r'[>/|]', distributor_category) if p.strip()]
    dist_parts = [_strip_category_noise(p) for p in dist_parts_raw]
    dist_parts = [p for p in dist_parts if p] or dist_parts_raw
    dist_tokens_flat  = _tokenise(' '.join(dist_parts), synonyms)
    dist_tokens_leafw = _leaf_tokens(dist_parts, synonyms)
    dist_leaf_tokens  = _tokenise(dist_parts[-1] if dist_parts else distributor_category, synonyms)

    results = []
    for cat in categories:
        inv_parts        = cat['path_parts']
        inv_tokens_flat  = _tokenise(' '.join(inv_parts), synonyms)
        inv_tokens_leafw = _leaf_tokens(inv_parts, synonyms)
        inv_leaf_tokens  = _tokenise(inv_parts[-1] if inv_parts else cat['name'], synonyms)

        s1 = _dice(dist_tokens_flat,  inv_tokens_flat)
        s2 = _dice(dist_tokens_leafw, inv_tokens_leafw)
        s3 = _dice(dist_leaf_tokens,  inv_leaf_tokens)
        dl = set(dist_leaf_tokens)
        il = set(inv_leaf_tokens)
        pb = 1.0 if (dl and il and (dl.issubset(il) or il.issubset(dl))) else (
            len(dl & il) / max(len(dl), len(il)) if (dl & il) else 0.0
        )
        score = (0.40 * s1 + 0.30 * s2 + 0.20 * s3 + 0.10 * pb) * 100

        results.append({
            'path': cat['full_path'],
            'score': round(score, 1),
            'sig_path': round(s1 * 100, 1),
            'sig_leaf_w': round(s2 * 100, 1),
            'sig_leaf': round(s3 * 100, 1),
            'prefix_bonus': round(pb * 100, 1),
        })

    results.sort(key=lambda x: x['score'], reverse=True)
    return results[:top_n]
