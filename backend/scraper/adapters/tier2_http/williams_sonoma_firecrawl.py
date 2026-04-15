"""
Williams-Sonoma adapter (williams-sonoma.com) — Firecrawl-powered.

WS category listing pages are bot-detected (serve identical fallback content
regardless of URL). Individual product pages work fine with Firecrawl.

Approach:
  1. URL discovery  → Firecrawl /v1/map with per-category search terms
                      (map API bypasses the category-page bot detection)
  2. Product detail → Firecrawl /v1/scrape on each product page
                      (price, SKU, description, images)

Cost: 1 map call per category + 1 scrape credit per product page.
"""
import re
import asyncio
from typing import Optional
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct
from config import settings
import httpx

# Category URL → (display label, search terms for /v1/map)
CATEGORIES: dict[str, tuple[str, str]] = {
    "https://www.williams-sonoma.com/shop/entertaining/serveware/": (
        "Serveware",
        "serveware platters bowls serving platter",
    ),
    "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/": (
        "Storage Containers",
        "food storage containers pantry organizers canisters",
    ),
    "https://www.williams-sonoma.com/shop/entertaining/tabletop/": (
        "Tabletop",
        "dinnerware plates bowls mugs tablescape",
    ),
    "https://www.williams-sonoma.com/shop/cookware/": (
        "Cookware",
        "cookware pots pans skillets dutch oven",
    ),
    "https://www.williams-sonoma.com/shop/entertaining/decorative-accessories/": (
        "Decorative Accessories",
        "decorative accessories vases candles home decor",
    ),
}

# Product page: price
_PRICE_RE = re.compile(r'\$([0-9,]+(?:\.[0-9]{1,2})?)')
# Product page: SKU
_SKU_RE = re.compile(r'SKU[:\s]+([A-Za-z0-9\-]+)', re.I)
# Product image URLs (wsimgs CDN, not ecm editorial)
_PROD_IMG_RE = re.compile(
    r'https://assets\.wsimgs\.com/wsimgs/(?:ab|rk)/images/dp/(?:wcm|ab|rk)/[^\s)"]+\.(?:jpg|jpeg|webp|png)'
)

_MAP_LIMIT = 200   # max product URLs to discover per category


class WilliamsSonomaFirecrawlAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "williams-sonoma"
    WAIT_MS = 2000   # product pages are server-rendered, no need for long wait

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Use Firecrawl /v1/map to discover product URLs for this category.
        Populates self._cache with minimal stubs so parse_product() can run.
        """
        label, search_term = CATEGORIES.get(
            category_url, ("Home", category_url.split("/")[-2])
        )
        self.log.info("ws_map_discovering", category=label, search=search_term)

        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://api.firecrawl.dev/v1/map",
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": "https://www.williams-sonoma.com",
                    "search": search_term,
                    "limit": _MAP_LIMIT,
                },
            )

        data = resp.json()
        all_links = data.get("links", [])

        # Keep only clean product URLs (no query strings, no duplicates)
        urls = list(dict.fromkeys(
            u.split("?")[0]
            for u in all_links
            if "/products/" in u
        ))

        self.log.info("ws_map_complete", category=label, total=len(urls))

        # Populate cache with minimal stubs — parse_product() will enrich them
        for url in urls:
            if url not in self._cache:
                slug = url.rstrip("/").split("/")[-1]
                name = slug.replace("-", " ").title()
                self._cache[url] = RawProduct(
                    url=url,
                    name=name,
                    retailer_slug=self.RETAILER_SLUG,
                    category=label,
                    currency="USD",
                    image_urls=[],
                    raw_attributes={},
                )

        return urls

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """
        Fetch the product page via Firecrawl to extract price, SKU,
        description, and images. Falls back to stub if fetch fails.
        """
        stub = self._cache.get(product_url)
        if not stub:
            return None

        await asyncio.sleep(0.2)   # light rate limit between product fetches
        md = await self._fetch_markdown(product_url)
        if not md:
            return stub

        # Price — take the lowest price in the first 3000 chars
        prices = []
        for m in _PRICE_RE.finditer(md[:3000]):
            try:
                prices.append(float(m.group(1).replace(",", "")))
            except ValueError:
                pass
        price = min(prices) if prices else None

        # SKU
        sku_m = _SKU_RE.search(md)
        sku = sku_m.group(1) if sku_m else None

        # Name — H1 heading takes priority over the URL-derived slug
        name = stub.name
        h1_m = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        if h1_m:
            name = h1_m.group(1).strip()

        # Description
        desc = _extract_description(md)

        # Product images (CDN product images, not editorial)
        img_urls = list(dict.fromkeys(
            u.split("?")[0] for u in _PROD_IMG_RE.findall(md)
        ))[:5]

        return RawProduct(
            url=product_url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            sku=sku,
            description=desc,
            price=price,
            currency="USD",
            category=stub.category,
            image_urls=img_urls,
            raw_attributes={},
        )

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """Not used — URL discovery is handled by get_product_urls() via /v1/map."""
        return []

    async def _polite_delay(self):
        """Delay is handled inside parse_product() itself."""
        pass


def _extract_description(md: str) -> Optional[str]:
    """Extract first meaningful paragraph from WS product page markdown."""
    lines = md.splitlines()
    in_desc = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("##") or "Key Features" in stripped:
            in_desc = True
            continue
        if in_desc and len(stripped) > 60 and not stripped.startswith("!") and not stripped.startswith("["):
            return stripped[:500]
    return None
