"""
Williams Sonoma Group adapter — covers Pottery Barn, West Elm, Williams-Sonoma,
and Pottery Barn AU, all of which share the same platform and API structure.
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
    "Accept-Language": "en-US,en;q=0.9",
}

# Williams Sonoma Group sites use a shared internal search/category API
WS_GROUP_API = "/api/catalog/products"


class WilliamsSonomaGroupAdapter(BaseAdapter):
    RETAILER_SLUG = "ws-group"  # overridden by registry per-site

    def __init__(self, retailer_config: dict):
        super().__init__(retailer_config)
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS,
            follow_redirects=True,
            timeout=30,
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self) -> list[str]:
        urls = []
        for cat_path in self.categories.values():
            urls.append(f"{self.base_url}/{cat_path.strip('/')}/")
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_product_urls(self, category_url: str) -> list[str]:
        product_urls = []
        page = 1

        while True:
            resp = await self._client.get(
                category_url,
                params={"pageNumber": page, "resultsPerPage": 48},
            )
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # WS Group sites inject product data as JSON in a script tag
            data = self._extract_page_data(soup)
            if data:
                for product in data.get("products", []):
                    path = product.get("seoUrl") or product.get("productUrl")
                    if path:
                        full = path if path.startswith("http") else self.base_url + path
                        product_urls.append(full)
            else:
                # Fallback: scrape links from HTML
                links = soup.select("a.product-tile__product-name, a[href*='/products/']")
                found = 0
                for a in links:
                    href = a.get("href", "")
                    if href:
                        full = href if href.startswith("http") else self.base_url + href
                        product_urls.append(full)
                        found += 1
                if found == 0:
                    break

            next_btn = soup.select_one("a.pagination__next:not([aria-disabled='true'])")
            if not next_btn:
                break
            page += 1

        return list(dict.fromkeys(product_urls))  # deduplicate preserving order

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        resp = await self._client.get(product_url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Try JSON-LD
        ld = self._get_json_ld(soup)
        if ld:
            return self._parse_from_ld(ld, soup, product_url)

        return self._parse_from_html(soup, product_url)

    def _extract_page_data(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.find_all("script"):
            text = script.string or ""
            if "window.__INITIAL_STATE__" in text or "window.DL.search" in text:
                try:
                    match = re.search(r"window\.__INITIAL_STATE__\s*=\s*({.+?});", text, re.DOTALL)
                    if match:
                        return json.loads(match.group(1))
                except (json.JSONDecodeError, AttributeError):
                    pass
        return None

    def _get_json_ld(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(script.string or "")
                if isinstance(d, list):
                    d = next((x for x in d if x.get("@type") == "Product"), None)
                if d and d.get("@type") == "Product":
                    return d
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _parse_from_ld(self, ld: dict, soup: BeautifulSoup, url: str) -> RawProduct:
        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        price = None
        for key in ("price", "lowPrice"):
            if raw := offers.get(key):
                try:
                    price = float(str(raw).replace(",", ""))
                    break
                except ValueError:
                    pass

        images = ld.get("image", [])
        if isinstance(images, str):
            images = [images]

        # Grab additional images from HTML media gallery
        extra_images = [
            img.get("data-src") or img.get("src", "")
            for img in soup.select("img.product-image, img.pip-media-grid__cell")
            if img.get("data-src") or img.get("src")
        ]
        all_images = list(dict.fromkeys(images + extra_images))

        return RawProduct(
            url=url,
            name=ld.get("name", ""),
            retailer_slug=self.RETAILER_SLUG,
            external_id=ld.get("sku"),
            sku=ld.get("sku"),
            description=ld.get("description"),
            price=price,
            currency=offers.get("priceCurrency", "USD"),
            brand=ld.get("brand", {}).get("name") if isinstance(ld.get("brand"), dict) else None,
            image_urls=all_images,
            raw_attributes={
                "color": ld.get("color"),
                "material": ld.get("material"),
            },
        )

    def _parse_from_html(self, soup: BeautifulSoup, url: str) -> Optional[RawProduct]:
        name_el = soup.select_one("h1.product-header__name, h1[itemprop='name']")
        if not name_el:
            return None

        price_el = soup.select_one("[data-testid='pip-price-retail'], [itemprop='price']")
        price = None
        if price_el:
            m = re.search(r"[\d,.]+", price_el.get_text())
            if m:
                try:
                    price = float(m.group().replace(",", ""))
                except ValueError:
                    pass

        images = [
            img.get("data-src") or img.get("src", "")
            for img in soup.select("img.product-image")
            if img.get("data-src") or img.get("src")
        ]

        desc_el = soup.select_one(".product-description, [itemprop='description']")
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            description=desc_el.get_text(strip=True) if desc_el else None,
            price=price,
            currency="USD",
            image_urls=images,
            raw_attributes={},
        )
