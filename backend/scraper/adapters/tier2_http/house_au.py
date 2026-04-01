"""
House AU adapter (house.com.au).

House.com.au uses a Shopify-compatible URL structure (/collections/, /products/).
The global /products.json endpoint may be disabled, but collection-level API
endpoints (/collections/{handle}/products.json) remain accessible.
Falls back to HTML link extraction if the API returns no results.
"""
import httpx
from typing import Optional, AsyncIterator
from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter
from scraper.base_adapter import RawProduct
from bs4 import BeautifulSoup
import structlog

log = structlog.get_logger()

COLLECTION_HANDLES = [
    "kitchen-storage",
    "pantry",
    "home-decor",
    "storage",
    "organisation",
    "bathroom",
    "candles",
    "vases",
    "cushions",
    "homewares",
    "all",
]


class HouseAUAdapter(ShopifyAdapter):
    """
    House.com.au — Shopify store with collection-level API access.
    Uses collection product API; falls back to HTML link scraping if needed.
    """
    RETAILER_SLUG = "house-au"
    COLLECTION_HANDLES = COLLECTION_HANDLES

    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Try the Shopify collection products API first.
        If it returns 0 results, fall back to scraping product links from the
        HTML collection page.
        """
        handles = await super().get_product_urls(category_url)
        if handles:
            return handles

        # Fallback: parse HTML collection page for /products/ links
        # category_url is like https://www.house.com.au/collections/kitchen-storage/products.json
        # Convert to the HTML page URL
        html_url = category_url.replace("/products.json", "")
        try:
            resp = await self._client.get(html_url)
            if resp.status_code != 200:
                return []
            soup = BeautifulSoup(resp.text, "lxml")
            seen: set[str] = set()
            result = []
            for a in soup.select("a[href*='/products/']"):
                href = a.get("href", "")
                if not href or "?" in href:
                    continue
                full = href if href.startswith("http") else self.base_url.rstrip("/") + href
                handle = full.rstrip("/").split("/products/")[-1]
                if handle and handle not in seen:
                    seen.add(handle)
                    result.append(f"shopify://{self.base_url.rstrip('/')}/{handle}")
            return result
        except Exception as exc:
            log.warning("house_au_html_fallback_failed", url=html_url, error=str(exc))
            return []
