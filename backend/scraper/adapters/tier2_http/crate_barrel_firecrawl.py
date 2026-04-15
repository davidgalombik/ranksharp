"""
Crate & Barrel adapter — Firecrawl /v1/map + stealth scrape on product pages.

Crate & Barrel uses Akamai Bot Manager which blocks plain HTTP and standard
Firecrawl scrapes. Stealth proxy is required for product pages.

Approach:
  1. URL discovery  → Firecrawl /v1/map with per-category search terms
  2. Product detail → Firecrawl /v1/scrape with stealth proxy
                      (extracts H1, price, SKU from URL, scene7 CDN images)

Product URL pattern: https://www.crateandbarrel.com/[slug]/s[digits]
Image CDN: cb.scene7.com — strip preset param to get full-size image
"""
import re
import asyncio
from typing import Optional, AsyncIterator
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct, _BEST_SELLER_KEYWORDS
from config import settings

_BATCH_SIZE = 5   # concurrent stealth proxy requests per batch

_PRODUCT_RE = re.compile(r'https://www\.crateandbarrel\.com/[^/?)\s"]+/s\d+')
_SKU_RE = re.compile(r'/s(\d+)$')
_PRICE_RE = re.compile(r'\$([0-9,]+(?:\.[0-9]{1,2})?)')

# Product images: cb.scene7.com URLs — capture base URL, normalise to zoom preset
_IMG_RE = re.compile(
    r'(https://cb\.scene7\.com/is/image/Crate/[A-Za-z0-9_\-]+)(?:\?[^\s")<>\\]*)?'
)

# Scene7 image ID prefixes that indicate site-wide promo/navigation assets — not product photos.
# C&B naming convention: desktop promo assets start with cb_d*, CB_d*, CA_d*,
# plus known shared assets (Vertical_Line_*, cbhcc_*, numeric dates like 04042022_*).
_PROMO_IMG_RE = re.compile(
    r'^(?:cb_d|CB_d|CA_d|Vertical_Line|cbhcc_|[0-9])',
    re.IGNORECASE,
)

_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_MAP_LIMIT = 200

# Category URL → (display label, search terms for /v1/map)
CATEGORIES: dict[str, tuple[str, str]] = {
    "https://www.crateandbarrel.com/dining/dinnerware/1": (
        "Dinnerware",
        "dinnerware plates bowls mugs sets",
    ),
    "https://www.crateandbarrel.com/dining/serveware/1": (
        "Serveware",
        "serveware platters serving bowls dishes",
    ),
    "https://www.crateandbarrel.com/food-and-kitchen/cookware/1": (
        "Cookware",
        "cookware pots pans skillets dutch oven",
    ),
    "https://www.crateandbarrel.com/dining/bar-and-drinkware/1": (
        "Bar & Drinkware",
        "bar drinkware glasses wine cocktail",
    ),
    "https://www.crateandbarrel.com/home-decor/decorative-accessories/1": (
        "Decorative Accessories",
        "decorative accessories vases bowls sculptures",
    ),
    "https://www.crateandbarrel.com/home-decor/candles-and-holders/1": (
        "Candles",
        "candles holders candlesticks lanterns",
    ),
    "https://www.crateandbarrel.com/kitchen/kitchen-storage-and-organization/1": (
        "Storage & Organization",
        "storage organization canisters kitchen containers",
    ),
}


class CrateBarrelFirecrawlAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "crate-and-barrel"
    WAIT_MS = 2500   # Stealth proxy pages — 2.5s is sufficient

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def get_product_urls(self, category_url: str) -> list[str]:
        """Use Firecrawl /v1/map to discover C&B product URLs for this category."""
        label, search_term = CATEGORIES.get(
            category_url, ("Home", category_url.rstrip("/").split("/")[-1])
        )
        self.log.info("cnb_map_discovering", category=label, search=search_term)

        resp = await self._client.post(
            "https://api.firecrawl.dev/v1/map",
            headers={
                "Authorization": f"Bearer {settings.firecrawl_api_key}",
                "Content-Type": "application/json",
            },
            json={
                "url": "https://www.crateandbarrel.com",
                "search": search_term,
                "limit": _MAP_LIMIT,
            },
        )

        data = resp.json()
        all_links = data.get("links", [])
        urls = list(dict.fromkeys(
            u.split("?")[0]
            for u in all_links
            if _PRODUCT_RE.match(u)
        ))

        self.log.info("cnb_map_complete", category=label, total=len(urls))

        for url in urls:
            if url not in self._cache:
                slug = url.rstrip("/").split("/")[-2]
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

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """
        Override base scrape() to process product pages in concurrent batches.
        Each stealth proxy call takes ~3-5s; batching _BATCH_SIZE at a time
        gives a ~5x speedup vs sequential.
        """
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

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """
        Fetch C&B product page via Firecrawl with stealth proxy.
        Akamai blocks standard scrapes; stealth proxy bypasses it.
        """
        stub = self._cache.get(product_url)
        if not stub:
            return None

        # Use stealth proxy — required to bypass Akamai on C&B product pages
        try:
            resp = await self._client.post(
                _FIRECRAWL_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": product_url,
                    "formats": ["markdown"],
                    "waitFor": self.WAIT_MS,
                    "timeout": 60000,
                    "proxy": "stealth",
                },
            )
            data = resp.json()
            if not data.get("success"):
                self.log.warning("cnb_scrape_failed", url=product_url,
                                 error=str(data.get("error", ""))[:100])
                return stub
            md = data.get("data", {}).get("markdown", "")
        except Exception as exc:
            self.log.warning("cnb_scrape_exception", url=product_url, error=str(exc))
            return stub

        if not md or len(md) < 200:
            return stub

        # Name — H1 heading
        name = stub.name
        h1_m = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
        if h1_m:
            name = h1_m.group(1).strip()

        # Price — first dollar amount in first 6000 chars
        price: Optional[float] = None
        for m in _PRICE_RE.finditer(md[:6000]):
            try:
                price = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                pass

        # SKU — digits after /s in URL
        sku: Optional[str] = None
        sku_m = _SKU_RE.search(product_url)
        if sku_m:
            sku = sku_m.group(1)

        # Images — scan markdown for scene7 product image URLs.
        # Promo/nav assets (CB_d*, cb_d*, Vertical_Line_*, etc.) always appear
        # first in the markdown; filter them out to reach actual product images.
        raw_imgs = _IMG_RE.findall(md)
        img_urls = list(dict.fromkeys(
            f"{base}?$web_pdp_main_carousel_zoom$"
            for base in raw_imgs
            if not _PROMO_IMG_RE.match(base.rsplit("/", 1)[-1])
        ))[:5]

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
        """Not used — URL discovery handled via /v1/map."""
        return []

    async def _polite_delay(self):
        """Delay handled inside parse_product()."""
        pass
