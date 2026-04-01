"""IKEA adapter — uses IKEA's unofficial internal JSON API."""
import httpx
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential


IKEA_API = "https://www.ikea.com/api"

CATEGORY_MAP = {
    "storage-organisation": {
        "us": "10925",
        "au": "10925",
    },
    "decoration": {
        "us": "10714",
        "au": "10714",
    },
}


class IkeaAdapter(BaseAdapter):
    RETAILER_SLUG = "ikea"

    def __init__(self, retailer_config: dict):
        super().__init__(retailer_config)
        # Derive locale from base_url: .../us/en → "us/en"
        parts = self.base_url.rstrip("/").split("/")
        self.country = parts[-2]  # e.g. "us" or "au"
        self.lang = parts[-1]     # e.g. "en"
        self.locale = f"{self.country}/{self.lang}"
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers={"Accept": "application/json"},
            timeout=30,
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self) -> list[str]:
        """Return synthetic category identifiers for IKEA (not real URLs)."""
        urls = []
        for cat_key in self.categories.values():
            cat_id = CATEGORY_MAP.get(cat_key, {}).get(self.country)
            if cat_id:
                urls.append(f"ikea://{self.country}/{cat_id}")
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Fetch product list from IKEA's product list API.
        category_url is a synthetic ikea:// URI with the category ID.
        """
        _, cat_id = category_url.split("/", 2)[-1].split("/")
        product_ids = []
        start = 0
        limit = 24

        while True:
            url = (
                f"https://www.ikea.com/{self.locale}/cat/"
                f"?productListFilters=&start={start}&end={start + limit}&type=range"
            )
            # IKEA's actual product list endpoint
            api_url = (
                f"https://sik.search.blue.cdtapps.com/{self.country}/{self.lang}"
                f"/product-list-page/more-products"
                f"?category={cat_id}&start={start}&end={start + limit}&c=listingpage&v=20220805"
            )
            resp = await self._client.get(api_url)
            resp.raise_for_status()
            data = resp.json()

            products = data.get("moreProducts", {}).get("productWindow", [])
            if not products:
                break

            for p in products:
                pid = p.get("id") or p.get("itemNo")
                if pid:
                    product_ids.append(f"ikea-product://{self.country}/{pid}")

            start += limit
            if start >= data.get("moreProducts", {}).get("totalCount", 0):
                break

        return product_ids

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """Fetch individual product from IKEA's pip API."""
        _, path = product_url.split("://", 1)
        country, item_no = path.split("/", 1)

        api_url = (
            f"https://www.ikea.com/{self.locale}/products/{item_no[:3]}/{item_no}"
            f"/{item_no}.json"
        )
        resp = await self._client.get(api_url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        d = resp.json()

        name = d.get("name", "") + " " + d.get("typeName", "")
        price_info = d.get("price", {})
        price = price_info.get("currentPrice", {}).get("value")
        currency = price_info.get("currentPrice", {}).get("currency", "USD")

        images = [
            img.get("url", "") for img in d.get("media", [])
            if img.get("type") == "IMAGE"
        ]

        return RawProduct(
            url=f"https://www.ikea.com/{self.locale}/p/{item_no}/",
            name=name.strip(),
            retailer_slug=self.RETAILER_SLUG,
            external_id=item_no,
            sku=d.get("partNumber"),
            description=d.get("description"),
            price=price,
            currency=currency,
            category=d.get("categoryPath", [{}])[-1].get("name"),
            image_urls=images,
            raw_attributes={
                "measurements": d.get("measurements"),
                "materials": d.get("materials", []),
                "care_instructions": d.get("careInstructions"),
                "color": d.get("colors", []),
            },
        )
