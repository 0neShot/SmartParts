"""
Base API Client
===============
Abstract base class for all distributor API clients.
Defines the standardized PartData structure and retry logic.
"""

import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger("inventree_smart_parts.api")


# ═══════════════════════════════════════════════════════════════════
#  Shared Data Structures
# ═══════════════════════════════════════════════════════════════════


@dataclass
class PriceBreak:
    """Represents a single price break from a supplier."""

    quantity: int
    price: float
    currency: str = "EUR"


@dataclass
class PartParameter:
    """Represents a single part parameter (e.g., 'Voltage Rating': '5V')."""

    name: str
    value: str
    unit: str = ""


@dataclass
class PartData:
    """
    Normalized part data from a distributor API.

    This is the universal exchange format between API clients,
    the data merger, and the part creator.
    """

    # ── Identification ──
    mpn: str = ""
    manufacturer: str = ""
    description: str = ""
    name: str = ""

    # ── Classification ──
    category: str = ""  # Distributor's category string
    subcategory: str = ""  # Distributor's subcategory string

    # ── Supplier Info ──
    supplier_name: str = ""  # e.g., 'Mouser', 'DigiKey'
    supplier_sku: str = ""  # Supplier's part number / SKU
    supplier_url: str = ""  # Direct link to supplier page

    # ── Technical Data ──
    datasheet_url: str = ""
    image_url: str = ""
    package: str = ""  # e.g., 'SOIC-8', 'TO-220'
    parameters: List[PartParameter] = field(default_factory=list)

    # ── Pricing & Stock ──
    price_breaks: List[PriceBreak] = field(default_factory=list)
    stock_available: Optional[int] = None
    minimum_order_qty: int = 1
    order_multiple: int = 1

    # ── Source Metadata ──
    source: str = ""  # API source identifier ('mouser', 'digikey', 'lcsc')
    raw_data: Dict[str, Any] = field(default_factory=dict)
    confidence: float = 1.0  # Match confidence (0.0 – 1.0)

    def has_field(self, field_name: str) -> bool:
        """Check whether a field has a non-empty, non-default value."""
        val = getattr(self, field_name, None)
        if val is None:
            return False
        if isinstance(val, str):
            return bool(val.strip())
        if isinstance(val, list):
            return len(val) > 0
        return True


# ═══════════════════════════════════════════════════════════════════
#  Abstract Base Client
# ═══════════════════════════════════════════════════════════════════


class BaseApiClient(ABC):
    """
    Abstract base class for distributor API clients.

    Provides:
    - Automatic retry with exponential backoff
    - Configurable timeouts
    - Request rate-limiting
    - Standardized error handling
    """

    # Subclasses must set these
    SOURCE_NAME: str = ""
    BASE_URL: str = ""

    def __init__(self, timeout: int = 15, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self._session: Optional[requests.Session] = None
        self._last_request_time: float = 0
        self._min_request_interval: float = 0.5  # seconds between requests

    @property
    def session(self) -> requests.Session:
        """Lazy-initialized requests session with retry adapter."""
        if self._session is None:
            self._session = requests.Session()
            retry_strategy = Retry(
                total=self.max_retries,
                backoff_factor=1,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["GET", "POST"],
            )
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self._session.mount("https://", adapter)
            self._session.mount("http://", adapter)
        return self._session

    def _rate_limit(self):
        """Enforce minimum interval between requests."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            time.sleep(self._min_request_interval - elapsed)
        self._last_request_time = time.time()

    def _request(
        self,
        method: str,
        url: str,
        headers: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        params: Optional[Dict] = None,
    ) -> Dict[str, Any]:
        """
        Make an HTTP request with rate limiting and error handling.

        Returns parsed JSON response or raises an exception.
        """
        self._rate_limit()

        try:
            response = self.session.request(
                method=method,
                url=url,
                headers=headers or {},
                json=json_data,
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()

            try:
                data = response.json()
            except (ValueError, TypeError):
                logger.error(
                    f"[{self.SOURCE_NAME}] Non-JSON response from {url} "
                    f"(status={response.status_code}, "
                    f"content-type={response.headers.get('content-type', '?')})"
                )
                raise ApiError(f"{self.SOURCE_NAME} API returned non-JSON response")

            if not isinstance(data, dict):
                logger.error(
                    f"[{self.SOURCE_NAME}] Expected dict, got {type(data).__name__} "
                    f"from {url}"
                )
                raise ApiError(
                    f"{self.SOURCE_NAME} API returned unexpected data format"
                )

            return data

        except requests.exceptions.Timeout:
            logger.error(f"[{self.SOURCE_NAME}] Request timeout for URL: {url}")
            raise ApiTimeoutError(f"{self.SOURCE_NAME} API request timed out")

        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            logger.error(f"[{self.SOURCE_NAME}] HTTP {status} error: {e}")
            raise ApiHttpError(
                f"{self.SOURCE_NAME} API returned HTTP {status}",
                status_code=status,
                response=e.response,
            )

        except requests.exceptions.ConnectionError:
            logger.error(f"[{self.SOURCE_NAME}] Connection failed for URL: {url}")
            raise ApiConnectionError(f"Could not connect to {self.SOURCE_NAME} API")

        except requests.exceptions.RequestException as e:
            logger.error(f"[{self.SOURCE_NAME}] Request error: {e}")
            raise ApiError(f"{self.SOURCE_NAME} API error: {e}")

    @abstractmethod
    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """
        Search for a part by Manufacturer Part Number.

        Returns a PartData instance if found, or None if no match.
        """
        ...

    @abstractmethod
    def test_connection(self) -> Dict[str, Any]:
        """
        Test the API connection and credentials.

        Returns a dict with keys:
        - 'success': bool
        - 'message': str
        - 'details': optional dict with extra info
        """
        ...


# ═══════════════════════════════════════════════════════════════════
#  Custom Exceptions
# ═══════════════════════════════════════════════════════════════════


class ApiError(Exception):
    """Base exception for API client errors."""

    pass


class ApiTimeoutError(ApiError):
    """API request timed out."""

    pass


class ApiConnectionError(ApiError):
    """Could not connect to the API."""

    pass


class ApiHttpError(ApiError):
    """API returned an HTTP error status."""

    def __init__(self, message: str, status_code=None, response=None):
        super().__init__(message)
        self.status_code = status_code
        self.response = response


class ApiAuthError(ApiError):
    """API authentication failed."""

    pass
