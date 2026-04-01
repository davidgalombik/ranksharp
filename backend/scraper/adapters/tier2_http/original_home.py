"""
Original Home (NL) adapter (originalhome.nl).

Original Home is a Dutch homewares boutique running on WooCommerce.
Products are at /product/{name}/ and categories at /shop/?product_cat={slug}.
Product pages include JSON-LD schema.org/Product markup.
"""
import json
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
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

# WooCommerce category slugs
COLLECTION_SLUGS = [
    "candle-holders",
    "glassware",
    "kitchenware",
    "baskets-trays",
    "scented-candles",
    "natural-candles",
    "pillar-candles",
    "dinner-candles",
    "table-linen",
    "throws-cushions",
    "new-products",
]


class OriginalHomeAdapter(BaseAdapter):
    RETAILER_SLUG = "original-home"

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

    async def get_category_urls(self) -> list[str]:
        # Original Home uses /collection/{slug}/ not WooCommerce default /product-category/
        return [
            f"{self.base_url}/collection/{slug}/"
            for slug in COLLECTION_SLUGS
        ]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url: str) -> list[str]:
        urls = []
        for page in range(1, 6):
            # Original Home uses /page/N/ for pagination
            page_url = f"{category_url}page/{page}/" if page > 1 else category_url
            resp = await self._client.get(page_url)
            if resp.status_code == 404:
                break
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            # Products linked from collection pages at /product/{slug}/
            links = soup.select("a[href*='/product/']")
            if not links:
                # Try WooCommerce standard selectors as fallback
                links = soup.select(".woocommerce-loop-product__link, .product-link, article a")
            if not links:
                break
            added = 0
            for a in links:
                href = a.get("href", "")
                full = href if href.startswith("http") else self.base_url + href
                if "/product/" in full and full not in urls and "originalhome.nl" in full:
                    urls.append(full)
                    added += 1
            if added == 0:
                break
            # Check for next page
            if not soup.select_one("a.next, .next.page-numbers"):
                break
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # JSON-LD (WooCommerce adds schema.org/Product)
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
                            price = float(str(raw).replace(",", ".").replace(" ", ""))
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
                        currency="EUR",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: WooCommerce HTML
        name_el = soup.select_one("h1.product_title, h1.entry-title, h1")
        if not name_el:
            return None
        price_el = soup.select_one(".price .woocommerce-Price-amount")
        price = None
        if price_el:
            try:
                price = float(
                    price_el.get_text(strip=True)
                    .replace(",", ".")
                    .replace("€", "")
                    .replace(" ", "")
                )
            except (ValueError, TypeError):
                pass
        imgs = [
            img.get("src", "")
            for img in soup.select(".woocommerce-product-gallery img")
            if img.get("src")
        ]
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="EUR",
            image_urls=imgs,
            raw_attributes={},
        )
