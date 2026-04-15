"""
West Elm adapter — Firecrawl stealth scrape for both category listing and product pages.

West Elm (URBN Group) blocks plain HTTP and standard Firecrawl. Stealth proxy
is required throughout. /v1/map returns too few product URLs to be useful.

Approach:
  1. URL discovery  → Firecrawl stealth scrape of category listing pages
                      (extracts product links from rendered HTML/markdown)
  2. Product detail → Firecrawl stealth scrape of each product page
                      (H1 name, price near H1, SKU from URL, weimgs CDN images)

Product URL pattern: https://www.westelm.com/products/[slug]-[id]/
Price format: '$ 48' or '$48 – $96' (space after $, range for multi-size)
Image CDN: assets.weimgs.com
"""
import re
import asyncio
from typing import Optional, AsyncIterator
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct, _BEST_SELLER_KEYWORDS
from config import settings

_PRODUCT_RE = re.compile(r'https://www\.westelm\.com/products/[^/?)\s"#]+/')
_SKU_RE = re.compile(r'-([a-z]\d{3,})/?$', re.I)
# Price: '$ 48' or '$48' — pick first number after $ near H1
_PRICE_RE = re.compile(r'\$\s*([0-9,]+(?:\.[0-9]{1,2})?)')
# Product carousel images appear just before the H1
_IMG_RE = re.compile(
    r'https://assets\.weimgs\.com/weimgs/(?:ab|rk)/images/(?:wcm|rk)/[^\s")(\\]+\.(?:jpg|jpeg|webp|png)'
)

_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_BATCH_SIZE = 5

# Category listing pages → (label, url)
CATEGORIES: dict[str, str] = {
    "https://www.westelm.com/shop/dining-kitchen/all-dinnerware-collections/": "Dinnerware",
    "https://www.westelm.com/shop/dining-kitchen/serveware/": "Serveware",
    "https://www.westelm.com/shop/dining-kitchen/bar-and-bar-storage/": "Bar & Drinkware",
    "https://www.westelm.com/shop/home-decor/decorative-accessories/": "Decorative Accessories",
    "https://www.westelm.com/shop/home-decor/candles-home-fragrance/": "Candles & Fragrance",
    "https://www.westelm.com/shop/storage-organization/": "Storage & Organization",
}


class WestElmFirecrawlAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "west-elm"
    WAIT_MS = 5000   # Category pages need longer for product grid to render

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def _stealth_scrape(self, url: str, wait_ms: int = None) -> str:
        """Firecrawl scrape with stealth proxy. Returns markdown or empty string."""
        try:
            resp = await self._client.post(
                _FIRECRAWL_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": url,
                    "formats": ["markdown", "links"],
                    "waitFor": wait_ms or self.WAIT_MS,
                    "timeout": 60000,
                    "proxy": "stealth",
                },
            )
            data = resp.json()
            if not data.get("success"):
                self.log.warning("westelm_scrape_failed", url=url,
                                 error=str(data.get("error", ""))[:100])
                return "", []
            page_data = data.get("data", {})
            return page_data.get("markdown", ""), page_data.get("links", [])
        except Exception as exc:
            self.log.warning("westelm_scrape_exception", url=url, error=str(exc))
            return "", []

    async def get_product_urls(self, category_url: str) -> list[str]:
        """Stealth scrape category listing page to extract product URLs."""
        label = CATEGORIES.get(category_url, category_url.split("/")[-2])
        self.log.info("westelm_category_scraping", category=label, url=category_url)

        md, links = await self._stealth_scrape(category_url)
        if not md:
            return []

        # Collect product URLs from both the links array and markdown hrefs
        seen = set()
        urls = []
        for u in links:
            clean = u.split("?")[0].rstrip("/") + "/"
            if _PRODUCT_RE.match(clean) and clean not in seen:
                seen.add(clean)
                urls.append(clean)

        # Also scan markdown for any product URLs not in links array
        for u in _PRODUCT_RE.findall(md):
            clean = u.split("?")[0].rstrip("/") + "/"
            if clean not in seen:
                seen.add(clean)
                urls.append(clean)

        self.log.info("westelm_category_complete", category=label, total=len(urls))

        for url in urls:
            if url not in self._cache:
                # Slug name from URL as fallback
                slug = url.rstrip("/").split("/")[-1]
                slug = re.sub(r'-[a-z]\d{3,}$', '', slug, flags=re.I)
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
        """Stealth scrape product page and extract name, price, SKU, images."""
        stub = self._cache.get(product_url)
        if not stub:
            return None

        md, _ = await self._stealth_scrape(product_url, wait_ms=3000)
        if not md or len(md) < 200:
            return stub

        # Name — H1
        name = stub.name
        h1_m = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        if h1_m:
            name = h1_m.group(1).strip()

        # Price — first $ amount after the H1
        price: Optional[float] = None
        h1_idx = md.find(f"# {name}") if name else -1
        search_start = h1_idx if h1_idx >= 0 else 0
        for m in _PRICE_RE.finditer(md[search_start:search_start + 3000]):
            try:
                price = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

        # SKU — letter+digits at end of URL slug (e.g. d17407, e3427)
        sku: Optional[str] = None
        sku_m = _SKU_RE.search(product_url.rstrip("/"))
        if sku_m:
            sku = sku_m.group(1)

        # Images — carousel images appear just BEFORE the H1 with -f.jpg suffix.
        # Searching only the 800 chars before the H1 avoids cross-sell images.
        h1_idx = md.find(f"# {name}") if name else len(md)
        if h1_idx < 0:
            h1_idx = len(md)
        pre_h1 = md[max(0, h1_idx - 1500):h1_idx]
        product_imgs = list(dict.fromkeys(_IMG_RE.findall(pre_h1)))[:5]

        return RawProduct(
            url=product_url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            sku=sku,
            price=price,
            currency="USD",
            category=stub.category,
            image_urls=product_imgs,
            raw_attributes={},
        )

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Concurrent batch scrape — 5 product pages at a time."""
        await self.before_scrape()
        try:
            category_urls = await self.get_category_urls()
            self.log.info("categories_found", count=len(category_urls))

            for cat_url in category_urls:
                is_best_seller_cat = any(kw in cat_url.lower() for kw in _BEST_SELLER_KEYWORDS)
                product_urls = await self.get_product_urls(cat_url)
                self.log.info("products_found", category=cat_url, count=len(product_urls),
                              best_seller_cat=is_best_seller_cat)

                for i in range(0, len(product_urls), _BATCH_SIZE):
                    batch = product_urls[i:i + _BATCH_SIZE]
                    results = await asyncio.gather(
                        *[self.parse_product(url) for url in batch],
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            self.log.warning("parse_error", error=str(result))
                        elif result:
                            if is_best_seller_cat:
                                result.is_best_seller = True
                            yield result
        finally:
            await self.after_scrape()

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """Not used."""
        return []

    async def _polite_delay(self):
        """Delay handled inside scrape() batching."""
        pass
