"""
World Market adapter (worldmarket.com — Cost Plus World Market).

World Market uses server-side rendered pages with JSON-LD product data.
Category URLs follow /c/{category}/ pattern.
"""
import json
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CATEGORY_PATHS = [
    "/c/kitchen-storage/",
    "/c/kitchen-food-storage/",
    "/c/storage-organization/",
    "/c/home-decor/",
    "/c/baskets-bins/",
    "/c/candles-holders/",
    "/c/vases-and-bowls/",
]


class WorldMarketAdapter(BaseAdapter):
    RETAILER_SLUG = "world-market"

    def __init__(self, rc):
        super().__init__(rc)
        self._client = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self):
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url: str) -> list[str]:
        urls = []
        for page in range(1, 6):
            params = {"start": (page - 1) * 48, "sz": 48} if page > 1 else {}
            resp = await self._client.get(category_url, params=params)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select(
                "a[href*='/p/'], a.product-tile__name-link, a[class*='product-link']"
            )
            if not links:
                links = soup.select("a[href*='/products/'], a[href*='/product/']")
            if not links:
                break
            added = 0
            for a in links:
                href = a.get("href", "")
                full = href if href.startswith("http") else self.base_url + href
                if full not in urls:
                    urls.append(full)
                    added += 1
            if added == 0:
                break
            if not soup.select_one("a.js-page-next, [class*='pagination-next']:not(.disabled)"):
                break
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        for s in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(s.string or "")
                if isinstance(d, list):
                    d = next((x for x in d if x.get("@type") == "Product"), None)
                if d and d.get("@type") == "Product":
                    offers = d.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price = None
                    if raw := offers.get("price"):
                        try:
                            price = float(str(raw).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    imgs = d.get("image", [])
                    if isinstance(imgs, str):
                        imgs = [imgs]
                    return RawProduct(
                        url=url,
                        name=d.get("name", ""),
                        retailer_slug=self.RETAILER_SLUG,
                        external_id=d.get("sku"),
                        description=d.get("description"),
                        price=price,
                        currency="USD",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        name_el = soup.select_one("h1.product-name, h1[itemprop='name'], h1")
        if not name_el:
            return None
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            currency="USD",
            raw_attributes={},
        )
