"""
InvenTree Smart Parts Plugin
============================
Intelligent inventory assistant that automates part creation from MPN lookup.
"""

try:
    from .core import SmartPartsPlugin
except Exception as e:
    # Prevent a broken sub-module from crashing InvenTree startup.
    # Log the real error so it can be debugged without a bootloop.
    import logging
    logging.getLogger('inventree_smart_parts').error(
        f"SmartPartsPlugin failed to load: {e}", exc_info=True
    )
    SmartPartsPlugin = None  # type: ignore[assignment,misc]

__all__ = ['SmartPartsPlugin']
