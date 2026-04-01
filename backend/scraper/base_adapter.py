"""Abstract base class that every site-specific adapter must implement."""
import asyncio
import random
import structlog
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import AsyncIterator, Optional
from config import settings

log = structlog.get_logger()


# URL fragments that indicate a best-sellers category page
_BEST_SELLER_KEYWORDS = frozenset([
    "best-seller", "bestseller", "best_seller",
    "best-sellers", "bestsellers", "best_sellers",
    "top-rated", "top-seller", "top-sellers",
    "most-popular", "top-picks", "top-pick",
    "best-selling", "bestselling",
])


@dataclass
class RawProduct:
    """Normalised product payload emitted by every adapter."""
    # Identity
    url: str
    name: str
    retailer_slug: str

    # Optional raw fields
    external_id: Optional[str] = None
    sku: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    currency: str = "USD"
    category: Optional[str] = None
    subcategory: Optional[str] = None
    brand: Optional[str] = None

    # Images — list of absolute URLs, primary first
    image_urls: list[str] = field(default_factory=list)

    # Any extra site-specific data (materials listed on page, etc.)
    raw_attributes: dict = field(default_factory=dict)

    # Signals
    is_best_seller: bool = False

    scraped_at: datetime = field(default_factory=datetime.utcnow)

    @property
    def primary_image_url(self) -> Optional[str]:
        return self.image_urls[0] if self.image_urls else None


class BaseAdapter(ABC):
    """
    Contract that all per-site adapters must fulfil.

    Tiers:
      Tier 1 — API adapters    (Etsy, IKEA)
      Tier 2 — HTTP adapters   (static HTML, JSON-LD)
      Tier 3 — Browser adapters (Playwright, React SPAs)

    Subclasses implement the abstract methods; the orchestrator calls
    `scrape()` which drives the full flow.
    """

    # Override in subclass
    RETAILER_SLUG: str = ""
    TARGET_CATEGORIES: list[str] = []
    # Set True on adapters that need a residential proxy to bypass bot protection
    REQUIRES_PROXY: bool = False

    def __init__(self, retailer_config: dict):
        self.config = retailer_config
        self.base_url = retailer_config["base_url"]
        self.categories = retailer_config.get("categories", {})
        self.log = structlog.get_logger(adapter=self.RETAILER_SLUG)

    # ── Abstract interface ──────────────────────────────────────────────────

    @abstractmethod
    async def get_category_urls(self) -> list[str]:
        """Return category / collection page URLs to crawl."""
        ...

    @abstractmethod
    async def get_product_urls(self, category_url: str) -> list[str]:
        """Extract all individual product page URLs from a category page,
        following pagination until exhausted."""
        ...

    @abstractmethod
    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """Download and parse a single product page. Return None to skip."""
        ...

    # ── Optional overrides ──────────────────────────────────────────────────

    async def handle_pagination(self, url: str, page: int) -> str:
        """Build the URL for a given page number. Override per-site as needed."""
        return f"{url}?page={page}"

    async def before_scrape(self):
        """Hook called once before scraping starts (e.g. OAuth, browser launch)."""
        pass

    async def after_scrape(self):
        """Hook called once after scraping finishes (e.g. close browser)."""
        pass

    # ── Orchestration ───────────────────────────────────────────────────────

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Full scrape pipeline: categories → product URLs → parsed products."""
        await self.before_scrape()
        try:
            category_urls = await self.get_category_urls()
            self.log.info("categories_found", count=len(category_urls))

            for cat_url in category_urls:
                # Auto-flag products from known best-seller category pages
                cat_lower = cat_url.lower()
                is_best_seller_cat = any(kw in cat_lower for kw in _BEST_SELLER_KEYWORDS)

                product_urls = await self.get_product_urls(cat_url)
                self.log.info("products_found", category=cat_url, count=len(product_urls),
                              best_seller_cat=is_best_seller_cat)

                for product_url in product_urls:
                    await self._polite_delay()
                    try:
                        product = await self.parse_product(product_url)
                        if product:
                            if is_best_seller_cat:
                                product.is_best_seller = True
                            yield product
                    except Exception as exc:
                        self.log.warning("parse_error", url=product_url, error=str(exc))
        finally:
            await self.after_scrape()

    # ── Utilities ───────────────────────────────────────────────────────────

    async def _polite_delay(self):
        delay = random.uniform(settings.request_delay_min, settings.request_delay_max)
        await asyncio.sleep(delay)

    def _build_proxy(self) -> Optional[str]:
        """Return proxy URL string for HTTP adapters (httpx/requests).
        Only used by adapters with REQUIRES_PROXY=True."""
        if not self.REQUIRES_PROXY:
            return None
        if settings.proxy_url and settings.proxy_username:
            return (
                f"http://{settings.proxy_username}:{settings.proxy_password}"
                f"@{settings.proxy_url}"
            )
        return settings.proxy_url or None

    def _build_playwright_proxy(self) -> Optional[dict]:
        """Return a Playwright-compatible proxy dict with separate credentials.
        Playwright does not parse credentials from the server URL — they must
        be passed as separate username/password fields."""
        if not self.REQUIRES_PROXY:
            return None
        if not settings.proxy_url:
            return None
        proxy: dict = {"server": f"http://{settings.proxy_url}"}
        if settings.proxy_username:
            proxy["username"] = settings.proxy_username
            proxy["password"] = settings.proxy_password or ""
        return proxy
