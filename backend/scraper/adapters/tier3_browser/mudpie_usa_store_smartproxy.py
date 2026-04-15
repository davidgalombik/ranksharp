"""
Mud Pie USA Store adapter — SmartProxy Universal Scraping API.

mudpieusastore.shop serves a "Coming Soon" placeholder to all bot/automated
requests. SmartProxy's residential JS rendering bypasses this detection.

Strategy:
  1. Category pages (/shop/{category}/) → SmartProxy → extract product URLs
     with pagination support
  2. Product pages (/product/{slug}) → SmartProxy → JSON-LD preferred,
     DOM fallback
  3. "Coming Soon" guard on every page fetch — retries once if hit

Requires:
  SCRAPING_API_USERNAME  in .env
  SCRAPING_API_PASSWORD  in .env
"""
import re
import asyncio
import json
from typing import Optional, AsyncIterator
from bs4 import BeautifulSoup
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct
import structlog

log = structlog.get_logger()

_BASE = "https://mudpieusastore.shop"
_BATCH_SIZE = 5

# Match product links — both relative (/product/...) and absolute (https://mudpieusastore.shop/product/...)
_PRODUCT_HREF_RE = re.compile(
    r'(?:https://mudpieusastore\.shop)?/product/[^"\'?\s#]+', re.I
)

# Extract ID from product URL slug (8–14 char alphanumeric suffix after last dash)
_ASIN_RE = re.compile(r'-([A-Z0-9]{8,14})(?:[/?#].*)?$')

# Only scrape categories relevant to home decor trend tracking
CATEGORIES: dict[str, str] = {
    "Home Decor": "/shop/home-decor/",
    "Kitchen":    "/shop/kitchen/",
}

# URL slug fragments that indicate non-home-decor products — skip these
_BLOCKED_SLUG_FRAGMENTS = (
    "/mud-pie-baby",
    "/mud-pie-toddler",
    "/mud-pie-womens-clothing",
    "/mud-pie-dresses-women",
    "/mud-pie-clothing",
)


class MudPieUSAStoreAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "mudpie-usa-store"
    REQUIRES_SCRAPING_API = True

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _is_blocked(self, html: str) -> bool:
        """Return True if SmartProxy returned the Coming Soon placeholder."""
        text = html[:500].lower()
        return "comming soon" in text or "coming soon" in text

    @staticmethod
    def _parse_price(text: str) -> Optional[float]:
        m = re.search(r'\$\s*([\d,]+\.?\d*)', text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    # ── Category → product URL discovery ────────────────────────────────────

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def get_product_urls(self, category_label: str) -> list[str]:
        cat_path = CATEGORIES.get(category_label, "")
        if not cat_path:
            return []

        seen: set[str] = set()
        urls: list[str] = []
        page = 1

        while True:
            page_url = f"{_BASE}{cat_path}" + (f"?page={page}" if page > 1 else "")
            self.log.info("mudpie_usa_category_fetch", category=category_label,
                          page=page, url=page_url)

            html = await self._fetch_rendered(
                page_url,
                wait_for_selector="a[href*='/product/']",
            )

            if not html or self._is_blocked(html):
                self.log.warning("mudpie_usa_category_blocked",
                                 category=category_label, page=page)
                break

            soup = BeautifulSoup(html, "lxml")
            found_on_page = 0

            for a in soup.find_all("a", href=True):
                href = a["href"]
                # Match relative (/product/...) or absolute (https://mudpieusastore.shop/product/...)
                if not _PRODUCT_HREF_RE.search(href):
                    continue
                # Normalise to absolute URL, strip query string
                if href.startswith("http"):
                    full = href.split("?")[0].rstrip("/")
                else:
                    full = _BASE + href.split("?")[0].rstrip("/")
                # Skip non-home-decor product slugs
                if any(frag in full for frag in _BLOCKED_SLUG_FRAGMENTS):
                    continue
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
                    found_on_page += 1

            self.log.info("mudpie_usa_category_page_done",
                          category=category_label, page=page,
                          found=found_on_page, total=len(urls))

            if found_on_page == 0:
                break

            # Check for a next-page link
            next_link = (
                soup.find("a", rel=lambda r: r and "next" in r)
                or soup.find("a", string=re.compile(r"next|›|»", re.I))
                or soup.select_one("a.next, a[aria-label*='next' i], .pagination__next")
            )
            if not next_link:
                break

            page += 1
            await asyncio.sleep(0.5)

        return urls

    # ── Product page parsing ─────────────────────────────────────────────────

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(product_url)
        if not html:
            return None

        if self._is_blocked(html):
            # One retry — SmartProxy occasionally returns placeholder on first hit
            self.log.warning("mudpie_usa_blocked_retry", url=product_url)
            await asyncio.sleep(2)
            html = await self._fetch_rendered(product_url)
            if not html or self._is_blocked(html):
                self.log.warning("mudpie_usa_blocked_final", url=product_url)
                return None

        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(script.string or "")
                if isinstance(d, list):
                    d = next(
                        (x for x in d if isinstance(x, dict)
                         and x.get("@type") == "Product"),
                        None,
                    )
                if d and d.get("@type") == "Product":
                    result = self._from_json_ld(d, product_url)
                    if result:
                        return result
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # DOM fallback
        return self._parse_from_dom(soup, product_url)

    def _from_json_ld(self, d: dict, url: str) -> Optional[RawProduct]:
        name = (d.get("name") or "").strip()
        if not name:
            return None

        offers = d.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        price: Optional[float] = None
        for key in ("price", "lowPrice"):
            raw = offers.get(key)
            if raw is not None:
                try:
                    price = float(str(raw).replace(",", "").replace("$", ""))
                    break
                except (ValueError, TypeError):
                    pass

        images = d.get("image", [])
        if isinstance(images, str):
            images = [images]
        elif isinstance(images, dict):
            images = [images.get("url", "")]
        images = [i for i in images if i and isinstance(i, str)]

        sku = d.get("sku") or d.get("mpn")
        if not sku:
            m = _ASIN_RE.search(url)
            if m:
                sku = m.group(1)

        category = d.get("category", "")
        if isinstance(category, list):
            category = category[0] if category else ""

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
            category=str(category) if category else None,
            raw_attributes={},
        )

    def _parse_from_dom(self, soup: BeautifulSoup, url: str) -> Optional[RawProduct]:
        # Name
        h1 = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else ""
        if not name:
            return None

        # Sale price first, then any price element
        price: Optional[float] = None
        for sel in [
            "[class*='sale-price']", "[class*='price-sale']", "[class*='sale_price']",
            "[class*='special-price']", "[class*='price']",
        ]:
            el = soup.select_one(sel)
            if el:
                price = self._parse_price(el.get_text(strip=True))
                if price:
                    break

        # Images — prefer product gallery images, skip logos/icons
        images: list[str] = []
        seen_imgs: set[str] = set()
        for img in soup.find_all("img", src=True):
            src: str = img.get("src", "")
            if not src or not re.search(r'\.(jpe?g|png|webp)', src, re.I):
                continue
            skip_words = ["logo", "icon", "placeholder", "blank", "pixel", "spinner"]
            if any(w in src.lower() for w in skip_words):
                continue
            full = src if src.startswith("http") else _BASE + src
            if full not in seen_imgs:
                seen_imgs.add(full)
                images.append(full)

        # SKU / external ID from URL ASIN suffix
        sku: Optional[str] = None
        m = _ASIN_RE.search(url)
        if m:
            sku = m.group(1)

        # Category from breadcrumb
        category: Optional[str] = None
        breadcrumb = soup.find(
            class_=re.compile(r"breadcrumb", re.I)
        ) or soup.find("nav", {"aria-label": re.compile(r"breadcrumb", re.I)})
        if breadcrumb:
            crumbs = breadcrumb.find_all("a")
            if crumbs:
                category = crumbs[-1].get_text(strip=True)

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            sku=sku,
            price=price,
            currency="USD",
            image_urls=images[:5],
            category=category,
            raw_attributes={},
        )

    # ── Main scrape loop ─────────────────────────────────────────────────────

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Discover product URLs per category, then scrape product pages in batches."""
        await self.before_scrape()
        try:
            seen_urls: set[str] = set()

            for cat_label in await self.get_category_urls():
                product_urls = await self.get_product_urls(cat_label)
                unique = [u for u in product_urls if u not in seen_urls]
                seen_urls.update(unique)

                self.log.info("mudpie_usa_scraping_products",
                              category=cat_label, count=len(unique))

                for i in range(0, len(unique), _BATCH_SIZE):
                    batch = unique[i: i + _BATCH_SIZE]
                    results = await asyncio.gather(
                        *[self.parse_product(u) for u in batch],
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            self.log.warning("mudpie_usa_parse_error", error=str(result))
                        elif result:
                            # Fall back to the known category label if JSON-LD/breadcrumb didn't find one
                            if not result.category:
                                result.category = cat_label
                            yield result
        finally:
            await self.after_scrape()
