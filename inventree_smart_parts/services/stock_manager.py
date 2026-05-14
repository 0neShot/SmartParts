"""
Stock Manager
=============
Creates StockItems and triggers label printing for newly received parts.
All InvenTree model imports are deferred so the module can be imported
at plugin load time without a fully-initialised Django environment.
"""

import logging
from typing import Optional

logger = logging.getLogger('inventree_smart_parts.services.stock')


# ─────────────────────────────────────────────────────────────────────────────
#  StockItem creation
# ─────────────────────────────────────────────────────────────────────────────

def create_stock_item(
    part_id: int,
    quantity: float,
    location_id: Optional[int] = None,
    batch: str = '',
    delete_on_deplete: bool = True,
    user=None,
) -> dict:
    """
    Create a new StockItem for the given Part.

    Args:
        user: The Django User object to record in the stock tracking history.
              Pass request.user from the view so the history page shows who
              booked the stock rather than "No user".

    Returns a dict with keys:
        success (bool), stock_id (int|None), message (str)
    """
    try:
        from stock.models import StockItem
        from stock.status_codes import StockHistoryCode
        from part.models import Part

        part = Part.objects.get(pk=part_id)

        kwargs = {
            'part': part,
            'quantity': quantity,
            'delete_on_deplete': delete_on_deplete,
        }

        if location_id:
            from stock.models import StockLocation
            try:
                kwargs['location'] = StockLocation.objects.get(pk=location_id)
            except StockLocation.DoesNotExist:
                logger.warning(f'StockLocation {location_id} not found – creating without location')

        if batch:
            kwargs['batch'] = batch[:100]

        stock_item = StockItem.objects.create(**kwargs)

        # InvenTree's post_save signal fires inside .create() and automatically
        # inserts a CREATED tracking entry with user=None.  We fix that entry
        # in-place instead of appending a second one (which caused the duplicate).
        try:
            from stock.models import StockItemTracking
            auto_entry = (
                StockItemTracking.objects
                .filter(item=stock_item, tracking_type=StockHistoryCode.CREATED)
                .order_by('pk')
                .last()
            )
            if auto_entry is not None:
                auto_entry.user = user
                auto_entry.notes = 'Created via Smart Parts plugin'
                auto_entry.save(update_fields=['user', 'notes'])
            else:
                # No auto-entry exists (older InvenTree) – create one manually.
                deltas: dict = {'quantity': float(quantity), 'status': stock_item.status}
                if kwargs.get('location'):
                    deltas['location'] = kwargs['location'].pk
                stock_item.add_tracking_entry(
                    StockHistoryCode.CREATED,
                    user,
                    deltas=deltas,
                    notes='Created via Smart Parts plugin',
                )
        except Exception as te:
            # Non-fatal – the stock item is created; just log the tracking failure
            logger.warning(f'Could not update tracking entry for StockItem {stock_item.pk}: {te}')

        logger.info(f'Created StockItem {stock_item.pk} for Part {part_id} (qty={quantity}, user={getattr(user, "username", "anon")})')
        return {'success': True, 'stock_id': stock_item.pk, 'message': f'Stock item created (ID {stock_item.pk})'}

    except Exception as e:
        logger.error(f'Failed to create StockItem for Part {part_id}: {e}', exc_info=True)
        return {'success': False, 'stock_id': None, 'message': str(e)}


# ─────────────────────────────────────────────────────────────────────────────
#  Label template discovery
# ─────────────────────────────────────────────────────────────────────────────

def get_label_templates() -> list:
    """
    Return available StockItem label templates as a list of dicts:
        [{'id': pk, 'name': name}, ...]

    InvenTree moved labels from label.models into report.models.LabelTemplate
    (filtered by model_type='stockitem').  We try the paths in order:
      1. report.models.LabelTemplate  (current InvenTree ≥ 0.15)
      2. label.models.LabelTemplate   (InvenTree 0.13-0.14)
      3. label.models.StockItemLabel  (InvenTree < 0.13 legacy)
    """
    # ── Path 1: report.models.LabelTemplate (current) ────────────
    try:
        from report.models import LabelTemplate
        # Filter to StockItem labels only
        qs = LabelTemplate.objects.filter(enabled=True, model_type='stockitem').order_by('name')
        results = [{'id': t.pk, 'name': t.name} for t in qs]
        if results:
            return results
        # If nothing for stockitem, fall back to ALL label templates
        qs_all = LabelTemplate.objects.filter(enabled=True).order_by('name')
        results = [{'id': t.pk, 'name': f"{t.name} [{t.model_type}]"} for t in qs_all]
        if results:
            logger.info('get_label_templates: no stockitem-specific templates found, returning all')
            return results
    except ImportError:
        logger.debug('report.models.LabelTemplate not available')
    except Exception as e:
        logger.warning(f'report.models.LabelTemplate query failed: {e}', exc_info=True)

    # ── Path 2: label.models.LabelTemplate (InvenTree 0.13-0.14) ─
    try:
        from label.models import LabelTemplate  # type: ignore[import]
        qs = LabelTemplate.objects.filter(enabled=True).order_by('name')
        results = [{'id': t.pk, 'name': t.name} for t in qs]
        if results:
            return results
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f'label.models.LabelTemplate query failed: {e}')

    # ── Path 3: StockItemLabel (InvenTree < 0.13 legacy) ─────────
    try:
        from label.models import StockItemLabel  # type: ignore[import]
        qs = StockItemLabel.objects.filter(enabled=True).order_by('name')
        return [{'id': t.pk, 'name': t.name} for t in qs]
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f'label.models.StockItemLabel query failed: {e}')

    logger.warning('get_label_templates: no label template model found in this InvenTree version')
    return []


# ─────────────────────────────────────────────────────────────────────────────
#  Stock location tree
# ─────────────────────────────────────────────────────────────────────────────

def get_stock_locations() -> list:
    """
    Return the full StockLocation tree as a flat list of dicts for a
    <select> dropdown, showing indented names.

    Uses InvenTree's MPTT tree ordering so the list is already depth-first.
    """
    try:
        from stock.models import StockLocation

        result = []
        # order_by('tree_id', 'lft') gives correct MPTT pre-order traversal
        try:
            qs = StockLocation.objects.all().order_by('tree_id', 'lft')
        except Exception:
            qs = StockLocation.objects.all().order_by('name')

        for loc in qs:
            # Calculate depth: count ancestors by checking level attribute (MPTT)
            depth = getattr(loc, 'level', 0)
            indent = '\u00a0\u00a0' * depth + ('\u2514\u2500 ' if depth else '')
            path = loc.pathstring if hasattr(loc, 'pathstring') else loc.name
            description = getattr(loc, 'description', '') or ''
            result.append({
                'id': loc.pk,
                'name': indent + loc.name,
                'path': path,
                'description': description,
            })

        return result

    except Exception as e:
        logger.error(f'get_stock_locations failed: {e}', exc_info=True)
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  Label printing
# ─────────────────────────────────────────────────────────────────────────────

def print_stock_label(
    stock_item_id: int,
    template_id: int,
    plugin_slug: str = '',
    request=None,
) -> dict:
    """
    Trigger InvenTree's built-in label print for a StockItem.

    Routing strategy:
      1. POST to InvenTree's native /api/label/print/ endpoint (handles all
         plugin types including machine-driver-based ones like Dymo).
      2. If the API approach fails, fall back to direct template.print().

    The ``plugin_slug`` should be the **key** of a LabelPrintingMixin plugin
    (e.g. ``inventreelabelmachine`` for machine-routed printers like Dymo,
    or ``inventreelabel`` for the built-in PDF generator).

    Returns: {'success': bool, 'message': str}
    """
    # ── Resolve the StockItem ─────────────────────────────────────────────
    try:
        from stock.models import StockItem
        stock_item = StockItem.objects.get(pk=stock_item_id)
    except Exception as e:
        return {'success': False, 'message': f'StockItem {stock_item_id} not found: {e}'}

    # ── Resolve the print plugin ──────────────────────────────────────────
    resolved_key = _resolve_print_plugin_key(plugin_slug)

    # ── Strategy 1: Native InvenTree API (preferred) ──────────────────────
    if request:
        try:
            api_result = _print_via_api(
                stock_item_id=stock_item_id,
                template_id=template_id,
                plugin_key=resolved_key,
                request=request,
            )
            if api_result['success']:
                return api_result
            logger.warning(f'API print failed: {api_result["message"]}, trying direct method')
        except Exception as e:
            logger.warning(f'API print exception: {e}, trying direct method')

    # ── Strategy 2: Direct template.print() ───────────────────────────────
    try:
        from report.models import LabelTemplate
        template = LabelTemplate.objects.get(pk=template_id)

        from plugin.registry import registry as plugin_registry
        printing_plugins = plugin_registry.with_mixin('labels', active=True)
        output_plugin = None

        if resolved_key:
            output_plugin = next(
                (p for p in printing_plugins if p.slug == resolved_key),
                None,
            )
        if not output_plugin and printing_plugins:
            output_plugin = printing_plugins[0]

        if not output_plugin:
            return {
                'success': False,
                'message': (
                    'No active label printing plugin found. '
                    'Enable a printing plugin (e.g. InvenTree Label Machine '
                    'for Dymo/Zebra printers) in InvenTree.'
                ),
            }

        output = template.print(
            items=[stock_item],
            plugin=output_plugin,
            request=request,
        )
        logger.info(
            f'Label printed (direct): StockItem {stock_item_id} via template '
            f'"{template.name}" using plugin "{output_plugin.slug}"'
        )
        return {'success': True, 'message': f'Label sent to "{output_plugin.name}" successfully'}

    except ImportError:
        logger.debug('report.models.LabelTemplate not available, trying legacy API')
    except Exception as e:
        logger.error(f'Label print failed (direct): {e}', exc_info=True)
        return {'success': False, 'message': f'Label print error: {e}'}

    # ── Strategy 3: Legacy fallback (InvenTree < 0.13) ────────────────────
    try:
        from label.models import StockItemLabel  # type: ignore[import]
        from label.views import StockItemLabelPrint  # type: ignore[import]

        template = StockItemLabel.objects.get(pk=template_id)

        from django.test import RequestFactory
        from django.contrib.auth.models import AnonymousUser
        factory = RequestFactory()
        fake_req = factory.get('/', {'items': str(stock_item_id)})
        fake_req.user = AnonymousUser()

        view = StockItemLabelPrint()
        view.request = fake_req
        view.print_labels(template, [stock_item])
        logger.info(f'Label printed (legacy API): StockItem {stock_item_id}')
        return {'success': True, 'message': 'Label sent to printer (legacy API)'}

    except Exception as e:
        logger.error(f'Label print failed (legacy API): {e}', exc_info=True)
        return {'success': False, 'message': f'Label print error (legacy): {e}'}


def _resolve_print_plugin_key(plugin_slug: str) -> str:
    """Resolve user-provided plugin slug to the correct InvenTree plugin key.

    If the user configured a machine-driver plugin (like inventree-dymo-plugin),
    we auto-route to 'inventreelabelmachine' which is the bridge plugin that
    forwards print jobs to machine drivers.
    """
    if not plugin_slug:
        return ''

    slug = plugin_slug.strip().lower()

    # Direct match to known label-printing plugin keys
    from plugin.registry import registry as plugin_registry
    label_plugins = plugin_registry.with_mixin('labels', active=True)
    label_keys = {p.slug for p in label_plugins}

    if slug in label_keys:
        return slug

    # Check if the slug matches a machine-driver plugin (e.g. inventree-dymo-plugin)
    # These need routing through 'inventreelabelmachine'
    all_plugins = plugin_registry.plugins
    for key, p in all_plugins.items():
        p_slug = getattr(p, 'slug', '')
        if p_slug == slug or key == slug:
            mro_names = [c.__name__ for c in type(p).__mro__]
            if 'MachineDriverMixin' in mro_names:
                logger.info(
                    f'Plugin "{slug}" is a machine driver — routing via inventreelabelmachine'
                )
                return 'inventreelabelmachine'

    # Partial match: try matching by substring
    for key in label_keys:
        if slug in key or key in slug:
            logger.info(f'Plugin slug "{slug}" fuzzy-matched to "{key}"')
            return key

    # Default: use first available label plugin
    if label_keys:
        default = next(iter(label_keys))
        logger.warning(f'Plugin slug "{slug}" not found, defaulting to "{default}"')
        return default

    return slug


def _print_via_api(
    stock_item_id: int,
    template_id: int,
    plugin_key: str,
    request,
) -> dict:
    """Call InvenTree's native POST /api/label/print/ endpoint internally.

    This ensures correct routing through all plugin types including
    machine-driver-based printers (Dymo, Zebra via machine registry).
    """
    import json as _json
    from django.test import RequestFactory

    payload = {
        'template': template_id,
        'items': [stock_item_id],
    }
    if plugin_key:
        payload['plugin'] = plugin_key

    factory = RequestFactory()
    internal_req = factory.post(
        '/api/label/print/',
        data=_json.dumps(payload),
        content_type='application/json',
    )
    # Copy auth from the original request
    internal_req.user = getattr(request, 'user', None)
    if hasattr(request, 'META'):
        for key in ('HTTP_COOKIE', 'HTTP_AUTHORIZATION', 'CSRF_COOKIE'):
            if key in request.META:
                internal_req.META[key] = request.META[key]

    try:
        from report.api import LabelPrint
        view = LabelPrint.as_view()
        response = view(internal_req)

        if response.status_code in (200, 201):
            logger.info(
                f'Label printed (API): StockItem {stock_item_id}, '
                f'template={template_id}, plugin={plugin_key}'
            )
            return {'success': True, 'message': f'Label sent via {plugin_key or "default"} printer'}
        else:
            body = getattr(response, 'data', {}) or {}
            err = body.get('detail', '') or str(body)
            return {'success': False, 'message': f'API returned {response.status_code}: {err}'}

    except Exception as e:
        logger.error(f'Internal API print call failed: {e}', exc_info=True)
        return {'success': False, 'message': f'API print error: {e}'}

