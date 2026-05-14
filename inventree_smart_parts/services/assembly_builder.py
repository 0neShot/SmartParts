"""
Assembly Builder
================
Creates an InvenTree Assembly Part and links BomItem records
from the results of a batch CSV import.

Called as Phase 2+3 of the BOM Assembly Generation workflow:
  Phase 2: Create a new Part (assembly=True) using the given name.
  Phase 3: Create BomItem records linking the assembly to each sub-part.
"""

import logging
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

logger = logging.getLogger('inventree_smart_parts.assembly')


@dataclass
class BomItemSpec:
    """Specification for a single BOM line item."""
    part_id: int
    quantity: float
    mpn: str = ''
    reference: str = ''   # Designator(s) from the CSV


@dataclass
class AssemblyResult:
    """Result of an assembly + BOM creation operation."""
    success: bool = False
    assembly_part_id: Optional[int] = None
    assembly_part_name: str = ''
    assembly_url: str = ''
    bom_items_created: int = 0
    bom_items_skipped: int = 0
    errors: List[str] = field(default_factory=list)
    message: str = ''


def create_assembly_with_bom(
    assembly_name: str,
    bom_specs: List[BomItemSpec],
    category_id: Optional[int] = None,
    description: str = '',
    revision: str = '',
) -> AssemblyResult:
    """
    Create an assembly Part and its BomItem children.

    Args:
        assembly_name: Name of the new assembly Part.
        bom_specs:     List of BomItemSpec objects – one per sub-part.
        category_id:   Optional InvenTree PartCategory pk.
        description:   Optional description for the assembly Part.
        revision:      Optional revision string.

    Returns:
        AssemblyResult with details of what was created.
    """
    result = AssemblyResult()

    if not assembly_name.strip():
        result.errors.append("Assembly name is required")
        return result

    if not bom_specs:
        result.errors.append("No BOM items to add")
        return result

    try:
        from part.models import Part, BomItem, PartCategory
    except ImportError:
        result.errors.append(
            "Could not import InvenTree Part models – "
            "is the plugin running inside InvenTree?"
        )
        return result

    # ── Phase 2: Create assembly Part ────────────────────────────────────────
    try:
        assembly_kwargs = {
            'name':        assembly_name.strip(),
            'description': description or f"Assembly created by Smart Parts BOM import",
            'assembly':    True,
            'component':   False,   # Assembly parts are not usually used as components
            'active':      True,
            'purchaseable': False,
        }
        if revision:
            assembly_kwargs['revision'] = revision.strip()
        if category_id:
            try:
                assembly_kwargs['category'] = PartCategory.objects.get(pk=category_id)
            except PartCategory.DoesNotExist:
                logger.warning(f"Category {category_id} not found, creating without category")

        assembly = Part.objects.create(**assembly_kwargs)
        result.assembly_part_id = assembly.pk
        result.assembly_part_name = assembly.name

        # Build the InvenTree URL for the new part
        result.assembly_url = f"/web/part/{assembly.pk}"
        logger.info(
            f"[Assembly] Created assembly '{assembly_name}' (pk={assembly.pk})"
        )

    except Exception as e:
        logger.error(f"[Assembly] Failed to create assembly Part: {e}", exc_info=True)
        result.errors.append(f"Assembly creation failed: {e}")
        return result

    # ── Phase 3: BomItem linking ──────────────────────────────────────────────
    for spec in bom_specs:
        try:
            sub_part = Part.objects.get(pk=spec.part_id)

            bom_kwargs = {
                'part':     assembly,   # parent assembly Part (InvenTree field name)
                'sub_part': sub_part,
                'quantity': max(spec.quantity, 1),
            }
            if spec.reference:
                bom_kwargs['reference'] = spec.reference[:200]  # InvenTree field limit

            # Avoid duplicate BOM entries for the same sub-part
            existing = BomItem.objects.filter(
                part=assembly,
                sub_part=sub_part,
            ).first()

            if existing:
                # Merge quantities if the same part appears on multiple CSV rows
                existing.quantity += max(spec.quantity, 1)
                existing.save()
                result.bom_items_created += 1
                logger.debug(
                    f"[Assembly] Merged BomItem: {sub_part.name} "
                    f"(qty now {existing.quantity})"
                )
            else:
                BomItem.objects.create(**bom_kwargs)
                result.bom_items_created += 1
                logger.debug(
                    f"[Assembly] BomItem: {sub_part.name} × {spec.quantity}"
                )

        except Part.DoesNotExist:
            msg = f"Sub-part pk={spec.part_id} (MPN: {spec.mpn}) not found"
            logger.warning(f"[Assembly] {msg}")
            result.errors.append(msg)
            result.bom_items_skipped += 1

        except Exception as e:
            msg = f"BomItem for pk={spec.part_id} failed: {e}"
            logger.error(f"[Assembly] {msg}", exc_info=True)
            result.errors.append(msg)
            result.bom_items_skipped += 1

    result.success = result.assembly_part_id is not None
    result.message = (
        f"Assembly '{assembly_name}' created with "
        f"{result.bom_items_created} BOM item(s)"
        + (f"; {result.bom_items_skipped} skipped" if result.bom_items_skipped else "")
    )
    logger.info(f"[Assembly] {result.message}")
    return result
