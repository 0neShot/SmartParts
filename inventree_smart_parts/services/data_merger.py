"""
Data Merger
===========
Merges PartData from multiple API sources using configurable priority.
First source with a non-empty field wins for that field.
"""

import logging
from typing import List, Optional
from ..api_clients.base import PartData, PriceBreak, PartParameter

logger = logging.getLogger("inventree_smart_parts.services.merger")

# Fields to merge (in order of importance)
MERGEABLE_FIELDS = [
    "mpn",
    "manufacturer",
    "description",
    "name",
    "category",
    "subcategory",
    "datasheet_url",
    "image_url",
    "package",
]


def merge_part_data(
    results: List[PartData],
    priority_order: Optional[List[str]] = None,
) -> Optional[PartData]:
    """
    Merge multiple PartData results into a single unified PartData.

    Args:
        results: List of PartData from different API sources
        priority_order: List of source names in priority order
                       e.g. ['mouser', 'digikey', 'lcsc']

    Returns:
        Merged PartData or None if no results
    """
    if not results:
        return None

    # Always run through the full merge path so that raw_data['supplier_data']
    # is built consistently, even for single-source results.
    # This ensures _get_supplier_entries() in part_creator always has the full
    # supplier list to iterate, creating one SupplierPart per distributor found.

    # Default priority
    if priority_order is None:
        priority_order = ["mouser", "digikey", "lcsc"]

    # Sort results by priority
    def sort_key(pd: PartData) -> int:
        try:
            return priority_order.index(pd.source)
        except ValueError:
            return len(priority_order)

    sorted_results = sorted(results, key=sort_key)

    logger.info(
        f"Merging {len(sorted_results)} results. "
        f"Priority: {[r.source for r in sorted_results]}"
    )

    # Start with the highest-priority result as base
    merged = PartData()

    # Merge simple string fields: first non-empty wins
    for field_name in MERGEABLE_FIELDS:
        for result in sorted_results:
            if result.has_field(field_name):
                setattr(merged, field_name, getattr(result, field_name))
                break

    # Merge parameters: collect all unique parameters
    merged.parameters = _merge_parameters(sorted_results)

    # Merge price breaks: keep all from all sources (tagged by supplier)
    merged.price_breaks = sorted_results[0].price_breaks if sorted_results else []

    # Source metadata
    merged.source = "merged"
    merged.confidence = max(r.confidence for r in sorted_results)

    # Collect all supplier info for later use
    merged.raw_data = {
        "merged_sources": [r.source for r in sorted_results],
        "supplier_data": [
            {
                "source": r.source,
                "supplier_name": r.supplier_name,
                # Fallback: if API confirmed a match but returned no SKU,
                # use the MPN so the supplier card is never silently dropped.
                "supplier_sku": r.supplier_sku or r.mpn or "UNKNOWN-SKU",
                "supplier_url": r.supplier_url,
                "price_breaks": [
                    {"qty": pb.quantity, "price": pb.price, "currency": pb.currency}
                    for pb in r.price_breaks
                ],
                "stock": r.stock_available,
            }
            for r in sorted_results
        ],
        # Per-source datasheet URLs so downstream code can fall back if the
        # merged value is empty (highest-priority source may have no datasheet).
        "source_datasheet_urls": [
            {"source": r.source, "url": r.datasheet_url}
            for r in sorted_results
            if r.datasheet_url and r.datasheet_url.startswith("http")
        ],
    }

    logger.info(
        f"Merged result: {merged.mpn} ({merged.manufacturer}) – {len(sorted_results)} source(s)"
    )
    return merged


def _merge_parameters(results: List[PartData]) -> List[PartParameter]:
    """Merge parameters from all sources, deduplicating by name."""
    seen_names = set()
    merged_params = []

    for result in results:
        for param in result.parameters:
            name_key = param.name.lower().strip()
            if name_key not in seen_names:
                seen_names.add(name_key)
                merged_params.append(param)

    return merged_params
