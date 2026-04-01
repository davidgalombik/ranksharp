"""Crate & Barrel adapter — static HTML + JSON-LD product data."""
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
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

CATEGORY_PATHS = {
    "storage-organization": [
        "/storage-organization/",
        "/kitchen/food-storage/",
    ],
    "decorative-accessories": [
        "/decorative-accessories/",
        "/vases/",
        "/candles-and-holders/",
    ],
}


class CrateAndBarrelAdapter(BaseAdapter):
    RETAILER_SLUG = "crate-and-barrel"

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
        for cat_key in self.categories.values():
            for path in CATEGORY_PATHS.get(cat_key, [f"/{cat_key}/"]):
                urls.append(self.base_url + path)
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def get_product_urls(self, category_url: str) -> list[str]:
        product_urls = []
        page = 1

        while True:
            paginated = category_url if page == 1 else f"{category_url}?page={page}"
            resp = await self._client.get(paginated)
            if resp.status_code != 200:
                break

            soup = BeautifulSoup(resp.text, "lxml")

            # C&B product cards have data-product-id attributes
            links = soup.select("a[data-product-id], a.product-miniset__title")
            if not links:
                links = soup.select("a[href*='/products/']")

            found = 0
            for a in links:
                href = a.get("href", "")
                if href and "/products/" in href:
                    full_url = href if href.startswith("http") else self.base_url + href
                    if full_url not in product_urls:
                        product_urls.append(full_url)
                        found += 1

            if found == 0:
                break

            # Check for a "next page" link
            next_btn = soup.select_one("a[rel='next'], .pagination__next:not(.disabled)")
            if not next_btn:
                break
            page += 1

        return product_urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        resp = await self._client.get(product_url)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "lxml")

        # Try JSON-LD first (most reliable)
        ld = self._extract_json_ld(soup)
        if ld:
            return self._from_json_ld(ld, product_url)

        # Fallback to HTML scraping
        return self._from_html(soup, product_url)

    def _extract_json_ld(self, soup: BeautifulSoup) -> Optional[dict]:
        for script in soup.select('script[type="application/ld+json"]'):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, list):
                    for item in data:
                        if item.get("@type") == "Product":
                            return item
                elif data.get("@type") == "Product":
                    return data
            except (json.JSONDecodeError, TypeError):
                continue
        return None

    def _from_json_ld(self, ld: dict, url: str) -> RawProduct:
        offers = ld.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}

        price = None
        raw_price = offers.get("price") or offers.get("lowPrice")
        if raw_price:
            try:
                price = float(str(raw_price).replace(",", ""))
            except ValueError:
                pass

        images = ld.get("image", [])
        if isinstance(images, str):
            images = [images]

        return RawProduct(
            url=url,
            name=ld.get("name", ""),
            retailer_slug=self.RETAILER_SLUG,
            external_id=ld.get("sku") or ld.get("productID"),
            sku=ld.get("sku"),
            description=ld.get("description"),
            price=price,
            currency=offers.get("priceCurrency", "USD"),
            category=ld.get("category"),
            brand=ld.get("brand", {}).get("name") if isinstance(ld.get("brand"), dict) else ld.get("brand"),
            image_urls=images,
            raw_attributes={
                "color": ld.get("color"),
                "material": ld.get("material"),
                "aggregate_rating": ld.get("aggregateRating"),
            },
        )

    def _from_html(self, soup: BeautifulSoup, url: str) -> Optional[RawProduct]:
        name_el = soup.select_one("h1.product-miniset__title, h1[itemprop='name'], h1.pdp__product-name")
        if not name_el:
            return None

        price_el = soup.select_one("[itemprop='price'], .pdp__price, .price__value")
        price = None
        if price_el:
            raw = re.sub(r"[^\d.]", "", price_el.get_text())
            try:
                price = float(raw)
            except ValueError:
                pass

        images = [
            img["src"]
            for img in soup.select("img.product-media__image, img[itemprop='image']")
            if img.get("src")
        ]

        desc_el = soup.select_one("[itemprop='description'], .pdp__description")

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
