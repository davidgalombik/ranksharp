"""
Oliver Bonas adapter (oliverbonas.com) — Sitemap + plain HTTP.

Oliver Bonas is a JS SPA, but individual product pages embed complete
JSON-LD Product data in the static HTML response. No Playwright needed.

Product URL pattern in sitemap: /homeware/{slug-with-numeric-id}
Sitemap: https://www.oliverbonas.com/sitemap.xml (7400+ URLs, ~819 products)
"""
import re
import asyncio
import json
import httpx
from bs4 import BeautifulSoup
from typing import Optional, AsyncIterator
from scraper.base_adapter import BaseAdapter, RawProduct

SITEMAP_URL = "https://www.oliverbonas.com/sitemap.xml"

# Product URLs match /homeware/{anything}-{digits}
_PRODUCT_RE = re.compile(r'^https://www\.oliverbonas\.com/homeware/[a-z0-9-]+-\d+$')

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

_BATCH = 8  # concurrent product fetches


class OliverBonasAdapter(BaseAdapter):
    RETAILER_SLUG = "oliver-bonas"

    def __init__(self, rc):
        super().__init__(rc)
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def _get_product_urls(self) -> list[str]:
        """Parse sitemap to find all product URLs."""
        resp = await self._client.get(SITEMAP_URL)
        resp.raise_for_status()
        urls = re.findall(r'<loc>(.*?)</loc>', resp.text)
        products = [u.strip() for u in urls if _PRODUCT_RE.match(u.strip())]
        self.log.info("ob_sitemap_parsed", total_urls=len(urls), products=len(products))
        return products

    async def _fetch_product(self, url: str) -> Optional[RawProduct]:
        """Fetch a product page and extract JSON-LD data."""
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return None
        except Exception as exc:
            self.log.warning("ob_fetch_error", url=url, error=str(exc))
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Parse all JSON-LD blocks — collect Product and BreadcrumbList
        product_data = None
        category: Optional[str] = None

        for s in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(s.string or "")
                blocks = d if isinstance(d, list) else [d]
                for block in blocks:
                    t = block.get("@type", "")
                    if t == "Product" and not product_data:
                        product_data = block
                    elif t == "BreadcrumbList" and not category:
                        # OB breadcrumb: [Homeware(1), <Category>(2), Product Name(3)]
                        # Name is nested under item.name, not at the ListItem level
                        items = block.get("itemListElement", [])
                        items_sorted = sorted(items, key=lambda x: x.get("position", 0))
                        # Skip first (Homeware) and last (product name) → middle item(s)
                        middle = items_sorted[1:-1]
                        if middle:
                            nested = middle[0].get("item", {})
                            category = (nested.get("name") or middle[0].get("name") or "").strip() or None
            except (json.JSONDecodeError, TypeError):
                continue

        if not product_data:
            return None

        name = product_data.get("name", "").strip()
        if not name:
            return None

        # Price
        price: Optional[float] = None
        offers = product_data.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        if raw := offers.get("price"):
            try:
                price = float(str(raw).replace(",", ""))
            except (ValueError, TypeError):
                pass

        # Images — only catalog product images, not logos/CMS assets
        imgs = product_data.get("image", [])
        if isinstance(imgs, str):
            imgs = [imgs]
        img_urls = [i for i in imgs if i and "/static/media/catalog/" in i]

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=product_data.get("sku"),
            sku=product_data.get("sku"),
            description=product_data.get("description"),
            price=price,
            currency="GBP",
            category=category,
            image_urls=img_urls,
            raw_attributes={},
        )

        return None

    async def scrape(self) -> AsyncIterator[RawProduct]:
        await self.before_scrape()
        try:
            product_urls = await self._get_product_urls()

            for i in range(0, len(product_urls), _BATCH):
                batch = product_urls[i:i + _BATCH]
                results = await asyncio.gather(
                    *[self._fetch_product(u) for u in batch],
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, Exception):
                        self.log.warning("ob_parse_error", error=str(result))
                    elif result:
                        yield result
        finally:
            await self.after_scrape()

    # ── Required abstract stubs ──
    async def get_category_urls(self) -> list[str]:
        return []

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None
