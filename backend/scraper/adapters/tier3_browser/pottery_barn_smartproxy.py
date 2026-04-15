"""
Pottery Barn US adapter — Firecrawl /v1/map (discovery) + Smartproxy (product pages).

Pottery Barn (Williams-Sonoma Inc.) uses Akamai Bot Manager. Their category listing
pages do not render the product grid for any scraping tool (Firecrawl stealth, Smartproxy,
Playwright) — the grid loads via deferred client-side API calls that are not reachable.

Solution:
  1. URL discovery  → Firecrawl /v1/map with per-category search terms
                      (bypasses Akamai, finds product URLs directly)
  2. Product detail → Smartproxy Universal Scraping API on each product page
                      (residential JS rendering bypasses Akamai on product pages)
                      (JSON-LD preferred; DOM fallback)

Requires:
  FIRECRAWL_API_KEY     in .env   (for /v1/map discovery)
  SCRAPING_API_USERNAME in .env   (for product pages)
  SCRAPING_API_PASSWORD in .env   (for product pages)
"""
import re
import asyncio
import httpx
from typing import Optional, AsyncIterator
from bs4 import BeautifulSoup
import json
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct, _BEST_SELLER_KEYWORDS
from config import settings
import structlog

log = structlog.get_logger()

_BATCH_SIZE = 5
_MAP_LIMIT = 50   # per search term call
_MAP_ENDPOINT = "https://api.firecrawl.dev/v1/map"

_PRODUCT_RE = re.compile(r'https://www\.potterybarn\.com/products/[^/?)\s"#]+', re.I)
_SKU_RE = re.compile(r'/([0-9]{6,})(?:\.html)?/?$')
# Price embedded in page JS: "regularPrice":"39.5" or "retailPrice":"79.00"
_PRICE_RE = re.compile(
    r'"(?:regularPrice|retailPrice|listPrice|salePrice)"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?'
)
# Product CDN images — wcm path, excluding cross-sell thumbnails (-c.jpg suffix)
_IMG_RE = re.compile(
    r'https://assets\.pbimgs\.com/pbimgs/[a-z]+/images/dp/wcm/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)',
    re.I,
)

# Category label → list of search terms for /v1/map
# Multiple terms per category to maximise product discovery (each call returns up to 50)
CATEGORIES: dict[str, list[str]] = {
    "Best Sellers": [
        "pottery barn vase candle holder best seller",
        "pottery barn decorative bowl tray sculpture figurine popular",
        "pottery barn canister storage basket organizer top rated",
        "pottery barn pillar taper candlestick lantern hurricane",
    ],
    "Storage & Organization": [
        "pottery barn storage basket bin wire wicker woven",
        "pottery barn shelf organizer rack kitchen pantry cabinet",
        "pottery barn storage box container lid wooden bamboo",
        "pottery barn drawer organizer desk office storage tray",
    ],
    "Vases": [
        "pottery barn vase ceramic glass flower vessel bud",
        "pottery barn vase tall decorative floral centerpiece",
        "pottery barn vase terracotta stoneware earthenware clay",
    ],
    "Candles & Holders": [
        "pottery barn candle candlestick holder pillar taper votives",
        "pottery barn candle fragrance diffuser scented",
        "pottery barn lantern hurricane candle holder glass",
    ],
    "Canisters & Jars": [
        "pottery barn canister jar ceramic kitchen set lid",
        "pottery barn canister storage cookie jar glass",
        "pottery barn canister set stoneware metal copper brass",
    ],
    "Decorative Accessories": [
        "pottery barn decorative accessories bowl tray figurine sculpture",
        "pottery barn home decor accent object tabletop",
        "pottery barn decorative box clock bookend tray catch-all",
    ],
}


class PotteryBarnSmartproxyAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "pottery-barn"
    REQUIRES_SCRAPING_API = True

    def __init__(self, retailer_config: dict):
        super().__init__(retailer_config)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._http_client = httpx.AsyncClient(timeout=60)

    async def after_scrape(self):
        if self._http_client:
            await self._http_client.aclose()

    # ── URL discovery via Firecrawl /v1/map ──────────────────────────────────

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def _map_search(self, search_term: str) -> list[str]:
        """Call Firecrawl /v1/map and return PB product URLs matching our pattern."""
        try:
            resp = await self._http_client.post(
                _MAP_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": "https://www.potterybarn.com",
                    "search": search_term,
                    "limit": _MAP_LIMIT,
                },
            )
            data = resp.json()
            links = data.get("links", [])
            product_urls = []
            seen = set()
            for link in links:
                clean = link.split("?")[0].rstrip("/") + "/"
                m = _PRODUCT_RE.match(clean.rstrip("/"))
                if m and clean not in seen:
                    seen.add(clean)
                    product_urls.append(clean)
            return product_urls
        except Exception as exc:
            self.log.warning("pb_map_error", search=search_term, error=str(exc))
            return []

    async def get_product_urls(self, category_label: str) -> list[str]:
        """Run all search terms for this category and deduplicate results."""
        search_terms = CATEGORIES.get(category_label, [])
        self.log.info("pb_category_discovery", category=category_label,
                      search_terms=len(search_terms))

        seen: set[str] = set()
        urls: list[str] = []

        for term in search_terms:
            found = await self._map_search(term)
            for url in found:
                if url not in seen:
                    seen.add(url)
                    urls.append(url)
            self.log.info("pb_map_term_done", term=term[:40], new=len(found),
                          total=len(urls))
            await asyncio.sleep(0.5)

        self.log.info("pb_category_complete", category=category_label, total=len(urls))
        return urls

    # ── Product page parsing via Smartproxy ──────────────────────────────────

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(
            product_url,
            wait_for_selector="h1, [class*='product-title'], script[type='application/ld+json']",
        )
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(script.string or "")
                # Unwrap @graph arrays
                if isinstance(d, dict) and "@graph" in d:
                    d = d["@graph"]
                # Find Product or ProductGroup
                if isinstance(d, list):
                    d = next(
                        (x for x in d if isinstance(x, dict)
                         and x.get("@type") in ("Product", "ProductGroup")),
                        None,
                    )
                if d and d.get("@type") in ("Product", "ProductGroup"):
                    # For ProductGroup, pull price/sku from first variant
                    if d.get("@type") == "ProductGroup":
                        variants = d.get("hasVariant", [])
                        if variants:
                            d = {**variants[0], "name": d.get("name", ""),
                                 "description": d.get("description", ""),
                                 "image": d.get("image", variants[0].get("image", []))}
                    result = self._from_json_ld(d, product_url, html)
                    if result:
                        return result
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: DOM + raw HTML price/image extraction
        return self._parse_from_dom(soup, product_url, html)

    @staticmethod
    def _extract_price(html: str) -> Optional[float]:
        """Extract price from raw HTML JS — PB embeds regularPrice/retailPrice in page scripts."""
        for m in _PRICE_RE.finditer(html):
            try:
                val = float(m.group(1))
                if val > 0:
                    return val
            except (ValueError, TypeError):
                pass
        return None

    @staticmethod
    def _extract_images(html: str) -> list[str]:
        """
        Extract product CDN images from raw HTML.
        Uses wcm (product detail) path, excludes -c.jpg cross-sell thumbnails.
        """
        all_imgs = _IMG_RE.findall(html)
        seen: set[str] = set()
        product_imgs = []
        for img in all_imgs:
            # Skip cross-sell thumbnails (always end with -c.jpg)
            if img.endswith("-c.jpg") or img.endswith("-c.jpeg"):
                continue
            if img not in seen:
                seen.add(img)
                product_imgs.append(img)
        return product_imgs[:5]

    def _from_json_ld(self, d: dict, url: str, html: str = "") -> Optional[RawProduct]:
        name = (d.get("name") or "").strip()
        if not name:
            return None

        offers = d.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        # Try JSON-LD price first, fall back to raw HTML extraction
        price: Optional[float] = None
        for key in ("price", "lowPrice"):
            raw = offers.get(key)
            if raw is not None:
                try:
                    price = float(str(raw).replace(",", ""))
                    break
                except (ValueError, TypeError):
                    pass
        if price is None and html:
            price = self._extract_price(html)

        # JSON-LD images OR raw HTML wcm images
        images = d.get("image", [])
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, dict):
            images = [images.get("url", "")]
        images = [i for i in images if i and isinstance(i, str)]
        if not images and html:
            images = self._extract_images(html)

        sku = d.get("sku") or d.get("mpn")
        if not sku:
            m = _SKU_RE.search(url)
            if m:
                sku = m.group(1)

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=str(sku) if sku else None,
            sku=str(sku) if sku else None,
            description=(d.get("description") or "").strip(),
            price=price,
            currency=offers.get("priceCurrency", "USD"),
            image_urls=images[:5],
            raw_attributes={},
        )

    def _parse_from_dom(self, soup: BeautifulSoup, url: str, html: str = "") -> Optional[RawProduct]:
        # Try multiple name selectors
        name = ""
        for sel in ["h1", "[class*='product-title']", "[class*='product-name']",
                    "[data-testid*='product-title']", "h2"]:
            el = soup.select_one(sel)
            if el:
                name = el.get_text(strip=True)
                if name:
                    break
        if not name:
            self.log.warning("pb_no_name_found", url=url)
            return None

        # Raw HTML price extraction (price loaded dynamically via JS)
        price: Optional[float] = self._extract_price(html) if html else None

        # Product images from raw HTML
        images = self._extract_images(html) if html else []

        sku: Optional[str] = None
        m = _SKU_RE.search(url)
        if m:
            sku = m.group(1)

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            sku=sku,
            price=price,
            currency="USD",
            image_urls=images,
            raw_attributes={},
        )

    # ── Concurrent batch scrape ───────────────────────────────────────────────

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Override: discover URLs via /v1/map, fetch product pages 5 at a time."""
        await self.before_scrape()
        try:
            category_labels = await self.get_category_urls()
            self.log.info("pb_categories_found", count=len(category_labels))

            for cat_label in category_labels:
                is_best_seller_cat = cat_label == "Best Sellers"
                product_urls = await self.get_product_urls(cat_label)
                self.log.info("pb_products_found", category=cat_label,
                              count=len(product_urls))

                for i in range(0, len(product_urls), _BATCH_SIZE):
                    batch = product_urls[i : i + _BATCH_SIZE]
                    results = await asyncio.gather(
                        *[self.parse_product(u) for u in batch],
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            self.log.warning("pb_parse_error", error=str(result))
                        elif result:
                            if not result.category:
                                result.category = cat_label
                            if is_best_seller_cat:
                                result.is_best_seller = True
                            yield result
        finally:
            await self.after_scrape()
