"""
At Home adapter (athome.com).

At Home is a US home decor superstore with server-side rendered pages and JSON-LD.
URLs to scrape come from the taxonomy catalog at
`backend/scraper/catalogs/at-home.csv` (search URLs of the form
/search/?q=...). Legacy /category/{slug}/ URLs are still supported as a
fallback when no catalog is present.
"""
import json
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Used only when no catalog is defined for this retailer (legacy fallback).
CATEGORY_PATHS = [
    "/category/storage/",
    "/category/kitchen-storage/",
    "/category/baskets/",
    "/category/home-decor/",
    "/category/candles/",
    "/category/vases/",
    "/category/wall-decor/",
    "/category/decorative-accessories/",
]

# At Home search returns 24 items per page; max pages we'll walk per URL
SEARCH_PAGE_SIZE = 24
MAX_PAGES = 5


def _paginate_search_url(search_url: str, page: int) -> str:
    """Build a paginated search URL by setting `start` based on page number."""
    parsed = urlparse(search_url)
    params = parse_qs(parsed.query)
    params["start"] = [str((page - 1) * SEARCH_PAGE_SIZE)]
    params["sz"] = [str(SEARCH_PAGE_SIZE)]
    return urlunparse(parsed._replace(query=urlencode(params, doseq=True)))


class AtHomeAdapter(BaseAdapter):
    RETAILER_SLUG = "at-home"

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
        # Only used in legacy mode when no catalog exists. Catalog mode reads
        # URLs directly from the catalog via BaseAdapter.scrape().
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url: str) -> list[str]:
        is_search = "/search/" in category_url
        urls: list[str] = []
        for page in range(1, MAX_PAGES + 1):
            if is_search:
                page_url = category_url if page == 1 else _paginate_search_url(category_url, page)
                resp = await self._client.get(page_url)
            else:
                params = {"page": page} if page > 1 else {}
                resp = await self._client.get(category_url, params=params)
            if resp.status_code != 200:
                break
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select(
                "a.product-card__title-link, a[href*='/product/'], a[href*='/p/']"
            )
            if not links:
                break
            added = 0
            for a in links:
                href = a.get("href", "")
                full = href if href.startswith("http") else self.base_url + href
                if full not in urls:
                    urls.append(full)
                    added += 1
            if added == 0:
                break
            # On category pages we honour pagination markup; on search we keep
            # walking until we hit a page with no new products.
            if not is_search and not soup.select_one("a[rel='next'], .pagination__next"):
                break
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

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
                        currency="USD",
                        image_urls=imgs,
                        raw_attributes={},
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        name_el = soup.select_one("h1.product-detail__title, h1")
        if not name_el:
            return None
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            currency="USD",
            raw_attributes={},
        )
