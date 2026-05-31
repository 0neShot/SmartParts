"""
Duplicate Checker
=================
Detects existing parts in InvenTree by MPN matching.
Returns existing part info with options to update or skip.
"""

import logging
from typing import Optional, Dict, List
from dataclasses import dataclass

logger = logging.getLogger("inventree_smart_parts.services.duplicates")


@dataclass
class DuplicateResult:
    """Result of a duplicate check."""

    is_duplicate: bool = False
    existing_part_id: Optional[int] = None
    existing_part_name: str = ""
    existing_mpn: str = ""
    existing_manufacturer: str = ""
    match_type: str = ""  # 'exact_mpn', 'fuzzy_mpn', 'name_match'
    confidence: float = 0.0


def check_duplicate(mpn: str, manufacturer: str = "") -> DuplicateResult:
    """
    Check if a part with the given MPN already exists in InvenTree.

    Checks in order:
    1. Exact MPN match in ManufacturerPart table
    2. Case-insensitive MPN match
    3. Partial MPN match (for variants like -ND, -TR suffixes)

    Args:
        mpn: Manufacturer Part Number to check
        manufacturer: Optional manufacturer name for stricter matching

    Returns:
        DuplicateResult with match details
    """
    if not mpn:
        return DuplicateResult()

    try:
        from company.models import ManufacturerPart

        mpn_clean = mpn.strip()

        # 1. Exact MPN match
        exact_matches = ManufacturerPart.objects.filter(MPN=mpn_clean)
        if manufacturer:
            exact_matches_mfr = exact_matches.filter(
                manufacturer__name__iexact=manufacturer.strip()
            )
            if exact_matches_mfr.exists():
                exact_matches = exact_matches_mfr

        if exact_matches.exists():
            match = exact_matches.first()
            return DuplicateResult(
                is_duplicate=True,
                existing_part_id=match.part.pk,
                existing_part_name=match.part.name,
                existing_mpn=match.MPN,
                existing_manufacturer=(
                    str(match.manufacturer) if match.manufacturer else ""
                ),
                match_type="exact_mpn",
                confidence=1.0,
            )

        # 2. Case-insensitive MPN match
        ci_matches = ManufacturerPart.objects.filter(MPN__iexact=mpn_clean)
        if ci_matches.exists():
            match = ci_matches.first()
            return DuplicateResult(
                is_duplicate=True,
                existing_part_id=match.part.pk,
                existing_part_name=match.part.name,
                existing_mpn=match.MPN,
                existing_manufacturer=(
                    str(match.manufacturer) if match.manufacturer else ""
                ),
                match_type="exact_mpn",
                confidence=0.95,
            )

        # 3. Partial match (strip common suffixes)
        base_mpn = _strip_suffixes(mpn_clean)
        if base_mpn != mpn_clean:
            partial = ManufacturerPart.objects.filter(MPN__icontains=base_mpn)
            if partial.exists():
                match = partial.first()
                return DuplicateResult(
                    is_duplicate=True,
                    existing_part_id=match.part.pk,
                    existing_part_name=match.part.name,
                    existing_mpn=match.MPN,
                    existing_manufacturer=(
                        str(match.manufacturer) if match.manufacturer else ""
                    ),
                    match_type="fuzzy_mpn",
                    confidence=0.7,
                )

        return DuplicateResult()

    except ImportError:
        logger.error("Could not import InvenTree models – running outside InvenTree?")
        return DuplicateResult()
    except Exception as e:
        logger.error(f"Duplicate check failed: {e}")
        return DuplicateResult()


def check_duplicates_batch(mpn_list: List[str]) -> Dict[str, DuplicateResult]:
    """
    Check multiple MPNs for duplicates in a single operation.

    Returns a dict mapping MPN -> DuplicateResult.
    """
    results = {}
    for mpn in mpn_list:
        results[mpn] = check_duplicate(mpn)
    return results


def _strip_suffixes(mpn: str) -> str:
    """Strip common ordering suffixes from MPNs."""
    suffixes = [
        "-ND",
        "-TR",
        "-CT",
        "-1",
        "-2",
        "-3",
        "/NOPB",
        "-REEL",
        "-TUBE",
        "-BULK",
        "#PBF",
        "-PBF",
    ]
    result = mpn.upper()
    for suffix in suffixes:
        if result.endswith(suffix):
            result = result[: -len(suffix)]
    return result
