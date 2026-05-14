"""
LCSC API Client
===============
Integration with LCSC Electronics search API.
LCSC does not require an API key – it uses public search endpoints.
"""

import logging
from typing import Optional, Dict, Any, List

from .base import BaseApiClient, PartData, PriceBreak, PartParameter, ApiError

logger = logging.getLogger('inventree_smart_parts.api.lcsc')


class LCSCClient(BaseApiClient):
    """Client for the LCSC Electronics search API (unofficial, public)."""

    SOURCE_NAME = 'lcsc'
    BASE_URL = 'https://wmsc.lcsc.com/ftps/wm'

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._min_request_interval = 1.0  # Be respectful – LCSC has no official API

    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """Search LCSC for a part by MPN."""
        url = f"{self.BASE_URL}/product/search"

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'User-Agent': 'InvenTree-SmartParts/1.0',
        }

        payload = {
            'currentPage': 1,
            'pageSize': 10,
            'keyword': mpn,
        }

        logger.info(f"[LCSC] Searching for MPN: {mpn}")

        try:
            data = self._request('POST', url, headers=headers, json_data=payload)
        except Exception as e:
            logger.warning(f"[LCSC] Search failed: {e}")
            return None

        # Parse response structure
        result = data.get('result', {})
        if isinstance(result, dict):
            products = result.get('dataList', [])
        else:
            products = []

        if not products:
            logger.info(f"[LCSC] No results found for MPN: {mpn}")
            return None

        # Find best match
        best = self._find_best_match(products, mpn)
        if best is None:
            best = products[0]

        return self._parse_part(best, mpn)

    def _find_best_match(self, products: List[Dict], mpn: str) -> Optional[Dict]:
        """Find product with closest MPN match."""
        mpn_lower = mpn.lower().strip()

        # Try exact MPN match
        for p in products:
            mfr_pn = (p.get('productModel', '') or '').lower().strip()
            if mfr_pn == mpn_lower:
                return p

        # Try contains
        for p in products:
            mfr_pn = (p.get('productModel', '') or '').lower().strip()
            if mpn_lower in mfr_pn:
                return p

        return None

    def _parse_part(self, raw: Dict[str, Any], search_mpn: str) -> PartData:
        """Convert LCSC product data to PartData."""
        # Price breaks
        price_breaks = []
        price_list = raw.get('productPriceList', []) or []
        for pb in price_list:
            try:
                qty = int(pb.get('ladder', 0))
                price = float(pb.get('usdPrice', 0) or pb.get('productPrice', 0))
                if qty > 0 and price > 0:
                    price_breaks.append(PriceBreak(
                        quantity=qty,
                        price=price,
                        currency='USD',
                    ))
            except (ValueError, TypeError):
                continue

        # Parameters from attributes
        parameters = []
        param_list = raw.get('paramVOList', []) or []
        for param in param_list:
            p_name = param.get('paramNameEn', '') or param.get('paramName', '')
            p_value = param.get('paramValueEn', '') or param.get('paramValue', '')
            if p_name and p_value:
                parameters.append(PartParameter(
                    name=p_name,
                    value=p_value,
                ))

        # Stock
        stock = None
        stock_val = raw.get('stockNumber', None)
        if stock_val is not None:
            try:
                stock = int(stock_val)
            except (ValueError, TypeError):
                pass

        # Category
        category_parts = []
        cat1 = raw.get('parentCatalogName', '')
        cat2 = raw.get('catalogName', '')
        if cat1:
            category_parts.append(cat1)
        if cat2:
            category_parts.append(cat2)
        category = ' > '.join(category_parts) if category_parts else ''

        # Core fields
        mpn_result = raw.get('productModel', search_mpn) or search_mpn
        manufacturer = raw.get('brandNameEn', '') or raw.get('brandName', '')
        description = raw.get('productDescEn', '') or raw.get('productDesc', '')
        package = raw.get('encapStandard', '') or raw.get('packageName', '')

        # URLs
        lcsc_code = raw.get('productCode', '')
        product_url = f"https://www.lcsc.com/product-detail/{lcsc_code}.html" if lcsc_code else ''

        # Datasheet
        datasheet_url = raw.get('pdfUrl', '') or ''
        if datasheet_url and not datasheet_url.startswith('http'):
            datasheet_url = f"https://datasheet.lcsc.com{datasheet_url}"

        # Image
        image_url = raw.get('productImageUrl', '') or ''
        if image_url and not image_url.startswith('http'):
            image_url = f"https:{image_url}"

        # Min order
        min_qty = 1
        min_val = raw.get('minBuyNumber', None)
        if min_val:
            try:
                min_qty = int(min_val)
            except (ValueError, TypeError):
                pass

        # Confidence
        confidence = 1.0
        if mpn_result.lower().strip() != search_mpn.lower().strip():
            confidence = 0.7  # LCSC unofficial API – slightly lower base confidence

        return PartData(
            mpn=mpn_result,
            manufacturer=manufacturer,
            description=description,
            name=f"{manufacturer} {mpn_result}" if manufacturer else mpn_result,
            category=category,
            supplier_name='LCSC',
            supplier_sku=lcsc_code,
            supplier_url=product_url,
            datasheet_url=datasheet_url,
            image_url=image_url,
            package=package,
            parameters=parameters,
            price_breaks=price_breaks,
            stock_available=stock,
            minimum_order_qty=min_qty,
            source='lcsc',
            raw_data=raw,
            confidence=confidence,
        )

    def test_connection(self) -> Dict[str, Any]:
        """Test LCSC API connectivity with a known part search."""
        try:
            result = self.search_by_mpn('LM7805')
            if result:
                return {
                    'success': True,
                    'message': f'Connected. Test search found: {result.mpn}',
                    'details': {
                        'test_mpn': result.mpn,
                        'manufacturer': result.manufacturer,
                    },
                }
            else:
                return {
                    'success': True,
                    'message': 'Connected, but test search returned no results.',
                }
        except Exception as e:
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
            }
