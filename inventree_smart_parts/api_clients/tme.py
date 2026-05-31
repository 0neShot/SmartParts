"""
TME API Client (v2.0)
====================
Integration with the new TME (Transfer Multisort Elektronik) REST API v2.

Authentication: OAuth 2.0 Client Credentials flow with Bearer Access Tokens.
  - Basic authentication to /auth/token using token as username and secret as password.
  - Subsequent requests use the Authorization: Bearer <access_token> header.

API docs:
  https://api-doc.tme.eu/v2
"""

import logging
from typing import Optional, Dict, Any, List

from .base import (
    BaseApiClient,
    PartData,
    PriceBreak,
    PartParameter,
    ApiError,
    ApiAuthError,
)

logger = logging.getLogger("inventree_smart_parts.api.tme")

_BASE_URL = "https://api.tme.eu"


class TMEClient(BaseApiClient):
    """
    Client for the TME REST API v2.0 using OAuth 2.0 Bearer authentication.

    Performs a three-step enrichment pipeline for each MPN search:
      1. `/products/search` – locate the best matching product symbol
      2. `/products/data` – fetch price breaks & stock level
      3. `/products/files` – fetch datasheet & image URLs
      4. `/products/parameters` – fetch technical parameters (best-effort)
    """

    SOURCE_NAME = "tme"
    BASE_URL = _BASE_URL

    def __init__(
        self,
        token: str,
        secret: str,
        country: str = "DE",
        language: str = "EN",
        currency: str = "EUR",
        **kwargs,
    ):
        super().__init__(**kwargs)

        # TME API v2 expects the 50-character token as the username
        # and the 20-character secret as the password.
        # Dynamically auto-detect and swap them if entered in the wrong order.
        t_val = token.strip() if token else ""
        s_val = secret.strip() if secret else ""
        if len(t_val) >= len(s_val):
            self.token = t_val
            self.secret = s_val
        else:
            self.token = s_val
            self.secret = t_val

        self.country = country.strip().upper() if country else "DE"
        self.language = language.strip().upper() if language else "EN"
        self.currency = currency.strip().upper() if currency else "EUR"

        # TME rate-limit: max 10 req/s; played safe at ~2 req/s
        self._min_request_interval = 0.5

        # OAuth cache properties
        self._access_token = None
        self._token_expires_at = 0

    # ── Public interface ──────────────────────────────────────────────────────

    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """
        Search TME for a part by MPN and enrich with prices, files, and parameters.
        """
        if not self.token or not self.secret:
            raise ApiAuthError("TME API token and/or secret are not configured")

        logger.info(f"[TME] Searching for MPN: {mpn}")

        # Step 1 – search
        symbol = self._search_symbol(mpn)
        if not symbol:
            logger.info(f"[TME] No results for MPN: {mpn}")
            return None

        symbol_name = symbol.get("symbol", "")

        # Steps 2-4 – enrich sequentially
        prices_data = self._get_prices([symbol_name])
        files_data = self._get_files([symbol_name])
        params_data = self._get_parameters([symbol_name])

        return self._build_part_data(
            symbol=symbol,
            mpn=mpn,
            prices_data=prices_data,
            files_data=files_data,
            params_data=params_data,
        )

    def test_connection(self) -> Dict[str, Any]:
        """Verify TME API credentials with a minimal search."""
        try:
            if not self.token or not self.secret:
                return {
                    "success": False,
                    "message": "Token and/or secret are not configured",
                }

            result = self.search_by_mpn("LM7805")
            if result:
                return {
                    "success": True,
                    "message": f"Connected successfully. Test search returned: {result.mpn}",
                    "details": {
                        "test_mpn": result.mpn,
                        "manufacturer": result.manufacturer,
                        "country": self.country,
                    },
                }
            return {
                "success": True,
                "message": f"Connected (country={self.country}), but test search returned no results.",
            }
        except ApiAuthError as e:
            from .base import sanitize_error_message

            return {
                "success": False,
                "message": f"Authentication failed: {sanitize_error_message(str(e))}",
            }
        except Exception as e:
            from .base import sanitize_error_message

            return {
                "success": False,
                "message": f"Unexpected error: {sanitize_error_message(str(e))}",
            }

    # ── API sub-calls ─────────────────────────────────────────────────────────

    def _search_symbol(self, mpn: str) -> Optional[Dict[str, Any]]:
        """Call /products/search and return the best-matching product element."""
        params = {
            "phrase": mpn,
            "scope[]": "products",
            "country": self.country,
        }
        data = self._request_v2("GET", "/products/search", params)

        product_list = data.get("data", {}).get("products", {}).get("elements", [])
        if not product_list:
            return None

        # Find the best matching symbol
        best = _find_best_symbol(product_list, mpn)
        return best or product_list[0]

    def _get_prices(self, symbols: List[str]) -> Dict[str, Any]:
        """Call /products/data for a list of TME symbols."""
        if not symbols:
            return {}
        params = {
            "country": self.country,
            "currency": self.currency,
            "scope[]": ["prices", "stock"],
        }
        for i, sym in enumerate(symbols):
            params[f"symbols[{i}]"] = sym

        try:
            data = self._request_v2("GET", "/products/data", params)
            return data.get("data", {})
        except Exception as e:
            from .base import sanitize_error_message

            logger.warning(f"[TME] GetPrices failed: {sanitize_error_message(str(e))}")
            return {}

    def _get_files(self, symbols: List[str]) -> Dict[str, Any]:
        """Call /products/files for a list of TME symbols."""
        if not symbols:
            return {}
        params = {
            "country": self.country,
        }
        for i, sym in enumerate(symbols):
            params[f"symbols[{i}]"] = sym

        try:
            data = self._request_v2("GET", "/products/files", params)
            return data.get("data", {})
        except Exception as e:
            from .base import sanitize_error_message

            logger.warning(
                f"[TME] GetProductsFiles failed: {sanitize_error_message(str(e))}"
            )
            return {}

    def _get_parameters(self, symbols: List[str]) -> Dict[str, Any]:
        """Call /products/parameters for a list of TME symbols."""
        if not symbols:
            return {}
        params = {
            "country": self.country,
        }
        for i, sym in enumerate(symbols):
            params[f"symbols[{i}]"] = sym

        try:
            data = self._request_v2("GET", "/products/parameters", params)
            return data.get("data", {})
        except Exception as e:
            from .base import sanitize_error_message

            logger.warning(
                f"[TME] GetParameters failed (non-fatal): {sanitize_error_message(str(e))}"
            )
            return {}

    # ── OAuth 2.0 and API helper calls ────────────────────────────────────────

    def _get_headers(self) -> Dict[str, str]:
        """Generate Authorization headers with a valid Bearer token."""
        import time

        # Refresh token if not set or within 10 seconds of expiry
        if not self._access_token or time.time() > self._token_expires_at - 10:
            self._authenticate()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    def _authenticate(self):
        """Fetch a fresh OAuth access token from TME."""
        import base64
        import time

        auth_url = f"{_BASE_URL}/auth/token"
        auth_str = f"{self.token}:{self.secret}"
        auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")

        headers = {
            "Authorization": f"Basic {auth_b64}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        data = {
            "grant_type": "client_credentials",
        }

        logger.info("[TME] Authenticating with TME OAuth token endpoint")
        response = self.session.post(
            auth_url, headers=headers, data=data, timeout=self.timeout
        )
        response.raise_for_status()

        res_data = response.json()
        self._access_token = res_data.get("access_token")
        expires_in = res_data.get("expires_in", 300)
        self._token_expires_at = time.time() + expires_in

    def _request_v2(
        self, method: str, endpoint: str, params: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """Perform a Bearer-authenticated REST query to the TME API."""
        url = f"{_BASE_URL}{endpoint}"
        headers = self._get_headers()

        self._rate_limit()

        try:
            if method.upper() == "GET":
                response = self.session.get(
                    url, headers=headers, params=params, timeout=self.timeout
                )
            else:
                response = self.session.post(
                    url, headers=headers, json=params, timeout=self.timeout
                )

            response.raise_for_status()

            data = response.json()
            if not isinstance(data, dict):
                raise ApiError(
                    f"TME API returned unexpected type: {type(data).__name__}"
                )

            status = data.get("status", "")
            if status not in ("OK", "ok", ""):
                raise ApiError(f"TME API error status: {status}")

            return data

        except ApiError:
            raise
        except Exception as e:
            from .base import sanitize_error_message

            cleaned_msg = sanitize_error_message(str(e))
            raise ApiError(f"TME request to {endpoint} failed: {cleaned_msg}") from e

    # ── Data assembly ─────────────────────────────────────────────────────────

    def _build_part_data(
        self,
        symbol: Dict[str, Any],
        mpn: str,
        prices_data: Dict[str, Any],
        files_data: Dict[str, Any],
        params_data: Dict[str, Any],
    ) -> PartData:
        """Assemble a PartData object from the raw TME v2 sub-call responses."""
        symbol_name = symbol.get("symbol", "")

        # Select best MPN from manufacturer_symbols list
        mfr_symbols = symbol.get("manufacturer_symbols", [])
        mpn_result = mfr_symbols[0] if mfr_symbols else symbol_name

        manufacturer = symbol.get("manufacturer", {}).get("name", "")
        description = symbol.get("description", "")
        category = symbol.get("category", {}).get("name", "")

        # ── Price breaks & stock ──
        price_breaks: List[PriceBreak] = []
        stock = None

        # prices_data structure:
        # {"elements": [{"symbol": "...", "stock_quantity": N, "prices": {"elements": [{"amount": N, "price": X.XX}]}}]}
        for prod in prices_data.get("elements") or []:
            if prod.get("symbol", "").upper() != symbol_name.upper():
                continue
            try:
                stock = int(prod.get("stock_quantity", 0))
            except (ValueError, TypeError):
                pass

            prices_dict = prod.get("prices", {}) or {}
            for pb in prices_dict.get("elements") or []:
                try:
                    qty = int(pb.get("amount", 0))
                    price = float(pb.get("price", 0))
                    if qty > 0 and price > 0:
                        price_breaks.append(
                            PriceBreak(
                                quantity=qty,
                                price=price,
                                currency=self.currency,
                            )
                        )
                except (ValueError, TypeError):
                    continue
            break

        # ── Datasheet & image ──
        datasheet_url = ""
        image_url = ""

        # files_data structure:
        # {"elements": [{"symbol": "...", "assets": {"primary_photo": {"high_resolution": "...", "prime": "..."}}, "documents": {"elements": [{"url": "...", "type": "..."}]}}]}
        for prod in files_data.get("elements") or []:
            if prod.get("symbol", "").upper() != symbol_name.upper():
                continue
            assets = prod.get("assets", {}) or {}
            primary_photo = assets.get("primary_photo", {}) or {}

            img_path = (
                primary_photo.get("high_resolution") or primary_photo.get("prime") or ""
            )
            if img_path:
                image_url = (
                    img_path if img_path.startswith("http") else f"https:{img_path}"
                )

            docs_dict = prod.get("documents", {}) or {}
            for doc in docs_dict.get("elements") or []:
                doc_type = (doc.get("type", "") or "").upper()
                if doc_type in ("DTE", "DATASHEET", "DS"):
                    url = doc.get("url", "") or ""
                    if url:
                        datasheet_url = (
                            url if url.startswith("http") else f"https:{url}"
                        )
                        break
            # Fallback: first document regardless of type
            if not datasheet_url:
                for doc in docs_dict.get("elements") or []:
                    url = doc.get("url", "") or ""
                    if url:
                        datasheet_url = (
                            url if url.startswith("http") else f"https:{url}"
                        )
                        break
            break

        # ── Parameters ──
        parameters: List[PartParameter] = []
        # params_data structure:
        # {"elements": [{"symbol": "...", "parameters": {"elements": [{"name": "...", "values": [{"value": "..."}]}]}}]}
        for prod in params_data.get("elements") or []:
            if prod.get("symbol", "").upper() != symbol_name.upper():
                continue
            params_dict = prod.get("parameters", {}) or {}
            for param in params_dict.get("elements") or []:
                p_name = param.get("name", "")
                values = param.get("values", [])
                if p_name and values:
                    p_val = values[0].get("value", "")
                    if p_val:
                        parameters.append(
                            PartParameter(
                                name=p_name,
                                value=p_val,
                                unit="",
                            )
                        )
            break

        # ── Min order qty ──
        min_qty = 1
        try:
            min_qty = int(symbol.get("minimal_amount", 1) or 1)
        except (ValueError, TypeError):
            pass

        # ── Order multiple ──
        order_mult = 1
        try:
            order_mult = int(symbol.get("multiples", 1) or 1)
        except (ValueError, TypeError):
            pass

        # ── Product URL ──
        product_url = (
            f"https://www.tme.eu/en/details/{symbol_name}/" if symbol_name else ""
        )

        # ── Confidence ──
        confidence = 1.0
        if mpn_result.lower().strip() != mpn.lower().strip():
            confidence = 0.85

        return PartData(
            mpn=mpn_result,
            manufacturer=manufacturer,
            description=description,
            name=f"{manufacturer} {mpn_result}" if manufacturer else mpn_result,
            category=category,
            supplier_name="TME",
            supplier_sku=symbol_name,
            supplier_url=product_url,
            datasheet_url=datasheet_url,
            image_url=image_url,
            package="",
            parameters=parameters,
            price_breaks=price_breaks,
            stock_available=stock,
            minimum_order_qty=min_qty,
            order_multiple=order_mult,
            source="tme",
            raw_data=symbol,
            confidence=confidence,
        )


# ── Module-level helpers ──────────────────────────────────────────────────────


def _find_best_symbol(product_list: List[Dict], mpn: str) -> Optional[Dict]:
    """
    Return the product from ``product_list`` whose ``manufacturer_symbols``
    (or ``symbol``) most closely matches ``mpn``.
    """
    mpn_lower = mpn.lower().strip()

    # Exact match on manufacturer_symbols (= the manufacturer's part numbers)
    for p in product_list:
        for m_sym in p.get("manufacturer_symbols", []):
            if m_sym.lower().strip() == mpn_lower:
                return p

    # Exact match on TME Symbol
    for p in product_list:
        sym = (p.get("symbol", "") or "").lower().strip()
        if sym == mpn_lower:
            return p

    # Contains match on manufacturer_symbols
    for p in product_list:
        for m_sym in p.get("manufacturer_symbols", []):
            if mpn_lower in m_sym.lower().strip():
                return p

    # Contains match on Symbol
    for p in product_list:
        sym = (p.get("symbol", "") or "").lower().strip()
        if mpn_lower in sym:
            return p

    return None
