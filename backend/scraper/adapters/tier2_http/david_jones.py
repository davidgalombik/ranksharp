"""
David Jones AU adapter (davidjones.com) — Plain HTTP, static HTML.

David Jones is a Next.js SPA, but:
  - Category listing pages embed ~22 product links in static HTML per page
  - Individual product pages embed full ProductGroup JSON-LD in static HTML
  - Pagination works via ?page=N query param

Strategy:
  1. Paginate each category URL via plain HTTP, extract product links from HTML
  2. Fetch each product page for ProductGroup JSON-LD (name, SKU, price, images, category)
  3. ProductGroup has magnify-size images (productimages/magnify/...) — no Playwright needed
"""
import re
import asyncio
import json
import httpx
from typing import Optional, AsyncIterator
from scraper.base_adapter import BaseAdapter, RawProduct

BASE_URL = "https://www.davidjones.com"

CATEGORY_URLS = [
    ("https://www.davidjones.com/home/kitchen", "Kitchen"),
    ("https://www.davidjones.com/home/dining", "Dining"),
    ("https://www.davidjones.com/home/living", "Living"),
    ("https://www.davidjones.com/home/kitchen/storage-organisation", "Kitchen Storage"),
    ("https://www.davidjones.com/home/new-in", "New In"),  # scoped to home department
]

# DJ category segments worth keeping (from ProductGroup category breadcrumb)
_INCLUDE_SEGMENTS = {
    "kitchen", "dining", "living", "home", "homewares", "cookware",
    "bakeware", "storage", "tableware", "glassware", "entertaining",
    "candles", "fragrances", "decor", "vases", "linen",
}

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-AU,en;q=0.9",
}

_PRODUCT_PATH_RE = re.compile(r'/product/[a-z0-9-]+-\d+')
_BATCH = 10


class DavidJonesAdapter(BaseAdapter):
    RETAILER_SLUG = "david-jones"

    def __init__(self, rc):
        super().__init__(rc)
        self._client: Optional[httpx.AsyncClient] = None
        self._cat_cache: dict[str, str] = {}

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def _get_product_urls_for_category(self, cat_url: str, label: str) -> list[str]:
        """Paginate through a DJ category page extracting product links from static HTML."""
        seen: set[str] = set()
        urls: list[str] = []

        for page in range(1, 60):  # max ~60 pages (~1320 products per category)
            url = f"{cat_url}?page={page}" if page > 1 else cat_url
            try:
                resp = await self._client.get(url)
                if resp.status_code != 200:
                    break
            except Exception as exc:
                self.log.warning("dj_category_fetch_error", url=url, error=str(exc))
                break

            paths = _PRODUCT_PATH_RE.findall(resp.text)
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
        """Fetch a product page and extract ProductGroup JSON-LD."""
        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return None
        except Exception as exc:
            self.log.warning("dj_product_fetch_error", url=url, error=str(exc))
            return None

        for match in re.finditer(
            r'type="application/ld\+json">([\s\S]*?)</script>', resp.text
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

            # Price from first variant's offers
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

            # SKU from first variant
            variants = d.get("hasVariant", [])
            sku = variants[0].get("sku") if variants else None
            external_id = d.get("productID")

            # Images — ProductGroup includes thumb/medium/magnify for each shot.
            # Use magnify (largest) and deduplicate, capping at 5.
            all_imgs = d.get("image", [])
            if isinstance(all_imgs, str):
                all_imgs = [all_imgs]
            magnify_imgs = [i for i in all_imgs if "/magnify/" in i]
            img_urls = (magnify_imgs or all_imgs)[:5]

            # Category — use the label from the category page we came from
            cat_label = self._cat_cache.get(url)
            if not cat_label:
                # Fallback: parse DJ's breadcrumb category string
                # e.g. "Brand > Joseph Joseph > Kitchen > Food Preparation"
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
