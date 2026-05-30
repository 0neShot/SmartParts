"""
Part Creator
============
Orchestrates the creation of InvenTree Part, ManufacturerPart,
and SupplierPart records from merged PartData.
"""

import logging
import tempfile
import os
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, field

from ..api_clients.base import PartData

logger = logging.getLogger("inventree_smart_parts.services.creator")

# ── Shared sentinel values: empty/placeholder parameter values ────────────────
# Used by both _create_parameters (DB writes) and the parameter_normalizer
# (display filtering) to guarantee consistent sanitisation.
EMPTY_SENTINELS: frozenset = frozenset(
    {
        "",
        "-",
        "--",
        "---",
        "n/a",
        "na",
        "null",
        "none",
        "unknown",
        "not specified",
        "not applicable",
        "tbd",
        "tba",
        "?",
        "\u2013",
        "\u2014",
    }
)


def is_useless_value(value) -> bool:
    """Return True if a parameter value is empty or a known placeholder."""
    if value is None:
        return True
    stripped = str(value).strip()
    return not stripped or stripped.lower() in EMPTY_SENTINELS


@dataclass
class CreationResult:
    """Result of a part creation operation."""

    success: bool = False
    part_id: Optional[int] = None
    part_name: str = ""
    action: str = ""  # 'created', 'updated', 'skipped'
    message: str = ""
    # Separate 'new' from 'already existed' for accurate reporting
    manufacturer_parts_created: List[int] = field(default_factory=list)
    manufacturer_parts_existing: List[int] = field(default_factory=list)
    supplier_parts_created: List[int] = field(default_factory=list)
    supplier_parts_existing: List[int] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    # Legacy shim so existing call-sites that read .manufacturer_parts / .supplier_parts still work
    @property
    def manufacturer_parts(self) -> List[int]:
        return self.manufacturer_parts_created + self.manufacturer_parts_existing

    @property
    def supplier_parts(self) -> List[int]:
        return self.supplier_parts_created + self.supplier_parts_existing


def create_part_from_data(
    part_data: PartData,
    category_id: Optional[int] = None,
    update_existing: bool = False,
    existing_part_id: Optional[int] = None,
    auto_create_companies: bool = True,
) -> CreationResult:
    """
    Create or update an InvenTree Part from merged PartData.

    Creates:
    1. Part (core component record)
    2. ManufacturerPart (links to manufacturer company)
    3. SupplierPart (for each supplier with SKU)

    Args:
        part_data: Merged/normalized part data
        category_id: InvenTree category ID (None = uncategorized)
        update_existing: Whether to update an existing part
        existing_part_id: ID of existing part to update
        auto_create_companies: Auto-create manufacturer/supplier companies

    Returns:
        CreationResult with details of what was created
    """
    result = CreationResult()

    try:
        from part.models import Part, PartCategory
        from company.models import (
            Company,
            ManufacturerPart,
            SupplierPart,
        )
        from django.db import transaction

        with transaction.atomic():
            # ── Step 1: Get or create the Part ──
            if existing_part_id and update_existing:
                part = _update_existing_part(existing_part_id, part_data, category_id)
                result.action = "updated"
            else:
                part = _create_new_part(part_data, category_id)
                result.action = "created"

            result.part_id = part.pk
            result.part_name = part.name

            # ── Step 2: ManufacturerPart ──
            if part_data.manufacturer and part_data.mpn:
                mfr_part, mfr_created = _create_manufacturer_part(
                    part, part_data, auto_create_companies
                )
                if mfr_part:
                    if mfr_created:
                        result.manufacturer_parts_created.append(mfr_part.pk)
                    else:
                        result.manufacturer_parts_existing.append(mfr_part.pk)

            # ── Step 3: SupplierParts ──
            supplier_entries = _get_supplier_entries(part_data, part=part)
            logger.debug(
                f"Supplier pipeline for '{part_data.mpn}': "
                f"{len(supplier_entries)} entries to create"
            )

            for i, entry in enumerate(supplier_entries):
                try:
                    # Each supplier gets its own savepoint so a constraint
                    # failure on one distributor doesn't roll back the rest.
                    with transaction.atomic():
                        sup_part, sup_created = _create_supplier_part(
                            part, part_data, entry, auto_create_companies
                        )
                        if sup_part:
                            if sup_created:
                                result.supplier_parts_created.append(sup_part.pk)
                            else:
                                result.supplier_parts_existing.append(sup_part.pk)
                except Exception as e:
                    logger.error(
                        f"Failed to create SupplierPart "
                        f"{entry.get('name', '?')}:{entry.get('sku', '?')} "
                        f"for '{part_data.mpn}': {e}",
                        exc_info=True,
                    )
                    result.errors.append(
                        f"Supplier link failed ({entry.get('name', '?')}): {e}"
                    )

            # ── Step 4: Add Parameters ──
            if part_data.parameters:
                _create_parameters(part, part_data.parameters)

            # ── Step 5: Media Import (Image & Datasheet) ──
            from .image_handler import auto_import_media, attach_datasheet_to_part

            if part_data.image_url or part_data.raw_data.get("source_image_urls"):
                img_result = auto_import_media(part, part_data)
                if img_result.success:
                    logger.info(f"Image: {img_result.message}")
                else:
                    # Non-critical – log but don't surface to user as error
                    logger.info(
                        f"Image not imported (non-critical): {img_result.message}"
                    )

            if part_data.datasheet_url:
                attach_datasheet_to_part(part, part_data.datasheet_url)

            result.success = True
            # Build an accurate human-readable summary
            parts = []
            mc = len(result.manufacturer_parts_created)
            me = len(result.manufacturer_parts_existing)
            sc = len(result.supplier_parts_created)
            se = len(result.supplier_parts_existing)
            if mc or me:
                bits = []
                if mc:
                    bits.append(f"{mc} created")
                if me:
                    bits.append(f"{me} already existed")
                parts.append(f"manufacturer part ({', '.join(bits)})")
            if sc or se:
                bits = []
                if sc:
                    bits.append(f"{sc} created")
                if se:
                    bits.append(f"{se} already existed")
                parts.append(f"supplier part ({', '.join(bits)})")
            suffix = (": " + "; ".join(parts)) if parts else " (no linked parts)"
            result.message = f"'{part.name}' {result.action} successfully{suffix}."

    except Exception as e:
        logger.error(f"Part creation failed: {e}", exc_info=True)
        result.success = False
        result.message = f"Creation failed: {str(e)}"
        result.errors.append(str(e))

    return result


def _create_new_part(data: PartData, category_id: Optional[int]) -> "Part":
    """Create a new InvenTree Part."""
    from part.models import Part, PartCategory

    category = None
    if category_id:
        try:
            category = PartCategory.objects.get(pk=category_id)
        except PartCategory.DoesNotExist:
            logger.warning(f"Category ID {category_id} not found")

    name = data.mpn
    if not name:
        name = "Unknown"

    # Truncate name if too long
    if len(name) > 100:
        name = name[:97] + "..."

    part = Part.objects.create(
        name=name,
        description=(
            data.description[:250]
            if data.description
            else f"{data.manufacturer} {data.mpn}"
        ),
        category=category,
        IPN="",  # Let InvenTree auto-generate if configured
        active=True,
        virtual=False,
        component=True,
        purchaseable=True,
        link=data.datasheet_url or "",
    )

    logger.info(f"Created Part: {part.name} (ID: {part.pk})")
    return part


def _update_existing_part(
    part_id: int, data: PartData, category_id: Optional[int]
) -> "Part":
    """Update an existing InvenTree Part with new data.

    This is deliberately non-destructive: existing values are NEVER
    overwritten. Only truly empty fields receive new values.
    """
    from part.models import Part, PartCategory

    part = Part.objects.get(pk=part_id)
    changed = False

    # Never touch part.name – it was set from the MPN at creation time
    # and the user may have renamed it manually.

    if data.description and not part.description:
        part.description = data.description[:250]
        changed = True

    if data.datasheet_url and not part.link:
        part.link = data.datasheet_url
        changed = True

    if category_id and not part.category_id:
        try:
            part.category = PartCategory.objects.get(pk=category_id)
            changed = True
        except PartCategory.DoesNotExist:
            pass

    if changed:
        part.save()
    logger.info(f"Updated Part: {part.name} (ID: {part.pk}, changed={changed})")
    return part


def _create_manufacturer_part(
    part, data: PartData, auto_create: bool
) -> Tuple[Optional["ManufacturerPart"], bool]:
    """Create a ManufacturerPart linking Part to Manufacturer.

    Uses get_or_create keyed on (part, manufacturer, MPN) so that:
    - Multiple suppliers sharing the same MPN reuse one ManufacturerPart
    - Concurrent requests or retries don't hit IntegrityError

    Returns (manufacturer_part, was_created).
    """
    from company.models import Company, ManufacturerPart

    # Get or create the manufacturer company
    manufacturer = _get_or_create_company(
        name=data.manufacturer,
        is_manufacturer=True,
        auto_create=auto_create,
    )
    if not manufacturer:
        logger.warning(f"Could not find/create manufacturer: {data.manufacturer}")
        return None, False

    mfr_part, created = ManufacturerPart.objects.get_or_create(
        part=part,
        manufacturer=manufacturer,
        MPN=data.mpn,
        defaults={
            "link": data.supplier_url or "",
        },
    )

    if created:
        logger.info(f"Created ManufacturerPart: {data.mpn} (ID: {mfr_part.pk})")
    else:
        logger.debug(f"ManufacturerPart already exists: {data.mpn} (ID: {mfr_part.pk})")

    return mfr_part, created


def _get_supplier_entries(data: PartData, part=None) -> List[Dict]:
    """
    Extract supplier entries from merged PartData.

    All supplier info lives in raw_data['supplier_data'] (populated by the
    merger for every source that returned a result).  We iterate that list
    exclusively so every distributor gets a SupplierPart.

    Deduplication is performed in two stages:
    1. In-memory: case-insensitive (name, sku) check within the current list
       so one distributor never produces two identical rows.
    2. Database: any (supplier, sku) pair already linked to this part is
       excluded so re-runs / updates don't create duplicates.
    """
    from company.models import SupplierPart

    entries = []
    seen_keys: set = set()  # lower-case "name:sku" keys

    def _mem_key(name: str, sku: str) -> str:
        return f"{name.strip().lower()}:{sku.strip().lower()}"

    def _already_in_db(name: str, sku: str) -> bool:
        if part is None:
            return False
        return SupplierPart.objects.filter(
            part=part,
            supplier__name__iexact=name.strip(),
            SKU__iexact=sku.strip(),
        ).exists()

    # ── Primary path: iterate raw_data['supplier_data'] (all API sources) ──
    supplier_data_list = data.raw_data.get("supplier_data", [])
    logger.debug(
        f"_get_supplier_entries: {len(supplier_data_list)} source(s) in raw_data "
        f"for MPN '{data.mpn}'"
    )

    for sd in supplier_data_list:
        name = (sd.get("supplier_name") or "").strip()
        sku = (sd.get("supplier_sku") or "").strip()
        src = sd.get("source", "?")

        if not name or not sku:
            logger.debug(f"  [{src}] Skipped – missing supplier_name or supplier_sku")
            continue

        key = _mem_key(name, sku)
        if key in seen_keys:
            logger.debug(
                f"  [{src}] Skipped – duplicate in current batch ({name}:{sku})"
            )
            continue

        if _already_in_db(name, sku):
            logger.debug(f"  [{src}] Skipped – already in DB ({name}:{sku})")
            seen_keys.add(key)
            continue

        from ..api_clients.base import PriceBreak

        pbs = [
            PriceBreak(
                quantity=pb["qty"],
                price=pb["price"],
                currency=pb.get("currency", "EUR"),
            )
            for pb in sd.get("price_breaks", [])
        ]
        entries.append(
            {
                "name": name,
                "sku": sku,
                "url": sd.get("supplier_url", ""),
                "price_breaks": pbs,
            }
        )
        seen_keys.add(key)
        logger.debug(f"  [{src}] Queued SupplierPart: {name} / {sku}")

    # ── Fallback: if raw_data was empty (e.g. single-source non-merged path),
    #    use the top-level supplier fields from PartData directly. ──
    if not supplier_data_list and data.supplier_name and data.supplier_sku:
        name, sku = data.supplier_name.strip(), data.supplier_sku.strip()
        key = _mem_key(name, sku)
        if key not in seen_keys and not _already_in_db(name, sku):
            entries.append(
                {
                    "name": name,
                    "sku": sku,
                    "url": data.supplier_url,
                    "price_breaks": data.price_breaks,
                }
            )
            logger.debug(f"  [fallback] Queued SupplierPart: {name} / {sku}")

    logger.info(
        f"_get_supplier_entries → {len(entries)} to create for MPN '{data.mpn}': "
        f"{[e['name'] + ':' + e['sku'] for e in entries]}"
    )
    return entries


def _create_supplier_part(
    part, data: PartData, supplier_entry: Dict, auto_create: bool
) -> Tuple[Optional["SupplierPart"], bool]:
    """Create or update a SupplierPart linking Part to Supplier.

    Returns (supplier_part, was_created). was_created=False means the record
    already existed (and may have had its price breaks refreshed).
    """
    from company.models import Company, SupplierPart, SupplierPriceBreak

    supplier_name = supplier_entry["name"]
    sku = supplier_entry["sku"]
    new_price_breaks = supplier_entry.get("price_breaks", [])

    supplier = _get_or_create_company(
        name=supplier_name,
        is_supplier=True,
        auto_create=auto_create,
    )
    if not supplier:
        logger.warning(f"Could not find/create supplier: {supplier_name}")
        return None, False

    existing = SupplierPart.objects.filter(
        part__pk=part.pk,
        supplier=supplier,
        SKU=sku,
    ).first()

    if existing:
        update_fields = []

        # ── Backfill manufacturer_part if missing ──
        if not existing.manufacturer_part:
            from company.models import ManufacturerPart as MP

            mfr = MP.objects.filter(part=part, MPN__iexact=data.mpn).first()
            if mfr:
                existing.manufacturer_part = mfr
                update_fields.append("manufacturer_part")
                logger.info(
                    f"Backfilled manufacturer_part on SupplierPart {existing.pk}"
                )

        # ── Update URL if changed ──
        new_url = supplier_entry.get("url", "")
        if new_url and existing.link != new_url:
            existing.link = new_url
            update_fields.append("link")

        if update_fields:
            existing.save(update_fields=update_fields)

        # ── Refresh price breaks if new ones are provided ──
        if new_price_breaks:
            existing.pricebreaks.all().delete()
            for pb in new_price_breaks:
                try:
                    SupplierPriceBreak.objects.create(
                        part=existing,
                        quantity=pb.quantity,
                        price=pb.price,
                        price_currency=pb.currency,
                    )
                except Exception as e:
                    logger.warning(f"Failed to refresh price break for {sku}: {e}")

        logger.debug(f"SupplierPart updated (existing): {sku} @ {supplier_name}")
        return existing, False  # ← not newly created

    # ── Create new SupplierPart via get_or_create ──
    # Reuse existing ManufacturerPart (case-insensitive) — critical when
    # multiple distributors share the same MPN from the same manufacturer.
    from company.models import ManufacturerPart

    mfr_part = ManufacturerPart.objects.filter(
        part=part,
        MPN__iexact=data.mpn,
    ).first()

    try:
        sup_part, was_created = SupplierPart.objects.get_or_create(
            part=part,
            supplier=supplier,
            SKU=sku,
            defaults={
                "link": supplier_entry.get("url", ""),
                "manufacturer_part": mfr_part,
            },
        )
    except Exception as e:
        logger.error(
            f"DB error creating SupplierPart: {sku} @ {supplier_name} "
            f"(part={part.pk}, mfr_part={getattr(mfr_part, 'pk', None)}): {e}",
            exc_info=True,
        )
        raise  # Re-raise so the savepoint in the caller can catch it

    if not was_created:
        update_fields = []

        # Backfill manufacturer_part if missing (created before MfrPart existed)
        if not sup_part.manufacturer_part and mfr_part:
            sup_part.manufacturer_part = mfr_part
            update_fields.append("manufacturer_part")
            logger.info(f"Backfilled manufacturer_part on SupplierPart {sup_part.pk}")

        # Update URL if changed
        new_url = supplier_entry.get("url", "")
        if new_url and sup_part.link != new_url:
            sup_part.link = new_url
            update_fields.append("link")

        if update_fields:
            sup_part.save(update_fields=update_fields)

        logger.debug(
            f"SupplierPart already existed: {sku} @ {supplier_name} (ID: {sup_part.pk})"
        )

    for pb in new_price_breaks:
        try:
            SupplierPriceBreak.objects.get_or_create(
                part=sup_part,
                quantity=pb.quantity,
                defaults={
                    "price": pb.price,
                    "price_currency": pb.currency,
                },
            )
        except Exception as e:
            logger.warning(f"Failed to create price break for {sku}: {e}")

    if was_created:
        logger.info(
            f"Created SupplierPart: {sku} @ {supplier_name} (ID: {sup_part.pk})"
        )

    return sup_part, was_created


def _get_or_create_company(
    name: str,
    is_manufacturer: bool = False,
    is_supplier: bool = False,
    auto_create: bool = True,
) -> Optional["Company"]:
    """Get an existing company or create a new one."""
    from company.models import Company

    if not name:
        return None

    # Try exact match first
    company = Company.objects.filter(name__iexact=name.strip()).first()
    if company:
        # Update flags if needed
        changed = False
        if is_manufacturer and not company.is_manufacturer:
            company.is_manufacturer = True
            changed = True
        if is_supplier and not company.is_supplier:
            company.is_supplier = True
            changed = True
        if changed:
            company.save()
        return company

    if not auto_create:
        return None

    # Create new company
    company = Company.objects.create(
        name=name.strip(),
        is_manufacturer=is_manufacturer,
        is_supplier=is_supplier,
    )
    logger.info(
        f"Created company: {name} (manufacturer={is_manufacturer}, supplier={is_supplier})"
    )
    return company


def _create_parameters(part, parameters: List[Any]):
    """Create Parameter entries for the Part.

    Parameters with empty, whitespace-only, or known-placeholder values are
    silently dropped before any database interaction, so InvenTree never
    accumulates rows like Tolerance="-" or Voltage=N/A.
    """
    from common.models import Parameter, ParameterTemplate
    from django.contrib.contenttypes.models import ContentType

    part_type = ContentType.objects.get_for_model(part)
    skipped = 0

    for param in parameters:
        # Drop parameters without a name or with a useless value
        if not param.name or is_useless_value(getattr(param, "value", None)):
            skipped += 1
            continue

        try:
            # Get or create the parameter template
            template, _ = ParameterTemplate.objects.get_or_create(
                name=param.name,
                defaults={
                    "units": param.unit if hasattr(param, "unit") and param.unit else ""
                },
            )

            # Create or update the parameter value for this part
            Parameter.objects.update_or_create(
                model_type=part_type,
                model_id=part.pk,
                template=template,
                defaults={"data": str(param.value).strip()[:2000]},
            )
            logger.debug(f"Parameter {param.name}={param.value} for Part {part.pk}")
        except Exception as e:
            logger.warning(f"Failed to create parameter {param.name}: {e}")

    if skipped:
        logger.debug(
            f"Skipped {skipped} empty/placeholder parameter(s) for Part {part.pk}"
        )


def _attach_image(part, image_url: str):
    """Download and attach an image to a Part."""
    import requests

    try:
        response = requests.get(image_url, timeout=10, stream=True)
        response.raise_for_status()

        # Determine file extension
        content_type = response.headers.get("content-type", "")
        ext = ".jpg"
        if "png" in content_type:
            ext = ".png"
        elif "webp" in content_type:
            ext = ".webp"

        # Save to temp file then attach
        with tempfile.NamedTemporaryFile(
            suffix=ext, delete=False, prefix="smartparts_"
        ) as tmp:
            for chunk in response.iter_content(chunk_size=8192):
                tmp.write(chunk)
            tmp_path = tmp.name

        try:
            from django.core.files import File

            with open(tmp_path, "rb") as f:
                part.image.save(f"part_{part.pk}{ext}", File(f), save=True)
            logger.info(f"Attached image to Part {part.pk}")
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        logger.warning(f"Failed to download/attach image: {e}")


def _attach_datasheet_link(part, datasheet_url: str):
    """Attach the datasheet link to the Part."""
    if not datasheet_url:
        return

    try:
        from common.models import Attachment
        from django.contrib.contenttypes.models import ContentType

        part_type = ContentType.objects.get_for_model(part)

        # InvenTree attachments support either file uploads or external URLs.
        Attachment.objects.get_or_create(
            model_type=part_type,
            model_id=part.pk,
            link=datasheet_url,
            defaults={"comment": "Datasheet"},
        )
        logger.info(f"Attached datasheet link to Part {part.pk}")
    except Exception as e:
        logger.warning(f"Failed to attach datasheet link: {e}")
