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

# Generic page titles returned when a product is unavailable/redirects to a collection
_GENERIC_NAMES = frozenset([
    "explore our latest collection",
    "shop our collection",
    "our collection",
    "collection",
    "page not found",
    "404",
])


def _extract_img_urls(raw):
    if not raw: return []
    if isinstance(raw, str): return [raw]
    if isinstance(raw, dict):
        u = raw.get("url") or raw.get("contentUrl") or raw.get("src")
        return [u] if u else []
    if isinstance(raw, list):
        out = []
        for x in raw:
            if isinstance(x, str): out.append(x)
            elif isinstance(x, dict):
                u = x.get("url") or x.get("contentUrl") or x.get("src")
                if u: out.append(u)
        return out
    return []


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-NL,nl;q=0.9,en;q=0.8",
}

# WooCommerce category slugs → display labels
COLLECTION_LABELS: dict[str, str] = {
    "candle-holders": "Candle Holders",
    "glassware": "Glassware",
    "kitchenware": "Kitchenware",
    "baskets-trays": "Baskets & Trays",
    "scented-candles": "Scented Candles",
    "natural-candles": "Natural Candles",
    "pillar-candles": "Pillar Candles",
    "dinner-candles": "Dinner Candles",
    "table-linen": "Table Linen",
    "throws-cushions": "Throws & Cushions",
    "new-products": "New Products",
}

COLLECTION_SLUGS = list(COLLECTION_LABELS.keys())


class OriginalHomeAdapter(BaseAdapter):
    RETAILER_SLUG = "original-home"

    def __init__(self, rc):
        super().__init__(rc)
        self._client = None
        self._cat_cache: dict[str, str] = {}

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
                    # Extract slug from category_url e.g. /collection/candle-holders/
                    slug = category_url.rstrip("/").split("/")[-1]
                    self._cat_cache.setdefault(full, COLLECTION_LABELS.get(slug, slug))
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
                    product_name = d.get("name", "")
                    if product_name.strip().lower() in _GENERIC_NAMES:
                        return None
                    offers = d.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    price = None
                    if raw := offers.get("price"):
                        try:
                            price = float(str(raw).replace(",", ".").replace(" ", ""))
                        except (ValueError, TypeError):
                            pass
                    imgs = _extract_img_urls(d.get("image"))
                    return RawProduct(
                        url=url,
                        name=product_name,
                        retailer_slug=self.RETAILER_SLUG,
                        external_id=d.get("sku"),
                        description=d.get("description"),
                        price=price,
                        currency="EUR",
                        category=self._cat_cache.get(url),
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # Fallback: WooCommerce HTML
        name_el = soup.select_one("h1.product_title, h1.entry-title, h1")
        if not name_el:
            return None
        if name_el.get_text(strip=True).lower() in _GENERIC_NAMES:
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
        imgs = []
        for img in soup.select(
            ".woocommerce-product-gallery img, "
            ".wp-post-image, "
            "img.attachment-woocommerce_single, "
            ".product-gallery img"
        ):
            src = (img.get("data-large_image") or img.get("data-src")
                   or img.get("src") or "")
            if src and "placeholder" not in src.lower() and src.startswith("http"):
                imgs.append(src)
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="EUR",
            category=self._cat_cache.get(url),
            image_urls=imgs,
            raw_attributes={},
        )
