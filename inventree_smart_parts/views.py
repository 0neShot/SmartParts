"""
Views
=====
Django views for the Smart Parts plugin UI.
All views are served under /plugin/smartparts/.
"""

import json
import logging

from django.http import JsonResponse, HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt

logger = logging.getLogger('inventree_smart_parts.views')

# ── Persistent Activity Logger ──────────────────────────────────────────────
from .services.activity_logger import log_activity, get_logs, get_recent

# Backward-compat shim for any code that still calls _log_activity
def _log_activity(level: str, message: str, details: str = ''):
    log_activity(level, message, details)


def _get_plugin():
    """Get the SmartPartsPlugin instance."""
    from plugin.registry import registry
    return registry.get_plugin('smartparts')


def _check_perm(request, perm: str):
    """Return a 403 JsonResponse if the user lacks the given permission, else None.

    Usage::

        denied = _check_perm(request, 'part.add_part')
        if denied:
            return denied
    """
    user = getattr(request, 'user', None)
    if user is None or not user.is_authenticated:
        return JsonResponse(
            {'error': 'Authentication required'},
            status=401,
        )
    if not user.has_perm(perm):
        logger.warning(
            f'Permission denied: user="{user.username}" lacks "{perm}"'
        )
        return JsonResponse(
            {'error': f'Permission denied – requires "{perm}"'},
            status=403,
        )
    return None


# ═══════════════════════════════════════════════════════════════════
#  Dashboard
# ═══════════════════════════════════════════════════════════════════

def dashboard(request):
    """Main dashboard with quick search and recent activity."""
    plugin = _get_plugin()
    context = {
        'plugin': plugin,
        'recent_logs': get_recent(10),
        'mouser_enabled': plugin.get_setting('MOUSER_ENABLED') if plugin else False,
        'digikey_enabled': plugin.get_setting('DIGIKEY_ENABLED') if plugin else False,
        'lcsc_enabled': plugin.get_setting('LCSC_ENABLED') if plugin else False,
    }
    return render(request, 'inventree_smart_parts/dashboard.html', context)


# ═══════════════════════════════════════════════════════════════════
#  PureScan – Zero-Click Warehouse Terminal
# ═══════════════════════════════════════════════════════════════════

def purescan(request):
    """PureScan full-screen barcode terminal."""
    return render(request, 'inventree_smart_parts/purescan.html', {})


# All control/quantity codes for the Command Sheet
_COMMAND_SHEET_CODES = [
    {'code': 'SYS:TRANSFER',  'label': 'Transfer Stock',  'icon': '🔄', 'color': '#f59e0b', 'group': 'action'},
    {'code': 'SYS:INFO',      'label': 'Info / Lookup',   'icon': 'ℹ️',  'color': '#3b82f6', 'group': 'action'},
    {'code': 'SYS:ADD',       'label': 'Add Stock',       'icon': '➕', 'color': '#10b981', 'group': 'action'},
    {'code': 'SYS:REMOVE',    'label': 'Remove Stock',    'icon': '➖', 'color': '#ef4444', 'group': 'action'},
    {'code': 'SYS:STOCKTAKE', 'label': 'Stocktake',       'icon': '📋', 'color': '#8b5cf6', 'group': 'action'},
    {'code': 'SYS:CANCEL',    'label': 'Cancel / Reset',  'icon': '❌', 'color': '#64748b', 'group': 'action'},
    {'code': 'SYS:UNDO',      'label': 'Undo Last',       'icon': '↩️',  'color': '#f59e0b', 'group': 'action'},
    {'code': 'SYS:QTY:1',     'label': 'Qty: 1',          'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:5',     'label': 'Qty: 5',          'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:10',    'label': 'Qty: 10',         'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:25',    'label': 'Qty: 25',         'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:50',    'label': 'Qty: 50',         'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:100',   'label': 'Qty: 100',        'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:250',   'label': 'Qty: 250',        'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:500',   'label': 'Qty: 500',        'icon': '#',  'color': '#6b7280', 'group': 'qty'},
    {'code': 'SYS:QTY:1000',  'label': 'Qty: 1000',       'icon': '#',  'color': '#6b7280', 'group': 'qty'},
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
        img = qr.make_image(fill_color='black', back_color='white')
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        b64 = base64.b64encode(buf.getvalue()).decode('ascii')
        return f'data:image/png;base64,{b64}'
    except Exception as e:
        logger.warning(f'QR generation failed for "{text}": {e}')
        return ''


def purescan_command_sheet(request):
    """Render the PureScan Command Sheet with server-generated QR codes.
    Separates action and quantity codes into distinct page groups,
    and generates a deep-link QR for the PureScan terminal URL.
    """
    action_commands = []
    qty_commands = []

    for cmd in _COMMAND_SHEET_CODES:
        enriched = {**cmd, 'qr_data_uri': _generate_qr_data_uri(cmd['code'])}
        if cmd['group'] == 'action':
            action_commands.append(enriched)
        else:
            qty_commands.append(enriched)

    # Deep-link QR: absolute URL to the PureScan terminal
    purescan_url = request.build_absolute_uri('/plugin/smartparts/purescan/')
    deeplink_qr = _generate_qr_data_uri(purescan_url)

    return render(request, 'inventree_smart_parts/purescan_commands.html', {
        'action_commands': action_commands,
        'qty_commands': qty_commands,
        'deeplink_url': purescan_url,
        'deeplink_qr': deeplink_qr,
    })


# ═══════════════════════════════════════════════════════════════════
#  MPN Search
# ═══════════════════════════════════════════════════════════════════

def search(request):
    """Search results page (renders after form submit)."""
    return render(request, 'inventree_smart_parts/search_results.html', {})


@csrf_exempt
def api_search(request):
    """AJAX endpoint: search for a part by MPN across all enabled APIs."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    mpn = body.get('mpn', '').strip()
    if not mpn:
        return JsonResponse({'error': 'MPN is required'}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    _log_activity('INFO', f'Searching for MPN: {mpn}')

    # Run API searches
    from .api_clients import MouserClient, DigiKeyClient, LCSCClient
    from .services.data_merger import merge_part_data
    from .services.duplicate_checker import check_duplicate
    from .services.category_mapper import fuzzy_match_category, get_all_categories_for_ui

    results = {}
    api_results = []

    # Mouser
    if plugin.get_setting('MOUSER_ENABLED'):
        try:
            client = MouserClient(api_key=plugin.get_setting('MOUSER_API_KEY'))
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results['mouser'] = _part_data_to_dict(r)
            else:
                results['mouser'] = None
        except Exception as e:
            results['mouser'] = {'error': str(e)}
            logger.warning(f"Mouser search error: {e}")

    # DigiKey
    if plugin.get_setting('DIGIKEY_ENABLED'):
        try:
            client = DigiKeyClient(
                client_id=plugin.get_setting('DIGIKEY_CLIENT_ID'),
                client_secret=plugin.get_setting('DIGIKEY_CLIENT_SECRET'),
            )
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results['digikey'] = _part_data_to_dict(r)
            else:
                results['digikey'] = None
        except Exception as e:
            results['digikey'] = {'error': str(e)}
            logger.warning(f"DigiKey search error: {e}")

    # LCSC
    if plugin.get_setting('LCSC_ENABLED'):
        try:
            client = LCSCClient()
            r = client.search_by_mpn(mpn)
            if r:
                api_results.append(r)
                results['lcsc'] = _part_data_to_dict(r)
            else:
                results['lcsc'] = None
        except Exception as e:
            results['lcsc'] = {'error': str(e)}
            logger.warning(f"LCSC search error: {e}")

    # Merge
    priority_str = plugin.get_setting('API_PRIORITY')
    priority_order = [p.strip() for p in priority_str.split(',')]
    merged = merge_part_data(api_results, priority_order)

    # Category mapping
    category_match = None
    if merged and merged.category:
        threshold = plugin.get_setting('FUZZY_THRESHOLD')
        default_cat = plugin.get_setting('DEFAULT_CATEGORY')
        user_synonyms = plugin.get_setting('CATEGORY_SYNONYMS') or '{}'
        learned_mappings = plugin.get_setting('LEARNED_CATEGORY_MAPPINGS') or '{}'
        cat_id, cat_path, score = fuzzy_match_category(
            merged.category, threshold, default_cat,
            user_synonyms_json=user_synonyms,
            learned_mappings_json=learned_mappings,
        )
        category_match = {
            'id': cat_id,
            'path': cat_path,
            'score': score,
            # Pass through so the editor can send it back on save for learning
            'distributor_category': merged.category,
        }

    # Duplicate check
    dup_info = None
    if merged:
        dup = check_duplicate(merged.mpn, merged.manufacturer)
        if dup.is_duplicate:
            dup_info = {
                'is_duplicate': True,
                'part_id': dup.existing_part_id,
                'part_name': dup.existing_part_name,
                'existing_mpn': dup.existing_mpn,
                'match_type': dup.match_type,
                'confidence': dup.confidence,
            }

    # All categories for manual selection
    all_categories = get_all_categories_for_ui()

    # Normalize parameters (data cleaning)
    from .services.parameter_normalizer import normalize_parameter_list

    merged_dict = _part_data_to_dict(merged) if merged else None
    if merged_dict and merged_dict.get('parameters'):
        merged_dict['parameters'] = normalize_parameter_list(merged_dict['parameters'])

    # Also normalize per-source parameters
    for src_key, src_data in results.items():
        if isinstance(src_data, dict) and 'parameters' in src_data and not src_data.get('error'):
            src_data['parameters'] = normalize_parameter_list(src_data['parameters'])

    response = {
        'mpn': mpn,
        'sources': results,
        'merged': merged_dict,
        'category_match': category_match,
        'duplicate': dup_info,
        'categories': all_categories,
    }

    _log_activity(
        'INFO',
        f'Search complete for {mpn}',
        f'{len(api_results)} source(s) returned data'
    )

    return JsonResponse(response)


# ═══════════════════════════════════════════════════════════════════
#  Part Creation
# ═══════════════════════════════════════════════════════════════════

@csrf_exempt
def create_part(request):
    """Create a part from confirmed search results."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    denied = _check_perm(request, 'part.add_part')
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    from .api_clients.base import PartData, PriceBreak, PartParameter
    from .services.part_creator import create_part_from_data

    # Reconstruct PartData from the request
    part_data = PartData(
        mpn=body.get('mpn', ''),
        manufacturer=body.get('manufacturer', ''),
        description=body.get('description', ''),
        name=body.get('name', ''),
        category=body.get('category_name', ''),
        datasheet_url=body.get('datasheet_url', ''),
        image_url=body.get('image_url', ''),
        package=body.get('package', ''),
        parameters=[
            PartParameter(name=p.get('name', ''), value=p.get('value', ''), unit=p.get('unit', ''))
            for p in body.get('parameters', [])
            if p.get('name') and p.get('value')
        ],
    )

    # Reconstruct supplier data into raw_data
    supplier_data = body.get('supplier_data', [])
    source_image_urls = body.get('source_image_urls', [])
    if supplier_data or source_image_urls:
        part_data.raw_data = {
            'supplier_data': supplier_data,
            'source_image_urls': source_image_urls,
        }

    # If no supplier_data but we have direct supplier info
    if not supplier_data and body.get('supplier_name'):
        part_data.supplier_name = body.get('supplier_name', '')
        part_data.supplier_sku = body.get('supplier_sku', '')
        part_data.supplier_url = body.get('supplier_url', '')

    category_id = body.get('category_id')
    update_existing = body.get('update_existing', False)
    existing_part_id = body.get('existing_part_id')

    # Fields needed for the learning hook (see below)
    distributor_category  = body.get('distributor_category', '')    # raw API category string
    suggested_cat_id      = body.get('suggested_category_id')       # what the fuzzy matcher suggested

    result = create_part_from_data(
        part_data=part_data,
        category_id=category_id,
        update_existing=update_existing,
        existing_part_id=existing_part_id,
        auto_create_companies=plugin.get_setting('AUTO_CREATE_MANUFACTURERS'),
    )

    _log_activity(
        'INFO' if result.success else 'ERROR',
        f'Part {"created" if result.action == "created" else "updated"}: {result.part_name}' if result.success
        else f'Part creation failed for {part_data.mpn}',
        result.message
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
                chosen_path = chosen_cat.pathstring if hasattr(chosen_cat, 'pathstring') else str(chosen_cat)
                # Learn if: no previous suggestion, or user picked a different category
                should_learn = (
                    not suggested_cat_id                    # matcher had no confident answer
                    or int(suggested_cat_id) != int(category_id)  # user corrected it
                )
                if should_learn:
                    learn_category_mapping(distributor_category, chosen_path, plugin)
        except Exception as e:
            logger.warning(f'Category learning hook failed (non-fatal): {e}')

    if not result.success:
        return JsonResponse({
            'success': False,
            'part_id': result.part_id,
            'part_name': result.part_name,
            'action': result.action,
            'message': result.message,
            'errors': result.errors,
        })

    # ── Step B: Receive Stock (optional) ──
    stock_result = None
    stock_qty = float(body.get('stock_quantity', 0) or 0)
    if stock_qty > 0:
        from .services.stock_manager import create_stock_item
        stock_result = create_stock_item(
            part_id=result.part_id,
            quantity=stock_qty,
            location_id=body.get('stock_location_id') or None,
            batch=body.get('stock_batch', ''),
            delete_on_deplete=bool(body.get('stock_delete_on_deplete', True)),
            user=request.user,
        )
        _log_activity(
            'INFO' if stock_result['success'] else 'WARNING',
            f'Stock receive: {stock_qty} x {result.part_name}',
            stock_result['message'],
        )

    # ── Step C: Print Label (optional) ──
    label_result = None
    if stock_result and stock_result['success'] and body.get('print_label') and body.get('label_template_id'):
        from .services.stock_manager import print_stock_label
        label_result = print_stock_label(
            stock_item_id=stock_result['stock_id'],
            template_id=int(body['label_template_id']),
            plugin_slug=plugin.get_setting('DEFAULT_PRINT_PLUGIN') or '',
            request=request,
        )
        _log_activity(
            'INFO' if label_result['success'] else 'WARNING',
            f'Label print for StockItem {stock_result["stock_id"]}',
            label_result['message'],
        )

    return JsonResponse({
        'success': result.success,
        'part_id': result.part_id,
        'part_name': result.part_name,
        'action': result.action,
        'message': result.message,
        'errors': result.errors,
        'stock': stock_result,
        'label': label_result,
    })


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
        for sp in SupplierPart.objects.filter(part=part).select_related('supplier'):
            pbs = []
            for pb in SupplierPriceBreak.objects.filter(part=sp):
                pbs.append({
                    'quantity': int(pb.quantity),
                    'price': str(pb.price),
                    'currency': getattr(pb, 'price_currency', 'EUR'),
                })
            supplier_parts.append({
                'supplier_name': sp.supplier.name if sp.supplier else '',
                'supplier_sku': sp.SKU or '',
                'supplier_url': sp.link or '',
                'price_breaks': pbs,
            })

        parameters = []
        for p in Parameter.objects.filter(model_type=part_type, model_id=part.pk).select_related('template'):
            parameters.append({
                'name': p.template.name,
                'value': p.data or '',
                'unit': getattr(p.template, 'units', '') or '',
            })

        image_url = ''
        if part.image:
            try:
                image_url = part.image.url
            except Exception:
                pass

        return JsonResponse({
            'id': part.pk,
            'name': part.name or '',
            'description': part.description or '',
            'link': part.link or '',
            'category_id': part.category_id,
            'category_name': str(part.category) if part.category else '',
            'mpn': mfr_part.MPN if mfr_part else '',
            'manufacturer': (mfr_part.manufacturer.name if mfr_part and mfr_part.manufacturer else ''),
            'package': '',  # Not a standard Part field; lives in parameters
            'image_url': image_url,
            'supplier_parts': supplier_parts,
            'parameters': parameters,
        })

    except Part.DoesNotExist:
        return JsonResponse({'error': f'Part {part_id} not found'}, status=404)
    except Exception as e:
        logger.error(f'api_get_part failed for {part_id}: {e}', exc_info=True)
        return JsonResponse({'error': str(e)}, status=500)


# ═══════════════════════════════════════════════════════════════════
#  Batch Import
# ═══════════════════════════════════════════════════════════════════

def batch_import(request):
    """Batch import page."""
    return render(request, 'inventree_smart_parts/batch_import.html', {})


@csrf_exempt
def batch_upload(request):
    """Handle batch file upload and start processing."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    denied = _check_perm(request, 'part.add_part')
    if denied:
        return denied

    uploaded_file = request.FILES.get('file')
    if not uploaded_file:
        return JsonResponse({'error': 'No file uploaded'}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    try:
        import json as _json
        from .batch.importer import parse_upload_file, process_batch

        # ── Priority 1: use pre-reviewed rows from the frontend grid ──────────
        reviewed_rows_raw = request.POST.get('reviewed_rows', '')
        if reviewed_rows_raw:
            try:
                rows = _json.loads(reviewed_rows_raw)
                if not isinstance(rows, list):
                    raise ValueError('reviewed_rows must be a JSON array')
                # Normalise field names to what the importer expects
                rows = [
                    {
                        'mpn':          str(r.get('mpn', '')).strip(),
                        'quantity':     str(r.get('quantity', '1')).strip() or '1',
                        'manufacturer': str(r.get('manufacturer', '')).strip(),
                        'description':  str(r.get('description', '')).strip(),
                        'name':         str(r.get('name', '')).strip(),
                        'designator':   str(r.get('designator', '')).strip(),
                    }
                    for r in rows
                    if str(r.get('mpn', '')).strip()  # skip rows without MPN
                ]
                assembly_metadata = {'name': '', 'description': '', 'revision': ''}
            except (_json.JSONDecodeError, ValueError) as je:
                return JsonResponse({'error': f'Invalid reviewed_rows: {je}'}, status=400)
        else:
            # ── Fallback: parse the uploaded file (original behaviour) ────────
            rows, assembly_metadata = parse_upload_file(uploaded_file)

        if not rows:
            return JsonResponse({'error': 'No valid rows found in file'}, status=400)

        # Assembly BOM options (optional, from the UI checkbox + inputs)
        assembly_name        = request.POST.get('assembly_name', '').strip()
        assembly_description = request.POST.get('assembly_description', '').strip()
        assembly_revision    = request.POST.get('assembly_revision', '').strip()
        assembly_category_id = request.POST.get('assembly_category_id', '').strip()
        create_assembly      = request.POST.get('create_assembly', '') in ('true', '1', 'on')
        if not create_assembly:
            assembly_name = ''   # Ignore name if checkbox not ticked

        # Convert category_id to int (or None)
        try:
            assembly_category_id = int(assembly_category_id) if assembly_category_id else None
        except (ValueError, TypeError):
            assembly_category_id = None

        _log_activity(
            'INFO',
            f'Batch upload: {len(rows)} rows from {uploaded_file.name}'
            + (f'; assembly="{assembly_name}"' if assembly_name else '')
            + (' [reviewed]' if reviewed_rows_raw else '')
        )

        job_id = process_batch(
            rows, plugin,
            assembly_name=assembly_name,
            assembly_description=assembly_description,
            assembly_revision=assembly_revision,
            assembly_category_id=assembly_category_id,
        )

        return JsonResponse({
            'success': True,
            'job_id': job_id,
            'total_rows': len(rows),
            'message': f'Processing {len(rows)} parts...',
            'assembly_metadata': assembly_metadata,
        })

    except ValueError as e:
        return JsonResponse({'error': str(e)}, status=400)
    except ImportError as e:
        return JsonResponse({'error': str(e)}, status=500)
    except Exception as e:
        logger.error(f"Batch upload failed: {e}", exc_info=True)
        return JsonResponse({'error': f'Upload failed: {str(e)}'}, status=500)


@csrf_exempt
def batch_status(request, job_id: str):
    """AJAX: Get batch job progress."""
    from .batch.importer import get_job

    job = get_job(job_id)
    if not job:
        return JsonResponse({'error': 'Job not found'}, status=404)

    return JsonResponse(job.to_dict())


@csrf_exempt
def batch_report(request, job_id: str):
    """Download batch job results as CSV."""
    from .batch.importer import get_job
    import csv

    job = get_job(job_id)
    if not job:
        return JsonResponse({'error': 'Job not found'}, status=404)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = f'attachment; filename="batch_report_{job_id}.csv"'

    writer = csv.writer(response)
    writer.writerow(['Row', 'MPN', 'Status', 'Action', 'Part Name', 'Part ID', 'Message', 'Error'])

    for r in job.results:
        writer.writerow([
            r.row_number, r.mpn, r.status, r.action,
            r.part_name, r.part_id or '', r.message, r.error,
        ])

    return response


# ═══════════════════════════════════════════════════════════════════
#  Settings
# ═══════════════════════════════════════════════════════════════════

def plugin_settings(request):
    """Plugin settings & API management page."""
    plugin = _get_plugin()
    context = {
        'plugin': plugin,
        'mouser_enabled':    plugin.get_setting('MOUSER_ENABLED')    if plugin else False,
        'mouser_has_key':    bool(plugin.get_setting('MOUSER_API_KEY')) if plugin else False,
        'digikey_enabled':   plugin.get_setting('DIGIKEY_ENABLED')   if plugin else False,
        'digikey_has_key':   bool(plugin.get_setting('DIGIKEY_CLIENT_ID')) if plugin else False,
        'lcsc_enabled':      plugin.get_setting('LCSC_ENABLED')      if plugin else False,
    }
    return render(request, 'inventree_smart_parts/settings_page.html', context)


@csrf_exempt
def test_connection(request, provider: str):
    """AJAX: Test API connection for a specific provider."""
    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    from .api_clients import MouserClient, DigiKeyClient, LCSCClient

    if provider == 'mouser':
        client = MouserClient(api_key=plugin.get_setting('MOUSER_API_KEY'))
    elif provider == 'digikey':
        client = DigiKeyClient(
            client_id=plugin.get_setting('DIGIKEY_CLIENT_ID'),
            client_secret=plugin.get_setting('DIGIKEY_CLIENT_SECRET'),
        )
    elif provider == 'lcsc':
        client = LCSCClient()
    else:
        return JsonResponse({'error': f'Unknown provider: {provider}'}, status=400)

    result = client.test_connection()

    _log_activity(
        'INFO' if result['success'] else 'WARNING',
        f'Connection test [{provider}]: {"OK" if result["success"] else "FAILED"}',
        result['message'],
    )

    return JsonResponse(result)


# ═══════════════════════════════════════════════════════════════════
#  Logs
# ═══════════════════════════════════════════════════════════════════

def logs_view(request):
    """Activity log viewer page."""
    return render(request, 'inventree_smart_parts/logs.html', {})


@csrf_exempt
def api_logs(request):
    """AJAX: Return activity logs as JSON."""
    level_filter = request.GET.get('level', '') or None
    limit = min(int(request.GET.get('limit', 200)), 1000)

    logs = get_logs(level_filter=level_filter, limit=limit)
    total = len(get_logs(limit=10000))  # Count without limit for the UI badge

    return JsonResponse({'logs': logs, 'total': total})


@csrf_exempt
def api_logs_clear(request):
    """AJAX: Clear all activity logs (POST only)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    denied = _check_perm(request, 'part.change_part')
    if denied:
        return denied
    from .services.activity_logger import clear_logs
    clear_logs()
    return JsonResponse({'success': True, 'message': 'All logs cleared.'})


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
        for entry in pd.raw_data.get('source_datasheet_urls', []):
            if entry.get('url', '').startswith('http'):
                datasheet_url = entry['url']
                break

    return {
        'mpn': pd.mpn,
        'manufacturer': pd.manufacturer,
        'description': pd.description,
        'name': pd.name,
        'category': pd.category,
        'subcategory': pd.subcategory,
        'supplier_name': pd.supplier_name,
        'supplier_sku': pd.supplier_sku,
        'supplier_url': pd.supplier_url,
        'datasheet_url': datasheet_url,
        'image_url': pd.image_url,
        'package': pd.package,
        'parameters': [
            {'name': p.name, 'value': p.value, 'unit': p.unit}
            for p in pd.parameters
        ],
        'price_breaks': [
            {'quantity': pb.quantity, 'price': pb.price, 'currency': pb.currency}
            for pb in pd.price_breaks
        ],
        'stock_available': pd.stock_available,
        'source': pd.source,
        'confidence': pd.confidence,
        # Pass through aggregated supplier list from the merger so the editor
        # receives one card per distributor and the backend creates all SupplierParts.
        'supplier_data': pd.raw_data.get('supplier_data', []),
        'source_image_urls': pd.raw_data.get('source_image_urls', []),
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
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    if request.method == 'GET':
        value = plugin.get_setting('CATEGORY_SYNONYMS') or '{}'
        return JsonResponse({'value': value})

    if request.method == 'POST':
        denied = _check_perm(request, 'part.change_partcategory')
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get('value', '{}')
            # Validate it's parseable JSON
            _json.loads(raw)
        except (_json.JSONDecodeError, TypeError, KeyError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            plugin.set_setting('CATEGORY_SYNONYMS', raw)
            return JsonResponse({'success': True, 'value': raw})
        except Exception as e:
            logger.error(f'Failed to save CATEGORY_SYNONYMS: {e}', exc_info=True)
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


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
        return JsonResponse({'error': 'Plugin not loaded'}, status=500)

    if request.method == 'GET':
        value = plugin.get_setting('LEARNED_CATEGORY_MAPPINGS') or '{}'
        return JsonResponse({'value': value})

    if request.method == 'POST':
        denied = _check_perm(request, 'part.change_partcategory')
        if denied:
            return denied
        try:
            body = _json.loads(request.body)
            raw = body.get('value', '{}')
            # Validate it's a JSON object
            parsed = _json.loads(raw)
            if not isinstance(parsed, dict):
                raise ValueError('Expected a JSON object')
        except (ValueError, _json.JSONDecodeError, TypeError) as e:
            return JsonResponse({'error': f'Invalid JSON: {e}'}, status=400)

        try:
            plugin.set_setting('LEARNED_CATEGORY_MAPPINGS', raw)
            return JsonResponse({'success': True, 'value': raw})
        except Exception as e:
            logger.error(f'Failed to save LEARNED_CATEGORY_MAPPINGS: {e}', exc_info=True)
            return JsonResponse({'error': str(e)}, status=500)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


# ═══════════════════════════════════════════════════════════════════
#  Stock & Label APIs
# ═══════════════════════════════════════════════════════════════════

def api_stock_locations(request):
    """Return the full StockLocation tree as a flat JSON list."""
    from .services.stock_manager import get_stock_locations
    locations = get_stock_locations()
    return JsonResponse({'locations': locations})


def api_label_templates(request):
    """Return available stock item label templates + the configured default."""
    from .services.stock_manager import get_label_templates
    plugin = _get_plugin()
    templates = get_label_templates()
    default_id = int(plugin.get_setting('DEFAULT_STOCK_LABEL') or 0) if plugin else 0
    return JsonResponse({'templates': templates, 'default_id': default_id})


@csrf_exempt
def api_create_stock(request):
    """
    POST: Create a StockItem for an existing Part.
    Body: { part_id, quantity, location_id?, batch?, delete_on_deplete? }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    denied = _check_perm(request, 'stock.add_stockitem')
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    part_id = body.get('part_id')
    quantity = float(body.get('quantity', 0) or 0)
    if not part_id or quantity <= 0:
        return JsonResponse({'error': 'part_id and quantity > 0 are required'}, status=400)

    from .services.stock_manager import create_stock_item
    result = create_stock_item(
        part_id=int(part_id),
        quantity=quantity,
        location_id=body.get('location_id') or None,
        batch=body.get('batch', ''),
        delete_on_deplete=bool(body.get('delete_on_deplete', True)),
        user=request.user,
    )
    status_code = 200 if result['success'] else 500
    return JsonResponse(result, status=status_code)


@csrf_exempt
def api_print_label(request):
    """
    POST: Print a label for an existing StockItem.
    Body: { stock_item_id, template_id, plugin_slug? }
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    denied = _check_perm(request, 'stock.view_stockitem')
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    stock_item_id = body.get('stock_item_id')
    template_id = body.get('template_id')
    if not stock_item_id or not template_id:
        return JsonResponse({'error': 'stock_item_id and template_id are required'}, status=400)

    plugin = _get_plugin()
    plugin_slug = body.get('plugin_slug') or (
        plugin.get_setting('DEFAULT_PRINT_PLUGIN') if plugin else ''
    ) or ''

    from .services.stock_manager import print_stock_label
    result = print_stock_label(
        stock_item_id=int(stock_item_id),
        template_id=int(template_id),
        plugin_slug=plugin_slug,
        request=request,
    )
    status_code = 200 if result['success'] else 500
    return JsonResponse(result, status=status_code)


# ═══════════════════════════════════════════════════════════════════
#  PureScan API endpoints
# ═══════════════════════════════════════════════════════════════════

def purescan_resolve_barcode(request):
    """GET: Resolve an InvenTree barcode string to its object type and PK.
    Query param: ?barcode=...
    Returns: { type: 'stockitem'|'stocklocation'|'part', id: int, name: str }
    """
    raw = request.GET.get('barcode', '').strip()
    if not raw:
        return JsonResponse({'error': 'barcode parameter required'}, status=400)

    # Try JSON parse
    import json as _json
    result = {'type': None, 'id': None, 'name': ''}

    if raw.startswith('{'):
        try:
            obj = _json.loads(raw)
            for key in ['stockitem', 'stocklocation', 'part']:
                if key in obj:
                    result['type'] = key
                    result['id'] = int(obj[key])
                    break
        except (ValueError, KeyError):
            pass

    if not result['type']:
        # Try key=value format
        import re
        m = re.match(r'^(stockitem|stocklocation|part)[=:](\d+)$', raw, re.I)
        if m:
            result['type'] = m.group(1).lower()
            result['id'] = int(m.group(2))

    if not result['type']:
        return JsonResponse({'error': 'Unrecognised barcode format', 'raw': raw}, status=400)

    # Fetch the name for display
    try:
        if result['type'] == 'stockitem':
            from stock.models import StockItem
            si = StockItem.objects.select_related('part', 'location').get(pk=result['id'])
            result['name'] = str(si.part)
            result['quantity'] = float(si.quantity)
            result['location'] = str(si.location) if si.location else 'No location'
        elif result['type'] == 'stocklocation':
            from stock.models import StockLocation
            loc = StockLocation.objects.get(pk=result['id'])
            result['name'] = loc.pathstring or str(loc)
        elif result['type'] == 'part':
            from part.models import Part
            result['name'] = str(Part.objects.get(pk=result['id']))
    except Exception as e:
        result['name'] = f'#{result["id"]} (lookup failed)'
        logger.warning(f'PureScan barcode lookup failed: {e}')

    return JsonResponse(result)


def purescan_print_label(request):
    """POST: Auto-print a label for a stock item using the plugin's default settings.
    Body: { "stock_item_id": int }
    Uses DEFAULT_STOCK_LABEL and DEFAULT_PRINT_PLUGIN from plugin settings.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    denied = _check_perm(request, 'stock.view_stockitem')
    if denied:
        return denied

    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    stock_item_id = body.get('stock_item_id')
    if not stock_item_id:
        return JsonResponse({'error': 'stock_item_id is required'}, status=400)

    plugin = _get_plugin()
    if not plugin:
        return JsonResponse({'error': 'Smart Parts plugin not found'}, status=500)

    template_id = plugin.get_setting('DEFAULT_STOCK_LABEL')
    if not template_id:
        return JsonResponse({
            'success': False,
            'error': 'No default label template configured. Set DEFAULT_STOCK_LABEL in plugin settings.',
        }, status=400)

    plugin_slug = plugin.get_setting('DEFAULT_PRINT_PLUGIN') or ''

    from .services.stock_manager import print_stock_label
    result = print_stock_label(
        stock_item_id=int(stock_item_id),
        template_id=int(template_id),
        plugin_slug=plugin_slug,
        request=request,
    )
    status_code = 200 if result['success'] else 500
    return JsonResponse(result, status=status_code)
