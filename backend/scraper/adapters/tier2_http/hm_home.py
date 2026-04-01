"""
H&M Home AU adapter (hm.com).

H&M exposes a JSON product listing API via ?format=json on category pages.
Products are parsed from the JSON response; individual product pages supply JSON-LD.
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
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "en-AU,en;q=0.9",
}

# H&M AU home category paths
CATEGORY_PATHS = [
    "/en_au/home/shop-all-home.html",
    "/en_au/home/decoration.html",
    "/en_au/home/textiles.html",
    "/en_au/home/kitchen-dining.html",
    "/en_au/home/storage-organisation.html",
]

PAGE_SIZE = 48


class HMHomeAdapter(BaseAdapter):
    """
    H&M Home uses a JSON API endpoint with ?format=json on category pages.
    Falls back to HTML link extraction if JSON API is unavailable.
    """
    RETAILER_SLUG = "hm-home"

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
        offset = 0

        while True:
            params = {
                "sort": "POPULARITY",
                "image-size": "small",
                "image-quality": "80",
                "offset": offset,
                "page-size": PAGE_SIZE,
                "format": "json",
            }
            resp = await self._client.get(category_url, params=params)
            if resp.status_code != 200:
                break

            try:
                data = resp.json()
            except Exception:
                # Fall back to HTML scraping
                soup = BeautifulSoup(resp.text, "lxml")
                for a in soup.select("a[href*='productpage'], a[href*='/p/']"):
                    href = a.get("href", "")
                    full = href if href.startswith("http") else self.base_url + href
                    if full not in urls:
                        urls.append(full)
                break

            # Parse product links from JSON response
            plp = data.get("plpList", data.get("results", []))
            if not plp:
                break

            added = 0
            for item in plp:
                link = (
                    item.get("link")
                    or item.get("url")
                    or item.get("pdpLink")
                )
                if link:
                    full = link if link.startswith("http") else self.base_url + link
                    if full not in urls:
                        urls.append(full)
                        added += 1

            if added == 0:
                break

            pagination = data.get("pagination", {})
            total = pagination.get("numberOfProducts", 0) or data.get("total", 0)
            offset += PAGE_SIZE
            if offset >= total:
                break

        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # JSON-LD
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
                        currency="AUD",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: DOM
        name_el = soup.select_one("h1.primary, h1[class*='product'], h1")
        if not name_el:
            return None
        price = None
        price_el = soup.select_one("[class*='price']:not([class*='original'])")
        if price_el:
            m = re.search(r"[\d.]+", price_el.get_text().replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass
        img_el = soup.select_one("img[class*='product'], section img")
        imgs = [img_el["src"]] if img_el and img_el.get("src") else []
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="AUD",
            image_urls=imgs,
            raw_attributes={},
        )
