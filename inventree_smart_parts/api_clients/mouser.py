"""
Mouser API Client
=================
Integration with Mouser Electronics Search API v2.
Searches parts by MPN and normalizes results to PartData.
"""

import logging
from typing import Optional, Dict, Any, List

from .base import BaseApiClient, PartData, PriceBreak, PartParameter, ApiError

logger = logging.getLogger('inventree_smart_parts.api.mouser')


class MouserClient(BaseApiClient):
    """Client for the Mouser Electronics Search API v2."""

    SOURCE_NAME = 'mouser'
    BASE_URL = 'https://api.mouser.com/api/v2'

    def __init__(self, api_key: str, **kwargs):
        super().__init__(**kwargs)
        self.api_key = api_key

    def search_by_mpn(self, mpn: str) -> Optional[PartData]:
        """
        Search Mouser for a part by MPN.

        Uses the SearchByPartNumber endpoint which gives the most
        precise results for exact MPN lookups.
        """
        if not self.api_key:
            raise ApiError("Mouser API key is not configured")

        url = f"{self.BASE_URL}/search/partnumber"
        params = {'apiKey': self.api_key}

        payload = {
            "SearchByPartRequest": {
                "mouserPartNumber": mpn,
                "records": 10,
                "startingRecord": 0,
                "searchOptions": "None",
            }
        }

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        logger.info(f"[Mouser] Searching for MPN: {mpn}")
        data = self._request('POST', url, headers=headers, json_data=payload, params=params)

        # Parse the response
        parts = data.get('SearchResults', {}).get('Parts', [])
        if not parts:
            logger.info(f"[Mouser] No results found for MPN: {mpn}")
            return None

        # Find the best match – prefer exact MPN match
        best_match = self._find_best_match(parts, mpn)
        if best_match is None:
            best_match = parts[0]

        return self._parse_part(best_match, mpn)

    def _find_best_match(self, parts: List[Dict], mpn: str) -> Optional[Dict]:
        """Find the part with the closest MPN match."""
        mpn_lower = mpn.lower().strip()

        # First pass: exact manufacturer part number match
        for part in parts:
            mfr_pn = part.get('ManufacturerPartNumber', '').lower().strip()
            if mfr_pn == mpn_lower:
                return part

        # Second pass: manufacturer part number contains the search term
        for part in parts:
            mfr_pn = part.get('ManufacturerPartNumber', '').lower().strip()
            if mpn_lower in mfr_pn:
                return part

        return None

    def _parse_part(self, raw: Dict[str, Any], search_mpn: str) -> PartData:
        """Convert raw Mouser API response to PartData."""
        # Parse price breaks
        price_breaks = []
        for pb in raw.get('PriceBreaks', []):
            try:
                qty = int(pb.get('Quantity', 0))
                # Price comes as string like "€1.23" or "$1.23"
                price_str = pb.get('Price', '0')
                currency = pb.get('Currency', 'EUR')
                # Strip currency symbols and whitespace
                price_clean = ''.join(
                    c for c in price_str if c.isdigit() or c in '.,'
                )
                # Handle European comma-as-decimal
                if ',' in price_clean and '.' not in price_clean:
                    price_clean = price_clean.replace(',', '.')
                elif ',' in price_clean and '.' in price_clean:
                    price_clean = price_clean.replace(',', '')

                price = float(price_clean) if price_clean else 0.0

                if qty > 0 and price > 0:
                    price_breaks.append(PriceBreak(
                        quantity=qty,
                        price=price,
                        currency=currency,
                    ))
            except (ValueError, TypeError):
                continue

        # Parse stock
        stock = None
        availability = raw.get('Availability', '')
        if availability:
            stock_str = ''.join(c for c in availability if c.isdigit())
            if stock_str:
                try:
                    stock = int(stock_str)
                except ValueError:
                    pass

        # Parse parameters from product attributes
        parameters = []
        for attr_name in ['ProductAttributes']:
            attrs = raw.get(attr_name, []) or []
            for attr in attrs:
                attr_label = attr.get('AttributeName', '')
                attr_value = attr.get('AttributeValue', '')
                if attr_label and attr_value:
                    parameters.append(PartParameter(
                        name=attr_label,
                        value=attr_value,
                    ))

        # Build the normalized PartData
        mpn_result = raw.get('ManufacturerPartNumber', search_mpn)
        manufacturer = raw.get('Manufacturer', '')
        description = raw.get('Description', '')

        # Category from Mouser
        category = raw.get('Category', '')

        # Datasheet — Mouser uses 'DataSheetUrl'; also guard against '#' placeholders
        datasheet_url = (
            raw.get('DataSheetUrl', '')   # Mouser v2 standard field
            or raw.get('DatasheetUrl', '') # alternate casing seen in some responses
            or ''
        )
        # Mouser sometimes returns '#' when no datasheet is available
        if datasheet_url and not datasheet_url.startswith('http'):
            datasheet_url = ''

        # Image
        image_url = raw.get('ImagePath', '')

        # Mouser part number as SKU
        mouser_pn = raw.get('MouserPartNumber', '')

        # Product URL
        product_url = raw.get('ProductDetailUrl', '')

        # Min order qty
        min_qty = 1
        min_str = raw.get('Min', '')
        if min_str:
            try:
                min_qty = int(min_str)
            except (ValueError, TypeError):
                pass

        # Order multiple
        mult = 1
        mult_str = raw.get('Mult', '')
        if mult_str:
            try:
                mult = int(mult_str)
            except (ValueError, TypeError):
                pass

        # Determine match confidence
        confidence = 1.0
        if mpn_result.lower().strip() != search_mpn.lower().strip():
            confidence = 0.8

        return PartData(
            mpn=mpn_result,
            manufacturer=manufacturer,
            description=description,
            name=f"{manufacturer} {mpn_result}" if manufacturer else mpn_result,
            category=category,
            supplier_name='Mouser',
            supplier_sku=mouser_pn,
            supplier_url=product_url,
            datasheet_url=datasheet_url,
            image_url=image_url,
            parameters=parameters,
            price_breaks=price_breaks,
            stock_available=stock,
            minimum_order_qty=min_qty,
            order_multiple=mult,
            source='mouser',
            raw_data=raw,
            confidence=confidence,
        )

    def test_connection(self) -> Dict[str, Any]:
        """Test Mouser API connectivity by performing a minimal search."""
        try:
            if not self.api_key:
                return {
                    'success': False,
                    'message': 'API key is not configured',
                }

            # Search for a well-known part as a connectivity test
            result = self.search_by_mpn('LM7805')
            if result:
                return {
                    'success': True,
                    'message': f'Connected successfully. Test search returned: {result.mpn}',
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

        except ApiError as e:
            return {
                'success': False,
                'message': f'Connection failed: {str(e)}',
            }
        except Exception as e:
            return {
                'success': False,
                'message': f'Unexpected error: {str(e)}',
            }
