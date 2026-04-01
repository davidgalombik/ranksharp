"""
Anthropologie adapter — uses Apify's generic Playwright scraper actor.

Anthropologie (URBN) uses bot-detection that blocks direct HTTP scraping.
We use Apify's playwright-scraper actor with residential proxies and a
custom pageFunction to extract products from category listing pages.

Setup:
  1. Ensure APIFY_API_TOKEN is set in .env (shared with Wayfair adapter).
  2. Update the Anthropologie retailer row in the DB:
       adapter_class = "scraper.adapters.tier1_api.anthropologie_apify.AnthropologieApifyAdapter"
"""
import asyncio
from typing import AsyncIterator, Optional
import structlog
from apify_client import ApifyClient
from scraper.base_adapter import BaseAdapter, RawProduct
from config import settings

log = structlog.get_logger()

# Apify's generic Playwright scraper — maintained by Apify, no monthly rental fee.
_ACTOR_ID = "apify/playwright-scraper"

# Home & decor category pages — focused on the categories most relevant
# to trend tracking (decor, furniture, candles, textiles, kitchen).
CATEGORY_URLS = [
    # Reduced to 2 URLs for initial test — expand once selectors are confirmed
    {"url": "https://www.anthropologie.com/new-home"},
    {"url": "https://www.anthropologie.com/home-catalog"},
]

# Max requests the Playwright crawler will make (pages loaded × resources).
# Each category page + scrolling = ~10–20 requests. Keep low for initial test.
MAX_REQUESTS_PER_CRAWL = 60   # ~6 category pages with scrolling
MAX_ITEMS_PER_RUN = 300

# JavaScript page function — runs inside the Playwright browser context on
# each loaded page. Extracts product cards from Anthropologie's listing grid.
# Anthropologie uses a React app; products are rendered into the DOM after
# the initial JS bundle executes.
PAGE_FUNCTION = """
async function pageFunction(context) {
    const { page, request, log } = context;

    log.info('Scraping page: ' + request.url);

    // Scroll down to trigger lazy loading
    for (let i = 0; i < 10; i++) {
        await page.evaluate(() => window.scrollBy(0, window.innerHeight));
        await page.waitForTimeout(600);
    }
    await page.waitForTimeout(2000);

    const debug = await page.evaluate(() => {
        // Sample the first 20 anchor hrefs to understand URL patterns
        const allLinks = Array.from(document.querySelectorAll('a[href]'))
            .map(a => a.getAttribute('href'))
            .filter(h => h && !h.startsWith('#') && !h.startsWith('mailto'))
            .slice(0, 30);

        // Try every likely product-card selector
        const selectors = [
            'a[href*="/shop/"]',
            'a[href*="/products/"]',
            '[data-testid="product-tile"] a',
            '[data-testid="product-card"] a',
            '[class*="ProductTile"] a',
            '[class*="ProductCard"] a',
            '[class*="product-tile"] a',
            '[class*="product-card"] a',
            '[class*="ProductGrid"] li a',
            'li[class*="product"] a',
        ];

        const counts = {};
        for (const sel of selectors) {
            counts[sel] = document.querySelectorAll(sel).length;
        }

        // Check if Next.js data is available (products in __NEXT_DATA__)
        const nextData = window.__NEXT_DATA__;
        const hasNextData = !!nextData;
        let nextProductCount = 0;
        if (nextData) {
            const str = JSON.stringify(nextData);
            nextProductCount = (str.match(/"displayName"/g) || []).length;
        }

        return { allLinks, selectorCounts: counts, hasNextData, nextProductCount };
    });

    log.info('DEBUG selector counts: ' + JSON.stringify(debug.selectorCounts));
    log.info('DEBUG sample links: ' + JSON.stringify(debug.allLinks));
    log.info('DEBUG Next.js data present: ' + debug.hasNextData + ' product hits: ' + debug.nextProductCount);

    // ── Attempt 1: Extract from __NEXT_DATA__ (cleanest if available) ────
    if (debug.hasNextData && debug.nextProductCount > 0) {
        const products = await page.evaluate(() => {
            const data = window.__NEXT_DATA__;
            const results = [];

            function walk(obj) {
                if (!obj || typeof obj !== 'object') return;
                if (obj.displayName && obj.pdpUrl) {
                    results.push({
                        name: obj.displayName,
                        productUrl: 'https://www.anthropologie.com' + obj.pdpUrl,
                        priceText: obj.defaultSkuPrice || obj.prices?.main || null,
                        imageUrl: obj.defaultSkuImageUrl || (obj.images && obj.images[0]) || null,
                        color: obj.defaultColor || null,
                        category: obj.category || null,
                    });
                }
                if (Array.isArray(obj)) { obj.forEach(walk); }
                else { Object.values(obj).forEach(walk); }
            }
            walk(data);

            const seen = new Set();
            return results.filter(p => {
                if (seen.has(p.productUrl)) return false;
                seen.add(p.productUrl);
                return true;
            });
        });
        log.info('Extracted ' + products.length + ' products from __NEXT_DATA__');
        return products;
    }

    // ── Attempt 2: DOM scraping using best selector from debug ────────────
    const bestSelector = Object.entries(debug.selectorCounts)
        .filter(([, count]) => count > 0)
        .sort(([, a], [, b]) => b - a)[0];

    if (!bestSelector) {
        log.warning('No product selectors matched on ' + request.url);
        return [];
    }

    log.info('Using selector: ' + bestSelector[0] + ' (' + bestSelector[1] + ' matches)');

    const products = await page.evaluate((selector) => {
        const results = [];
        const cards = document.querySelectorAll(selector);

        cards.forEach(card => {
            try {
                const href = card.getAttribute('href');
                if (!href) return;
                const productUrl = href.startsWith('http')
                    ? href : 'https://www.anthropologie.com' + href;

                const nameEl = card.querySelector('h2, h3, [class*="name"], [class*="Name"], [class*="title"], [class*="Title"]');
                const name = nameEl ? nameEl.textContent.trim() : card.getAttribute('aria-label') || null;
                if (!name) return;

                const priceEl = card.querySelector('[class*="price"], [class*="Price"]');
                const priceText = priceEl ? priceEl.textContent.trim() : null;

                const imgEl = card.querySelector('img');
                const imageUrl = imgEl ? (imgEl.src || imgEl.dataset.src || null) : null;

                results.push({ name, productUrl, priceText, imageUrl });
            } catch (e) {}
        });

        const seen = new Set();
        return results.filter(p => {
            if (seen.has(p.productUrl)) return false;
            seen.add(p.productUrl);
            return true;
        });
    }, bestSelector[0]);

    log.info('Extracted ' + products.length + ' products via DOM from ' + request.url);
    return products;
}
"""


class AnthropologieApifyAdapter(BaseAdapter):
    """
    Tier-1 adapter: uses Apify's playwright-scraper to bypass Anthropologie's
    bot detection, navigating category listing pages and extracting products
    via a custom JavaScript pageFunction.
    """

    RETAILER_SLUG = "anthropologie"

    # ── Unused abstract methods (required by BaseAdapter) ──────────────────

    async def get_category_urls(self) -> list[str]:
        return [c["url"] for c in CATEGORY_URLS]

    async def get_product_urls(self, category_url: str) -> list[str]:
        return []

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        return None

    # ── Main scrape flow ───────────────────────────────────────────────────

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

    # ── Apify helpers ──────────────────────────────────────────────────────

    def _run_actor(self) -> list[dict]:
        client = ApifyClient(settings.apify_api_token)

        log.info(
            "apify_run_starting",
            actor=_ACTOR_ID,
            category_count=len(CATEGORY_URLS),
        )

        run = client.actor(_ACTOR_ID).call(
            run_input={
                "startUrls": CATEGORY_URLS,
                "pageFunction": PAGE_FUNCTION,
                "maxRequestsPerCrawl": MAX_REQUESTS_PER_CRAWL,
                "proxyConfiguration": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
                "launchContext": {
                    "launchOptions": {
                        "headless": True,
                    }
                },
                "browserPoolOptions": {
                    "useFingerprints": True,
                },
            },
            timeout_secs=1800,
        )

        status = run.get("status") if run else "no response"

        if status not in ("SUCCEEDED", "TIMED-OUT"):
            log.error("apify_run_failed", status=status, actor=_ACTOR_ID)
            return []

        if status == "TIMED-OUT":
            log.warning("apify_run_timed_out", actor=_ACTOR_ID,
                        hint="Fetching partial results anyway")

        dataset_id = run["defaultDatasetId"]

        # The playwright-scraper stores each page's return value as one dataset
        # item — which in our case is a list of products. We need to flatten.
        raw_items = list(client.dataset(dataset_id).iterate_items())
        log.info("apify_raw_pages", pages=len(raw_items), dataset=dataset_id)

        # Flatten: each item may be a list (our pageFunction returns an array)
        products = []
        for item in raw_items:
            if isinstance(item, list):
                products.extend(item)
            elif isinstance(item, dict) and item.get("name"):
                products.append(item)

        log.info("apify_run_complete", products=len(products), status=status)
        return products

    # ── Field mapping ──────────────────────────────────────────────────────

    def _map_item(self, item: dict) -> Optional[RawProduct]:
        name = item.get("name")
        url = item.get("productUrl") or item.get("url")

        if not name or not url:
            return None

        price = self._parse_price(item.get("priceText") or item.get("price"))

        image_url = item.get("imageUrl") or item.get("image")
        images = [image_url] if image_url else []

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="USD",
            image_urls=images,
            primary_image_url=image_url,
            category=item.get("category"),
            brand="Anthropologie",
            raw_attributes={
                "color": item.get("color"),
                "material": item.get("material"),
            },
        )

    @staticmethod
    def _parse_price(raw) -> Optional[float]:
        if raw is None:
            return None
        if isinstance(raw, (int, float)):
            return float(raw)
        if isinstance(raw, str):
            # Handle ranges like "$49.95 - $79.95" — take the lower price
            raw = raw.split("-")[0]
            cleaned = raw.replace("$", "").replace(",", "").strip()
            try:
                return float(cleaned)
            except ValueError:
                return None
        return None
