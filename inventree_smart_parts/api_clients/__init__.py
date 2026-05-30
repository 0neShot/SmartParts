"""
API Client Layer
================
Provides uniform access to distributor APIs (Mouser, DigiKey, LCSC).
Each client normalizes response data into a shared PartData structure.
"""

from .base import BaseApiClient, PartData, PriceBreak, PartParameter
from .mouser import MouserClient
from .digikey import DigiKeyClient
from .lcsc import LCSCClient

__all__ = [
    "BaseApiClient",
    "PartData",
    "PriceBreak",
    "PartParameter",
    "MouserClient",
    "DigiKeyClient",
    "LCSCClient",
]
