"""
Bunnings adapter (bunnings.com.au) — Smartproxy Universal Scraping API.

Bunnings blocks both cloud datacenter IPs (403) and raw residential proxy
routing (ConnectTimeout). The Universal Scraping API handles bot bypass
with full JS rendering on residential IPs.

Strategy:
  1. get_product_urls():  Fetch each category page via Scraping API, parse
                          product links and thumbnail images into cache.
  2. parse_product():     Fetch product page via Scraping API, extract
                          JSON-LD for name/price/SKU + cached images.
"""
import json
import re
from bs4 import BeautifulSoup
from typing import Optional
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential

CATEGORY_PATHS = [
    "/products/storage-cleaning/storage",
    "/products/indoor-living/home-decor",
    "/products/storage-cleaning/pantry-kitchen-storage",
]

CATEGORY_LABELS: dict[str, str] = {
    "/products/storage-cleaning/storage": "Storage",
    "/products/indoor-living/home-decor": "Home Decor",
    "/products/storage-cleaning/pantry-kitchen-storage": "Pantry & Kitchen Storage",
}

_MEDIA_RE = re.compile(
    r'https://media\.bunnings\.com\.au/api/public/content/[a-f0-9]{32}(?:\?[^"\s<>]*)?'
)

_SHARED_HASHES = {
    "6687037f7ee44dc08bdf157e9c673985",
    "9526214cf0eb486a87e6db8b8f3b3d91",
}


def _extract_article_images(article_tag) -> list[str]:
    seen: set[str] = set()
    imgs: list[str] = []
    for tag in article_tag.find_all(True):
        for attr in ("src", "srcset", "data-src", "data-srcset"):
            val = tag.get(attr, "")
            if not val or "media.bunnings" not in val:
                continue
            for m in _MEDIA_RE.finditer(val):
                full_url = m.group(0).split("?")[0]
                hash_part = full_url.rsplit("/", 1)[-1]
                if hash_part in _SHARED_HASHES:
                    continue
                if full_url not in seen:
                    seen.add(full_url)
                    imgs.append(full_url)
    return imgs[:3]


class BunningsAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "bunnings"

    def __init__(self, rc):
        super().__init__(rc)
        self._img_cache: dict[str, list[str]] = {}
        self._cat_cache: dict[str, str] = {}

    async def get_category_urls(self):
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url: str) -> list[str]:
        path = category_url.replace(self.base_url, "")
        cat_label = CATEGORY_LABELS.get(path, path.rstrip("/").split("/")[-1].replace("-", " ").title())

        urls = []
        for page in range(1, 3):  # Cap at 2 pages — Scraping API is ~1min/page
            page_url = f"{category_url}?page={page}" if page > 1 else category_url
            html = await self._fetch_rendered(page_url, country="AU")
            if not html:
                break

            soup = BeautifulSoup(html, "lxml")
            articles = soup.find_all("article")
            if not articles:
                articles = [soup]

            found_this_page = 0
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
                    found_this_page += 1
                if imgs:
                    self._img_cache[full] = imgs
                elif full not in self._img_cache:
                    self._img_cache[full] = []
                if full not in self._cat_cache:
                    self._cat_cache[full] = cat_label

            # Also catch links outside article tags
            for a in soup.select("a[href*='_p']"):
                href = a.get("href", "")
                if re.search(r"_p\d+", href):
                    full = href if href.startswith("http") else self.base_url + href
                    if full not in urls:
                        urls.append(full)
                        found_this_page += 1

            if found_this_page == 0:
                break

        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(url, country="AU")
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        name = ""
        h1 = soup.select_one("h1")
        if h1:
            name = h1.get_text(strip=True)

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

        if not name:
            slug_part = url.rstrip("/").split("/")[-1]
            slug_part = re.sub(r"_p\d+$", "", slug_part)
            name = slug_part.replace("-", " ").title()

        if not name:
            return None

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
