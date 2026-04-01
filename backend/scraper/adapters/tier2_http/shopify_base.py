"""
Shopify adapter base class.

Shopify exposes a public JSON API on every store — no scraping of HTML needed:
  GET /products.json?limit=250&page_info=<cursor>   → product list
  GET /products/<handle>.json                        → single product detail

Subclass this and set RETAILER_SLUG + optionally override COLLECTION_HANDLES.
"""
import httpx
from typing import Optional, AsyncIterator
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential
import structlog

log = structlog.get_logger()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Default collections to scrape — override in subclass
DEFAULT_COLLECTIONS = ["storage", "home-decor", "organisation", "kitchen", "decor", "homewares"]


class ShopifyAdapter(BaseAdapter):
    """
    Works for any Shopify store. Uses the storefront /products.json endpoint
    which is publicly accessible on all Shopify sites without authentication.
    """
    RETAILER_SLUG = ""
    COLLECTION_HANDLES: list[str] = []  # override to scope to specific collections

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
        """
        Returns collection API URLs. Falls back to the all-products endpoint
        if no collection handles are configured.
        """
        handles = self.COLLECTION_HANDLES or self._detect_collections()
        if handles:
            return [f"{self.base_url}/collections/{h}/products.json" for h in handles]
        return [f"{self.base_url}/products.json"]

    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Paginate through Shopify's products.json endpoint using cursor-based pagination.
        Returns a list of synthetic shopify:// URIs containing the product handle + base_url.
        """
        handles = []
        url = category_url
        params: dict = {"limit": 250}

        while True:
            resp = await self._client.get(url, params=params if "?" not in url else {})
            if resp.status_code != 200:
                self.log.warning("shopify_fetch_failed", url=url, status=resp.status_code)
                break

            data = resp.json()
            products = data.get("products", [])
            if not products:
                break

            for p in products:
                handle = p.get("handle")
                if handle:
                    handles.append(f"shopify://{self.base_url.rstrip('/')}/{handle}")

            # Shopify cursor-based pagination via Link header
            link_header = resp.headers.get("Link", "")
            next_url = self._parse_next_link(link_header)
            if not next_url:
                break
            url = next_url
            params = {}

        return handles

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """
        Fetch full product data from Shopify's product JSON endpoint.
        """
        # Decode the synthetic URI
        rest = product_url.replace("shopify://", "", 1)
        # rest = "https://domain.com/handle"
        slash_idx = rest.rfind("/")
        base = rest[:slash_idx]   # e.g. https://www.hawkinsnewyork.com
        handle = rest[slash_idx + 1:]  # e.g. linen-storage-basket

        api_url = f"{base}/products/{handle}.json"
        resp = await self._client.get(api_url)
        if resp.status_code in (404, 410):
            return None
        resp.raise_for_status()

        p = resp.json().get("product", {})
        if not p:
            return None

        return self._parse_shopify_product(p, base)

    def _parse_shopify_product(self, p: dict, base_url: str) -> RawProduct:
        handle = p.get("handle", "")
        product_url = f"{base_url}/products/{handle}"

        # Images
        images = [img.get("src", "") for img in p.get("images", []) if img.get("src")]

        # Price from first available variant
        price = None
        variants = p.get("variants", [])
        if variants:
            raw_price = variants[0].get("price")
            if raw_price:
                try:
                    price = float(raw_price)
                except (ValueError, TypeError):
                    pass

        # Tags → raw attributes
        tags = p.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",")]

        # Description: strip HTML
        body_html = p.get("body_html", "") or ""
        description = self._strip_html(body_html)

        return RawProduct(
            url=product_url,
            name=p.get("title", ""),
            retailer_slug=self.RETAILER_SLUG,
            external_id=str(p.get("id", "")),
            sku=variants[0].get("sku") if variants else None,
            description=description,
            price=price,
            currency="USD",
            category=p.get("product_type"),
            brand=p.get("vendor"),
            image_urls=images,
            raw_attributes={
                "tags": tags,
                "vendor": p.get("vendor"),
                "product_type": p.get("product_type"),
                "options": [o.get("name") for o in p.get("options", [])],
                "variants_count": len(variants),
            },
        )

    def _detect_collections(self) -> list[str]:
        """Try common collection handles for home/storage categories."""
        return DEFAULT_COLLECTIONS

    def _parse_next_link(self, link_header: str) -> Optional[str]:
        """Parse Shopify's Link header for the next page URL."""
        if not link_header:
            return None
        for part in link_header.split(","):
            part = part.strip()
            if 'rel="next"' in part:
                url = part.split(";")[0].strip().strip("<>")
                return url
        return None

    @staticmethod
    def _strip_html(html: str) -> str:
        """Very basic HTML tag stripper."""
        import re
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text)
        return text.strip()
