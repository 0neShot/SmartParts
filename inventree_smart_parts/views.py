"""
Views
=====
Django views for the Smart Parts plugin UI.
All views are served under /plugin/smartparts/.
"""

import json
import logging
from datetime import datetime, timezone

from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger("inventree_smart_parts.views")


def _is_useless_param_value(value: str) -> bool:
    """Thin wrapper around part_creator.is_useless_value for parameter filtering."""
    from .services.part_creator import is_useless_value

    return is_useless_value(value)


# ── Persistent Activity Logger ──────────────────────────────────────────────
from .services.activity_logger import log_activity, get_logs, get_recent


# Backward-compat shim for any code that still calls _log_activity
def _log_activity(level: str, message: str, details: str = ""):
    log_activity(level, message, details)


def _get_plugin():
    """Get the SmartPartsPlugin instance."""
    from plugin.registry import registry

    return registry.get_plugin("smartparts")


def _check_perm(request, perm: str):
    """Return a 403 JsonResponse if the user lacks the given permission, else None.

    Usage::

        denied = _check_perm(request, 'part.add_part')
        if denied:
            return denied
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return JsonResponse(
            {"error": "Authentication required"},
            status=401,
        )
    if not user.has_perm(perm):
        logger.warning(f'Permission denied: user="{user.username}" lacks "{perm}"')
        return JsonResponse(
            {"error": f'Permission denied – requires "{perm}"'},
            status=403,
        )
    return None


def _resolve_user(request):
    """
    Resolve the authenticated user from either:
    - 'Authorization: Token <key>' header  (InvenTree API token)
    - Active Django session                (existing browser auth)

    Returns (user, error_response).
    If auth succeeds, error_response is None.
    If auth fails, user is None and error_response is a JsonResponse(status=401).
    """
    auth_header = request.META.get("HTTP_AUTHORIZATION", "")
    if auth_header.startswith("Token "):
        token_key = auth_header.split(" ", 1)[1].strip()
        # Try InvenTree native API tokens (users.models.ApiToken)
        try:
            from users.models import ApiToken

            token = ApiToken.objects.select_related("user").get(key=token_key)
            active = getattr(token, "active", True)
            expired = getattr(token, "expired", False)
            revoked = getattr(token, "revoked", False)
            if active and not expired and not revoked:
                return token.user, None
        except Exception:
            pass

        # Try standard Django REST Framework token (rest_framework.authtoken.models.Token)
        try:
            from rest_framework.authtoken.models import Token

            token = Token.objects.select_related("user").get(key=token_key)
            return token.user, None
        except Exception:
            pass

        return None, JsonResponse({"error": "Invalid or expired token"}, status=401)

    # Fall back to session auth
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return user, None

    return None, JsonResponse({"error": "Authentication required"}, status=401)


# ═══════════════════════════════════════════════════════════════════
#  Dashboard
# ═══════════════════════════════════════════════════════════════════


def dashboard(request):
    """Main dashboard with quick search and recent activity."""
    plugin = _get_plugin()
    context = {
        "plugin": plugin,
        "recent_logs": get_recent(10),
        "mouser_enabled": plugin.get_setting("MOUSER_ENABLED") if plugin else False,
        "digikey_enabled": plugin.get_setting("DIGIKEY_ENABLED") if plugin else False,
        "lcsc_enabled": plugin.get_setting("LCSC_ENABLED") if plugin else False,
        "element14_enabled": (
            plugin.get_setting("ELEMENT14_ENABLED") if plugin else False
        ),
        "tme_enabled": plugin.get_setting("TME_ENABLED") if plugin else False,
    }
    return render(request, "inventree_smart_parts/dashboard.html", context)


# ═══════════════════════════════════════════════════════════════════
#  PureScan – Zero-Click Warehouse Terminal
# ═══════════════════════════════════════════════════════════════════


def purescan(request):
    """PureScan full-screen barcode terminal."""
    return render(request, "inventree_smart_parts/purescan.html", {})


# All control/quantity codes for the Command Sheet
_COMMAND_SHEET_CODES = [
    {
        "code": "SYS:TRANSFER",
        "label": "Transfer Stock",
        "icon": "🔄",
        "color": "#f59e0b",
        "group": "action",
    },
    {
        "code": "SYS:INFO",
        "label": "Info / Lookup",
        "icon": "ℹ️",
        "color": "#3b82f6",
        "group": "action",
    },
    {
        "code": "SYS:ADD",
        "label": "Add Stock",
        "icon": "➕",
        "color": "#10b981",
        "group": "action",
    },
    {
        "code": "SYS:REMOVE",
        "label": "Remove Stock",
        "icon": "➖",
        "color": "#ef4444",
        "group": "action",
    },
    {
        "code": "SYS:STOCKTAKE",
        "label": "Stocktake",
        "icon": "📋",
        "color": "#8b5cf6",
        "group": "action",
    },
    {
        "code": "SYS:CANCEL",
        "label": "Cancel / Reset",
        "icon": "❌",
        "color": "#64748b",
        "group": "action",
    },
    {
        "code": "SYS:UNDO",
        "label": "Undo Last",
        "icon": "↩️",
        "color": "#f59e0b",
        "group": "action",
    },
    {
        "code": "SYS:QTY:1",
        "label": "Qty: 1",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:5",
        "label": "Qty: 5",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:10",
        "label": "Qty: 10",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:25",
        "label": "Qty: 25",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:50",
        "label": "Qty: 50",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:100",
        "label": "Qty: 100",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:250",
        "label": "Qty: 250",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:500",
        "label": "Qty: 500",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
    {
        "code": "SYS:QTY:1000",
        "label": "Qty: 1000",
        "icon": "#",
        "color": "#6b7280",
        "group": "qty",
    },
]


def _generate_qr_data_uri(text: str) -> str:
    """Generate a QR code as a base64 data:image/png URI."""
    try:
        import qrcode
        import io
        import base64

        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=6,
            border=2,
        )
        qr.add_data(text)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        logger.warning(f'QR generation failed for "{text}": {e}')
        return ""


def purescan_command_sheet(request):
    """Render the PureScan Command Sheet with server-generated QR codes.
    Separates action and quantity codes into distinct page groups,
    and generates a deep-link QR for the PureScan terminal URL.
    """
    action_commands = []
    qty_commands = []

    for cmd in _COMMAND_SHEET_CODES:
        enriched = {**cmd, "qr_data_uri": _generate_qr_data_uri(cmd["code"])}
        if cmd["group"] == "action":
            action_commands.append(enriched)
        else:
            qty_commands.append(enriched)

    # Deep-link QR: absolute URL to the PureScan terminal
    purescan_url = request.build_absolute_uri("/plugin/smartparts/purescan/")
    deeplink_qr = _generate_qr_data_uri(purescan_url)

    return render(
        request,
        "inventree_smart_parts/purescan_commands.html",
        {
            "action_commands": action_commands,
            "qty_commands": qty_commands,
            "deeplink_url": purescan_url,
            "deeplink_qr": deeplink_qr,
        },
    )


# ═══════════════════════════════════════════════════════════════════
#  Shared Search Helper
# ═══════════════════════════════════════════════════════════════════


def _run_all_api_searches(plugin, mpn: str):
    """
    Query all enabled distributor APIs for the given MPN.

    Returns a tuple:
        (api_results: list[PartData], results_dict: dict)

    - api_results:  list of PartData objects (one per source that returned a hit)
    - results_dict: mapping of source name → serialized dict, None (not found),
                    or {"error": "..."} (exception during search)

    Enabled sources are driven entirely by plugin settings so no code change is
    needed here when new distributors are added to core.py.
    """
    from .api_clients import MouserClient, DigiKeyClient, LCSCClient

    results: dict = {}
    api_results: list = []

    # ── Mouser ──────────────────────────────────────────────────────
    if plugin.get_setting("MOUSER_ENABLED"):
        try:
            client = MouserClient(api_key=plugin.get_setting("MOUSER_API_KEY"))
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results["mouser"] = _part_data_to_dict(r)
            else:
                results["mouser"] = None
        except Exception as e:
            results["mouser"] = {"error": str(e)}
            logger.warning(f"Mouser search error: {e}")

    # ── DigiKey ─────────────────────────────────────────────────────
    if plugin.get_setting("DIGIKEY_ENABLED"):
        try:
            client = DigiKeyClient(
                client_id=plugin.get_setting("DIGIKEY_CLIENT_ID"),
                client_secret=plugin.get_setting("DIGIKEY_CLIENT_SECRET"),
            )
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results["digikey"] = _part_data_to_dict(r)
            else:
                results["digikey"] = None
        except Exception as e:
            results["digikey"] = {"error": str(e)}
            logger.warning(f"DigiKey search error: {e}")

    # ── LCSC ────────────────────────────────────────────────────────
    if plugin.get_setting("LCSC_ENABLED"):
        try:
            client = LCSCClient()
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results["lcsc"] = _part_data_to_dict(r)
            else:
                results["lcsc"] = None
        except Exception as e:
            results["lcsc"] = {"error": str(e)}
            logger.warning(f"LCSC search error: {e}")

    # ── element14 / Farnell ─────────────────────────────────────────
    if plugin.get_setting("ELEMENT14_ENABLED"):
        try:
            from .api_clients import Element14Client

            client = Element14Client(
                api_key=plugin.get_setting("ELEMENT14_API_KEY"),
                store_name=plugin.get_setting("ELEMENT14_STORE") or "uk.farnell.com",
            )
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results["element14"] = _part_data_to_dict(r)
            else:
                results["element14"] = None
        except Exception as e:
            results["element14"] = {"error": str(e)}
            logger.warning(f"element14 search error: {e}")

    # ── TME ─────────────────────────────────────────────────────────
    if plugin.get_setting("TME_ENABLED"):
        try:
            from .api_clients import TMEClient

            client = TMEClient(
                token=plugin.get_setting("TME_API_TOKEN"),
                secret=plugin.get_setting("TME_API_SECRET"),
                country=plugin.get_setting("TME_COUNTRY") or "DE",
                currency=plugin.get_setting("TME_CURRENCY") or "EUR",
            )
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results["tme"] = _part_data_to_dict(r)
            else:
                results["tme"] = None
        except Exception as e:
            results["tme"] = {"error": str(e)}
            logger.warning(f"TME search error: {e}")

    return api_results, results


# ═══════════════════════════════════════════════════════════════════
#  MPN Search
# ═══════════════════════════════════════════════════════════════════


def search(request):
    """Search results page (renders after form submit)."""
    return render(request, "inventree_smart_parts/search_results.html", {})


@csrf_exempt
def api_search(request):
    """AJAX endpoint: search for a part by MPN across all enabled APIs."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    mpn = body.get("mpn", "").strip()
    if not mpn:
        return JsonResponse({"error": "MPN is required"}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    _log_activity("INFO", f"Searching for MPN: {mpn}")

    from .services.data_merger import merge_part_data
    from .services.duplicate_checker import check_duplicate
    from .services.category_mapper import (
        fuzzy_match_category,
        get_all_categories_for_ui,
    )

    api_results, results = _run_all_api_searches(plugin, mpn)

    # Merge
    priority_str = (
        plugin.get_setting("API_PRIORITY") or "mouser,digikey,element14,tme,lcsc"
    )
    priority_order = [p.strip() for p in priority_str.split(",") if p.strip()]
    merged = merge_part_data(api_results, priority_order)

    logger.debug(
        f"Search '{mpn}': {len(api_results)} API result(s), "
        f"merged {len(merged.raw_data.get('supplier_data', [])) if merged else 0} supplier(s)"
    )

    # Category mapping
    category_match = None
    if merged and merged.category:
        threshold = plugin.get_setting("FUZZY_THRESHOLD") or 65
        default_cat = plugin.get_setting("DEFAULT_CATEGORY") or ""
        user_synonyms = plugin.get_setting("CATEGORY_SYNONYMS") or "{}"
        learned_mappings = plugin.get_setting("LEARNED_CATEGORY_MAPPINGS") or "{}"
        cat_id, cat_path, score = fuzzy_match_category(
            merged.category,
            threshold,
            default_cat,
            user_synonyms_json=user_synonyms,
            learned_mappings_json=learned_mappings,
        )
        category_match = {
            "id": cat_id,
            "path": cat_path,
            "score": score,
            # Pass through so the editor can send it back on save for learning
            "distributor_category": merged.category,
        }

    # Duplicate check
    dup_info = None
    if merged:
        dup = check_duplicate(merged.mpn, merged.manufacturer)
        if dup.is_duplicate:
            dup_info = {
                "is_duplicate": True,
                "part_id": dup.existing_part_id,
                "part_name": dup.existing_part_name,
                "existing_mpn": dup.existing_mpn,
                "match_type": dup.match_type,
                "confidence": dup.confidence,
            }

    # All categories for manual selection
    all_categories = get_all_categories_for_ui()

    # Normalize parameters (data cleaning)
    from .services.parameter_normalizer import normalize_parameter_list

    merged_dict = _part_data_to_dict(merged) if merged else None
    if merged_dict and merged_dict.get("parameters"):
        merged_dict["parameters"] = normalize_parameter_list(merged_dict["parameters"])

    # Also normalize per-source parameters
    for src_key, src_data in results.items():
        if (
            isinstance(src_data, dict)
            and "parameters" in src_data
            and not src_data.get("error")
        ):
            src_data["parameters"] = normalize_parameter_list(src_data["parameters"])

    limit_params = bool(
        plugin.get_setting("LIMIT_PARAMETERS_TO_CATEGORY") if plugin else True
    )

    if merged_dict and "parameters" in merged_dict:
        # Retain full normalized distributor parameters for UI re-filtering on category change
        merged_dict["all_parameters"] = list(merged_dict["parameters"])
        merged_dict["excluded_parameters"] = []

        if limit_params:
            from .core import get_resolved_category_templates
            from .services.parameter_normalizer import filter_parameters_by_category

            cat_id = category_match.get("id") if category_match else None
            category_obj = None
            if cat_id:
                try:
                    from part.models import PartCategory

                    category_obj = PartCategory.objects.get(pk=cat_id)
                except Exception:
                    category_obj = None

            if category_obj and not getattr(category_obj, "structural", False):
                resolved = get_resolved_category_templates(category_obj)
                fr = filter_parameters_by_category(merged_dict["parameters"], resolved)
                merged_dict["parameters"] = fr.accepted_parameters
                merged_dict["excluded_parameters"] = fr.dropped_parameters
            else:
                # No valid category matched or category is structural:
                # Exclude all distributor parameters from active list until user picks a category
                merged_dict["excluded_parameters"] = [
                    {
                        "supplier_key": p.get("name", ""),
                        "supplier_value": p.get("value", ""),
                    }
                    for p in merged_dict["all_parameters"]
                ]
                merged_dict["parameters"] = []

    response = {
        "mpn": mpn,
        "sources": results,
        "merged": merged_dict,
        "category_match": category_match,
        "duplicate": dup_info,
        "categories": all_categories,
        # Tells the JS editor whether to gate parameters on category selection
        "limit_parameters_to_category": limit_params,
        "excluded_parameters": (
            merged_dict.get("excluded_parameters", []) if merged_dict else []
        ),
    }

    _log_activity(
        "INFO",
        f"Search complete for {mpn}",
        f"{len(api_results)} source(s) returned data",
    )

    return JsonResponse(response)


# ═══════════════════════════════════════════════════════════════════
#  Part Creation
# ═══════════════════════════════════════════════════════════════════


@csrf_exempt
def create_part(request):
    """Create a part from confirmed search results."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    denied = _check_perm(request, "part.add_part")
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    from .api_clients.base import PartData, PartParameter
    from .services.part_creator import create_part_from_data

    # Reconstruct PartData from the request
    part_data = PartData(
        mpn=body.get("mpn", ""),
        manufacturer=body.get("manufacturer", ""),
        description=body.get("description", ""),
        name=body.get("name", ""),
        category=body.get("category_name", ""),
        datasheet_url=body.get("datasheet_url", ""),
        image_url=body.get("image_url", ""),
        package=body.get("package", ""),
        parameters=[
            PartParameter(
                name=p.get("name", ""),
                value=p.get("value", ""),
                unit=p.get("unit", ""),
                manual=bool(p.get("manual", False)),
            )
            for p in body.get("parameters", [])
            if p.get("name")
            and p.get("value")
            and not _is_useless_param_value(p.get("value", ""))
        ],
    )

    # Reconstruct supplier data into raw_data
    supplier_data = body.get("supplier_data", [])
    source_image_urls = body.get("source_image_urls", [])

    logger.debug(
        f"Create '{part_data.mpn}': {len(supplier_data)} supplier(s) from frontend"
    )

    if supplier_data or source_image_urls:
        part_data.raw_data = {
            "supplier_data": supplier_data,
            "source_image_urls": source_image_urls,
        }

    # If no supplier_data but we have direct supplier info
    if not supplier_data and body.get("supplier_name"):
        part_data.supplier_name = body.get("supplier_name", "")
        part_data.supplier_sku = body.get("supplier_sku", "")
        part_data.supplier_url = body.get("supplier_url", "")

    category_id = body.get("category_id")
    update_existing = body.get("update_existing", False)
    existing_part_id = body.get("existing_part_id")

    # Fields needed for the learning hook (see below)
    distributor_category = body.get(
        "distributor_category", ""
    )  # raw API category string
    suggested_cat_id = body.get(
        "suggested_category_id"
    )  # what the fuzzy matcher suggested

    result = create_part_from_data(
        part_data=part_data,
        category_id=category_id,
        update_existing=update_existing,
        existing_part_id=existing_part_id,
        auto_create_companies=plugin.get_setting("AUTO_CREATE_MANUFACTURERS"),
    )

    _log_activity(
        "INFO" if result.success else "ERROR",
        (
            f'Part {"created" if result.action == "created" else "updated"}: {result.part_name}'
            if result.success
            else f"Part creation failed for {part_data.mpn}"
        ),
        result.message,
    )

    # ── Persistent Category Learning ──────────────────────────────────────────
    # If the user's chosen category differs from what the fuzzy matcher suggested
    # (or the matcher had no suggestion), record this correction for future imports.
    if result.success and distributor_category and category_id:
        try:
            from part.models import PartCategory
            from .services.category_mapper import learn_category_mapping

            chosen_cat = PartCategory.objects.filter(pk=category_id).first()
            if chosen_cat:
                chosen_path = (
                    chosen_cat.pathstring
                    if hasattr(chosen_cat, "pathstring")
                    else str(chosen_cat)
                )
                # Learn if: no previous suggestion, or user picked a different category
                should_learn = (
                    not suggested_cat_id  # matcher had no confident answer
                    or int(suggested_cat_id) != int(category_id)  # user corrected it
                )
                if should_learn:
                    learn_category_mapping(distributor_category, chosen_path, plugin)
        except Exception as e:
            logger.warning(f"Category learning hook failed (non-fatal): {e}")

    if not result.success:
        return JsonResponse(
            {
                "success": False,
                "part_id": result.part_id,
                "part_name": result.part_name,
                "action": result.action,
                "message": result.message,
                "errors": result.errors,
            }
        )

    # ── Step B: Receive Stock (optional) ──
    stock_result = None
    stock_qty = float(body.get("stock_quantity", 0) or 0)
    if stock_qty > 0:
        from .services.stock_manager import create_stock_item

        stock_result = create_stock_item(
            part_id=result.part_id,
            quantity=stock_qty,
            location_id=body.get("stock_location_id") or None,
            batch=body.get("stock_batch", ""),
            delete_on_deplete=bool(body.get("stock_delete_on_deplete", True)),
            user=request.user,
        )
        _log_activity(
            "INFO" if stock_result["success"] else "WARNING",
            f"Stock receive: {stock_qty} x {result.part_name}",
            stock_result["message"],
        )

    # ── Step C: Print Label (optional) ──
    label_result = None
    if (
        stock_result
        and stock_result["success"]
        and body.get("print_label")
        and body.get("label_template_id")
    ):
        from .services.stock_manager import print_stock_label

        label_result = print_stock_label(
            stock_item_id=stock_result["stock_id"],
            template_id=int(body["label_template_id"]),
            plugin_slug=plugin.get_setting("DEFAULT_PRINT_PLUGIN") or "",
            request=request,
        )
        _log_activity(
            "INFO" if label_result["success"] else "WARNING",
            f'Label print for StockItem {stock_result["stock_id"]}',
            label_result["message"],
        )

    return JsonResponse(
        {
            "success": result.success,
            "part_id": result.part_id,
            "part_name": result.part_name,
            "action": result.action,
            "message": result.message,
            "errors": result.errors,
            "stock": stock_result,
            "label": label_result,
        }
    )


# ═══════════════════════════════════════════════════════════════════
#  Part Data API (for editor comparison)
# ═══════════════════════════════════════════════════════════════════


@csrf_exempt
def api_get_part(request, part_id: int):
    """
    Fetch current InvenTree part data for the Live-Editor comparison view.
    Returns all editable fields + parameters + supplier parts as JSON.
    """
    try:
        from part.models import Part
        from company.models import ManufacturerPart, SupplierPart, SupplierPriceBreak
        from common.models import Parameter
        from django.contrib.contenttypes.models import ContentType

        part = Part.objects.get(pk=part_id)
        part_type = ContentType.objects.get_for_model(part)

        mfr_part = ManufacturerPart.objects.filter(part=part).first()

        supplier_parts = []
        for sp in SupplierPart.objects.filter(part=part).select_related("supplier"):
            pbs = []
            for pb in SupplierPriceBreak.objects.filter(part=sp):
                pbs.append(
                    {
                        "quantity": int(pb.quantity),
                        "price": str(pb.price),
                        "currency": getattr(pb, "price_currency", "EUR"),
                    }
                )
            supplier_parts.append(
                {
                    "supplier_name": sp.supplier.name if sp.supplier else "",
                    "supplier_sku": sp.SKU or "",
                    "supplier_url": sp.link or "",
                    "price_breaks": pbs,
                }
            )

        parameters = []
        for p in Parameter.objects.filter(
            model_type=part_type, model_id=part.pk
        ).select_related("template"):
            parameters.append(
                {
                    "name": p.template.name,
                    "value": p.data or "",
                    "unit": getattr(p.template, "units", "") or "",
                }
            )

        image_url = ""
        if part.image:
            try:
                image_url = part.image.url
            except Exception:
                pass

        return JsonResponse(
            {
                "id": part.pk,
                "name": part.name or "",
                "description": part.description or "",
                "link": part.link or "",
                "category_id": part.category_id,
                "category_name": str(part.category) if part.category else "",
                "mpn": mfr_part.MPN if mfr_part else "",
                "manufacturer": (
                    mfr_part.manufacturer.name
                    if mfr_part and mfr_part.manufacturer
                    else ""
                ),
                "package": "",  # Not a standard Part field; lives in parameters
                "image_url": image_url,
                "supplier_parts": supplier_parts,
                "parameters": parameters,
            }
        )

    except Part.DoesNotExist:
        return JsonResponse({"error": f"Part {part_id} not found"}, status=404)
    except Exception as e:
        logger.error(f"api_get_part failed for {part_id}: {e}", exc_info=True)
        return JsonResponse({"error": str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════
#  Batch Import
# ═══════════════════════════════════════════════════════════════════


def batch_import(request):
    """Batch import page."""
    return render(request, "inventree_smart_parts/batch_import.html", {})


@csrf_exempt
def batch_upload(request):
    """Handle batch file upload and start processing."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    denied = _check_perm(request, "part.add_part")
    if denied:
        return denied

    uploaded_file = request.FILES.get("file")
    if not uploaded_file:
        return JsonResponse({"error": "No file uploaded"}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    try:
        import json as _json
        from .batch.importer import parse_upload_file, process_batch

        # ── Priority 1: use pre-reviewed rows from the frontend grid ──────────
        reviewed_rows_raw = request.POST.get("reviewed_rows", "")
        if reviewed_rows_raw:
            try:
                rows = _json.loads(reviewed_rows_raw)
                if not isinstance(rows, list):
                    raise ValueError("reviewed_rows must be a JSON array")
                # Normalise field names to what the importer expects
                rows = [
                    {
                        "mpn": str(r.get("mpn", "")).strip(),
                        "quantity": str(r.get("quantity", "1")).strip() or "1",
                        "manufacturer": str(r.get("manufacturer", "")).strip(),
                        "description": str(r.get("description", "")).strip(),
                        "name": str(r.get("name", "")).strip(),
                        "designator": str(r.get("designator", "")).strip(),
                    }
                    for r in rows
                    if str(r.get("mpn", "")).strip()  # skip rows without MPN
                ]
                assembly_metadata = {"name": "", "description": "", "revision": ""}
            except (_json.JSONDecodeError, ValueError) as je:
                return JsonResponse(
                    {"error": f"Invalid reviewed_rows: {je}"}, status=400
                )
        else:
            # ── Fallback: parse the uploaded file (original behaviour) ────────
            rows, assembly_metadata = parse_upload_file(uploaded_file)

        if not rows:
            return JsonResponse({"error": "No valid rows found in file"}, status=400)

        # Assembly BOM options (optional, from the UI checkbox + inputs)
        assembly_name = request.POST.get("assembly_name", "").strip()
        assembly_description = request.POST.get("assembly_description", "").strip()
        assembly_revision = request.POST.get("assembly_revision", "").strip()
        assembly_category_id = request.POST.get("assembly_category_id", "").strip()
        create_assembly = request.POST.get("create_assembly", "") in ("true", "1", "on")
        if not create_assembly:
            assembly_name = ""  # Ignore name if checkbox not ticked

        # Convert category_id to int (or None)
        try:
            assembly_category_id = (
                int(assembly_category_id) if assembly_category_id else None
            )
        except (ValueError, TypeError):
            assembly_category_id = None

        _log_activity(
            "INFO",
            f"Batch upload: {len(rows)} rows from {uploaded_file.name}"
            + (f'; assembly="{assembly_name}"' if assembly_name else "")
            + (" [reviewed]" if reviewed_rows_raw else ""),
        )

        job_id = process_batch(
            rows,
            plugin,
            assembly_name=assembly_name,
            assembly_description=assembly_description,
            assembly_revision=assembly_revision,
            assembly_category_id=assembly_category_id,
        )

        return JsonResponse(
            {
                "success": True,
                "job_id": job_id,
                "total_rows": len(rows),
                "message": f"Processing {len(rows)} parts...",
                "assembly_metadata": assembly_metadata,
            }
        )

    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    except ImportError as e:
        return JsonResponse({"error": str(e)}, status=500)
    except Exception as e:
        logger.error(f"Batch upload failed: {e}", exc_info=True)
        return JsonResponse({"error": f"Upload failed: {str(e)}"}, status=500)


@csrf_exempt
def batch_status(request, job_id: str):
    """AJAX: Get batch job progress."""
    from .batch.importer import get_job

    job = get_job(job_id)
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)

    return JsonResponse(job.to_dict())


@csrf_exempt
def batch_report(request, job_id: str):
    """Download batch job results as CSV."""
    from .batch.importer import get_job
    import csv

    job = get_job(job_id)
    if not job:
        return JsonResponse({"error": "Job not found"}, status=404)

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = (
        f'attachment; filename="batch_report_{job_id}.csv"'
    )

    writer = csv.writer(response)
    writer.writerow(
        ["Row", "MPN", "Status", "Action", "Part Name", "Part ID", "Message", "Error"]
    )

    for r in job.results:
        writer.writerow(
            [
                r.row_number,
                r.mpn,
                r.status,
                r.action,
                r.part_name,
                r.part_id or "",
                r.message,
                r.error,
            ]
        )

    return response


# ═══════════════════════════════════════════════════════════════════
#  Settings
# ═══════════════════════════════════════════════════════════════════


def plugin_settings(request):
    """Plugin settings & API management page."""
    plugin = _get_plugin()
    context = {
        "plugin": plugin,
        "mouser_enabled": plugin.get_setting("MOUSER_ENABLED") if plugin else False,
        "mouser_has_key": (
            bool(plugin.get_setting("MOUSER_API_KEY")) if plugin else False
        ),
        "digikey_enabled": plugin.get_setting("DIGIKEY_ENABLED") if plugin else False,
        "digikey_has_key": (
            bool(plugin.get_setting("DIGIKEY_CLIENT_ID")) if plugin else False
        ),
        "lcsc_enabled": plugin.get_setting("LCSC_ENABLED") if plugin else False,
        "element14_enabled": (
            plugin.get_setting("ELEMENT14_ENABLED") if plugin else False
        ),
        "element14_has_key": (
            bool(plugin.get_setting("ELEMENT14_API_KEY")) if plugin else False
        ),
        "element14_store": (
            plugin.get_setting("ELEMENT14_STORE") or "uk.farnell.com"
            if plugin
            else "uk.farnell.com"
        ),
        "tme_enabled": plugin.get_setting("TME_ENABLED") if plugin else False,
        "tme_has_token": bool(plugin.get_setting("TME_API_TOKEN")) if plugin else False,
        "tme_country": plugin.get_setting("TME_COUNTRY") or "DE" if plugin else "DE",
        "tme_currency": (
            plugin.get_setting("TME_CURRENCY") or "EUR" if plugin else "EUR"
        ),
    }
    return render(request, "inventree_smart_parts/settings_page.html", context)


@csrf_exempt
def test_connection(request, provider: str):
    """AJAX: Test API connection for a specific provider."""
    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    from .api_clients import (
        MouserClient,
        DigiKeyClient,
        LCSCClient,
        Element14Client,
        TMEClient,
    )

    if provider == "mouser":
        client = MouserClient(api_key=plugin.get_setting("MOUSER_API_KEY"))
    elif provider == "digikey":
        client = DigiKeyClient(
            client_id=plugin.get_setting("DIGIKEY_CLIENT_ID"),
            client_secret=plugin.get_setting("DIGIKEY_CLIENT_SECRET"),
        )
    elif provider == "lcsc":
        client = LCSCClient()
    elif provider == "element14":
        client = Element14Client(
            api_key=plugin.get_setting("ELEMENT14_API_KEY"),
            store_name=plugin.get_setting("ELEMENT14_STORE") or "uk.farnell.com",
        )
    elif provider == "tme":
        client = TMEClient(
            token=plugin.get_setting("TME_API_TOKEN"),
            secret=plugin.get_setting("TME_API_SECRET"),
            country=plugin.get_setting("TME_COUNTRY") or "DE",
            currency=plugin.get_setting("TME_CURRENCY") or "EUR",
        )
    else:
        return JsonResponse({"error": f"Unknown provider: {provider}"}, status=400)

    result = client.test_connection()

    _log_activity(
        "INFO" if result["success"] else "WARNING",
        f'Connection test [{provider}]: {"OK" if result["success"] else "FAILED"}',
        result["message"],
    )

    return JsonResponse(result)


# ═══════════════════════════════════════════════════════════════════
#  Logs
# ═══════════════════════════════════════════════════════════════════


def logs_view(request):
    """Activity log viewer page."""
    return render(request, "inventree_smart_parts/logs.html", {})


@csrf_exempt
def api_category_parameters(request):
    """
    Return the parameter templates defined for a category hierarchy and,
    optionally, filter a raw attribute list against that whitelist.

    GET /plugin/smartparts/api/category/parameters/?category_id=<id>
    POST /plugin/smartparts/api/category/parameters/
         Body: { "category_id": <int>, "attributes": [{"name":..,"value":..}] }

    Response:
        {
          "category_id":      <int | null>,
          "category_path":    "<str>",
          "structural":       <bool>,
          "templates":        [{"name": "...", "units": "..."}],
          "accepted_parameters":  [...],   // only present when attributes were sent
          "dropped_parameters":   [...]    // only present when attributes were sent
        }

    If category_id is null/absent: returns empty templates + all attributes dropped.
    Permission: part.view_part
    """
    # ── Auth ─────────────────────────────────────────────────────────────────
    if not request.user.is_authenticated:
        return JsonResponse({"error": "Authentication required"}, status=401)
    if not request.user.has_perm("part.view_part"):
        return JsonResponse({"error": "Permission denied"}, status=403)

    # ── Parse input ───────────────────────────────────────────────────────────
    if request.method == "POST":
        try:
            body = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid JSON"}, status=400)
        category_id = body.get("category_id")
        raw_attributes = body.get("attributes") or []
    else:
        category_id_str = request.GET.get("category_id", "")
        try:
            category_id = int(category_id_str) if category_id_str else None
        except ValueError:
            return JsonResponse({"error": "category_id must be an integer"}, status=400)
        raw_attributes = []

    # ── Resolve category ──────────────────────────────────────────────────────
    from .core import get_resolved_category_templates

    category = None
    category_path = ""
    is_structural = False

    if category_id is not None:
        try:
            from part.models import PartCategory

            category = PartCategory.objects.get(pk=category_id)
            category_path = (
                category.pathstring
                if hasattr(category, "pathstring")
                else str(category)
            )
            is_structural = bool(getattr(category, "structural", False))
        except Exception:
            return JsonResponse(
                {"error": f"Category {category_id} not found"}, status=404
            )

    resolved = get_resolved_category_templates(category)

    templates_list = []
    for name, tpl in resolved.items():
        template_obj = getattr(tpl, "template", None) or getattr(
            tpl, "parameter_template", None
        )
        units = getattr(template_obj, "units", "") if template_obj else ""
        templates_list.append({"name": name, "units": units or ""})

    response: dict = {
        "category_id": category_id,
        "category_path": category_path,
        "structural": is_structural,
        "templates": sorted(templates_list, key=lambda t: t["name"]),
    }

    # ── Optional: filter supplied attributes ──────────────────────────────────
    if raw_attributes:
        from .services.parameter_normalizer import filter_parameters_by_category

        # Normalise: accept both {"name":..,"value":..} and raw strings
        norm_attrs = []
        for a in raw_attributes:
            if isinstance(a, dict):
                norm_attrs.append(
                    {
                        "name": a.get("name", ""),
                        "value": a.get("value", ""),
                        "unit": a.get("unit", ""),
                    }
                )
            elif isinstance(a, str):
                norm_attrs.append({"name": a, "value": "", "unit": ""})

        fr = filter_parameters_by_category(norm_attrs, resolved)
        response["accepted_parameters"] = fr.accepted_parameters
        response["dropped_parameters"] = fr.dropped_parameters

    return JsonResponse(response)


@csrf_exempt
def api_logs(request):
    """AJAX: Return activity logs as JSON."""
    level_filter = request.GET.get("level", "") or None
    limit = min(int(request.GET.get("limit", 200)), 1000)

    logs = get_logs(level_filter=level_filter, limit=limit)
    total = len(get_logs(limit=10000))  # Count without limit for the UI badge

    return JsonResponse({"logs": logs, "total": total})


@csrf_exempt
def api_logs_clear(request):
    """AJAX: Clear all activity logs (POST only)."""
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)
    denied = _check_perm(request, "part.change_part")
    if denied:
        return denied
    from .services.activity_logger import clear_logs

    clear_logs()
    return JsonResponse({"success": True, "message": "All logs cleared."})


# ═══════════════════════════════════════════════════════════════════
#  Helpers
# ═══════════════════════════════════════════════════════════════════


def _part_data_to_dict(pd) -> dict:
    """Convert a PartData instance to a JSON-serializable dict."""
    if pd is None:
        return {}

    # If the merged datasheet_url is empty, fall back to any source that has one
    datasheet_url = pd.datasheet_url
    if not datasheet_url:
        for entry in pd.raw_data.get("source_datasheet_urls", []):
            if entry.get("url", "").startswith("http"):
                datasheet_url = entry["url"]
                break

    return {
        "mpn": pd.mpn,
        "manufacturer": pd.manufacturer,
        "description": pd.description,
        "name": pd.name,
        "category": pd.category,
        "subcategory": pd.subcategory,
        "supplier_name": pd.supplier_name,
        "supplier_sku": pd.supplier_sku,
        "supplier_url": pd.supplier_url,
        "datasheet_url": datasheet_url,
        "image_url": pd.image_url,
        "package": pd.package,
        "parameters": [
            {"name": p.name, "value": p.value, "unit": p.unit} for p in pd.parameters
        ],
        "price_breaks": [
            {"quantity": pb.quantity, "price": pb.price, "currency": pb.currency}
            for pb in pd.price_breaks
        ],
        "stock_available": pd.stock_available,
        "source": pd.source,
        "confidence": pd.confidence,
        # Pass through aggregated supplier list from the merger so the editor
        # receives one card per distributor and the backend creates all SupplierParts.
        "supplier_data": pd.raw_data.get("supplier_data", []),
        "source_image_urls": pd.raw_data.get("source_image_urls", []),
    }


@csrf_exempt
def api_synonyms(request):
    """
    GET  → return current CATEGORY_SYNONYMS plugin setting as JSON
    POST → validate & save a new CATEGORY_SYNONYMS value
    """
    import json as _json

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    if request.method == "GET":
        value = plugin.get_setting("CATEGORY_SYNONYMS") or "{}"
        return JsonResponse({"value": value})

    if request.method == "POST":
        denied = _check_perm(request, "part.change_partcategory")
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get("value", "{}")
            # Validate it's parseable JSON
            _json.loads(raw)
        except (_json.JSONDecodeError, TypeError, KeyError) as e:
            return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

        try:
            plugin.set_setting("CATEGORY_SYNONYMS", raw)
            return JsonResponse({"success": True, "value": raw})
        except Exception as e:
            logger.error(f"Failed to save CATEGORY_SYNONYMS: {e}", exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def api_learned(request):
    """
    GET  → return current LEARNED_CATEGORY_MAPPINGS plugin setting as JSON
    POST → validate & save a new LEARNED_CATEGORY_MAPPINGS value
    DELETE (POST with ?delete=1 + key) → remove a single mapping entry
    """
    import json as _json

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    if request.method == "GET":
        value = plugin.get_setting("LEARNED_CATEGORY_MAPPINGS") or "{}"
        return JsonResponse({"value": value})

    if request.method == "POST":
        denied = _check_perm(request, "part.change_partcategory")
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get("value", "{}")
            # Validate it's a JSON object
            parsed = _json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, _json.JSONDecodeError, TypeError) as e:
            return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

        try:
            plugin.set_setting("LEARNED_CATEGORY_MAPPINGS", raw)
            return JsonResponse({"success": True, "value": raw})
        except Exception as e:
            logger.error(
                f"Failed to save LEARNED_CATEGORY_MAPPINGS: {e}", exc_info=True
            )
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def api_parameter_mappings(request):
    """
    GET  → return current LEARNED_PARAMETER_MAPPINGS plugin setting as JSON
    POST → validate & save a new LEARNED_PARAMETER_MAPPINGS value
    """
    import json as _json

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    if request.method == "GET":
        value = plugin.get_setting("LEARNED_PARAMETER_MAPPINGS") or "{}"
        return JsonResponse({"value": value})

    if request.method == "POST":
        denied = _check_perm(request, "part.change_part")
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get("value", "{}")
            # Validate it's a JSON object
            parsed = _json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, _json.JSONDecodeError, TypeError) as e:
            return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

        try:
            plugin.set_setting("LEARNED_PARAMETER_MAPPINGS", raw)
            return JsonResponse({"success": True, "value": raw})
        except Exception as e:
            logger.error(
                f"Failed to save LEARNED_PARAMETER_MAPPINGS: {e}", exc_info=True
            )
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


@csrf_exempt
def api_unknown_parameters(request):
    """
    GET  → return current TRACKED_UNKNOWN_PARAMETERS plugin setting as JSON
    POST → validate & save a new TRACKED_UNKNOWN_PARAMETERS value
    """
    import json as _json

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    if request.method == "GET":
        value = plugin.get_setting("TRACKED_UNKNOWN_PARAMETERS") or "{}"
        return JsonResponse({"value": value})

    if request.method == "POST":
        denied = _check_perm(request, "part.change_part")
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get("value", "{}")
            # Validate it's a JSON object
            parsed = _json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError("Expected a JSON object")
        except (ValueError, _json.JSONDecodeError, TypeError) as e:
            return JsonResponse({"error": f"Invalid JSON: {e}"}, status=400)

        try:
            plugin.set_setting("TRACKED_UNKNOWN_PARAMETERS", raw)
            return JsonResponse({"success": True, "value": raw})
        except Exception as e:
            logger.error(
                f"Failed to save TRACKED_UNKNOWN_PARAMETERS: {e}", exc_info=True
            )
            return JsonResponse({"error": str(e)}, status=500)

    return JsonResponse({"error": "Method not allowed"}, status=405)


def parameter_dashboard(request):
    """View to display the Parameter Normalization Dashboard."""
    plugin = _get_plugin()
    context = {
        "plugin": plugin,
    }
    return render(request, "inventree_smart_parts/parameter_dashboard.html", context)


def api_canonical_parameters(request):
    """Return a list of all existing canonical parameter names from DB and built-in map."""
    from common.models import ParameterTemplate
    from .services.parameter_normalizer import PARAMETER_MAP

    # Get database parameter templates
    try:
        db_names = list(ParameterTemplate.objects.all().values_list("name", flat=True))
    except Exception:
        db_names = []

    # Get built-in canonical names
    builtin_names = list(set(PARAMETER_MAP.values()))

    # Merge and deduplicate
    all_names = sorted(list(set(db_names + builtin_names)))

    return JsonResponse({"names": all_names})


# ═══════════════════════════════════════════════════════════════════
#  Stock & Label APIs
# ═══════════════════════════════════════════════════════════════════


def api_stock_locations(request):
    """Return the full StockLocation tree as a flat JSON list."""
    from .services.stock_manager import get_stock_locations

    locations = get_stock_locations()
    return JsonResponse({"locations": locations})


def api_label_templates(request):
    """Return available stock item label templates + the configured default."""
    from .services.stock_manager import get_label_templates

    plugin = _get_plugin()
    templates = get_label_templates()
    default_id = int(plugin.get_setting("DEFAULT_STOCK_LABEL") or 0) if plugin else 0
    return JsonResponse({"templates": templates, "default_id": default_id})


@csrf_exempt
def api_create_stock(request):
    """
    POST: Create a StockItem for an existing Part.
    Body: { part_id, quantity, location_id?, batch?, delete_on_deplete? }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    denied = _check_perm(request, "stock.add_stockitem")
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    part_id = body.get("part_id")
    quantity = float(body.get("quantity", 0) or 0)
    if not part_id or quantity <= 0:
        return JsonResponse(
            {"error": "part_id and quantity > 0 are required"}, status=400
        )

    from .services.stock_manager import create_stock_item

    result = create_stock_item(
        part_id=int(part_id),
        quantity=quantity,
        location_id=body.get("location_id") or None,
        batch=body.get("batch", ""),
        delete_on_deplete=bool(body.get("delete_on_deplete", True)),
        user=request.user,
    )
    status_code = 200 if result["success"] else 500
    return JsonResponse(result, status=status_code)


@csrf_exempt
def api_print_label(request):
    """
    POST: Print a label for an existing StockItem.
    Body: { stock_item_id, template_id, plugin_slug? }
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    denied = _check_perm(request, "stock.view_stockitem")
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    stock_item_id = body.get("stock_item_id")
    template_id = body.get("template_id")
    if not stock_item_id or not template_id:
        return JsonResponse(
            {"error": "stock_item_id and template_id are required"}, status=400
        )

    plugin = _get_plugin()
    plugin_slug = (
        body.get("plugin_slug")
        or (plugin.get_setting("DEFAULT_PRINT_PLUGIN") if plugin else "")
        or ""
    )

    from .services.stock_manager import print_stock_label

    result = print_stock_label(
        stock_item_id=int(stock_item_id),
        template_id=int(template_id),
        plugin_slug=plugin_slug,
        request=request,
    )
    status_code = 200 if result["success"] else 500
    return JsonResponse(result, status=status_code)


# ═══════════════════════════════════════════════════════════════════
#  PureScan API endpoints
# ═══════════════════════════════════════════════════════════════════


def purescan_resolve_barcode(request):
    """GET: Resolve an InvenTree barcode string to its object type and PK.
    Query param: ?barcode=...
    Returns: { type: 'stockitem'|'stocklocation'|'part', id: int, name: str }
    """
    raw = request.GET.get("barcode", "").strip()
    if not raw:
        return JsonResponse({"error": "barcode parameter required"}, status=400)

    # Try JSON parse
    import json as _json

    result = {"type": None, "id": None, "name": ""}

    if raw.startswith("{"):
        try:
            obj = _json.loads(raw)
            for key in ["stockitem", "stocklocation", "part"]:
                if key in obj:
                    result["type"] = key
                    result["id"] = int(obj[key])
                    break
        except (ValueError, KeyError):
            pass

    if not result["type"]:
        # Try key=value format
        import re

        m = re.match(r"^(stockitem|stocklocation|part)[=:](\d+)$", raw, re.I)
        if m:
            result["type"] = m.group(1).lower()
            result["id"] = int(m.group(2))

    if not result["type"]:
        return JsonResponse(
            {"error": "Unrecognised barcode format", "raw": raw}, status=400
        )

    # Fetch the name for display
    try:
        if result["type"] == "stockitem":
            from stock.models import StockItem

            si = StockItem.objects.select_related("part", "location").get(
                pk=result["id"]
            )
            result["name"] = str(si.part)
            result["quantity"] = float(si.quantity)
            result["location"] = str(si.location) if si.location else "No location"
        elif result["type"] == "stocklocation":
            from stock.models import StockLocation

            loc = StockLocation.objects.get(pk=result["id"])
            result["name"] = loc.pathstring or str(loc)
        elif result["type"] == "part":
            from part.models import Part

            result["name"] = str(Part.objects.get(pk=result["id"]))
    except Exception as e:
        result["name"] = f'#{result["id"]} (lookup failed)'
        logger.warning(f"PureScan barcode lookup failed: {e}")

    return JsonResponse(result)


def purescan_print_label(request):
    """POST: Auto-print a label for a stock item using the plugin's default settings.
    Body: { "stock_item_id": int }
    Uses DEFAULT_STOCK_LABEL and DEFAULT_PRINT_PLUGIN from plugin settings.
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    denied = _check_perm(request, "stock.view_stockitem")
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    stock_item_id = body.get("stock_item_id")
    if not stock_item_id:
        return JsonResponse({"error": "stock_item_id is required"}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Smart Parts plugin not found"}, status=500)

    template_id = plugin.get_setting("DEFAULT_STOCK_LABEL")
    if not template_id:
        return JsonResponse(
            {
                "success": False,
                "error": "No default label template configured. Set DEFAULT_STOCK_LABEL in plugin settings.",
            },
            status=400,
        )

    plugin_slug = plugin.get_setting("DEFAULT_PRINT_PLUGIN") or ""

    from .services.stock_manager import print_stock_label

    result = print_stock_label(
        stock_item_id=int(stock_item_id),
        template_id=int(template_id),
        plugin_slug=plugin_slug,
        request=request,
    )
    status_code = 200 if result["success"] else 500
    return JsonResponse(result, status=status_code)


# ═══════════════════════════════════════════════════════════════════
#  External REST API – v1
#  Base path: /plugin/smartparts/api/v1/
#
#  Authentication: InvenTree token (Authorization: Token <key>)
#                  or active session (browser).
# ═══════════════════════════════════════════════════════════════════


@csrf_exempt
def api_v1_import(request):
    """
    Single-shot MPN search + optional part ingestion endpoint.

    POST /plugin/smartparts/api/v1/import/
    Authorization: Token <inventree-api-token>

    Body (JSON):
        mpn             str   – required. Manufacturer part number to look up.
        auto_create     bool  – default false.  Set true to persist the part.
        dry_run         bool  – default false.  If true, forces no DB writes
                                regardless of auto_create.
        update_existing mixed – null (default) = use plugin's DUPLICATE_ACTION
                                setting; true/false = explicit override.
        full_response   bool  – default false.  When true, includes the full
                                merged PartData, per-source dicts, and parameters.

    Response (concise, default):
        {
          "mpn": "...", "found": true, "manufacturer": "...",
          "description": "...", "category_suggestion": {...},
          "duplicate": {...}, "sources_queried": [...],
          "sources_found": [...], "dry_run": false, "auto_create": false,
          "created": false, "action": null, "part_id": null,
          "part_name": null, "message": "", "errors": []
        }

    Response (full_response=true, adds):
        "merged": { <full PartData dict> },
        "sources": { "mouser": {...}, ... },
        "parameters": [...],
        "supplier_data": [...]

    Permission: part.add_part (only enforced when auto_create=True)
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    # ── Authentication ───────────────────────────────────────────────
    user, auth_error = _resolve_user(request)
    if auth_error:
        return auth_error

    # ── Parse body ──────────────────────────────────────────────────
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    mpn = body.get("mpn", "").strip()
    if not mpn:
        return JsonResponse({"error": "mpn is required"}, status=400)

    auto_create = bool(body.get("auto_create", False))
    dry_run = bool(body.get("dry_run", False))
    full_response = bool(body.get("full_response", False))

    # update_existing: None → use plugin setting; True/False → explicit override
    update_existing_raw = body.get("update_existing", None)
    if update_existing_raw is None:
        update_existing_override = None  # resolved later from plugin setting
    else:
        update_existing_override = bool(update_existing_raw)

    # Permission check – only needed when we will actually write to the DB
    if auto_create and not dry_run:
        if not user.has_perm("part.add_part"):
            logger.warning(
                f'API v1 import: permission denied for user="{user.username}"'
            )
            return JsonResponse(
                {"error": 'Permission denied – requires "part.add_part"'},
                status=403,
            )

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    # ── Distributor queries ─────────────────────────────────────────
    _log_activity(
        "INFO",
        f"[API v1] MPN lookup: {mpn} (auto_create={auto_create}, dry_run={dry_run})",
    )
    api_results, results_dict = _run_all_api_searches(plugin, mpn)

    # ── Merge ───────────────────────────────────────────────────────
    from .services.data_merger import merge_part_data

    priority_str = (
        plugin.get_setting("API_PRIORITY") or "mouser,digikey,element14,tme,lcsc"
    )
    priority_order = [p.strip() for p in priority_str.split(",") if p.strip()]
    merged = merge_part_data(api_results, priority_order)

    # ── Category mapping ────────────────────────────────────────────
    category_match = None
    if merged and merged.category:
        from .services.category_mapper import fuzzy_match_category

        threshold = plugin.get_setting("FUZZY_THRESHOLD") or 65
        default_cat = plugin.get_setting("DEFAULT_CATEGORY") or ""
        user_synonyms = plugin.get_setting("CATEGORY_SYNONYMS") or "{}"
        learned_mappings = plugin.get_setting("LEARNED_CATEGORY_MAPPINGS") or "{}"
        cat_id, cat_path, score = fuzzy_match_category(
            merged.category,
            threshold,
            default_cat,
            user_synonyms_json=user_synonyms,
            learned_mappings_json=learned_mappings,
        )
        category_match = {
            "id": cat_id,
            "path": cat_path,
            "score": score,
            "distributor_category": merged.category,
        }

    # ── Duplicate check ─────────────────────────────────────────────
    dup_info = None
    if merged:
        from .services.duplicate_checker import check_duplicate

        dup = check_duplicate(merged.mpn, merged.manufacturer)
        if dup.is_duplicate:
            dup_info = {
                "is_duplicate": True,
                "part_id": dup.existing_part_id,
                "part_name": dup.existing_part_name,
                "existing_mpn": dup.existing_mpn,
                "match_type": dup.match_type,
                "confidence": dup.confidence,
            }
        else:
            dup_info = {"is_duplicate": False}

    # ── Serialize merged result ──────────────────────────────────────
    merged_dict = _part_data_to_dict(merged) if merged else None
    if merged_dict and merged_dict.get("parameters"):
        from .services.parameter_normalizer import normalize_parameter_list

        merged_dict["parameters"] = normalize_parameter_list(merged_dict["parameters"])

    # ── Determine which sources actually returned data ────────────────
    def _is_found(v):
        """True if a results_dict entry represents a successful hit."""
        return v is not None and isinstance(v, dict) and "error" not in v

    sources_found = [k for k, v in results_dict.items() if _is_found(v)]

    # ── Resolve effective update_existing flag ────────────────────────
    if update_existing_override is not None:
        effective_update_existing = update_existing_override
    else:
        effective_update_existing = plugin.get_setting("DUPLICATE_ACTION") == "update"

    # ── Build base response ─────────────────────────────────────────
    response = {
        "mpn": mpn,
        "found": merged is not None,
        "manufacturer": merged.manufacturer if merged else "",
        "description": merged.description if merged else "",
        "category_suggestion": category_match,
        "duplicate": dup_info,
        "sources_queried": list(results_dict.keys()),
        "sources_found": sources_found,
        "dry_run": dry_run,
        "auto_create": auto_create,
        "created": False,
        "action": None,
        "part_id": None,
        "part_name": None,
        "message": "",
        "errors": [],
    }

    # ── Part creation (only if requested and not dry_run) ────────────
    if auto_create and not dry_run and merged is not None:
        from .services.part_creator import create_part_from_data

        existing_part_id = None
        if dup_info and dup_info["is_duplicate"] and effective_update_existing:
            existing_part_id = dup_info["part_id"]

        creation_result = create_part_from_data(
            part_data=merged,
            category_id=category_match["id"] if category_match else None,
            update_existing=effective_update_existing,
            existing_part_id=existing_part_id,
            auto_create_companies=plugin.get_setting("AUTO_CREATE_MANUFACTURERS"),
        )

        response["created"] = creation_result.success
        response["action"] = creation_result.action
        response["part_id"] = creation_result.part_id
        response["part_name"] = creation_result.part_name
        response["message"] = creation_result.message
        response["errors"] = creation_result.errors
        # Stash for the dropped_parameters block below
        response["_dropped_parameters"] = creation_result.dropped_parameters

        _log_activity(
            "INFO" if creation_result.success else "ERROR",
            f"[API v1] Part {'created' if creation_result.action == 'created' else creation_result.action}: {creation_result.part_name or mpn}",
            creation_result.message,
        )

    # ── Build dropped_parameters for dry-run preview or full_response ──────────
    # Populated from the creation pipeline if a part was actually created,
    # or computed inline for dry-run / full_response inspection passes.
    dropped_parameters: list = response.pop("_dropped_parameters", [])

    if (dry_run or full_response) and merged is not None and merged_dict is not None:
        try:
            limit_setting = plugin.get_setting("LIMIT_PARAMETERS_TO_CATEGORY")
            limit_to_category = True if limit_setting is None else bool(limit_setting)

            cat_id = category_match["id"] if category_match else None
            if limit_to_category and cat_id is not None:
                from part.models import PartCategory
                from .services.parameter_normalizer import filter_parameters_by_category
                from .core import get_resolved_category_templates

                try:
                    category = PartCategory.objects.get(pk=cat_id)
                    resolved = get_resolved_category_templates(category)
                    params = merged_dict.get("parameters", []) or []
                    fr = filter_parameters_by_category(params, resolved)
                    dropped_parameters = fr.dropped_parameters
                    # For dry-run/full_response, narrow the visible parameters list
                    # to the accepted subset so the caller sees exactly what would persist.
                    merged_dict["parameters"] = fr.accepted_parameters
                except Exception as _flt_exc:
                    logger.debug(
                        "[API v1] dry-run category filter failed: %s", _flt_exc
                    )
        except Exception as _plug_exc:
            logger.debug(
                "[API v1] Could not read LIMIT_PARAMETERS_TO_CATEGORY: %s", _plug_exc
            )

    if dry_run or full_response:
        response["dropped_parameters"] = dropped_parameters

    # ── Augment with full data if requested ─────────────────────────
    if full_response:
        response["merged"] = merged_dict
        response["sources"] = results_dict
        response["parameters"] = (
            merged_dict.get("parameters", []) if merged_dict else []
        )
        response["supplier_data"] = (
            merged_dict.get("supplier_data", []) if merged_dict else []
        )

    return JsonResponse(response)


@csrf_exempt
def api_v1_raw_lookup(request):
    """
    Raw distributor aggregation endpoint for debugging / data inspection.

    POST /plugin/smartparts/api/v1/raw-lookup/
    Authorization: Token <inventree-api-token>

    Body (JSON):
        mpn  str  – required.

    Response:
        {
          "mpn": "...",
          "sources": {
            "mouser":   { <PartData dict> | null | {"error": "..."} | {"error": "disabled"} },
            "digikey":  ...,
            "lcsc":     ...,
            "element14": ...,
            "tme":      ...
          },
          "queried_at": "2026-09-08T11:00:00Z"
        }

    All five distributor keys are always present; disabled sources receive
    {"error": "disabled"} so callers can distinguish "not configured" from
    "configured but returned no result" (null).

    Permission: part.view_part
    """
    if request.method != "POST":
        return JsonResponse({"error": "POST required"}, status=405)

    # ── Authentication ───────────────────────────────────────────────
    user, auth_error = _resolve_user(request)
    if auth_error:
        return auth_error

    if not user.has_perm("part.view_part"):
        return JsonResponse(
            {"error": 'Permission denied – requires "part.view_part"'},
            status=403,
        )

    # ── Parse body ──────────────────────────────────────────────────
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON"}, status=400)

    mpn = body.get("mpn", "").strip()
    if not mpn:
        return JsonResponse({"error": "mpn is required"}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({"error": "Plugin not loaded"}, status=500)

    # ── Query all enabled distributors ───────────────────────────────
    _log_activity("INFO", f"[API v1] Raw lookup: {mpn}")
    _api_results, results_dict = _run_all_api_searches(plugin, mpn)

    # ── Ensure all 5 known sources are present in the response ───────
    # Sources not queried (disabled) get {"error": "disabled"} so the caller
    # can distinguish "disabled" from "enabled but no result" (null).
    all_known_sources = ["mouser", "digikey", "lcsc", "element14", "tme"]
    for source in all_known_sources:
        if source not in results_dict:
            results_dict[source] = {"error": "disabled"}

    return JsonResponse(
        {
            "mpn": mpn,
            "sources": results_dict,
            "queried_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )


# ═══════════════════════════════════════════════════════════════════
#  Global Barcode Lookup Endpoint
# ═══════════════════════════════════════════════════════════════════


@csrf_exempt
def api_barcode_lookup(request):
    """
    Lookup a part in InvenTree using a scanned barcode or extracted MPN/SKU.

    Endpoint: POST or GET /plugin/smartparts/api/barcode/lookup/
    Payload (JSON or Form/Query data):
        - barcode: raw barcode string (with control characters, DataMatrix, etc.)
        - mpn: optional extracted MPN string
        - sku: optional extracted supplier SKU string

    Returns:
        {
            "found": True / False,
            "part_id": int or None,
            "part_name": str,
            "part_ipn": str,
            "part_url": str or None,
            "mpn": str,
            "distributor": str or None,
            "barcode_data": dict or None
        }
    """
    raw_barcode = ""
    mpn = ""
    sku = ""

    if request.method == "POST":
        if request.content_type and "application/json" in request.content_type:
            try:
                body = json.loads(
                    request.body.decode("utf-8")
                    if isinstance(request.body, bytes)
                    else request.body
                )
                raw_barcode = str(body.get("barcode", "")).strip()
                mpn = str(body.get("mpn", "")).strip()
                sku = str(body.get("sku", "")).strip()
            except Exception:
                pass
        if not raw_barcode and not mpn and not sku:
            raw_barcode = str(request.POST.get("barcode", "")).strip()
            mpn = str(request.POST.get("mpn", "")).strip()
            sku = str(request.POST.get("sku", "")).strip()
    else:
        raw_barcode = str(request.GET.get("barcode", "")).strip()
        mpn = str(request.GET.get("mpn", "")).strip()
        sku = str(request.GET.get("sku", "")).strip()

    from .services.barcode_decoder import parse_barcode

    parsed = None
    if raw_barcode:
        parsed = parse_barcode(raw_barcode)
        if not mpn and parsed.mpn:
            mpn = parsed.mpn
        if not sku and parsed.supplier_sku:
            sku = parsed.supplier_sku

    # ── Native InvenTree Barcode Discrimination ──────────────────────
    clean_raw = raw_barcode.strip()
    if clean_raw.startswith("{") and clean_raw.endswith("}"):
        try:
            j_obj = json.loads(clean_raw)
            if not isinstance(j_obj, dict):
                j_obj = {}
        except Exception:
            j_obj = {}

        # 1. Stock Location JSON: {"stocklocation": 5} or {"location": 5}
        loc_id = (
            j_obj.get("stocklocation")
            if "stocklocation" in j_obj
            else j_obj.get("location")
        )
        if loc_id is not None:
            try:
                from stock.models import StockLocation

                loc = StockLocation.objects.filter(pk=int(loc_id)).first()
                if loc:
                    url = f"/web/stock/location/{loc.pk}/"
                    return JsonResponse(
                        {
                            "found": True,
                            "is_native": True,
                            "native_type": "stocklocation",
                            "part_id": None,
                            "part_name": loc.name,
                            "part_ipn": "",
                            "part_url": url,
                            "mpn": "",
                            "distributor": None,
                            "barcode_data": {"type": "stocklocation", "pk": loc.pk},
                        }
                    )
                else:
                    return JsonResponse(
                        {
                            "found": False,
                            "is_native": True,
                            "native_type": "stocklocation",
                            "part_id": None,
                            "part_name": "",
                            "part_ipn": "",
                            "part_url": None,
                            "mpn": "",
                            "error": f"Stock location {loc_id} not found in database",
                            "barcode_data": {"type": "stocklocation", "pk": loc_id},
                        }
                    )
            except Exception as e:
                logger.warning(f"SmartParts: Error resolving stocklocation JSON: {e}")

        # 2. Stock Item JSON: {"stockitem": 71} or {"item": 71}
        item_id = j_obj.get("stockitem") if "stockitem" in j_obj else j_obj.get("item")
        if item_id is not None:
            try:
                from stock.models import StockItem

                si = (
                    StockItem.objects.filter(pk=int(item_id))
                    .select_related("part")
                    .first()
                )
                if si:
                    url = f"/web/stock/item/{si.pk}/"
                    part_obj = si.part
                    return JsonResponse(
                        {
                            "found": True,
                            "is_native": True,
                            "native_type": "stockitem",
                            "part_id": part_obj.pk if part_obj else None,
                            "part_name": part_obj.name if part_obj else str(si),
                            "part_ipn": (
                                getattr(part_obj, "IPN", "") or "" if part_obj else ""
                            ),
                            "part_url": url,
                            "mpn": part_obj.name if part_obj else "",
                            "distributor": None,
                            "barcode_data": {"type": "stockitem", "pk": si.pk},
                        }
                    )
                else:
                    return JsonResponse(
                        {
                            "found": False,
                            "is_native": True,
                            "native_type": "stockitem",
                            "part_id": None,
                            "part_name": "",
                            "part_ipn": "",
                            "part_url": None,
                            "mpn": "",
                            "error": f"Stock item {item_id} not found in database",
                            "barcode_data": {"type": "stockitem", "pk": item_id},
                        }
                    )
            except Exception as e:
                logger.warning(f"SmartParts: Error resolving stockitem JSON: {e}")

        # 3. Part JSON: {"part": 326}
        part_id = j_obj.get("part")
        if part_id is not None:
            try:
                from part.models import Part

                part = Part.objects.filter(pk=int(part_id)).first()
                if part:
                    url = (
                        part.get_absolute_url()
                        if hasattr(part, "get_absolute_url")
                        else f"/web/part/{part.pk}/"
                    )
                    return JsonResponse(
                        {
                            "found": True,
                            "is_native": True,
                            "native_type": "part",
                            "part_id": part.pk,
                            "part_name": part.name,
                            "part_ipn": getattr(part, "IPN", "") or "",
                            "part_url": url,
                            "mpn": part.name,
                            "distributor": None,
                            "barcode_data": {"type": "part", "pk": part.pk},
                        }
                    )
                else:
                    return JsonResponse(
                        {
                            "found": False,
                            "is_native": True,
                            "native_type": "part",
                            "part_id": None,
                            "part_name": "",
                            "part_ipn": "",
                            "part_url": None,
                            "mpn": "",
                            "error": f"Part {part_id} not found in database",
                            "barcode_data": {"type": "part", "pk": part_id},
                        }
                    )
            except Exception as e:
                logger.warning(f"SmartParts: Error resolving part JSON: {e}")

        # 4. Core Order & Manufacturing JSON models (Purchase Orders, Build Orders, Sales Orders, Return Orders, Supplier/Manufacturer Parts)
        ORDER_ROUTE_MAP = {
            "purchaseorder": (
                "order.models",
                "PurchaseOrder",
                "/web/purchasing/purchase-order/",
                "reference",
            ),
            "purchase_order": (
                "order.models",
                "PurchaseOrder",
                "/web/purchasing/purchase-order/",
                "reference",
            ),
            "build": (
                "build.models",
                "Build",
                "/web/manufacturing/build-order/",
                "reference",
            ),
            "buildorder": (
                "build.models",
                "Build",
                "/web/manufacturing/build-order/",
                "reference",
            ),
            "build_order": (
                "build.models",
                "Build",
                "/web/manufacturing/build-order/",
                "reference",
            ),
            "salesorder": (
                "order.models",
                "SalesOrder",
                "/web/sales/sales-order/",
                "reference",
            ),
            "sales_order": (
                "order.models",
                "SalesOrder",
                "/web/sales/sales-order/",
                "reference",
            ),
            "returnorder": (
                "order.models",
                "ReturnOrder",
                "/web/sales/return-order/",
                "reference",
            ),
            "return_order": (
                "order.models",
                "ReturnOrder",
                "/web/sales/return-order/",
                "reference",
            ),
            "supplierpart": (
                "company.models",
                "SupplierPart",
                "/web/purchasing/supplier-part/",
                "SKU",
            ),
            "supplier_part": (
                "company.models",
                "SupplierPart",
                "/web/purchasing/supplier-part/",
                "SKU",
            ),
            "manufacturerpart": (
                "company.models",
                "ManufacturerPart",
                "/web/part/manufacturer-part/",
                "MPN",
            ),
            "manufacturer_part": (
                "company.models",
                "ManufacturerPart",
                "/web/part/manufacturer-part/",
                "MPN",
            ),
        }

        for k, v in j_obj.items():
            norm_k = str(k).lower().replace("-", "").strip()
            match_def = ORDER_ROUTE_MAP.get(norm_k)
            if match_def and v is not None:
                mod_name, cls_name, route_pfx, name_attr = match_def
                try:
                    pk_val = (
                        int(v)
                        if not isinstance(v, dict)
                        else int(v.get("pk") or v.get("id"))
                    )
                except (ValueError, TypeError):
                    continue
                try:
                    mod = __import__(mod_name, fromlist=[cls_name])
                    model_cls = getattr(mod, cls_name)
                    obj = model_cls.objects.filter(pk=pk_val).first()
                    clean_norm = norm_k.replace("_", "")
                    if obj:
                        url = f"{route_pfx}{obj.pk}/"
                        obj_name = str(getattr(obj, name_attr, "") or obj)
                        return JsonResponse(
                            {
                                "found": True,
                                "is_native": True,
                                "native_type": clean_norm,
                                "part_id": None,
                                "part_name": obj_name,
                                "part_ipn": "",
                                "part_url": url,
                                "mpn": "",
                                "distributor": None,
                                "barcode_data": {"type": clean_norm, "pk": obj.pk},
                            }
                        )
                    else:
                        return JsonResponse(
                            {
                                "found": False,
                                "is_native": True,
                                "native_type": clean_norm,
                                "part_id": None,
                                "part_name": "",
                                "part_ipn": "",
                                "part_url": None,
                                "mpn": "",
                                "error": f"{cls_name} {pk_val} not found in database",
                                "barcode_data": {"type": clean_norm, "pk": pk_val},
                            }
                        )
                except Exception as e:
                    logger.warning(f"SmartParts: Error resolving {cls_name} JSON: {e}")

        # 5. Any other JSON object: internal object, never an MPN
        return JsonResponse(
            {
                "found": False,
                "is_native": True,
                "native_type": "json_internal",
                "part_id": None,
                "part_name": "",
                "part_ipn": "",
                "part_url": None,
                "mpn": "",
                "error": "Internal InvenTree JSON barcode payload",
                "barcode_data": j_obj,
            }
        )

    # InvenTree Short Codes: INV-...
    if clean_raw.startswith("INV-"):
        try:
            from plugin import PluginMixinEnum, registry

            for p in registry.with_mixin(PluginMixinEnum.BARCODE):
                if getattr(p, "slug", "") == "smartparts":
                    continue
                res = p.scan(clean_raw)
                if res and isinstance(res, dict):
                    MODEL_SHORTCODE_MAP = {
                        "stocklocation": ("/web/stock/location/", "name"),
                        "stockitem": ("/web/stock/item/", None),
                        "part": ("/web/part/", "name"),
                        "purchaseorder": (
                            "/web/purchasing/purchase-order/",
                            "reference",
                        ),
                        "build": ("/web/manufacturing/build-order/", "reference"),
                        "salesorder": ("/web/sales/sales-order/", "reference"),
                        "returnorder": ("/web/sales/return-order/", "reference"),
                        "supplierpart": ("/web/purchasing/supplier-part/", "SKU"),
                        "manufacturerpart": ("/web/part/manufacturer-part/", "MPN"),
                    }
                    for model_key, (
                        route_pfx,
                        name_field,
                    ) in MODEL_SHORTCODE_MAP.items():
                        if model_key in res:
                            info = res[model_key]
                            pk = info.get("pk")
                            url = info.get("web_url") or f"{route_pfx}{pk}/"
                            inst = info.get("instance", {})
                            name = (
                                (inst.get(name_field) if name_field else None)
                                or inst.get("name")
                                or str(pk)
                            )
                            return JsonResponse(
                                {
                                    "found": True,
                                    "is_native": True,
                                    "native_type": model_key,
                                    "part_id": pk if model_key == "part" else None,
                                    "part_name": name,
                                    "part_ipn": "",
                                    "part_url": url,
                                    "mpn": name if model_key == "part" else "",
                                    "distributor": None,
                                    "barcode_data": res,
                                }
                            )
        except Exception as e:
            logger.warning(f"SmartParts: Error checking short barcode: {e}")

        return JsonResponse(
            {
                "found": False,
                "is_native": True,
                "native_type": "short_code",
                "part_id": None,
                "part_name": "",
                "part_ipn": "",
                "part_url": None,
                "mpn": "",
                "error": f"InvenTree short barcode {clean_raw} not found",
                "barcode_data": {"barcode": clean_raw},
            }
        )

    part = None

    try:
        from part.models import Part
        from company.models import ManufacturerPart, SupplierPart

        # 1. Match MPN against ManufacturerPart.MPN
        if mpn:
            mfg = (
                ManufacturerPart.objects.filter(MPN__iexact=mpn)
                .select_related("part")
                .first()
            )
            if mfg and mfg.part:
                part = mfg.part

        # 2. Match MPN against Part.IPN or Part.name
        if not part and mpn:
            part = (
                Part.objects.filter(IPN__iexact=mpn).first()
                or Part.objects.filter(name__iexact=mpn).first()
            )

        # 3. Match MPN against SupplierPart.SKU
        if not part and mpn:
            sup = (
                SupplierPart.objects.filter(SKU__iexact=mpn)
                .select_related("part")
                .first()
            )
            if sup and sup.part:
                part = sup.part

        # 4. Match supplier SKU against SupplierPart.SKU
        if not part and sku:
            sup = (
                SupplierPart.objects.filter(SKU__iexact=sku)
                .select_related("part")
                .first()
            )
            if sup and sup.part:
                part = sup.part

        # 5. Fallback: if mpn is purely digits, check if it's a Part PK directly
        if not part and mpn and mpn.isdigit():
            part = Part.objects.filter(pk=int(mpn)).first()

    except Exception as exc:
        logger.warning(f"SmartParts: barcode lookup exception: {exc}")

    if part:
        url = (
            part.get_absolute_url()
            if hasattr(part, "get_absolute_url")
            else f"/web/part/{part.pk}"
        )
        return JsonResponse(
            {
                "found": True,
                "part_id": part.pk,
                "part_name": part.name,
                "part_ipn": getattr(part, "IPN", "") or "",
                "part_url": url,
                "mpn": mpn or part.name,
                "distributor": parsed.distributor if parsed else None,
                "barcode_data": parsed.to_dict() if parsed else None,
            }
        )

    # Final safety guard: ensure raw JSON, braces, or internal prefixes NEVER leak as mpn
    if mpn and (
        "{" in mpn or "}" in mpn or mpn.startswith("INV-") or mpn.startswith("IN:")
    ):
        mpn = ""

    return JsonResponse(
        {
            "found": False,
            "part_id": None,
            "part_name": "",
            "part_ipn": "",
            "part_url": None,
            "mpn": mpn,
            "distributor": parsed.distributor if parsed else None,
            "barcode_data": parsed.to_dict() if parsed else None,
        }
    )


api_barcode_lookup.auth_exempt = True
