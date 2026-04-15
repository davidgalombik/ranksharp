"""
World Market browser adapter (worldmarket.com) — Playwright.

Cost Plus World Market uses a heavily JS-rendered product grid.
The homepage loads with minimal HTML; product content is injected client-side.
"""
import json
import re
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from scraper.base_adapter import BaseAdapter, RawProduct
import structlog

log = structlog.get_logger()


def _extract_img_urls(raw):
    if not raw: return []
    if isinstance(raw, str): return [raw]
    if isinstance(raw, dict):
        u = raw.get("url") or raw.get("contentUrl") or raw.get("src")
        return [u] if u else []
    if isinstance(raw, list):
        out = []
        for x in raw:
            if isinstance(x, str): out.append(x)
            elif isinstance(x, dict):
                u = x.get("url") or x.get("contentUrl") or x.get("src")
                if u: out.append(u)
        return out
    return []

CATEGORY_URLS = [
    "https://www.worldmarket.com/category/home/bestsellers.do",  # best-seller flag auto-applied
    "https://www.worldmarket.com/category/food/food-storage.do",
    "https://www.worldmarket.com/category/food/kitchen-tools-gadgets.do",
    "https://www.worldmarket.com/category/decorating/baskets.do",
    "https://www.worldmarket.com/category/decorating/candles-and-holders.do",
    "https://www.worldmarket.com/category/decorating/vases-and-decorative-bottles.do",
    "https://www.worldmarket.com/category/decorating/decorative-accessories.do",
    "https://www.worldmarket.com/category/decorating/home-decor.do",
]


class WorldMarketAdapter(BaseAdapter):
    RETAILER_SLUG = "world-market"

    def __init__(self, rc):
        super().__init__(rc)
        self._playwright = None
        self._browser = None
        self._context = None

    async def before_scrape(self):
        self._playwright = await async_playwright().start()
        launch_args = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        if proxy := self._build_proxy():
            launch_args["proxy"] = {"server": proxy}

        self._browser = await self._playwright.chromium.launch(**launch_args)
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/Chicago",
        )
        await self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

    async def after_scrape(self):
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

    async def get_category_urls(self) -> list[str]:
        return CATEGORY_URLS

    async def get_product_urls(self, category_url: str) -> list[str]:
        page = await self._context.new_page()
        urls: list[str] = []
        try:
            for page_num in range(1, 6):
                url = f"{category_url}?start={(page_num-1)*48}&sz=48" if page_num > 1 else category_url
                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await page.wait_for_timeout(3000)
                except PwTimeout:
                    log.warning("world_market_timeout", url=url)
                    break

                # Scroll to trigger lazy loading
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                await page.wait_for_timeout(2000)

                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href*="/p/"]'))
                         .map(a => a.href.split('?')[0])
                         .filter(h => h && h.includes('worldmarket.com') && h.endsWith('.html'))
                """)

                if not links:
                    break

                added = 0
                for href in links:
                    if href not in urls:
                        urls.append(href)
                        added += 1

                if added == 0:
                    break

                has_next = await page.evaluate("""
                    () => !!document.querySelector(
                        'a[aria-label="Next"], [class*="pagination-next"]:not(.disabled), button[class*="next"]:not([disabled])'
                    )
                """)
                if not has_next:
                    break

        finally:
            await page.close()
        return list(dict.fromkeys(urls))

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        page = await self._context.new_page()
        try:
            await page.goto(product_url, wait_until="networkidle", timeout=60_000)
            await page.wait_for_timeout(1500)

            # Try JSON-LD
            ld_texts = await page.evaluate("""
                () => Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                     .map(s => s.textContent)
            """)
            for ld_text in ld_texts:
                try:
                    d = json.loads(ld_text or "")
                    if isinstance(d, list):
                        d = next((x for x in d if x.get("@type") == "Product"), None)
                    if d and d.get("@type") == "Product":
                        offers = d.get("offers", {})
                        if isinstance(offers, list):
                            offers = offers[0] if offers else {}
                        price = None
                        if raw := offers.get("price"):
                            try:
                                price = float(str(raw).replace(",", ""))
                            except (ValueError, TypeError):
                                pass
                        imgs = _extract_img_urls(d.get("image"))
                        return RawProduct(
                            url=product_url,
                            name=d.get("name", ""),
                            retailer_slug=self.RETAILER_SLUG,
                            external_id=d.get("sku"),
                            description=d.get("description"),
                            price=price,
                            currency="USD",
                            image_urls=imgs,
                            raw_attributes={},
                        )
                except (json.JSONDecodeError, TypeError, StopIteration):
                    pass

            name = await page.text_content("h1") or ""
            if not name.strip():
                return None

            imgs = await page.evaluate("""
                () => {
                    const selectors = [
                        'img[src*="worldmarket"]',
                        '.product-image-container img',
                        '.product-detail img',
                        '[data-testid*="product"] img',
                        '.product-gallery img'
                    ];
                    for (const sel of selectors) {
                        const found = Array.from(document.querySelectorAll(sel))
                            .map(img => img.src || img.dataset.src || '')
                            .filter(src => src && !src.includes('placeholder'));
                        if (found.length) return found.slice(0, 5);
                    }
                    return [];
                }
            """)

            return RawProduct(
                url=product_url,
                name=name.strip(),
                retailer_slug=self.RETAILER_SLUG,
                currency="USD",
                image_urls=imgs,
                raw_attributes={},
            )

        except PwTimeout:
            log.warning("world_market_product_timeout", url=product_url)
            return None
        finally:
            await page.close()
