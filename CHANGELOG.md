# Changelog

All notable changes to SmartParts are documented in this file.

## [1.1.1] — 2026-08-10

### Fixed
- **PureScan Barcode Format Support**: Resolved issue where PureScan terminal only accepted JSON format barcodes and rejected InvenTree default shortcode QR codes (`INV-SI722`, `INV-SL5`, `INV-PA42`) and linked barcodes. Updated both backend (`purescan_resolve_barcode`) and frontend JS (`_parseInvenTreeBarcode`) parsers to natively handle shortcode, JSON, key=value, and custom linked barcodes.

## [1.1.0] — 2026-05-31

### Added
- **Dedicated Parameter Normalization Dashboard**: Built a separate Django View and HTML Template specifically for the Parameter Normalization Dashboard, moving management cleanly out of cramped plugin settings.
- **Permanent Ignore Filter Heuristics**: Added permanent ignore dropdown support. Ignored parameters are saved with `is_ignored = True` and silently dropped during the data merge phase in `data_merger.py` (and Creator safeguards).
- **Autocomplete Dynamic Dropdowns**: Integrated autocomplete inputs for Canonical Values utilizing the database-backed `ParameterTemplate` options combined with hardcoded canonical electronic maps.
- **TME.eu API Client**: Secure, HMAC-SHA1 signed API integration to retrieve TME pricing, stock, and parameters.
- **element14 / Farnell API Client**: regional storefront selection with fallback lookup capabilities.
- **Regex-Based Parameter Sanitization**: String pre-processing filter to strip punctuation (hyphens, underscores, brackets, parentheses, slashes) and normalize whitespaces for robust canonical matches.
- **"Catch & Learn" Parameter Map UI**: Interactive Dashboard interface to track unknown parameters returned from APIs and map them dynamically.
- **GS character Wedge Handling**: Parser support for high-density DataMatrix barcodes containing non-printable GS (ASCII 29) group separator characters from hardware keyboard wedge scanners.
- **CI/CD Integration**: Configured GitHub Actions workflows for automated PEP 8 lints (`flake8`), `black` code styling checks, and full normalizer test execution.

### Changed
- **Parameter Normalization Expansion**: Expanded standard `PARAMETER_MAP` to cover 100+ common electronic, mechanical, and semiconductor units with SI scaling (e.g. converting `0.00001 F` -> `10 µF`).
- **Codebase Sanitization & Formatting**: Reformatted the entire Python codebase (22 modules) using the `black` formatter to strictly conform to PEP 8.
- **Documentation Overhaul**: Rewrote the entire `README.md` to showcase the new multi-source features, parameter mapping architecture, PureScan terminal commands, and configuration variables.

### Fixed
- **Sanitized API Diagnostics & Logs**: Uniformly standardized all connection test results to return clean, standard success formats (e.g. `Connected successfully. Test search returned: LM7805`) and implemented a robust query parameter filter to scrub sensitive parameters/URLs from all API error exceptions and activity log warnings.
- **Dead Code & Unused Imports Cleanup**: Removed unused imports and legacy variables across all Python files, including `duplicate_checker.py`, `image_handler.py`, `views.py`, and `tools/generate_command_sheet.py`.
- **Unicode Console Output**: Resolved cp1252 encoding and character crashes on Windows terminal environments when testing values containing Ω or µ symbols.

## [1.0.1] — 2026-05-16

### Fixed
- **Multi-Supplier Creation**: All confirmed API hits now create `SupplierPart` records. Previously, only the first supplier was saved when an item was found on multiple distributors (e.g. Mouser AND DigiKey).
- **DigiKey Empty SKU**: Added a three-tier fallback chain for extracting DigiKey Part Numbers (`DigiKeyPartNumber` → `ExactDigiKeyPartNumber` → `ProductVariations` → MPN-based synthetic). Parts from DigiKey are no longer silently dropped when the primary SKU field is empty.
- **DigiKey Price Breaks**: Fixed pricing extraction for DigiKey API v4, which nests `StandardPricing` inside `ProductVariations` instead of at the root level. Added `UnitPrice` single-price fallback.
- **Parameter Sanitization Bypass**: The part update path now routes all parameter data through the same sentinel filters (`is_useless_value`) as the creation path. Empty/placeholder values (`-`, `N/A`, `?`) are no longer written to the database during updates.
- **SupplierPart Backfill**: Legacy `SupplierPart` records created without a `manufacturer_part` FK are automatically backfilled on subsequent imports, making them visible in the standard InvenTree UI.

### Added
- **UI Edit Highlighting**: Manual changes to API-parsed fields are now visually highlighted with an orange left border (`sp-input-edited`), preventing accidental overwrites.
- **SKU Fallback Badge**: Supplier cards using a fallback SKU display a `⚠ SKU Fallback` warning badge so the user can review and correct the value.
- **Dashboard Escape Hatch**: "Back to InvenTree" button added to the SmartParts Dashboard header for kiosk/fullscreen environments.

### Changed
- Diagnostic `print()` statements replaced with production `logger.debug()` calls.
- Test and diagnostic scripts excluded from packaging via `.gitignore`.

## [1.0.0] — 2026-05-10

### Initial Release
- MPN lookup across Mouser, DigiKey, and LCSC with configurable priority merging
- Intelligent fuzzy category matching with persistent learning
- Batch BOM import (Altium CSV/XLSX) with auto-column mapping
- PureScan zero-click warehouse terminal (Transfer, Add, Remove, Stocktake, Info, Undo)
- Auto label printing (Dymo/Zebra via inventreelabelmachine)
- Duplicate detection with update-in-place support
- DataMatrix barcode parsing (ANSI MH10.8.2 / ISO/IEC 15434)
- PUI integration (panels + dashboard widgets)