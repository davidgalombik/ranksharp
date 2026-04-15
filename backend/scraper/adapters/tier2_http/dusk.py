"""
Dusk AU adapter (dusk.com.au) — Magento GraphQL API.

Dusk runs on Magento 2 which exposes a GraphQL endpoint at /graphql.
All product data (name, SKU, price, image, URL) is available directly,
no browser rendering required.

Category IDs (from categoryList query):
  7    → Candles & Melts
  349  → Diffusers & Oils
  2356 → Home & Living
  3384 → Best Sellers (is_best_seller flag applied)
"""
import httpx
from typing import Optional, AsyncIterator
from scraper.base_adapter import BaseAdapter, RawProduct

GRAPHQL_URL = "https://www.dusk.com.au/graphql"
BASE_URL = "https://www.dusk.com.au"
PAGE_SIZE = 48

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Content-Type": "application/json",
    "Accept": "application/json",
}

# category_id → (label, is_best_seller)
CATEGORIES: dict[str, tuple[str, bool]] = {
    "7":    ("Candles & Melts", False),
    "349":  ("Diffusers & Oils", False),
    "2356": ("Home & Living", False),
    "3384": ("Best Sellers", True),
}

_GQL_QUERY = """
query GetProducts($catId: String!, $page: Int!) {
  products(
    filter: { category_id: { eq: $catId } }
    pageSize: %d
    currentPage: $page
    sort: { name: ASC }
  ) {
    total_count
    page_info { total_pages }
    items {
      name
      sku
      canonical_url
      price_range {
        minimum_price {
          regular_price { value }
        }
      }
      small_image { url }
      description { html }
    }
  }
}
""" % PAGE_SIZE


class DuskAdapter(BaseAdapter):
    RETAILER_SLUG = "dusk"

    def __init__(self, rc):
        super().__init__(rc)
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30)

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def _gql(self, category_id: str, page: int) -> dict:
        """Execute a single paginated GraphQL query."""
        resp = await self._client.post(
            GRAPHQL_URL,
            json={"query": _GQL_QUERY, "variables": {"catId": category_id, "page": page}},
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("data", {}).get("products", {})

    async def scrape(self) -> AsyncIterator[RawProduct]:
        await self.before_scrape()
        seen_skus: set[str] = set()
        try:
            for cat_id, (label, is_best_seller) in CATEGORIES.items():
                self.log.info("dusk_category_start", category=label, cat_id=cat_id)
                page = 1
                total_pages = 1

                while page <= total_pages:
                    try:
                        result = await self._gql(cat_id, page)
                    except Exception as exc:
                        self.log.warning("dusk_gql_error", cat_id=cat_id, page=page, error=str(exc))
                        break

                    if page == 1:
                        info = result.get("page_info", {})
                        total_pages = info.get("total_pages", 1)
                        self.log.info("dusk_category_pages",
                                      category=label,
                                      total=result.get("total_count", 0),
                                      pages=total_pages)

                    for item in result.get("items", []):
                        sku = item.get("sku") or ""
                        if sku in seen_skus:
                            page += 1
                            continue
                        seen_skus.add(sku)

                        canonical = item.get("canonical_url") or ""
                        url = f"{BASE_URL}/{canonical}" if canonical else ""
                        if not url:
                            continue

                        price_val = (
                            item.get("price_range", {})
                            .get("minimum_price", {})
                            .get("regular_price", {})
                            .get("value")
                        )
                        price = float(price_val) if price_val is not None else None

                        img_url = item.get("small_image", {}).get("url") or ""
                        # Ensure image is a real product photo (not a placeholder)
                        img_urls = [img_url] if img_url and "/media/catalog/product/" in img_url else []

                        desc_html = item.get("description", {}).get("html") or ""

                        product = RawProduct(
                            url=url,
                            name=item.get("name", "").strip(),
                            retailer_slug=self.RETAILER_SLUG,
                            external_id=sku,
                            sku=sku,
                            price=price,
                            currency="AUD",
                            category=label,
                            image_urls=img_urls,
                            raw_attributes={"description_html": desc_html} if desc_html else {},
                        )
                        if is_best_seller:
                            product.is_best_seller = True
                        yield product

                    page += 1

        finally:
            await self.after_scrape()

    # ── Required abstract stubs (not used — scrape() is overridden directly) ──

    async def get_category_urls(self) -> list[str]:
        return []

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None
