"""
Target US adapter — uses Apify's ecomscrape/target-product-search-scraper actor.

Monthly rental: $20/month.

Bot detection notes:
- Target uses PerimeterX which blocks all Apify IPs and headless Chrome.
- The ecomscrape actor uses its own proxy network and bypasses PerimeterX
  on roughly 25-30% of requests, giving ~100-200 products per run.
- Running one actor call per URL prevents cross-URL TCIN deduplication
  (all 20 URLs in one call = only 24 total items due to actor-internal dedup).
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

_ACTOR_ID = "ecomscrape/target-product-search-scraper"

SEARCH_URLS = [
    "https://www.target.com/s?searchTerm=candles&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=pillows&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=baskets&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=blankets&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=frames&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=vases&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=bowls&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=trays&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=rugs&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=lamps&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=mirrors&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=curtains&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=shelves&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=organizers&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=diffuser&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=planters&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=clocks&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=throws&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=mugs&sortBy=bestselling",
    "https://www.target.com/s?searchTerm=containers&sortBy=bestselling",
]

MAX_ITEMS_PER_URL = 24


class TargetApifyAdapter(BaseAdapter):
    RETAILER_SLUG = "target-us"

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
        """
        One actor call per URL to avoid the actor's global TCIN deduplication
        (all URLs in one call = only 24 total items returned).
        """
        client = ApifyClient(settings.apify_api_token)

        log.info("apify_run_starting", actor=_ACTOR_ID, total_urls=len(SEARCH_URLS))

        all_items: list[dict] = []
        seen_tcins: set[str] = set()

        for i, url in enumerate(SEARCH_URLS, start=1):
            log.info("apify_url_starting", index=i, total=len(SEARCH_URLS), url=url)

            try:
                run = client.actor(_ACTOR_ID).call(
                    run_input={
                        "urls": [url],
                        "max_items_per_url": MAX_ITEMS_PER_URL,
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
                tcin = item.get("tcin")
                if tcin and tcin in seen_tcins:
                    continue
                if tcin:
                    seen_tcins.add(tcin)
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
        name = item.get("title")
        url = item.get("buy_url") or item.get("url")
        if not name or not url:
            return None

        price_obj = item.get("price") or {}
        price = price_obj.get("current_retail") or price_obj.get("reg_retail")
        if isinstance(price, str):
            try:
                price = float(price.replace("$", "").replace(",", "").strip())
            except ValueError:
                price = None

        images_obj = item.get("images") or {}
        primary_image = images_obj.get("primary_image_url")
        alt_images = images_obj.get("alternate_image_urls") or []
        all_images = [primary_image] + alt_images if primary_image else alt_images

        brand_obj = item.get("primary_brand") or {}
        brand = brand_obj.get("name")

        desc_obj = item.get("description") or {}
        bullets = desc_obj.get("bullet_descriptions") or []
        soft = (desc_obj.get("soft_bullets") or {}).get("bullets") or []
        description = " | ".join(bullets + soft) or None

        classification = item.get("product_classification") or {}
        category = classification.get("product_type_name") or item.get("from_url")

        # All search URLs use sortBy=bestselling — every result is a top seller.
        # Also honour any explicit badge the actor returns.
        badges = item.get("badges") or item.get("labels") or []
        explicit_badge = any(
            "best" in str(b).lower() and "sell" in str(b).lower()
            for b in (badges if isinstance(badges, list) else [badges])
        )
        is_best_seller = explicit_badge or True  # all queries are bestselling-sorted

        product = RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=item.get("tcin"),
            description=description,
            price=price,
            currency="USD",
            category=category,
            brand=brand,
            image_urls=all_images,
            raw_attributes={
                "tcin": item.get("tcin"),
                "dpci": item.get("dpci"),
                "rating_score": item.get("rating_score"),
                "total_ratings": item.get("total_ratings"),
            },
        )
        product.is_best_seller = is_best_seller
        return product
