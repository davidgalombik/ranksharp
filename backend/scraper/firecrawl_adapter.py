"""
FirecrawlAdapter — base class for adapters that use Firecrawl to render
JS-heavy category listing pages and extract product data.

Firecrawl handles headless Chrome rendering and returns clean markdown,
bypassing most bot-detection that blocks plain HTTP requests.

Usage:
  1. Add FIRECRAWL_API_KEY=fc-... to .env
  2. Subclass FirecrawlAdapter and implement:
       - get_category_urls()  → list of category page URLs
       - _parse_listing(url, markdown) → list[RawProduct]

The base class drives pagination automatically via _paginate_url() and
caches parsed products so parse_product() can return them without a
second Firecrawl call (listing-only approach).
"""
import re
import asyncio
import httpx
import structlog
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

_FIRECRAWL_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_DEFAULT_WAIT_MS = 5000
_DEFAULT_TIMEOUT = 60000
_MAX_PAGES = 8


class FirecrawlAdapter(BaseAdapter):
    """
    Base adapter that fetches fully-rendered markdown via Firecrawl and
    parses product listings directly — no per-product page requests needed.

    Subclasses override:
      get_category_urls()         — return site-specific category URLs
      _parse_listing(url, md)     — parse markdown → list[RawProduct]
      _paginate_url(url, page)    — build page-N URL (default: ?page=N)
    """

    WAIT_MS: int = _DEFAULT_WAIT_MS

    def __init__(self, rc):
        super().__init__(rc)
        self._cache: dict[str, RawProduct] = {}   # product_url → RawProduct
        self._client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(timeout=120)

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()
        self._cache.clear()

    # ── Firecrawl fetch ──────────────────────────────────────────────────────

    async def _fetch_markdown(self, url: str) -> str:
        """POST url to Firecrawl and return rendered markdown."""
        if not settings.firecrawl_api_key:
            log.warning("firecrawl_key_missing", url=url)
            return ""

        payload = {
            "url": url,
            "formats": ["markdown"],
            "waitFor": self.WAIT_MS,
            "timeout": _DEFAULT_TIMEOUT,
        }
        try:
            resp = await self._client.post(
                _FIRECRAWL_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            data = resp.json()
            if not data.get("success"):
                log.warning("firecrawl_failed", url=url, resp=str(data)[:200])
                return ""
            return data.get("data", {}).get("markdown", "")
        except Exception as exc:
            log.warning("firecrawl_exception", url=url, error=str(exc))
            return ""

    # ── Pagination ───────────────────────────────────────────────────────────

    def _paginate_url(self, base_url: str, page: int) -> str:
        """Build page-N URL. Override per-site if needed."""
        if page == 1:
            return base_url
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}page={page}"

    # ── Base adapter contract ────────────────────────────────────────────────

    async def get_product_urls(self, category_url: str) -> list[str]:
        """
        Scrape listing pages (with pagination), parse products into cache,
        return list of product URLs.
        """
        urls: list[str] = []
        for page in range(1, _MAX_PAGES + 1):
            page_url = self._paginate_url(category_url, page)
            md = await self._fetch_markdown(page_url)
            if not md:
                break

            products = await self._parse_listing(page_url, md)
            if not products:
                break

            new = 0
            for p in products:
                if p.url and p.url not in self._cache:
                    self._cache[p.url] = p
                    urls.append(p.url)
                    new += 1

            log.info("firecrawl_listing_scraped",
                     url=page_url, page=page, new_products=new)

            # Stop if this page had no new products (de-duplication signal)
            if new == 0:
                break

            await asyncio.sleep(1.5)   # polite delay between pages

        return urls

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """Return cached product parsed from listing page."""
        return self._cache.get(product_url)

    async def _polite_delay(self):
        """No delay needed — parse_product() is a cache lookup, not a network call."""
        pass

    # ── Subclass interface ───────────────────────────────────────────────────

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """
        Parse Firecrawl markdown from a category listing page.
        Must return a list of RawProduct objects.
        Subclasses implement this.
        """
        raise NotImplementedError

    # ── Shared markdown helpers ──────────────────────────────────────────────

    @staticmethod
    def _extract_price(text: str) -> Optional[float]:
        """Extract the first dollar price from a text block."""
        m = re.search(r'\$([0-9,]+(?:\.[0-9]{1,2})?)', text)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                pass
        return None

    @staticmethod
    def _extract_image(text: str) -> Optional[str]:
        """Extract first https image URL from markdown image syntax."""
        m = re.search(r'!\[[^\]]*\]\((https://[^)]+)\)', text)
        return m.group(1) if m else None

    @staticmethod
    def _clean_name(text: str) -> str:
        """Strip markdown, backslashes, extra whitespace from a product name."""
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)   # remove links
        text = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', text)         # remove images
        text = text.replace('\\', ' ').replace('\n', ' ')
        text = re.sub(r'\s+', ' ', text).strip()
        return text
