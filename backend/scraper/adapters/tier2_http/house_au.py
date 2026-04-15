"""
House AU adapter (house.com.au) — sitemap + __NEXT_DATA__ extraction.

house.com.au is a Next.js/Shopify hybrid. Products are rendered via JS so the
Shopify /products.json API is disabled and collection pages have no static
product links. However:
  1. A full sitemap at /sitemap-0.xml lists all 10k+ product URLs.
  2. Each product page embeds complete product data in __NEXT_DATA__ JSON,
     including name, price, SKU, images, and description.

Strategy:
  1. get_product_urls():  Parse sitemap XML for /products/ URLs.
  2. parse_product():     Fetch product page, extract __NEXT_DATA__ JSON.
"""
import re
import json
import httpx
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct, _BEST_SELLER_KEYWORDS

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU",
}

SITEMAP_URL = "https://www.house.com.au/sitemap-0.xml"

# Collections to INCLUDE — maps collection string fragment → category label.
# Matched against uppercased collection strings from __NEXT_DATA__.
# Excluded: DINING (cutlery/glassware/drinkware), BEDROOM & BATHROOM (bedding/linen).
TARGET_COLLECTIONS = {
    "BAKEWARE":     "Bakeware",
    "KITCHENWARE":  "Kitchen",
    "LIVING":       "Home Decor",
    "CANDLE":       "Candles",
    "BEST-SELLER":  "Best Sellers",
}


_NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
    re.DOTALL,
)


class HouseAUAdapter(BaseAdapter):
    RETAILER_SLUG = "house-au"

    def __init__(self, rc):
        super().__init__(rc)
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self) -> list[str]:
        """Return a single placeholder; actual URL discovery is via sitemap."""
        return ["sitemap"]

    async def get_product_urls(self, category_url: str) -> list[str]:
        """Parse sitemap-0.xml to get all product URLs."""
        try:
            resp = await self._client.get(SITEMAP_URL)
            if resp.status_code != 200:
                self.log.warning("house_au_sitemap_failed", status=resp.status_code)
                return []
            urls = re.findall(
                r"<loc>(https://www\.house\.com\.au/products/[^<]+)</loc>",
                resp.text,
            )
            self.log.info("house_au_sitemap_parsed", total=len(urls))
            return urls
        except Exception as exc:
            self.log.warning("house_au_sitemap_error", error=str(exc))
            return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        try:
            resp = await self._client.get(product_url)
            if resp.status_code != 200:
                return None
        except Exception as exc:
            self.log.warning("house_au_fetch_error", url=product_url, error=str(exc))
            return None

        m = _NEXT_DATA_RE.search(resp.text)
        if not m:
            return None

        try:
            data = json.loads(m.group(1))
            product = data.get("props", {}).get("pageProps", {}).get("product")
        except (json.JSONDecodeError, AttributeError):
            return None

        if not product:
            return None

        name = product.get("name", "").strip()
        if not name:
            return None

        # Price
        price: Optional[float] = None
        price_data = product.get("price", {})
        if isinstance(price_data, dict):
            try:
                price = float(price_data.get("value", 0) or 0) or None
            except (ValueError, TypeError):
                pass
        if price is None and product.get("variants"):
            try:
                price = float(product["variants"][0].get("price", 0) or 0) or None
            except (ValueError, TypeError):
                pass

        # SKU
        sku: Optional[str] = None
        variants = product.get("variants", [])
        if variants:
            sku = variants[0].get("sku")
        if not sku:
            raw_id = product.get("id")
            sku = str(raw_id) if raw_id else None

        # Images
        img_urls: list[str] = []
        for img in product.get("images", []):
            src = img.get("url") or img.get("src") or ""
            if src and "placeholder" not in src.lower():
                img_urls.append(src)
        if not img_urls:
            for item in product.get("media", []):
                src = item.get("url") or item.get("src") or item.get("originalSrc") or ""
                if src and "placeholder" not in src.lower():
                    img_urls.append(src)

        # Category + best-seller flag from collections list
        collections = [str(c).upper() for c in product.get("collections", [])]

        category: Optional[str] = None
        is_best_seller = False
        for col in collections:
            for key, label in TARGET_COLLECTIONS.items():
                if key in col:
                    if not category:
                        category = label
                    if key == "BEST-SELLER":
                        is_best_seller = True
            if any(kw in col.lower() for kw in _BEST_SELLER_KEYWORDS):
                is_best_seller = True

        # Skip products with no matching target category — these are DINING,
        # BEDROOM & BATHROOM, PET, etc. that we don't track.
        if not category:
            return None

        description = product.get("description") or product.get("whyYouWillLoveIt") or None
        brand = product.get("vendor") or None

        return RawProduct(
            url=product_url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=str(product.get("id", "")) or None,
            sku=sku,
            description=description,
            price=price,
            currency="AUD",
            category=category,
            brand=brand,
            image_urls=img_urls[:5],
            raw_attributes={"brand": brand} if brand else {},
            is_best_seller=is_best_seller,
        )
