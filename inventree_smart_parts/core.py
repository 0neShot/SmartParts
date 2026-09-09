"""
Smart Parts Plugin – Core
=========================
Plugin class definition with all InvenTree mixins, settings, and URL routing.
"""

import logging

from django.urls import path

from plugin import InvenTreePlugin
from plugin.mixins import (
    BarcodeMixin,
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
)

logger = logging.getLogger("inventree_smart_parts")


class SmartPartsPlugin(UserInterfaceMixin, BarcodeMixin, SettingsMixin, UrlsMixin, InvenTreePlugin):
    """
    InvenTree Smart Parts – Intelligent Inventory Assistant.

    Automates part creation by looking up MPNs across multiple distributor APIs,
    merging data with configurable priority, mapping categories via fuzzy matching,
    and supporting resilient batch imports.
    """

    # ── Plugin Metadata ──────────────────────────────────────────────
    NAME = "SmartParts"
    SLUG = "smartparts"
    TITLE = "Smart Parts – Inventory Assistant"
    DESCRIPTION = (
        "Automates part creation from MPN lookup. "
        "Fetches data from Mouser, DigiKey, LCSC, element14/Farnell, and TME. "
        "Maps categories, detects duplicates, and supports batch Excel import."
    )
    VERSION = "1.2.0"
    AUTHOR = "StarkStrom Engineering"

    # ── Plugin Settings (Admin-configurable) ─────────────────────────
    SETTINGS = {
        # ── Mouser ──
        "MOUSER_API_KEY": {
            "name": "Mouser API Key",
            "description": "API key for Mouser Electronics Search API v2",
            "default": "",
        },
        "MOUSER_ENABLED": {
            "name": "Enable Mouser",
            "description": "Enable or disable Mouser as a data source",
            "default": True,
            "validator": bool,
        },
        # ── DigiKey ──
        "DIGIKEY_CLIENT_ID": {
            "name": "DigiKey Client ID",
            "description": "OAuth2 Client ID for DigiKey API v4",
            "default": "",
        },
        "DIGIKEY_CLIENT_SECRET": {
            "name": "DigiKey Client Secret",
            "description": "OAuth2 Client Secret for DigiKey API v4",
            "default": "",
        },
        "DIGIKEY_ENABLED": {
            "name": "Enable DigiKey",
            "description": "Enable or disable DigiKey as a data source",
            "default": True,
            "validator": bool,
        },
        # ── LCSC ──
        "LCSC_ENABLED": {
            "name": "Enable LCSC",
            "description": "Enable or disable LCSC as a data source (no API key required)",
            "default": True,
            "validator": bool,
        },
        # ── element14 / Farnell / Newark ──
        "ELEMENT14_API_KEY": {
            "name": "element14 API Key",
            "description": (
                "API key for the element14 Product Search REST API. "
                "Covers Farnell (EU), Newark (US), and element14 (Asia-Pacific). "
                "Register at https://partner.element14.com/"
            ),
            "default": "",
        },
        "ELEMENT14_STORE": {
            "name": "element14 Store / Storefront",
            "description": (
                "Regional storefront to query. Examples: "
                "uk.farnell.com  de.farnell.com  fr.farnell.com  "
                "www.newark.com  au.element14.com  sg.element14.com  in.element14.com"
            ),
            "default": "uk.farnell.com",
        },
        "ELEMENT14_ENABLED": {
            "name": "Enable element14 / Farnell",
            "description": "Enable or disable element14/Farnell as a data source",
            "default": False,
            "validator": bool,
        },
        # ── TME ──
        "TME_API_TOKEN": {
            "name": "TME API Token",
            "description": (
                "Public API token for the TME REST API (HMAC-SHA1 signed). "
                "Obtain from https://developers.tme.eu/"
            ),
            "default": "",
        },
        "TME_API_SECRET": {
            "name": "TME API Secret",
            "description": ("Secret API key used to sign TME requests"),
            "default": "",
        },
        "TME_COUNTRY": {
            "name": "TME Country Code",
            "description": (
                "ISO 3166-1 alpha-2 country code for TME pricing and stock. "
                "Examples: DE  PL  GB  US  FR  NL"
            ),
            "default": "DE",
        },
        "TME_CURRENCY": {
            "name": "TME Currency",
            "description": "Currency for TME price breaks (e.g. EUR, USD, GBP, PLN)",
            "default": "EUR",
        },
        "TME_ENABLED": {
            "name": "Enable TME",
            "description": "Enable or disable TME as a data source",
            "default": False,
            "validator": bool,
        },
        # ── Data Merging ──
        "API_PRIORITY": {
            "name": "API Priority Order",
            "description": (
                "Comma-separated priority order for data merging. "
                "First source wins for each field. "
                "Valid tokens: mouser, digikey, lcsc, element14, tme. "
                "Example: mouser,digikey,element14,tme,lcsc"
            ),
            "default": "mouser,digikey,element14,tme,lcsc",
        },
        # ── Category Mapping ──
        "FUZZY_THRESHOLD": {
            "name": "Fuzzy Match Threshold",
            "description": (
                "Minimum confidence score (0-100) for category auto-mapping. "
                "Lower = more permissive. Recommended: 40-55 with the intelligent engine."
            ),
            "default": 45,
            "validator": int,
        },
        "DEFAULT_CATEGORY": {
            "name": "Default Category",
            "description": (
                "Fallback InvenTree category name if no fuzzy match is found. "
                "Leave empty to require manual selection."
            ),
            "default": "Uncategorized",
        },
        "CATEGORY_SYNONYMS": {
            "name": "Category Synonym Map (JSON)",
            "description": (
                "Custom synonym pairs to improve category matching. "
                "JSON object: keys are distributor terms, values are your InvenTree equivalents. "
                'Example: {"MLCC": "Ceramic Capacitor", "MCU": "Microcontroller"}'
            ),
            "default": "{}",
        },
        "LEARNED_CATEGORY_MAPPINGS": {
            "name": "Learned Category Mappings (JSON)",
            "description": (
                "Auto-populated by the plugin when you manually correct a category during import. "
                "Each key is the exact distributor category string; each value is the InvenTree "
                "category path you chose. Acts as a 100%% confidence override – edit or delete "
                "entries here to fix incorrect learning. "
                'Example: {"Semiconductors > Voltage Regulators": "Power > LDO Regulators"}'
            ),
            "default": "{}",
        },
        "LEARNED_PARAMETER_MAPPINGS": {
            "name": "Learned Parameter Mappings (JSON)",
            "description": (
                "JSON dictionary mapping raw distributor parameter names to canonical names. "
                'Example: {"ic mounting": "Mounting Type", "capacitance - value": "Capacitance"}'
            ),
            "default": "{}",
        },
        "TRACKED_UNKNOWN_PARAMETERS": {
            "name": "Tracked Unknown Parameters (JSON)",
            "description": (
                "Automatically populated with unknown parameter names and their frequency counts. "
                "Map these in Learned Parameter Mappings to standardize them."
            ),
            "default": "{}",
        },
        # ── Duplicate Handling ──
        "DUPLICATE_ACTION": {
            "name": "Duplicate Part Action",
            "description": "What to do when a part with the same MPN already exists (ask, update, skip)",
            "default": "ask",
        },
        # ── General ──
        "AUTO_CREATE_MANUFACTURERS": {
            "name": "Auto-create Manufacturers",
            "description": "Automatically create manufacturer companies if they do not exist",
            "default": True,
            "validator": bool,
        },
        "AUTO_CREATE_SUPPLIERS": {
            "name": "Auto-create Suppliers",
            "description": "Automatically create supplier companies if they do not exist",
            "default": True,
            "validator": bool,
        },
        "LOG_RETENTION_DAYS": {
            "name": "Log Retention (days)",
            "description": "Number of days to keep plugin activity logs",
            "default": 30,
            "validator": int,
        },
        # ── Parameter Filtering ──
        "LIMIT_PARAMETERS_TO_CATEGORY": {
            "name": "Limit Parameters to Category Templates",
            "description": (
                "When enabled, only import parameters that have a matching "
                "ParameterTemplate defined in the part's InvenTree category (or "
                "any ancestor category). Dropped parameters are audited in "
                "dry-run and full_response API calls. "
                "Disable to import all normalized supplier parameters."
            ),
            "default": True,
            "validator": bool,
        },
        # ── Stock & Label ──
        "DEFAULT_STOCK_LABEL": {
            "name": "Default Stock Label Template ID",
            "description": (
                "InvenTree Label Template ID to pre-select when printing stock labels. "
                "Find the ID in InvenTree > Labels > Stock Item Labels."
            ),
            "default": 0,
            "validator": int,
        },
        "DEFAULT_PRINT_PLUGIN": {
            "name": "Default Label Printer Plugin Slug",
            "description": (
                'Slug of the label printing plugin (e.g. "inventreelabelmachine" '
                'for Dymo/Zebra machine-based printers, or "inventreelabel" for PDF). '
                'If you enter a machine-driver slug (e.g. "inventree-dymo-plugin"), '
                "it will be auto-routed to inventreelabelmachine. "
                "Leave empty to use the first available printing plugin."
            ),
            "default": "",
        },
    }

    # ── PUI Integration (Modern React UI) ──────────────────────────────
    STATIC_URL_BASE = "/static/plugins/smartparts/inventree_smart_parts/ui"

    def get_ui_panels(self, request, context, **kwargs):
        """Add Smart Parts panels to Part detail and Category pages."""
        panels = []

        # Panel on Part detail pages – quick MPN search & supplier data
        if context.get("target_model") in ("part", "partcategory"):
            panels.append(
                {
                    "key": "smartparts-panel",
                    "title": "Smart Parts",
                    "icon": "ti:cpu",
                    "source": f"{self.STATIC_URL_BASE}/smartparts_panel.js:renderSmartPartsPanel",
                }
            )

        return panels

    def get_ui_dashboard_items(self, request, context, **kwargs):
        """Add Smart Parts quick-access widget to the dashboard."""
        return [
            {
                "key": "smartparts-dashboard-widget",
                "title": "Smart Parts",
                "description": "Quick MPN search across Mouser, DigiKey, LCSC, Farnell & TME",
                "icon": "ti:cpu",
                "source": f"{self.STATIC_URL_BASE}/smartparts_dashboard.js:renderSmartPartsDashboard",
                "options": {
                    "width": 4,
                    "height": 3,
                },
            },
        ]

    def get_ui_navigation_items(self, request, context, **kwargs):
        """Return custom navigation items (empty: accessed via sidebar under 'Plugin provided')."""
        return []

    # ── BarcodeMixin Handler ─────────────────────────────────────────
    def scan(self, barcode_data):
        """
        Scan a barcode against SmartParts part database.
        Called by InvenTree's native /api/barcode/ endpoint.
        """
        try:
            from .services.barcode_decoder import parse_barcode
            from part.models import Part
            from company.models import ManufacturerPart, SupplierPart

            parsed = parse_barcode(str(barcode_data))
            mpn = (parsed.mpn or "").strip()
            sku = (parsed.supplier_sku or "").strip()

            part = None
            if mpn:
                mfg = ManufacturerPart.objects.filter(MPN__iexact=mpn).select_related("part").first()
                if mfg and mfg.part:
                    part = mfg.part
                if not part:
                    part = Part.objects.filter(IPN__iexact=mpn).first() or Part.objects.filter(name__iexact=mpn).first()
                if not part:
                    sup = SupplierPart.objects.filter(SKU__iexact=mpn).select_related("part").first()
                    if sup and sup.part:
                        part = sup.part
            if not part and sku:
                sup = SupplierPart.objects.filter(SKU__iexact=sku).select_related("part").first()
                if sup and sup.part:
                    part = sup.part

            if part and hasattr(part, "format_matched_response"):
                return {"part": part.format_matched_response()}
            elif part:
                return {
                    "part": {
                        "pk": part.pk,
                        "name": part.name,
                        "url": part.get_absolute_url() if hasattr(part, "get_absolute_url") else f"/web/part/{part.pk}",
                    }
                }
        except Exception as exc:
            logger.warning(f"SmartParts: scan error: {exc}")
        return None

    # ── URL Routing ──────────────────────────────────────────────────
    def setup_urls(self):
        """Define custom URL patterns for the plugin."""
        from . import views

        # ── Ensure /media/ is served even without reverse proxy (DEBUG=False) ──
        _ensure_media_url_served()

        # ── Ensure InvenTree global setting has browser UA for external image downloads ──
        _ensure_download_user_agent()

        # ── Ensure global barcode scanner script is injected into InvenTree web UI ──
        _ensure_global_scanner_script()

        return [
            # Dashboard / Home
            path("", views.dashboard, name="dashboard"),
            # Single MPN Search
            path("search/", views.search, name="search"),
            path("api/search/", views.api_search, name="api-search"),
            # Part Creation
            path("create/", views.create_part, name="create-part"),
            # Part data fetch (for Live-Editor comparison)
            path("api/part/<int:part_id>/", views.api_get_part, name="api-get-part"),
            # Batch Import
            path("batch/", views.batch_import, name="batch-import"),
            path("batch/upload/", views.batch_upload, name="batch-upload"),
            path("batch/status/<str:job_id>/", views.batch_status, name="batch-status"),
            path("batch/report/<str:job_id>/", views.batch_report, name="batch-report"),
            # Settings & Admin
            path("settings/", views.plugin_settings, name="settings"),
            path("parameters/", views.parameter_dashboard, name="parameter-dashboard"),
            path(
                "api/test-connection/<str:provider>/",
                views.test_connection,
                name="test-connection",
            ),
            # Logs
            path("logs/", views.logs_view, name="logs"),
            path("api/logs/", views.api_logs, name="api-logs"),
            path("api/logs/clear/", views.api_logs_clear, name="api-logs-clear"),
            # Plugin-level settings helpers
            path("api/settings/synonyms/", views.api_synonyms, name="api-synonyms"),
            path("api/settings/learned/", views.api_learned, name="api-learned"),
            path(
                "api/settings/parameters/",
                views.api_parameter_mappings,
                name="api-parameter-mappings",
            ),
            path(
                "api/settings/unknown-parameters/",
                views.api_unknown_parameters,
                name="api-unknown-parameters",
            ),
            path(
                "api/settings/canonical-parameters/",
                views.api_canonical_parameters,
                name="api-canonical-parameters",
            ),
            # Stock & Label APIs
            path(
                "api/stock/locations/",
                views.api_stock_locations,
                name="api-stock-locations",
            ),
            path(
                "api/label/templates/",
                views.api_label_templates,
                name="api-label-templates",
            ),
            path("api/stock/create/", views.api_create_stock, name="api-create-stock"),
            path("api/label/print/", views.api_print_label, name="api-print-label"),
            # PureScan – Zero-Click Warehouse Terminal
            path("purescan/", views.purescan, name="purescan"),
            path(
                "purescan/commands/",
                views.purescan_command_sheet,
                name="purescan-commands",
            ),
            path(
                "api/purescan/resolve/",
                views.purescan_resolve_barcode,
                name="purescan-resolve",
            ),
            path(
                "api/purescan/print/", views.purescan_print_label, name="purescan-print"
            ),
            # ── External REST API – v1 ──────────────────────────────
            # Authenticate via: Authorization: Token <inventree-api-token>
            # or active InvenTree session.
            path("api/v1/import/", views.api_v1_import, name="api-v1-import"),
            path("api/v1/raw-lookup/", views.api_v1_raw_lookup, name="api-v1-raw-lookup"),
            # ── Category Parameter Template API ─────────────────────────
            path(
                "api/category/parameters/",
                views.api_category_parameters,
                name="api-category-parameters",
            ),
            # ── Global Barcode Scanner Lookup API ───────────────────────
            path(
                "api/barcode/lookup/",
                views.api_barcode_lookup,
                name="api-barcode-lookup",
            ),
        ]


def _serve_media(request, path, document_root=None):
    """Serve media files directly with auth_exempt so browser image tags load without redirects."""
    from django.views.static import serve
    return serve(request, path, document_root=document_root)

_serve_media.auth_exempt = True


def _ensure_media_url_served():
    """
    Ensure InvenTree serves /media/ files even when running without a reverse proxy (DEBUG=False).
    InvenTree's default urls.py only adds static(MEDIA_URL) when settings.DEBUG=True.
    In standalone container setups without Nginx/Caddy, media requests otherwise fall through
    to the SPA catch-all and return index.html (causing broken image placeholders in the browser).
    """
    try:
        from django.conf import settings
        from django.urls import re_path, clear_url_caches
        import InvenTree.urls

        # Check if already in urlpatterns
        for p in InvenTree.urls.urlpatterns:
            if getattr(p, "pattern", None) and str(p.pattern).startswith(r"^media/"):
                return

        media_pattern = re_path(
            r"^media/(?P<path>.*)$",
            _serve_media,
            {"document_root": settings.MEDIA_ROOT},
        )
        InvenTree.urls.urlpatterns.insert(0, media_pattern)
        clear_url_caches()
        logger.info("SmartParts: Added auth-exempt media file serve route to root urlpatterns")
    except Exception as e:
        logger.warning(f"SmartParts: Could not ensure media route: {e}")


def _ensure_download_user_agent():
    """
    Ensure InvenTree's global setting INVENTREE_DOWNLOAD_FROM_URL_USER_AGENT is configured.
    When left blank, InvenTree uses Python-requests default User-Agent, which is blocked
    by distributors with bot protection (e.g. Mouser's Akamai firewall).
    """
    try:
        from common.models import InvenTreeSetting

        current_ua = InvenTreeSetting.get_setting(
            "INVENTREE_DOWNLOAD_FROM_URL_USER_AGENT", ""
        )
        if not current_ua:
            InvenTreeSetting.set_setting(
                "INVENTREE_DOWNLOAD_FROM_URL_USER_AGENT",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                None,
            )
            logger.info(
                "SmartParts: Configured INVENTREE_DOWNLOAD_FROM_URL_USER_AGENT for external image downloads"
            )
    except Exception as e:
        logger.warning(f"SmartParts: Could not set download user agent: {e}")


def _ensure_global_scanner_script():
    """
    Ensure InvenTree's web interface includes the SmartParts global barcode scanner script.
    Appends the script tag to InvenTree's favicon.html template (which is included in <head>
    by both web/templates/web/index.html and base.html) so scanner_global.js is loaded across
    all routes in the web application.
    """
    try:
        import os
        from django.conf import settings

        target_tag = '<script type="module" src="/static/plugins/smartparts/inventree_smart_parts/ui/scanner_global.js"></script>'

        candidate_paths = []
        for tpl_cfg in getattr(settings, "TEMPLATES", []):
            for d in tpl_cfg.get("DIRS", []):
                candidate_paths.append(os.path.join(d, "favicon.html"))
        candidate_paths.append("/home/inventree/src/backend/InvenTree/templates/favicon.html")

        for fpath in candidate_paths:
            if os.path.exists(fpath):
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
                if target_tag not in content:
                    with open(fpath, "a", encoding="utf-8") as f:
                        f.write(f"\n<!-- SmartParts Global Barcode Scanner -->\n{target_tag}\n")
                    logger.info(f"SmartParts: Injected global scanner script into {fpath}")
                break
    except Exception as e:
        logger.warning(f"SmartParts: Could not ensure global scanner script: {e}")


def _ensure_plugin_static_served():
    """
    Ensure InvenTree serves plugin static files under /static/plugins/smartparts/
    even in standalone container setups (DEBUG=False) where WhiteNoise or Nginx
    might not serve unversioned plugin assets.
    """
    try:
        import os
        from django.conf import settings
        from django.urls import re_path, clear_url_caches
        from django.views.static import serve
        import InvenTree.urls

        for p in InvenTree.urls.urlpatterns:
            if getattr(p, "pattern", None) and str(p.pattern).startswith(r"^static/plugins/smartparts/"):
                return

        static_plugin_dir = os.path.join(getattr(settings, "STATIC_ROOT", ""), "plugins", "smartparts")
        if not os.path.exists(static_plugin_dir):
            static_plugin_dir = os.path.join(os.path.dirname(__file__), "static")

        def _serve_plugin_static(request, path, document_root=None):
            resp = serve(request, path, document_root=document_root)
            resp["Access-Control-Allow-Origin"] = "*"
            return resp

        _serve_plugin_static.auth_exempt = True

        static_pattern = re_path(
            r"^static/plugins/smartparts/(?P<path>.*)$",
            _serve_plugin_static,
            {"document_root": static_plugin_dir},
        )
        InvenTree.urls.urlpatterns.insert(0, static_pattern)
        clear_url_caches()
        logger.info(f"SmartParts: Added static file serve route for {static_plugin_dir}")
    except Exception as e:
        logger.warning(f"SmartParts: Could not ensure plugin static route: {e}")


# Configure media serving, plugin static serving, download UA, and global scanner script immediately on plugin module load
_ensure_media_url_served()
_ensure_plugin_static_served()
_ensure_download_user_agent()
_ensure_global_scanner_script()


# ── Category Template Resolution ─────────────────────────────────────────────


def get_resolved_category_templates(category) -> dict:
    """
    Return the effective set of ParameterTemplates for a given PartCategory,
    applying child-over-parent precedence across the full ancestor hierarchy.

    Traversal order: leaf category first, then successive parents up to the root.
    A template name seen at a deeper (child) level is never overwritten by a
    shallower (parent) level definition.

    Args:
        category: A ``PartCategory`` model instance (may be None).

    Returns:
        dict mapping ``str(template.template.name)`` →
        ``PartCategoryParameterTemplate`` instance.
        Returns ``{}`` when ``category`` is None or no templates are defined
        anywhere in the hierarchy.
    """
    if category is None:
        return {}

    try:
        from part.models import PartCategory, PartCategoryParameterTemplate

        # Support category as ID (int or str) or model instance
        if isinstance(category, (int, str)):
            try:
                category = PartCategory.objects.get(pk=int(category))
            except Exception:
                return {}

        if not hasattr(category, "get_ancestors"):
            return {}

        # get_ancestors(include_self=True) returns QuerySet ordered root → leaf.
        # We want leaf → root, so we reverse the list so that the child's entry
        # is inserted first and never overwritten by a parent entry.
        ancestors = list(category.get_ancestors(include_self=True))
        ancestors.reverse()  # leaf first

        resolved: dict = {}
        for anc in ancestors:
            # InvenTree PartCategoryParameterTemplate foreign key to ParameterTemplate is 'template'
            try:
                templates = PartCategoryParameterTemplate.objects.filter(
                    category=anc
                ).select_related("template")
            except Exception:
                templates = PartCategoryParameterTemplate.objects.filter(
                    category=anc
                )

            for tpl in templates:
                template_obj = getattr(tpl, "template", None) or getattr(
                    tpl, "parameter_template", None
                )
                if template_obj and hasattr(template_obj, "name"):
                    name = template_obj.name
                    if name not in resolved:
                        # First (deepest) definition wins
                        resolved[name] = tpl

        return resolved

    except Exception as exc:
        logger.warning(
            f"SmartParts: get_resolved_category_templates failed for "
            f"category={getattr(category, 'pk', category)}: {exc}"
        )
        return {}

