"""
Amazon US adapter — uses Apify's junglee/Amazon-crawler actor.

Actor: https://apify.com/junglee/amazon-crawler
Input:  categoryOrProductUrls (list of {url} objects), maxItemsPerStartUrl, country
Output: title, url, asin, price, brand, stars, reviewsCount, thumbnailImage,
        highResolutionImages, description, features, breadCrumbs, isAmazonChoice,
        monthlyPurchaseVolume

URLs to scrape come from the taxonomy catalog at
`backend/scraper/catalogs/amazon-us.csv`. Each product is stamped with the
(category, subcategory) of the URL it was found at.
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from scraper import category_catalog as cc
from config import settings

log = structlog.get_logger()

_ACTOR_ID = "junglee/Amazon-crawler"
MAX_ITEMS_PER_URL = 48  # 2 pages of Amazon search results per term


class AmazonApifyAdapter(BaseAdapter):
    """
    Tier-1 adapter: junglee/Amazon-crawler via Apify.
    Runs one actor call per catalog URL so we can tag products with their
    (category, subcategory) based on the URL they were found at.
    """

    RETAILER_SLUG = "amazon-us"

    async def get_category_urls(self) -> list[str]:
        return [e.url for e in cc.all_entries(self.RETAILER_SLUG)]

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None

    async def scrape(self) -> AsyncIterator[RawProduct]:
        if not settings.apify_api_token:
            log.error("apify_not_configured", hint="Set APIFY_API_TOKEN in .env")
            return

        # Each tagged_items entry is (category, subcategory, product_segment, item_dict)
        tagged_items = await asyncio.get_event_loop().run_in_executor(
            None, self._run_actor
        )
        for category, subcategory, product_segment, item in tagged_items:
            product = self._map_item(item)
            if product:
                product.category = category
                product.subcategory = subcategory
                product.product_segment = product_segment
                yield product

    def _run_actor(self) -> list[tuple[str, str, str, dict]]:
        client = ApifyClient(settings.apify_api_token)
        entries = cc.all_entries(self.RETAILER_SLUG)

        if not entries:
            log.error("catalog_empty", retailer=self.RETAILER_SLUG,
                      hint="Add entries to backend/scraper/catalogs/amazon-us.csv")
            return []

        log.info("apify_run_starting", actor=_ACTOR_ID, total_urls=len(entries))

        tagged: list[tuple[str, str, str, dict]] = []
        seen_asins: set[str] = set()

        for i, entry in enumerate(entries, start=1):
            url = entry.url
            log.info("apify_url_starting", index=i, total=len(entries),
                     category=entry.category, subcategory=entry.subcategory,
                     product_segment=entry.product_segment, url=url)

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

            new_count = 0
            for item in items:
                asin = item.get("asin")
                if asin and asin in seen_asins:
                    continue
                if asin:
                    seen_asins.add(asin)
                tagged.append((entry.category, entry.subcategory, entry.product_segment, item))
                new_count += 1

            log.info(
                "apify_url_complete",
                index=i,
                url=url,
                status=status,
                items_this_url=len(items),
                new_unique=new_count,
                total_so_far=len(tagged),
            )

        log.info("apify_all_urls_complete", total_unique_items=len(tagged))
        return tagged

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
