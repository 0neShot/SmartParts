"""
element14 / Farnell / Newark API Client
========================================
Integration with the element14 Product Search API (Powered by "PARTMINER" /
the Farnell global search REST service).

Authentication: A single ``api_key`` passed as a query-string parameter.
The same key works across all element14 storefronts (Farnell, Newark, element14).
A ``store_name`` is required to target the right regional catalogue.

API docs:
  https://partner.element14.com/docs/Product_Search_API_REST__Description

Supported store names (not exhaustive):
  uk.farnell.com  de.farnell.com  fr.farnell.com
  www.newark.com  au.element14.com  sg.element14.com  in.element14.com
"""

import logging
from typing import Optional, Dict, Any, List

from .base import (
    BaseApiClient,
    PartData,
    PriceBreak,
    PartParameter,
    ApiAuthError,
)

logger = logging.getLogger("inventree_smart_parts.api.element14")

# element14 REST endpoint template – store_name is injected at runtime
# element14 REST endpoint — manuPartNum exact search (primary)
_SEARCH_URL_TEMPLATE = (
    "https://api.element14.com/catalog/products"
    "?term=manuPartNum%3A{mpn}"
    "&storeInfo.id={store}"
    "&resultsSettings.offset=0"
    "&resultsSettings.numberOfResults=10"
    "&resultsSettings.responseGroup=large"
    "&callInfo.omitXmlSchema=false"
    "&callInfo.responseDataFormat=json"
    "&callInfo.apiKey={api_key}"
)

# Keyword / fallback search (prefixed with manuPartNum for compatibility)
_KEYWORD_URL_TEMPLATE = (
    "https://api.element14.com/catalog/products"
    "?term=manuPartNum%3A{mpn}"
    "&storeInfo.id={store}"
    "&resultsSettings.offset=0"
    "&resultsSettings.numberOfResults=10"
    "&resultsSettings.responseGroup=large"
    "&callInfo.omitXmlSchema=false"
    "&callInfo.responseDataFormat=json"
    "&callInfo.apiKey={api_key}"
)


class Element14Client(BaseApiClient):
    """
    Client for the element14 Product Search REST API.

    Covers all regional storefronts (Farnell, Newark, element14) via a single
    ``store_name`` selector.  One API key is shared across all stores.
    """

    SOURCE_NAME = "element14"
    # Base URL used for rate-limiting tracking only; actual calls use the full
    # template above.
    BASE_URL = "https://api.element14.com"

    def __init__(self, api_key: str, store_name: str = "uk.farnell.com", **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key.strip() if api_key else ""
        self.store_name = store_name.strip() if store_name else "uk.farnell.com"
        # element14 recommend ≤ 1 req/s for free tier keys
        self._min_request_interval = 1.0

    # ── Public interface ──────────────────────────────────────────────────────

    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """
        Search element14 for a part by Manufacturer Part Number.

        Tries an exact ``manuPartNum`` filter first (high precision).
        Falls back to a plain keyword search if the exact filter returns nothing,
        which handles cases where the MPN is indexed differently in the catalogue.
        """
        if not self.api_key:
            raise ApiAuthError("element14 API key is not configured")

        logger.info(f"[element14] Searching for MPN: {mpn} on {self.store_name}")

        # --- Pass 1: exact manufacturer part number filter ---
        url = _SEARCH_URL_TEMPLATE.format(
            mpn=_url_encode(mpn),
            store=self.store_name,
            api_key=self.api_key,
        )
        try:
            data = self._request("GET", url)
            products = self._extract_products(data)
        except Exception as e:
            logger.warning(f"[element14] MPN filter search failed: {e}")
            products = []

        # --- Pass 2: keyword fallback ---
        if not products:
            logger.info(
                f"[element14] MPN filter returned nothing, trying keyword search for: {mpn}"
            )
            kw_url = _KEYWORD_URL_TEMPLATE.format(
                mpn=_url_encode(mpn),
                store=self.store_name,
                api_key=self.api_key,
            )
            try:
                data = self._request("GET", kw_url)
                products = self._extract_products(data)
            except Exception as e:
                logger.warning(f"[element14] Keyword search also failed: {e}")
                return None

        if not products:
            logger.info(f"[element14] No results found for MPN: {mpn}")
            return None

        best = self._find_best_match(products, mpn)
        if best is None:
            best = products[0]

        return self._parse_part(best, mpn)

    def test_connection(self) -> Dict[str, Any]:
        """Test element14 API key with a plain keyword search for a common part."""
        try:
            if not self.api_key:
                return {"success": False, "message": "API key is not configured"}

            # Use a plain keyword search so results are returned regardless of
            # whether the test part is currently in stock.
            kw_url = _KEYWORD_URL_TEMPLATE.format(
                mpn=_url_encode("LM7805"),
                store=self.store_name,
                api_key=self.api_key,
            )
            data = self._request("GET", kw_url)
            products = self._extract_products(data)

            if products:
                first = products[0]
                name = (
                    first.get("displayName", "")
                    or first.get("translatedManufacturerPartNumber", "")
                    or first.get("sku", "unknown")
                )
                return {
                    "success": True,
                    "message": f"Connected successfully. Test search returned: {name}",
                    "details": {"store": self.store_name, "results": len(products)},
                }
            return {
                "success": True,
                "message": f"Connected successfully to {self.store_name} (test search returned 0 results).",
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

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _extract_products(self, data: Dict[str, Any]) -> List[Dict]:
        """
        Extract the product list from an element14 API response dict.
        Supports all potential wrapper keys returned by the different search types.
        """
        wrapper_keys = [
            "manufacturerPartNumberSearchReturn",
            "premierFarnellPartNumberReturn",
            "keywordSearchReturn",
            "keywordSearchResults",
        ]
        for key in wrapper_keys:
            # Check direct key and capitalized variant
            wrapper = data.get(key) or data.get(key[0].upper() + key[1:]) or {}
            if isinstance(wrapper, dict):
                products = wrapper.get("products") or wrapper.get("Products")
                if products:
                    return products
        return []

    def _find_best_match(self, products: List[Dict], mpn: str) -> Optional[Dict]:
        """Return the product whose MPN most closely matches the search term."""
        mpn_lower = mpn.lower().strip()

        # Exact manufacturer part number match
        for p in products:
            mfr_pn = (
                (
                    p.get("translatedManufacturerPartNumber", "")
                    or p.get("manufacturerPartNumber", "")
                    or ""
                )
                .lower()
                .strip()
            )
            if mfr_pn == mpn_lower:
                return p

        # Contains match
        for p in products:
            mfr_pn = (
                (
                    p.get("translatedManufacturerPartNumber", "")
                    or p.get("manufacturerPartNumber", "")
                    or ""
                )
                .lower()
                .strip()
            )
            if mpn_lower in mfr_pn:
                return p

        return None

    def _parse_part(self, raw: Dict[str, Any], search_mpn: str) -> PartData:
        """Map a raw element14 product dict to the shared PartData structure."""

        # ── Identification ──
        mpn_result = (
            raw.get("translatedManufacturerPartNumber", "")
            or raw.get("manufacturerPartNumber", "")
            or search_mpn
        )
        sku = raw.get("sku", "")
        manufacturer = raw.get("vendorName", "")
        description = raw.get("displayName", "") or raw.get("description", "")

        # ── Category ──
        category_parts = []
        cat1 = raw.get("categoryTree", "")
        if cat1:
            category_parts.append(cat1)
        cat2 = raw.get("subCategory", {})
        if isinstance(cat2, dict):
            cat2_name = cat2.get("name", "")
            if cat2_name:
                category_parts.append(cat2_name)
        elif isinstance(cat2, str) and cat2:
            category_parts.append(cat2)
        category = " > ".join(category_parts) if category_parts else ""

        # ── Package ──
        package = raw.get("packageType", "") or raw.get("vendorPackage", "")

        # ── URLs ──
        product_url = raw.get("manuLeadTime", "")  # placeholder – replaced below
        product_url = ""
        # element14 product page: https://<store>/p/<sku>
        if sku and self.store_name:
            product_url = f"https://{self.store_name}/p/{sku}"

        datasheet_url = ""
        for doc in raw.get("datasheets") or []:
            if isinstance(doc, dict):
                url = doc.get("url", "") or doc.get("URL", "")
                if url and url.startswith("http"):
                    datasheet_url = url
                    break

        image_url = ""
        for img in raw.get("imageList") or []:
            if isinstance(img, dict):
                url = img.get("url", "") or img.get("baseName", "")
                if url:
                    if not url.startswith("http"):
                        url = f"https://{self.store_name}{url}"
                    image_url = url
                    break
            elif isinstance(img, str) and img:
                if not img.startswith("http"):
                    img = f"https://{self.store_name}{img}"
                image_url = img
                break

        # Fallback: single image field
        if not image_url:
            img_val = raw.get("image", "")
            if isinstance(img_val, dict):
                base_name = img_val.get("baseName", "")
                vrnt_path = img_val.get("vrntPath", "")
                if base_name:
                    if not base_name.startswith("/"):
                        base_name = "/" + base_name

                    if vrnt_path == "farnell/":
                        # Use French locale for French storefront, fallback to en_GB
                        lang = (
                            "fr_FR" if "fr.farnell.com" in self.store_name else "en_GB"
                        )
                        image_url = f"https://{self.store_name}/productimages/standard/{lang}{base_name}"
                    elif vrnt_path == "nio/":
                        image_url = f"https://{self.store_name}/productimages/standard/en_US{base_name}"
                    else:
                        image_url = f"https://{self.store_name}/productimages/standard/en_GB{base_name}"
            elif isinstance(img_val, str) and img_val:
                image_url = img_val
                if not image_url.startswith("http"):
                    image_url = f"https://{self.store_name}{image_url}"

        # ── Pricing ──
        price_breaks: List[PriceBreak] = []
        for pb in raw.get("prices") or []:
            try:
                qty = int(pb.get("from", 0))
                price = float(pb.get("cost", 0))
                currency = pb.get("currency", "GBP")
                if qty > 0 and price > 0:
                    price_breaks.append(
                        PriceBreak(quantity=qty, price=price, currency=currency)
                    )
            except (ValueError, TypeError):
                continue

        # ── Stock ──
        stock = None
        stock_val = raw.get("stock", {})
        if isinstance(stock_val, dict):
            try:
                stock = int(stock_val.get("level", 0))
            except (ValueError, TypeError):
                pass
        elif stock_val is not None:
            try:
                stock = int(stock_val)
            except (ValueError, TypeError):
                pass

        # ── Parameters ──
        parameters: List[PartParameter] = []
        for attr in raw.get("attributes") or []:
            if not isinstance(attr, dict):
                continue
            p_name = attr.get("attributeLabel", "") or attr.get("attributeName", "")
            p_value = attr.get("attributeValue", "")
            p_unit = attr.get("attributeUnit", "")
            if p_name and p_value:
                parameters.append(
                    PartParameter(name=p_name, value=p_value, unit=p_unit)
                )

        # ── Min order ──
        min_qty = 1
        try:
            min_qty = int(raw.get("translatedMinimumOrderQuality", 1) or 1)
        except (ValueError, TypeError):
            pass

        # ── Order multiple ──
        order_mult = 1
        try:
            order_mult = int(raw.get("translatedOrderMultiple", 1) or 1)
        except (ValueError, TypeError):
            pass

        # ── Confidence ──
        confidence = 1.0
        if mpn_result.lower().strip() != search_mpn.lower().strip():
            confidence = 0.85  # element14 uses official Farnell catalogue

        return PartData(
            mpn=mpn_result,
            manufacturer=manufacturer,
            description=description,
            name=f"{manufacturer} {mpn_result}" if manufacturer else mpn_result,
            category=category,
            supplier_name="Farnell / element14",
            supplier_sku=sku,
            supplier_url=product_url,
            datasheet_url=datasheet_url,
            image_url=image_url,
            package=package,
            parameters=parameters,
            price_breaks=price_breaks,
            stock_available=stock,
            minimum_order_qty=min_qty,
            order_multiple=order_mult,
            source="element14",
            raw_data=raw,
            confidence=confidence,
        )


# ── Tiny helper ───────────────────────────────────────────────────────────────


def _url_encode(text: str) -> str:
    """Percent-encode a string for use in a URL query parameter value."""
    try:
        from urllib.parse import quote

        return quote(text, safe="")
    except Exception:
        return text
