"""
Wayfair adapter — uses the Apify Wayfair scraper actor.

Wayfair's PerimeterX + Akamai stack blocks all proxy-based HTML scraping.
The Apify actor (epctex/wayfair-scraper) calls Wayfair's internal product
API endpoints directly, bypassing bot-detection entirely.

Setup:
  1. Sign up at apify.com (free tier: $5/month credit).
  2. Copy your API token from apify.com/account/integrations.
  3. Add to .env:
       APIFY_API_TOKEN=apify_api_xxxxxxxxxxxx
  4. Update the Wayfair retailer row in the DB:
       adapter_class = "scraper.adapters.tier1_api.wayfair_apify.WayfairApifyAdapter"
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

# Apify actor that scrapes Wayfair product/category pages
# Actor page: https://apify.com/123webdata/wayfair-scraper
# Pay-per-result: $0.005/product (~$1 per 200 products). No monthly rental fee.
_ACTOR_ID = "123webdata/wayfair-scraper"

# Category pages to scrape — keep this list small so the actor doesn't
# hammer Wayfair with parallel requests (causes 429 rate limiting).
# The actor's own pagination (usePagination=True) handles getting more
# products from each category sequentially.
CATEGORY_URLS = [
    "https://www.wayfair.com/storage-organization/cat/bins-baskets-c45272.html",
    "https://www.wayfair.com/storage-organization/cat/shelf-organizers-c1862684.html",
    "https://www.wayfair.com/kitchen-tabletop/cat/food-storage-containers-c47067.html",
    "https://www.wayfair.com/decor-pillows/cat/vases-c215093.html",
    "https://www.wayfair.com/lighting/cat/candles-holders-c215337.html",
    "https://www.wayfair.com/decor-pillows/cat/vases-c215335.html",
]

# maxResultsPerScrape = total request cap for the entire run (category pages
# + pagination pages + individual product pages). It is NOT products-per-category.
# Breakdown for ~500 products:
#   6 category pages + ~24 pagination pages + ~500 product pages = ~530 requests
MAX_RESULTS_PER_CATEGORY = 700
# Hard cap on items written to the dataset (pure product records, no pages)
MAX_ITEMS_PER_RUN = 500


class WayfairApifyAdapter(BaseAdapter):
    """
    Tier-1 adapter: delegates all scraping to an Apify actor.

    Overrides `scrape()` directly — Apify runs are batch jobs, not
    page-by-page crawls, so the standard category → product URL flow
    doesn't apply here.
    """

    RETAILER_SLUG = "wayfair"

    # ── Unused abstract methods (required by BaseAdapter) ──────────────────

    async def get_category_urls(self) -> list[str]:
        return CATEGORY_URLS

    async def get_product_urls(self, category_url: str) -> list[str]:
        # Not called — scrape() is overridden
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        # Not called — scrape() is overridden
        return None

    # ── Main scrape flow ───────────────────────────────────────────────────

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """
        Trigger one Apify actor run covering all category URLs,
        wait for it to finish, then yield RawProduct objects.
        """
        if not settings.apify_api_token:
            log.error(
                "apify_not_configured",
                hint="Set APIFY_API_TOKEN in .env",
            )
            return

        # Run the blocking Apify calls in a thread so we don't block the
        # event loop (ApifyClient is synchronous).
        items = await asyncio.get_event_loop().run_in_executor(
            None, self._run_actor
        )

        for item in items:
            product = self._map_item(item)
            if product:
                yield product

    # ── Apify helpers (sync — called in thread executor) ──────────────────

    def _run_actor(self) -> list[dict]:
        """Start the actor, wait for completion, return dataset items."""
        client = ApifyClient(settings.apify_api_token)

        log.info(
            "apify_run_starting",
            actor=_ACTOR_ID,
            category_count=len(CATEGORY_URLS),
        )

        run = client.actor(_ACTOR_ID).call(
            run_input={
                "categoryUrls": CATEGORY_URLS,
                "maxItems": MAX_ITEMS_PER_RUN,
                "maxResultsPerScrape": MAX_RESULTS_PER_CATEGORY,
                "usePagination": True,
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
            },
            # Wait up to 30 minutes for the run to finish
            timeout_secs=1800,
        )

        status = run.get("status") if run else "no response"

        if status not in ("SUCCEEDED", "TIMED-OUT"):
            log.error("apify_run_failed", status=status, actor=_ACTOR_ID)
            return []

        if status == "TIMED-OUT":
            log.warning(
                "apify_run_timed_out",
                actor=_ACTOR_ID,
                hint="Fetching partial results from dataset anyway",
            )

        dataset_id = run["defaultDatasetId"]
        items = list(
            client.dataset(dataset_id).iterate_items()
        )
        log.info("apify_run_complete", items=len(items), dataset=dataset_id, status=status)
        return items

    # ── Field mapping ──────────────────────────────────────────────────────

    def _map_item(self, item: dict) -> Optional[RawProduct]:
        """Map an Apify dataset item to a RawProduct."""
        name = item.get("name") or item.get("title") or item.get("productName")
        url = item.get("url") or item.get("productUrl")

        if not name or not url:
            return None

        # Price — actor may return a float or a string like "$49.99"
        price = self._parse_price(
            item.get("price") or item.get("salePrice") or item.get("currentPrice")
        )

        # Images — actor returns main_image (str) + images (list of str)
        images: list[str] = []
        main_image = item.get("main_image")
        if main_image and isinstance(main_image, str):
            images.append(main_image)
        for img in item.get("images") or []:
            if isinstance(img, str) and img not in images:
                images.append(img)

        # SKU / external ID
        sku = str(item.get("sku") or item.get("productId") or item.get("id") or "")

        # Category — actor returns full breadcrumb path e.g. "Storage > Bins > ..."
        # Use first breadcrumb element as category, second as subcategory
        breadcrumbs = item.get("breadcrumbs") or []
        category = breadcrumbs[0] if breadcrumbs else item.get("category")
        subcategory = breadcrumbs[1] if len(breadcrumbs) > 1 else None

        # Extra attributes the actor returns in a flat dict
        attributes = item.get("attributes") or {}

        # Best seller: check explicit actor fields and attributes dict
        is_best_seller = bool(
            item.get("isBestSeller")
            or item.get("is_best_seller")
            or item.get("bestSeller")
            or attributes.get("isBestSeller")
            or attributes.get("is_best_seller")
        )

        product = RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku or None,
            sku=sku or None,
            description=item.get("description"),
            price=price,
            currency=item.get("currency", "USD"),
            category=str(category) if category else None,
            subcategory=str(subcategory) if subcategory else None,
            brand=item.get("brand"),
            image_urls=images,
            raw_attributes={
                "color": item.get("color"),
                "material": item.get("material"),
                "weight": item.get("weight"),
                "style": item.get("style"),
                "features": item.get("features"),
                "attributes": attributes,
                "in_stock": item.get("in_stock"),
                "regular_price": item.get("regular_price"),
            },
        )
        product.is_best_seller = is_best_seller
        return product

    @staticmethod
    def _parse_price(raw) -> Optional[float]:
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            cleaned = raw.replace("$", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None
