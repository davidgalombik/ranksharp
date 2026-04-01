"""Etsy adapter — uses the official Etsy Open API v3."""
import httpx
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings
from tenacity import retry, stop_after_attempt, wait_exponential

ETSY_API = "https://openapi.etsy.com/v3/application"

# Etsy taxonomy IDs for our target categories
TAXONOMY_IDS = {
    "home_storage": 891,   # Home & Living > Storage & Organisation
    "home_decor": 68,      # Home & Living > Home Décor
    "kitchen_storage": 904,
    "candles": 1069,
}


class EtsyAdapter(BaseAdapter):
    RETAILER_SLUG = "etsy"

    def __init__(self, retailer_config: dict):
        super().__init__(retailer_config)
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers={
                "x-api-key": settings.etsy_keystring,
                "Accept": "application/json",
            },
            timeout=30,
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self) -> list[str]:
        """Return Etsy taxonomy IDs as synthetic URLs."""
        return [f"etsy-taxonomy://{tid}" for tid in TAXONOMY_IDS.values()]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_product_urls(self, category_url: str) -> list[str]:
        taxonomy_id = int(category_url.split("://")[1])
        listing_ids = []
        offset = 0
        limit = 100

        while True:
            resp = await self._client.get(
                f"{ETSY_API}/listings/active",
                params={
                    "taxonomy_id": taxonomy_id,
                    "limit": limit,
                    "offset": offset,
                    "sort_on": "score",
                    "sort_order": "desc",
                },
            )
            resp.raise_for_status()
            data = resp.json()
            results = data.get("results", [])
            if not results:
                break
            listing_ids.extend([f"etsy-listing://{r['listing_id']}" for r in results])
            offset += limit
            if offset >= data.get("count", 0) or offset >= 500:  # cap at 500
                break

        return listing_ids

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        listing_id = product_url.split("://")[1]

        resp = await self._client.get(
            f"{ETSY_API}/listings/{listing_id}",
            params={"includes": "Images,Shop,MainImage"},
        )
        if resp.status_code in (404, 410):
            return None
        resp.raise_for_status()
        d = resp.json()

        images = [img["url_fullxfull"] for img in d.get("images", [])]
        price = float(d.get("price", {}).get("amount", 0)) / float(
            d.get("price", {}).get("divisor", 100)
        )

        return RawProduct(
            url=d.get("url", f"https://www.etsy.com/listing/{listing_id}"),
            name=d.get("title", ""),
            retailer_slug=self.RETAILER_SLUG,
            external_id=str(listing_id),
            description=d.get("description", ""),
            price=price,
            currency=d.get("price", {}).get("currency_code", "USD"),
            category=d.get("taxonomy_path", [""])[0] if d.get("taxonomy_path") else None,
            subcategory=d.get("taxonomy_path", [None, None])[-1],
            brand=d.get("shop", {}).get("shop_name"),
            image_urls=images,
            raw_attributes={
                "tags": d.get("tags", []),
                "materials": d.get("materials", []),
                "style": d.get("style", []),
                "views": d.get("views"),
                "num_favorers": d.get("num_favorers"),
            },
        )
