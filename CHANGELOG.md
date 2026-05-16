# Changelog

All notable changes to SmartParts are documented in this file.

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
