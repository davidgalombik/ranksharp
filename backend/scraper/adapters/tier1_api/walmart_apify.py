"""
Walmart US adapter — uses Apify's epctex/walmart-scraper actor.

Actor: https://apify.com/epctex/walmart-scraper
Input:  startUrls (list of {url} objects), maxItems
Output: name, price, url, images, description, brand, itemId,
        ratings, reviewCount, categories

Each search URL yields up to MAX_ITEMS_PER_URL products.
30 search terms × 48 items = up to ~1,440 unique products per run.
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

_ACTOR_ID = "epctex/walmart-scraper"
MAX_ITEMS_PER_URL = 48

SEARCH_URLS = [
    # Candles & fragrance
    "https://www.walmart.com/search?q=scented+candles&sort=best_seller",
    "https://www.walmart.com/search?q=wax+melts&sort=best_seller",
    "https://www.walmart.com/search?q=reed+diffuser&sort=best_seller",
    # Soft furnishings
    "https://www.walmart.com/search?q=throw+pillows&sort=best_seller",
    "https://www.walmart.com/search?q=throw+blankets&sort=best_seller",
    "https://www.walmart.com/search?q=area+rugs&sort=best_seller",
    "https://www.walmart.com/search?q=curtain+panels&sort=best_seller",
    # Storage & organisation
    "https://www.walmart.com/search?q=storage+baskets&sort=best_seller",
    "https://www.walmart.com/search?q=floating+shelves&sort=best_seller",
    "https://www.walmart.com/search?q=desk+organizer&sort=best_seller",
    "https://www.walmart.com/search?q=kitchen+canisters&sort=best_seller",
    "https://www.walmart.com/search?q=bathroom+organizer&sort=best_seller",
    "https://www.walmart.com/search?q=closet+organizer&sort=best_seller",
    # Decorative objects
    "https://www.walmart.com/search?q=decorative+vases&sort=best_seller",
    "https://www.walmart.com/search?q=decorative+bowls&sort=best_seller",
    "https://www.walmart.com/search?q=decorative+trays&sort=best_seller",
    "https://www.walmart.com/search?q=picture+frames&sort=best_seller",
    "https://www.walmart.com/search?q=wall+mirrors&sort=best_seller",
    "https://www.walmart.com/search?q=wall+clocks&sort=best_seller",
    "https://www.walmart.com/search?q=wall+art&sort=best_seller",
    "https://www.walmart.com/search?q=decorative+lanterns&sort=best_seller",
    # Lighting
    "https://www.walmart.com/search?q=table+lamps&sort=best_seller",
    "https://www.walmart.com/search?q=floor+lamps&sort=best_seller",
    "https://www.walmart.com/search?q=string+lights&sort=best_seller",
    # Plants & outdoors
    "https://www.walmart.com/search?q=indoor+planters&sort=best_seller",
    "https://www.walmart.com/search?q=artificial+plants&sort=best_seller",
    # Kitchen & dining
    "https://www.walmart.com/search?q=serving+boards&sort=best_seller",
    "https://www.walmart.com/search?q=coffee+mugs&sort=best_seller",
    "https://www.walmart.com/search?q=kitchen+towels&sort=best_seller",
    # Trending styles
    "https://www.walmart.com/search?q=boho+home+decor&sort=best_seller",
]


class WalmartApifyAdapter(BaseAdapter):
    """
    Tier-1 adapter: epctex/walmart-scraper via Apify.
    Runs one actor call per search URL to avoid cross-URL deduplication.
    """

    RETAILER_SLUG = "walmart-us"

    async def get_category_urls(self) -> list[str]:
        return SEARCH_URLS

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None

    async def scrape(self) -> AsyncIterator[RawProduct]:
        if not settings.apify_api_token:
            log.error("apify_not_configured", hint="Set APIFY_API_TOKEN in .env")
            return

        items = await asyncio.get_event_loop().run_in_executor(
            None, self._run_actor
        )
        for item in items:
            product = self._map_item(item)
            if product:
                yield product

    def _run_actor(self) -> list[dict]:
        client = ApifyClient(settings.apify_api_token)

        log.info("apify_run_starting", actor=_ACTOR_ID, total_urls=len(SEARCH_URLS))

        all_items: list[dict] = []
        seen_ids: set[str] = set()

        for i, url in enumerate(SEARCH_URLS, start=1):
            log.info("apify_url_starting", index=i, total=len(SEARCH_URLS), url=url)

            try:
                run = client.actor(_ACTOR_ID).call(
                    run_input={
                        "startUrls": [{"url": url}],
                        "maxItems": MAX_ITEMS_PER_URL,
                    },
                    timeout_secs=300,
                )
            except Exception as exc:
                log.warning("apify_url_error", index=i, url=url, error=str(exc))
                continue

            status = run.get("status") if run else "no response"

            if status not in ("SUCCEEDED", "TIMED-OUT", "FAILED"):
                log.warning("apify_url_skipped", index=i, url=url, status=status)
                continue

            if status in ("TIMED-OUT", "FAILED"):
                log.warning("apify_url_partial", index=i, url=url, status=status)

            dataset_id = run.get("defaultDatasetId")
            if not dataset_id:
                continue

            items = list(client.dataset(dataset_id).iterate_items())

            new_items = []
            for item in items:
                item_id = item.get("itemId") or item.get("id") or item.get("url")
                if item_id and item_id in seen_ids:
                    continue
                if item_id:
                    seen_ids.add(str(item_id))
                new_items.append(item)

            all_items.extend(new_items)
            log.info(
                "apify_url_complete",
                index=i,
                url=url,
                status=status,
                items_this_url=len(items),
                new_unique=len(new_items),
                total_so_far=len(all_items),
            )

        log.info("apify_all_urls_complete", total_unique_items=len(all_items))
        return all_items

    def _map_item(self, item: dict) -> Optional[RawProduct]:
        name = item.get("name") or item.get("title")
        url = item.get("url")
        if not name or not url:
            return None

        # Price
        price = None
        raw_price = item.get("price") or item.get("currentPrice")
        if isinstance(raw_price, (int, float)):
            price = float(raw_price)
        elif isinstance(raw_price, str):
            try:
                price = float(raw_price.replace("$", "").replace(",", "").strip())
            except ValueError:
                pass

        # Images
        images = item.get("images") or []
        if isinstance(images, str):
            images = [images]

        # Description
        description = item.get("description") or item.get("shortDescription")

        # Category from breadcrumbs
        categories = item.get("categories") or []
        category = categories[-1] if categories else None

        # External ID
        item_id = item.get("itemId") or item.get("id")
        external_id = str(item_id) if item_id else None

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=external_id,
            description=description,
            price=price,
            currency="USD",
            category=category,
            brand=item.get("brand"),
            image_urls=images,
            raw_attributes={
                "item_id": external_id,
                "rating": item.get("rating") or item.get("ratings"),
                "review_count": item.get("reviewCount") or item.get("numberOfReviews"),
                "in_stock": item.get("availabilityStatus") == "IN_STOCK",
            },
        )
