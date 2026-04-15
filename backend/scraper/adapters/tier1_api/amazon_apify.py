"""
Amazon US adapter — uses Apify's junglee/Amazon-crawler actor.

Actor: https://apify.com/junglee/amazon-crawler
Input:  categoryOrProductUrls (list of {url} objects), maxItemsPerStartUrl, country
Output: title, url, asin, price, brand, stars, reviewsCount, thumbnailImage,
        highResolutionImages, description, features, breadCrumbs, isAmazonChoice,
        monthlyPurchaseVolume

Each search URL yields up to MAX_ITEMS_PER_URL products.
40 search terms × 48 items = up to ~1,920 unique products per run.
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

_ACTOR_ID = "junglee/Amazon-crawler"
MAX_ITEMS_PER_URL = 48  # 2 pages of Amazon search results per term

SEARCH_URLS = [
    # Candles & fragrance
    "https://www.amazon.com/s?k=scented+candles&s=popularity-rank",
    "https://www.amazon.com/s?k=wax+melts&s=popularity-rank",
    "https://www.amazon.com/s?k=reed+diffuser&s=popularity-rank",
    "https://www.amazon.com/s?k=essential+oil+diffuser&s=popularity-rank",
    # Soft furnishings
    "https://www.amazon.com/s?k=throw+pillows&s=popularity-rank",
    "https://www.amazon.com/s?k=throw+blankets&s=popularity-rank",
    "https://www.amazon.com/s?k=curtain+panels&s=popularity-rank",
    "https://www.amazon.com/s?k=area+rugs&s=popularity-rank",
    # Storage & organisation
    "https://www.amazon.com/s?k=storage+baskets&s=popularity-rank",
    "https://www.amazon.com/s?k=floating+shelves&s=popularity-rank",
    "https://www.amazon.com/s?k=desk+organizers&s=popularity-rank",
    "https://www.amazon.com/s?k=kitchen+canisters&s=popularity-rank",
    "https://www.amazon.com/s?k=bathroom+organizer&s=popularity-rank",
    "https://www.amazon.com/s?k=closet+organizer&s=popularity-rank",
    # Decorative objects
    "https://www.amazon.com/s?k=decorative+vases&s=popularity-rank",
    "https://www.amazon.com/s?k=decorative+bowls&s=popularity-rank",
    "https://www.amazon.com/s?k=decorative+trays&s=popularity-rank",
    "https://www.amazon.com/s?k=picture+frames&s=popularity-rank",
    "https://www.amazon.com/s?k=wall+mirrors&s=popularity-rank",
    "https://www.amazon.com/s?k=wall+clocks&s=popularity-rank",
    "https://www.amazon.com/s?k=wall+art+prints&s=popularity-rank",
    "https://www.amazon.com/s?k=decorative+figurines&s=popularity-rank",
    "https://www.amazon.com/s?k=bookends&s=popularity-rank",
    "https://www.amazon.com/s?k=decorative+lanterns&s=popularity-rank",
    # Lighting
    "https://www.amazon.com/s?k=table+lamps&s=popularity-rank",
    "https://www.amazon.com/s?k=floor+lamps&s=popularity-rank",
    "https://www.amazon.com/s?k=string+lights&s=popularity-rank",
    "https://www.amazon.com/s?k=led+night+light&s=popularity-rank",
    # Plants & outdoors
    "https://www.amazon.com/s?k=indoor+planters&s=popularity-rank",
    "https://www.amazon.com/s?k=artificial+plants&s=popularity-rank",
    # Kitchen & dining
    "https://www.amazon.com/s?k=serving+boards&s=popularity-rank",
    "https://www.amazon.com/s?k=salad+bowls&s=popularity-rank",
    "https://www.amazon.com/s?k=coffee+mugs&s=popularity-rank",
    "https://www.amazon.com/s?k=kitchen+towels&s=popularity-rank",
    # Bedroom & bath
    "https://www.amazon.com/s?k=decorative+mirrors+bedroom&s=popularity-rank",
    "https://www.amazon.com/s?k=bath+accessories+set&s=popularity-rank",
    "https://www.amazon.com/s?k=soap+dispenser&s=popularity-rank",
    # Seasonal & trending
    "https://www.amazon.com/s?k=boho+home+decor&s=popularity-rank",
    "https://www.amazon.com/s?k=minimalist+home+decor&s=popularity-rank",
    "https://www.amazon.com/s?k=coastal+home+decor&s=popularity-rank",
]


class AmazonApifyAdapter(BaseAdapter):
    """
    Tier-1 adapter: junglee/Amazon-crawler via Apify.
    Runs one actor call per search URL to avoid cross-URL deduplication.
    """

    RETAILER_SLUG = "amazon-us"

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
        seen_asins: set[str] = set()

        for i, url in enumerate(SEARCH_URLS, start=1):
            log.info("apify_url_starting", index=i, total=len(SEARCH_URLS), url=url)

            try:
                run = client.actor(_ACTOR_ID).call(
                    run_input={
                        "categoryOrProductUrls": [{"url": url}],
                        "maxItemsPerStartUrl": MAX_ITEMS_PER_URL,
                        "country": "US",
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
                asin = item.get("asin")
                if asin and asin in seen_asins:
                    continue
                if asin:
                    seen_asins.add(asin)
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
        url = item.get("url")
        if not name or not url:
            return None

        # Price
        price_obj = item.get("price") or {}
        price = price_obj.get("value")
        if isinstance(price, str):
            try:
                price = float(price.replace("$", "").replace(",", "").strip())
            except ValueError:
                price = None

        # Images — prefer high-res, fall back to thumbnail
        high_res = item.get("highResolutionImages") or []
        thumbnail = item.get("thumbnailImage")
        image_urls = high_res if high_res else ([thumbnail] if thumbnail else [])

        # Description — join features (bullet points) + description text
        features = item.get("features") or []
        description_text = item.get("description") or ""
        description = " | ".join(features) if features else description_text or None

        # Category from breadcrumbs
        breadcrumbs = item.get("breadCrumbs") or ""
        category = breadcrumbs.split(">")[-1].strip() if breadcrumbs else None

        # Best seller: explicit badge from actor, Amazon's Choice, or high purchase volume
        monthly_vol = item.get("monthlyPurchaseVolume") or ""
        high_volume = bool(monthly_vol and any(
            marker in str(monthly_vol) for marker in ["K+", "k+", "1,000", "2,000", "5,000", "10,000"]
        ))
        is_best_seller = bool(
            item.get("isBestSeller")
            or item.get("bestSeller")
            or item.get("isAmazonChoice")
            or high_volume
        )

        product = RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=item.get("asin"),
            description=description,
            price=price,
            currency="USD",
            category=category,
            brand=item.get("brand"),
            image_urls=image_urls,
            raw_attributes={
                "asin": item.get("asin"),
                "stars": item.get("stars"),
                "reviews_count": item.get("reviewsCount"),
                "is_amazon_choice": item.get("isAmazonChoice"),
                "is_best_seller": item.get("isBestSeller") or item.get("bestSeller"),
                "monthly_purchase_volume": item.get("monthlyPurchaseVolume"),
                "in_stock": item.get("inStock"),
                "breadcrumbs": breadcrumbs,
            },
        )
        product.is_best_seller = is_best_seller
        return product
