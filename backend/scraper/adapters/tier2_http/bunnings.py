"""
Bunnings adapter (bunnings.com.au) — category-page image extraction.

Bunnings runs a Next.js SSR app where product images are loaded 100% client-side
via an authenticated API.  The static HTML served by httpx contains:
  • No JSON-LD Product markup
  • No product-specific <img> or <source> tags
  • Only shared site-wide asset hashes in __NEXT_DATA__

WHAT DOES WORK in the server-rendered HTML:
  • Category listing pages render <article> elements, each containing:
      – An <a href="/{slug}_p{sku}"> link
      – <source srcset="https://media.bunnings.com.au/api/public/content/{hash}">
        for the product thumbnail image
  • JSON-LD Product schema IS present and contains name, price, SKU.

Strategy:
  1. get_product_urls():  Parse each category page, extract product URL → image(s)
                          mapping and store in self._img_cache.
  2. parse_product():     Fetch product page for JSON-LD (name, price, SKU).
                          Use self._img_cache for image_urls.
"""
import json
import re
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
    "Accept-Language": "en-AU",
}

CATEGORY_PATHS = [
    "/products/storage-cleaning/storage",
    "/products/indoor-living/home-decor",
    "/products/storage-cleaning/pantry-kitchen-storage",
]

# Human-readable label for each category path
CATEGORY_LABELS: dict[str, str] = {
    "/products/storage-cleaning/storage": "Storage",
    "/products/indoor-living/home-decor": "Home Decor",
    "/products/storage-cleaning/pantry-kitchen-storage": "Pantry & Kitchen Storage",
}

# Bunnings CDN image URL pattern
_MEDIA_RE = re.compile(
    r'https://media\.bunnings\.com\.au/api/public/content/[a-f0-9]{32}(?:\?[^"\s<>]*)?'
)

# Known shared brand/placeholder hashes to exclude from product images
_SHARED_HASHES = {
    "6687037f7ee44dc08bdf157e9c673985",   # All Set brand logo repeated on many products
    "9526214cf0eb486a87e6db8b8f3b3d91",   # Bunnings logo/icon
}


def _extract_article_images(article_tag) -> list[str]:
    """Extract product-specific image URLs from a category-page article element."""
    seen: set[str] = set()
    imgs: list[str] = []
    for tag in article_tag.find_all(True):
        for attr in ("src", "srcset", "data-src", "data-srcset"):
            val = tag.get(attr, "")
            if not val or "media.bunnings" not in val:
                continue
            for m in _MEDIA_RE.finditer(val):
                full_url = m.group(0).split("?")[0]   # strip query string
                hash_part = full_url.rsplit("/", 1)[-1]
                if hash_part in _SHARED_HASHES:
                    continue
                if full_url not in seen:
                    seen.add(full_url)
                    imgs.append(full_url)
    return imgs[:3]


class BunningsAdapter(BaseAdapter):
    RETAILER_SLUG = "bunnings"
    REQUIRES_PROXY = True  # Bunnings blocks cloud IPs — route via residential proxy

    def __init__(self, rc):
        super().__init__(rc)
        self._client = None
        # Maps product_url → list of image URLs collected from category pages
        self._img_cache: dict[str, list[str]] = {}
        # Maps product_url → category label
        self._cat_cache: dict[str, str] = {}

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30,
            proxy=self._build_proxy(),
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self):
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url):
        # Derive category label from URL path
        path = category_url.replace(self.base_url, "")
        cat_label = CATEGORY_LABELS.get(path, path.rstrip("/").split("/")[-1].replace("-", " ").title())

        urls = []
        for page in range(1, 6):
            resp = await self._client.get(category_url, params={"page": page})
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")

            # ── Collect images from article/product-card elements ────────────
            articles = soup.find_all("article")
            if not articles:
                # Fallback: group links by proximity
                articles = [soup]   # treat whole page as one chunk

            for art in articles:
                link = art.find("a", href=re.compile(r"_p\d+"))
                if not link:
                    continue
                href = link.get("href", "")
                if not re.search(r"_p\d+", href):
                    continue
                full = href if href.startswith("http") else self.base_url + href
                imgs = _extract_article_images(art)
                if full not in urls:
                    urls.append(full)
                # Always update the cache (later pages may have better images)
                if imgs:
                    self._img_cache[full] = imgs
                elif full not in self._img_cache:
                    self._img_cache[full] = []
                # Store category label (first category seen wins)
                if full not in self._cat_cache:
                    self._cat_cache[full] = cat_label

            # Legacy fallback for links not inside article tags
            links = soup.select("a[href*='_p']")
            found = 0
            for a in links:
                href = a.get("href", "")
                if re.search(r"_p\d+", href):
                    full = href if href.startswith("http") else self.base_url + href
                    if full not in urls:
                        urls.append(full)
                        found += 1

            if not articles and found == 0:
                break

        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None

        html = resp.text
        soup = BeautifulSoup(html, "lxml")

        # ── Name ─────────────────────────────────────────────────────────────
        name = ""
        h1 = soup.select_one("h1")
        if h1:
            name = h1.get_text(strip=True)

        # ── JSON-LD (name + price + SKU; images unreliable here) ─────────────
        price: Optional[float] = None
        sku: Optional[str] = None
        description: Optional[str] = None

        for s in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(s.string or "")
                if isinstance(d, list):
                    d = next((x for x in d if x.get("@type") == "Product"), None)
                if d and d.get("@type") == "Product":
                    if not name:
                        name = d.get("name", "")
                    sku = d.get("sku")
                    description = d.get("description")
                    offers = d.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if raw := offers.get("price"):
                        try:
                            price = float(str(raw).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    break
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # ── Name fallback: extract from URL slug ─────────────────────────────
        if not name:
            slug_part = url.rstrip("/").split("/")[-1]
            slug_part = re.sub(r"_p\d+$", "", slug_part)
            name = slug_part.replace("-", " ").title()

        if not name:
            return None

        # ── Images + category: use caches collected from category pages ──────
        imgs = self._img_cache.get(url, [])
        category = self._cat_cache.get(url)

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            description=description,
            price=price,
            currency="AUD",
            category=category,
            image_urls=imgs,
            raw_attributes={},
        )
