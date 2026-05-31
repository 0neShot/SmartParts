"""
DigiKey API Client
==================
Integration with DigiKey Product Information API v4.
Uses OAuth2 Client Credentials flow for server-to-server authentication.
"""

import time
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

logger = logging.getLogger("inventree_smart_parts.api.digikey")


class DigiKeyClient(BaseApiClient):
    """Client for the DigiKey Product Information API v4."""

    SOURCE_NAME = "digikey"
    BASE_URL = "https://api.digikey.com"
    TOKEN_URL = "https://api.digikey.com/v1/oauth2/token"
    SEARCH_URL = "https://api.digikey.com/products/v4/search/keyword"

    def __init__(self, client_id: str, client_secret: str, **kwargs):
        super().__init__(**kwargs)
        self.client_id = client_id
        self.client_secret = client_secret
        self._access_token: Optional[str] = None
        self._token_expiry: float = 0

    def _authenticate(self):
        """
        Obtain an OAuth2 access token using Client Credentials grant.
        Tokens are cached until expiry.
        """
        if self._access_token and time.time() < self._token_expiry:
            return  # Token still valid

        if not self.client_id or not self.client_secret:
            raise ApiAuthError("DigiKey Client ID and/or Client Secret not configured")

        logger.info("[DigiKey] Requesting OAuth2 access token...")

        try:
            response = self.session.post(
                self.TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=self.timeout,
            )
            response.raise_for_status()

            token_data = response.json()
            self._access_token = token_data["access_token"]
            # Expire 60 seconds early to be safe
            expires_in = token_data.get("expires_in", 3600)
            self._token_expiry = time.time() + expires_in - 60

            logger.info("[DigiKey] OAuth2 token obtained successfully")

        except Exception as e:
            self._access_token = None
            self._token_expiry = 0
            raise ApiAuthError(f"DigiKey authentication failed: {e}")

    def _get_auth_headers(self) -> Dict[str, str]:
        """Return headers with a valid OAuth2 bearer token."""
        self._authenticate()
        return {
            "Authorization": f"Bearer {self._access_token}",
            "X-DIGIKEY-Client-Id": self.client_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """Search DigiKey for a part by MPN."""
        if not self.client_id or not self.client_secret:
            raise ApiError("DigiKey credentials are not configured")

        headers = self._get_auth_headers()

        payload = {
            "Keywords": mpn,
            "RecordCount": 10,
            "RecordStartPosition": 0,
            "ExcludeMarketPlaceProducts": True,
        }

        logger.info(f"[DigiKey] Searching for MPN: {mpn}")
        data = self._request(
            "POST", self.SEARCH_URL, headers=headers, json_data=payload
        )

        products = data.get("Products", [])
        if not products:
            logger.info(f"[DigiKey] No results found for MPN: {mpn}")
            return None

        # Find best match
        best = self._find_best_match(products, mpn)
        if best is None:
            best = products[0]

        return self._parse_part(best, mpn)

    def _find_best_match(self, products: List[Dict], mpn: str) -> Optional[Dict]:
        """Find product with closest MPN match."""
        mpn_lower = mpn.lower().strip()

        for product in products:
            mfr_pn = product.get("ManufacturerPartNumber", "").lower().strip()
            if mfr_pn == mpn_lower:
                return product

        for product in products:
            mfr_pn = product.get("ManufacturerPartNumber", "").lower().strip()
            if mpn_lower in mfr_pn:
                return product

        return None

    def _parse_part(self, raw: Dict[str, Any], search_mpn: str) -> PartData:
        """Convert DigiKey product data to PartData."""
        # Price breaks — handle both v3 (StandardPricing at root) and
        # v4 (StandardPricing nested inside ProductVariations).
        price_breaks = []
        pricing_list = raw.get("StandardPricing", [])

        # Fallback: v4 nests pricing inside ProductVariations
        if not pricing_list:
            for variation in raw.get("ProductVariations", []):
                pricing_list = variation.get("StandardPricing", [])
                if pricing_list:
                    break

        for pb in pricing_list:
            try:
                qty = int(pb.get("BreakQuantity", 0))
                price = float(pb.get("UnitPrice", 0) or pb.get("Price", 0))
                currency = pb.get("Currency", "USD")
                if qty > 0 and price > 0:
                    price_breaks.append(
                        PriceBreak(
                            quantity=qty,
                            price=price,
                            currency=currency,
                        )
                    )
            except (ValueError, TypeError):
                continue

        # Last resort: single unit price from top-level field
        if not price_breaks:
            unit_price = raw.get("UnitPrice", 0)
            if unit_price:
                try:
                    price_breaks.append(
                        PriceBreak(
                            quantity=1,
                            price=float(unit_price),
                            currency=(
                                raw.get("Currency", {}).get("Code", "USD")
                                if isinstance(raw.get("Currency"), dict)
                                else "USD"
                            ),
                        )
                    )
                except (ValueError, TypeError):
                    pass

        # Parameters
        parameters = []
        for param in raw.get("Parameters", []):
            p_name = param.get("ParameterText", "")
            p_value = param.get("ValueText", "")
            if p_name and p_value:
                parameters.append(
                    PartParameter(
                        name=p_name,
                        value=p_value,
                    )
                )

        # Stock
        stock = None
        qty_available = raw.get("QuantityAvailable", None)
        if qty_available is not None:
            try:
                stock = int(qty_available)
            except (ValueError, TypeError):
                pass

        # Category
        category_parts = []
        cat = raw.get("Category", {})
        if isinstance(cat, dict):
            cat_name = cat.get("Name", "")
            if cat_name:
                category_parts.append(cat_name)
            # Check for parent category
            parent = cat.get("ParentCategory", {})
            if isinstance(parent, dict):
                parent_name = parent.get("Name", "")
                if parent_name:
                    category_parts.insert(0, parent_name)
        elif isinstance(cat, str):
            category_parts.append(cat)

        category = " > ".join(category_parts) if category_parts else ""

        # Datasheet — DigiKey v4 field name changed across API versions;
        # try all known locations in priority order.
        datasheet_url = (
            raw.get("DatasheetUrl", "")  # v4 primary field
            or raw.get("PrimaryDatasheet", "")  # v3 / legacy alias
            or ""
        )
        if not datasheet_url:
            # MediaLinks array: [{"MediaType": "Datasheets", "Url": "..."}]
            for link in raw.get("MediaLinks") or raw.get("MediaLinks", []):
                if isinstance(link, dict):
                    media_type = (
                        link.get("MediaType", "")
                        or link.get("Type", "")
                        or link.get("mediaType", "")
                    ).lower()
                    if "datasheet" in media_type:
                        datasheet_url = link.get("Url", "") or link.get("url", "")
                        break
        if datasheet_url and not datasheet_url.startswith("http"):
            datasheet_url = f"https://www.digikey.com{datasheet_url}"

        # Image
        image_url = ""
        primary_photo = raw.get("PrimaryPhoto", "")
        if primary_photo:
            image_url = primary_photo

        mpn_result = raw.get("ManufacturerPartNumber", search_mpn)
        manufacturer_info = raw.get("Manufacturer", {})
        manufacturer = ""
        if isinstance(manufacturer_info, dict):
            manufacturer = manufacturer_info.get("Name", "")
        elif isinstance(manufacturer_info, str):
            manufacturer = manufacturer_info

        description = raw.get("ProductDescription", "") or raw.get(
            "DetailedDescription", ""
        )

        # Packaging
        package = ""
        packaging_info = raw.get("Packaging", {})
        if isinstance(packaging_info, dict):
            package = packaging_info.get("Name", "")

        # DigiKey part number as SKU — fallback chain for edge-case MPNs
        # where the primary field is empty.
        dk_pn = raw.get("DigiKeyPartNumber", "").strip()

        # Fallback 1: Check ExactDigiKeyPartNumber (v4 alternate field)
        if not dk_pn:
            dk_pn = raw.get("ExactDigiKeyPartNumber", "").strip()

        # Fallback 2: Packaging/ordering variations may carry a part number
        if not dk_pn:
            for variation in raw.get("ProductVariations", []):
                pn = (
                    variation.get("DigiKeyProductNumber", "")
                    or variation.get("DigiKeyPartNumber", "")
                ).strip()
                if pn:
                    dk_pn = pn
                    break

        # Fallback 3: Synthesise from MPN (last resort — data confirmed by API)
        if not dk_pn and mpn_result:
            dk_pn = mpn_result

        # Product URL
        product_url = raw.get("ProductUrl", "")
        if product_url and not product_url.startswith("http"):
            product_url = f"https://www.digikey.com{product_url}"

        # Min order
        min_qty = 1
        min_val = raw.get("MinimumOrderQuantity", None)
        if min_val:
            try:
                min_qty = int(min_val)
            except (ValueError, TypeError):
                pass

        # Confidence
        confidence = 1.0
        if mpn_result.lower().strip() != search_mpn.lower().strip():
            confidence = 0.8

        return PartData(
            mpn=mpn_result,
            manufacturer=manufacturer,
            description=description,
            name=f"{manufacturer} {mpn_result}" if manufacturer else mpn_result,
            category=category,
            supplier_name="DigiKey",
            supplier_sku=dk_pn,
            supplier_url=product_url,
            datasheet_url=datasheet_url,
            image_url=image_url,
            package=package,
            parameters=parameters,
            price_breaks=price_breaks,
            stock_available=stock,
            minimum_order_qty=min_qty,
            source="digikey",
            raw_data=raw,
            confidence=confidence,
        )

    def test_connection(self) -> Dict[str, Any]:
        """Test DigiKey API connectivity by authenticating."""
        try:
            if not self.client_id or not self.client_secret:
                return {
                    "success": False,
                    "message": "Client ID and/or Client Secret not configured",
                }

            self._authenticate()
            return {
                "success": True,
                "message": "Connected successfully. OAuth2 authentication OK.",
                "details": {
                    "token_expires_in": int(self._token_expiry - time.time()),
                },
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
