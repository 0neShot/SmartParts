"""
Smart Parts Plugin – Core
=========================
Plugin class definition with all InvenTree mixins, settings, and URL routing.
"""

import logging

from django.urls import path

from plugin import InvenTreePlugin
from plugin.mixins import (
    SettingsMixin,
    UrlsMixin,
    UserInterfaceMixin,
)

logger = logging.getLogger("inventree_smart_parts")


class SmartPartsPlugin(UserInterfaceMixin, SettingsMixin, UrlsMixin, InvenTreePlugin):
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
    VERSION = "1.1.1"
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

    # ── URL Routing ──────────────────────────────────────────────────
    def setup_urls(self):
        """Define custom URL patterns for the plugin."""
        from . import views

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
        ]
