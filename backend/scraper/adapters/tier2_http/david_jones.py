"""
David Jones AU adapter (davidjones.com) — Smartproxy Universal Scraping API.

David Jones serves a bot-detection page (200 OK, 0 products) to both cloud
datacenter IPs and residential proxy IPs. The Universal Scraping API handles
full JS rendering on residential IPs with bot bypass.

Strategy:
  1. Fetch each category page via Scraping API, extract product links from HTML
  2. Fetch each product page via Scraping API for ProductGroup JSON-LD
  3. Pagination via ?page=N, capped at 3 pages per category
"""
import re
import asyncio
import json
from typing import Optional, AsyncIterator
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct

BASE_URL = "https://www.davidjones.com"

CATEGORY_URLS = [
    ("https://www.davidjones.com/home/kitchen", "Kitchen"),
    ("https://www.davidjones.com/home/dining", "Dining"),
    ("https://www.davidjones.com/home/living", "Living"),
    ("https://www.davidjones.com/home/kitchen/storage-organisation", "Kitchen Storage"),
    ("https://www.davidjones.com/home/new-in", "New In"),
]

_INCLUDE_SEGMENTS = {
    "kitchen", "dining", "living", "home", "homewares", "cookware",
    "bakeware", "storage", "tableware", "glassware", "entertaining",
    "candles", "fragrances", "decor", "vases", "linen",
}

_PRODUCT_PATH_RE = re.compile(r'/product/[a-z0-9-]+-\d+')
_BATCH = 5  # Reduced batch size — Scraping API is slow


class DavidJonesAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "david-jones"

    def __init__(self, rc):
        super().__init__(rc)
        self._cat_cache: dict[str, str] = {}

    async def _get_product_urls_for_category(self, cat_url: str, label: str) -> list[str]:
        seen: set[str] = set()
        urls: list[str] = []

        for page in range(1, 4):  # Max 3 pages per category
            url = f"{cat_url}?page={page}" if page > 1 else cat_url
            html = await self._fetch_rendered(url, country="AU")
            if not html:
                break

            paths = _PRODUCT_PATH_RE.findall(html)
            added = 0
            for path in dict.fromkeys(paths):
                full = BASE_URL + path
                if full not in seen:
                    seen.add(full)
                    urls.append(full)
                    self._cat_cache.setdefault(full, label)
                    added += 1

            if added == 0:
                break

        self.log.info("dj_category_done", category=label, products=len(urls))
        return urls

    async def _fetch_product(self, url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(url, country="AU")
        if not html:
            return None

        for match in re.finditer(
            r'type="application/ld\+json">([\s\S]*?)</script>', html
        ):
            try:
                d = json.loads(match.group(1).strip())
            except (json.JSONDecodeError, ValueError):
                continue

            if d.get("@type") != "ProductGroup":
                continue

            name = d.get("name", "").strip()
            if not name:
                continue

            price: Optional[float] = None
            for variant in d.get("hasVariant", []):
                offers = variant.get("offers", {})
                if isinstance(offers, list):
                    offers = offers[0] if offers else {}
                raw = offers.get("price")
                if raw:
                    try:
                        price = float(str(raw).replace(",", ""))
                        break
                    except (ValueError, TypeError):
                        pass

            variants = d.get("hasVariant", [])
            sku = variants[0].get("sku") if variants else None
            external_id = d.get("productID")

            all_imgs = d.get("image", [])
            if isinstance(all_imgs, str):
                all_imgs = [all_imgs]
            magnify_imgs = [i for i in all_imgs if "/magnify/" in i]
            img_urls = (magnify_imgs or all_imgs)[:5]

            cat_label = self._cat_cache.get(url)
            if not cat_label:
                dj_cat = d.get("category", "")
                for seg in [s.strip() for s in dj_cat.split(">")]:
                    if seg.lower() in _INCLUDE_SEGMENTS:
                        cat_label = seg.title()
                        break

            return RawProduct(
                url=url,
                name=name,
                retailer_slug=self.RETAILER_SLUG,
                external_id=external_id,
                sku=sku,
                price=price,
                currency="AUD",
                category=cat_label,
                image_urls=img_urls,
                raw_attributes={},
            )

        return None

    async def scrape(self) -> AsyncIterator[RawProduct]:
        await self.before_scrape()
        seen_urls: set[str] = set()
        try:
            for cat_url, label in CATEGORY_URLS:
                product_urls = await self._get_product_urls_for_category(cat_url, label)
                for i in range(0, len(product_urls), _BATCH):
                    batch = [u for u in product_urls[i:i + _BATCH] if u not in seen_urls]
                    seen_urls.update(batch)
                    results = await asyncio.gather(
                        *[self._fetch_product(u) for u in batch],
                        return_exceptions=True,
                    )
                    for result in results:
                        if isinstance(result, Exception):
                            self.log.warning("dj_parse_error", error=str(result))
                        elif result:
                            yield result
        finally:
            await self.after_scrape()

    # ── Required abstract stubs ──
    async def get_category_urls(self) -> list[str]:
        return []

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None
