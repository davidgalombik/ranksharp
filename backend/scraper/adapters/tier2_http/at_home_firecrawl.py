"""
At Home adapter (athome.com) — Firecrawl /v1/map + Firecrawl product pages.

At Home (Akamai Bot Manager) blocks all plain HTTP requests, including to
product pages and sitemaps. Firecrawl bypasses this on both map and scrape.

Approach:
  1. URL discovery  → Firecrawl /v1/map with per-category search terms
  2. Product detail → Firecrawl /v1/scrape on each product page
                      (extracts H1 name, price, SKU from URL, CDN images)

Cost: 1 map call per category + 1 scrape credit per product page.
"""
import re
import asyncio
from typing import Optional
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct
from config import settings

_PRODUCT_RE = re.compile(r'https://www\.athome\.com/[^/?)\s"]+/[A-Za-z0-9]{6,}\.html')

# Category URL → (display label, search terms for /v1/map)
CATEGORIES: dict[str, tuple[str, str]] = {
    "https://www.athome.com/storage-organization/": (
        "Storage & Organization",
        "storage baskets bins organization shelving",
    ),
    "https://www.athome.com/home-decor/": (
        "Home Decor",
        "home decor vases candles wall art decorative",
    ),
    "https://www.athome.com/holiday/": (
        "Holiday & Seasonal",
        "holiday christmas seasonal halloween thanksgiving",
    ),
}

_MAP_LIMIT = 200

# Large product images from At Home's CDN — match any width, skip tiny thumbnails
_IMG_RE = re.compile(
    r'https://static\.athome\.com/images/w_\d+[,/][^\s")(\\]+\.(?:jpg|jpeg|png|webp)'
)
_PRICE_RE = re.compile(r'\$([0-9,]+(?:\.[0-9]{1,2})?)')
# SKU is the numeric/alphanumeric code just before .html in the product URL
_SKU_RE = re.compile(r'/([A-Za-z0-9]{6,})\.html$')


class AtHomeFirecrawlAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "at-home"
    WAIT_MS = 3000   # At Home product pages need a short wait for price to render

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Use Firecrawl /v1/map to discover At Home product URLs for this category.
        Populates self._cache with minimal stubs so parse_product() can run.
        """
        label, search_term = CATEGORIES.get(
            category_url, ("Home", category_url.rstrip("/").split("/")[-1])
        )
        self.log.info("athome_map_discovering", category=label, search=search_term)

        resp = await self._client.post(
            "https://api.firecrawl.dev/v1/map",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": "https://www.athome.com",
                "search": search_term,
                "limit": _MAP_LIMIT,
            },
        )

        data = resp.json()
        all_links = data.get("links", [])

        # Keep only At Home product URLs (e.g. /black-storage-basket/124385350.html)
        urls = list(dict.fromkeys(
            u.split("?")[0]
            for u in all_links
            if _PRODUCT_RE.match(u)
        ))

        self.log.info("athome_map_complete", category=label, total=len(urls))

        # Populate cache with minimal stubs — parse_product() will enrich them
        for url in urls:
            if url not in self._cache:
                slug = url.rstrip("/").split("/")[-1].replace(".html", "")
                # Strip trailing product code to get human name
                name = re.sub(r'/[A-Za-z0-9]{6,}$', '', url.split("athome.com")[1])
                name = name.strip("/").replace("-", " ").replace("/", " ").title()
                self._cache[url] = RawProduct(
                    url=url,
                    name=name or slug,
                    retailer_slug=self.RETAILER_SLUG,
                    category=label,
                    currency="USD",
                    image_urls=[],
                    raw_attributes={},
                )

        return urls

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """
        Fetch product page via Firecrawl and extract name, price, SKU, images.
        At Home blocks plain HTTP; Firecrawl bypasses Akamai on product pages.
        """
        stub = self._cache.get(product_url)
        if not stub:
            return None

        await asyncio.sleep(0.2)   # light rate limit between product fetches
        md = await self._fetch_markdown(product_url)
        if not md:
            return stub

        # Name — H1 heading
        name = stub.name
        h1_m = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        if h1_m:
            name = h1_m.group(1).strip()

        # Price — first dollar amount in first 6000 chars
        # (products with colour swatches push the price past 4000 chars)
        price: Optional[float] = None
        for m in _PRICE_RE.finditer(md[:6000]):
            try:
                price = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

        # SKU — numeric/alphanumeric code before .html in the URL
        sku: Optional[str] = None
        sku_m = _SKU_RE.search(product_url)
        if sku_m:
            sku = sku_m.group(1)

        # Images — large CDN images from linked thumbnails ([![alt](thumb)](large))
        img_urls = list(dict.fromkeys(_IMG_RE.findall(md)))[:5]

        return RawProduct(
            url=product_url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            sku=sku,
            price=price,
            currency="USD",
            category=stub.category,
            image_urls=img_urls,
            raw_attributes={},
        )

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """Not used — URL discovery handled by get_product_urls() via /v1/map."""
        return []

    async def _polite_delay(self):
        """Delay is handled inside parse_product() itself."""
        pass
