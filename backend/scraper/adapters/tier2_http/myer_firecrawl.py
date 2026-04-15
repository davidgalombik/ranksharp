"""
Myer AU adapter — Firecrawl-based (map + scrape).

URL discovery: Firecrawl /v1/map searches within myer.com.au return product URLs.
Product scraping: Firecrawl /v1/scrape renders JS and returns full markdown.

Myer blocks headless Chrome, residential proxies, and Smartproxy's rendering API.
Only Firecrawl product page scrapes (not category listing pages) work reliably.
"""
import re
import asyncio
from typing import Optional, AsyncIterator
import httpx
import structlog
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

_MAP_ENDPOINT = "https://api.firecrawl.dev/v1/map"
_SCRAPE_ENDPOINT = "https://api.firecrawl.dev/v1/scrape"
_MAP_LIMIT = 50
_BATCH_SIZE = 5

# URL slug keywords that indicate a non-home product — if ANY found, exclude
_EXCLUDE_SLUG_WORDS = frozenset([
    # Apparel
    "dress", "gown", "skirt", "blouse", "shirt", "tshirt",
    "pants", "jeans", "denim", "trousers", "shorts", "leggings", "suit",
    "jacket", "coat", "blazer", "cardigan", "jumper", "sweater", "hoodie",
    "bikini", "swimsuit", "swimwear", "underwear", "bra", "briefs", "socks",
    # Footwear
    "shoes", "boots", "heels", "sneakers", "sandals", "loafers", "flats",
    "thongs", "slippers", "mules", "stilettos",
    # Bags & accessories
    "handbag", "purse", "wallet", "luggage", "backpack",
    "watch", "jewellery", "jewelry", "ring", "necklace", "bracelet", "earring",
    "sunglasses", "tie", "cufflinks", "scarf",
    # Beauty & personal care
    "makeup", "foundation", "lipstick", "mascara", "eyeshadow", "blush",
    "skincare", "moisturiser", "moisturizer", "serum", "cleanser", "toner",
    "shampoo", "conditioner", "hairdryer", "straightener", "perfume",
    "parfum", "cologne", "toilette", "eau",
    # Electronics, appliances & toys
    "laptop", "tablet", "phone", "headphones", "speakers", "keyboard",
    "airfryer", "microwave", "blender", "kettle", "toaster",
    "toy", "doll", "lego", "puzzle",
    # Furniture (chairs, tables, beds)
    "chair", "chairs", "sofa", "couch", "bed", "mattress", "table", "desk",
])

# URL slug keywords indicating a home/decor/storage product — at least ONE required
_INCLUDE_SLUG_WORDS = frozenset([
    # Storage
    "storage", "basket", "bin", "box", "container", "organizer", "organiser",
    "shelf", "shelves", "rack", "tray", "caddy",
    # Vessels & serveware
    "vase", "bowl", "jar", "canister", "crock", "pot", "planter",
    "bottle", "decanter", "pitcher", "jug",
    # Candles & fragrance
    "candle", "candles", "candleholder", "lantern", "hurricane", "diffuser",
    "votives", "fragrance", "scented", "wax",
    # Decorative
    "decor", "decorative", "ornament", "figurine", "sculpture", "statue",
    "frame", "mirror", "clock", "bookend",
    # Textiles (home)
    "cushion", "throw", "blanket", "rug",
    # Materials commonly home-specific
    "ceramic", "stoneware", "terracotta", "rattan", "wicker", "bamboo",
])


def _is_home_url(url: str) -> bool:
    """Return True only if the URL slug looks like a home/decor/storage product:
    no non-home keyword AND at least one home keyword present."""
    slug = url.rstrip("/").split("/p/")[-1].lower()
    words = set(re.split(r'[-_\s]', slug))
    if words & _EXCLUDE_SLUG_WORDS:
        return False
    return bool(words & _INCLUDE_SLUG_WORDS)

# Search terms per category label — multiple terms per category to maximise
# coverage since each /v1/map call returns up to 50 URLs.
CATEGORIES: dict[str, list[str]] = {
    "Storage & Organisation": [
        "myer storage basket bin wicker woven organizer",
        "myer kitchen canister jar storage container lid",
        "myer bathroom storage basket organizer box tray",
        "myer desk office storage organizer box",
        "myer pantry storage jar container set",
    ],
    "Home Decor": [
        "myer home decor vase ceramic glass flower",
        "myer candle holder candlestick lantern hurricane",
        "myer decorative bowl tray ornament figurine object",
        "myer wall art mirror picture frame clock",
        "myer decorative accessories tabletop accent object",
    ],
    "Candles & Fragrance": [
        "myer scented candle soy wax fragrance diffuser reed",
        "myer candle votive pillar tealight holder set",
    ],
    "Vases & Pots": [
        "myer vase ceramic stoneware glass terracotta",
        "myer planter pot indoor plant succulent",
    ],
}

# Current price — first $XX.XX in the markdown
_PRICE_RE = re.compile(r'\$(\d[\d,]*(?:\.\d{1,2})?)')

# Product images on Myer's CDN (jpg/jpeg/png, exclude webp thumbnails for primary)
_IMG_JPG_RE = re.compile(
    r'https://myer-media\.com\.au/wcsstore/MyerCatalogAssetStore/images/[^\s\)\'"<>]+\.(?:jpg|jpeg|png)',
    re.I,
)
_IMG_ALL_RE = re.compile(
    r'https://myer-media\.com\.au/wcsstore/MyerCatalogAssetStore/images/[^\s\)\'"<>]+\.(?:jpg|jpeg|png|webp)',
    re.I,
)


class MyerFirecrawlAdapter(BaseAdapter):
    """
    Tier-2 adapter for Myer AU using Firecrawl.
    URL discovery: /v1/map  |  Product scraping: /v1/scrape
    """
    RETAILER_SLUG = "myer"

    def __init__(self, retailer_config: dict):
        super().__init__(retailer_config)
        self._http_client: Optional[httpx.AsyncClient] = None

    async def before_scrape(self):
        self._http_client = httpx.AsyncClient(timeout=60)

    async def after_scrape(self):
        if self._http_client:
            await self._http_client.aclose()

    # ── URL discovery via Firecrawl /v1/map ──────────────────────────────────

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def _map_search(self, search_term: str) -> list[str]:
        """Call Firecrawl /v1/map and return Myer product URLs matching the term."""
        try:
            resp = await self._http_client.post(
                _MAP_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": "https://www.myer.com.au",
                    "search": search_term,
                    "limit": _MAP_LIMIT,
                },
            )
            if resp.status_code != 200:
                log.warning("myer_map_error", term=search_term, status=resp.status_code)
                return []
            links = resp.json().get("links", [])
            return [l for l in links if "/p/" in l and "myer.com.au" in l and _is_home_url(l)]
        except Exception as exc:
            log.warning("myer_map_failed", term=search_term, error=str(exc))
            return []

    async def get_product_urls(self, category_url: str) -> list[str]:
        """Run all search terms for a category and return deduplicated product URLs."""
        search_terms = CATEGORIES.get(category_url, [])
        seen: set[str] = set()
        results: list[str] = []
        for term in search_terms:
            await asyncio.sleep(0.3)
            urls = await self._map_search(term)
            for url in urls:
                if url not in seen:
                    seen.add(url)
                    results.append(url)
        log.info("myer_category_urls", category=category_url, count=len(results))
        return results

    # ── Product page parsing via Firecrawl /v1/scrape ────────────────────────

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """Scrape a Myer product page and extract structured fields from markdown."""
        try:
            resp = await self._http_client.post(
                _SCRAPE_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {settings.firecrawl_api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "url": product_url,
                    "formats": ["markdown", "html"],
                    "waitFor": 3000,
                },
            )
            if resp.status_code != 200:
                return None
            data = resp.json().get("data", {}) or {}
            md = data.get("markdown", "") or ""
            html = data.get("html", "") or ""
            if not md or len(md) < 200:
                return None

            # ── Name: first h1 in the markdown ───────────────────────────────
            name_match = re.search(r'^#\s+(.+)$', md, re.MULTILINE)
            name = name_match.group(1).strip() if name_match else ""
            if not name:
                return None

            # ── Brand: linked brand name before the h1 ────────────────────────
            brand_match = re.search(
                r'\[([^\]]+)\]\(https://www\.myer\.com\.au/b/[^\)]+\)', md
            )
            brand = brand_match.group(1).strip() if brand_match else None

            # If brand is embedded in the h1 (e.g. "# HeritageName"), strip it
            if brand and name.startswith(brand):
                name = name[len(brand):].strip()

            # ── Price: first dollar amount in the markdown ────────────────────
            price_matches = _PRICE_RE.findall(md[:4000])
            price: Optional[float] = None
            if price_matches:
                try:
                    price = float(price_matches[0].replace(",", ""))
                except ValueError:
                    pass

            # ── Images: deduplicated JPG product images from CDN ──────────────
            jpg_imgs = list(dict.fromkeys(
                img.split("?")[0]  # strip query string
                for img in _IMG_JPG_RE.findall(html)
            ))
            # Prefer numbered _1_ _2_ shots over thumbnails
            # Each unique filename base = one shot
            image_urls = jpg_imgs[:8]

            # ── Description: product details section ─────────────────────────
            desc_match = re.search(
                r'##\s+(?:Product Details|Description|About this product)(.*?)(?=\n##|\Z)',
                md, re.DOTALL | re.IGNORECASE,
            )
            description = desc_match.group(1).strip() if desc_match else None

            # ── is_best_seller: "Top Rated" badge in the markdown ────────────
            is_best_seller = bool(re.search(r'\bTop\s+Rated\b', md[:2000], re.IGNORECASE))

            product = RawProduct(
                url=product_url,
                name=name,
                retailer_slug=self.RETAILER_SLUG,
                brand=brand,
                price=price,
                currency="AUD",
                image_urls=image_urls,
                description=description,
                raw_attributes={},
            )
            product.is_best_seller = is_best_seller
            return product

        except Exception as exc:
            log.warning("myer_parse_failed", url=product_url, error=str(exc))
            return None

    # ── Scrape orchestration (batched concurrent product scraping) ────────────

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Override base scrape to batch product page requests concurrently."""
        await self.before_scrape()
        try:
            category_urls = await self.get_category_urls()
            for cat_url in category_urls:
                product_urls = await self.get_product_urls(cat_url)
                is_best_seller_cat = "best-seller" in cat_url.lower() or "best-sellers" in cat_url.lower()

                for i in range(0, len(product_urls), _BATCH_SIZE):
                    batch = product_urls[i:i + _BATCH_SIZE]
                    tasks = [self.parse_product(url) for url in batch]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    for result in results:
                        if isinstance(result, Exception):
                            log.warning("myer_batch_error", error=str(result))
                        elif result:
                            result.category = cat_url
                            if is_best_seller_cat:
                                result.is_best_seller = True
                            yield result
                    await asyncio.sleep(0.5)
        finally:
            await self.after_scrape()
