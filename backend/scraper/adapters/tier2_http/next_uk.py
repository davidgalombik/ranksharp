"""
Next UK adapter (next.co.uk).

Next uses server-side rendered pages with JSON-LD product data.
Category pages are at /shop/gb/s/{category}; product URLs follow /p/{name}/{id}.
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
    "Accept-Language": "en-GB,en;q=0.9",
}

CATEGORY_PATHS = [
    "/shop/gb/s/home/storage",
    "/shop/gb/s/home/homeaccessories",
    "/shop/gb/s/home/kitchen",
    "/shop/gb/s/home/bathroom",
    "/shop/gb/s/home/bedroom",
    "/shop/gb/s/home/livingroom",
]


class NextUKAdapter(BaseAdapter):
    RETAILER_SLUG = "next-uk"

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
        for page in range(1, 6):
            params = {"page": page} if page > 1 else {}
            resp = await self._client.get(category_url, params=params)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            # Next UK product cards: links like /p/name/STYLEID
            links = soup.select("a[href^='/p/']")
            if not links:
                links = soup.select("a[href*='/p/'][href*='/g']")
            if not links:
                break
            added = 0
            for a in links:
                href = a.get("href", "")
                # Skip pagination/filter links (they're short like /p/1)
                parts = href.strip("/").split("/")
                if len(parts) < 3:
                    continue
                full = href if href.startswith("http") else self.base_url + href
                if full not in urls:
                    urls.append(full)
                    added += 1
            if added == 0:
                break
            if not soup.select_one("a[rel='next'], [data-testid='pagination-next']"):
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
                        currency="GBP",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: Next.js __NEXT_DATA__
        script = soup.select_one("script#__NEXT_DATA__")
        if script:
            try:
                nd = json.loads(script.string or "")
                product = (
                    nd.get("props", {})
                    .get("pageProps", {})
                    .get("product", {})
                )
                if product.get("name"):
                    price = None
                    p_data = product.get("price", {})
                    if p_data:
                        try:
                            price = float(str(p_data.get("value", "")).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    imgs = [
                        img.get("url", "")
                        for img in product.get("images", [])
                        if img.get("url")
                    ]
                    return RawProduct(
                        url=url,
                        name=product["name"],
                        retailer_slug=self.RETAILER_SLUG,
                        external_id=product.get("id"),
                        description=product.get("description"),
                        price=price,
                        currency="GBP",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, KeyError):
                pass

        name_el = soup.select_one("h1")
        if not name_el:
            return None
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            currency="GBP",
            raw_attributes={},
        )
