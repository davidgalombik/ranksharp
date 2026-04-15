"""
Williams Sonoma Group browser adapter — covers Pottery Barn, West Elm,
Williams-Sonoma, and Pottery Barn AU.

All these sites share the same Next.js platform (Williams-Sonoma Inc.) and use
client-side rendered product grids. Playwright is required.
"""
import json
import re
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from scraper.base_adapter import BaseAdapter, RawProduct
import structlog

log = structlog.get_logger()

# Per-site category paths within each brand
CATEGORY_PATHS_BY_SLUG = {
    "pottery-barn": [
        "https://www.potterybarn.com/shop/best-sellers/",  # best-seller flag auto-applied
        "https://www.potterybarn.com/shop/storage-organization/",
        "https://www.potterybarn.com/shop/decorating/vases/",
        "https://www.potterybarn.com/shop/decorating/candles-and-candle-holders/",
        "https://www.potterybarn.com/shop/kitchen/canisters-and-jars/",
        "https://www.potterybarn.com/shop/kitchen/kitchen-storage/",
        "https://www.potterybarn.com/shop/decorating/decorative-accessories/",
    ],
    "west-elm": [
        "https://www.westelm.com/shop/best-sellers/",  # best-seller flag auto-applied
        "https://www.westelm.com/shop/storage-organization/",
        "https://www.westelm.com/shop/decorating/vases/",
        "https://www.westelm.com/shop/decorating/candles-and-candle-holders/",
        "https://www.westelm.com/shop/decorating/decorative-accessories/",
        "https://www.westelm.com/shop/kitchen/kitchen-storage/",
    ],
    "williams-sonoma": [
        "https://www.williams-sonoma.com/shop/best-sellers/",  # best-seller flag auto-applied
        "https://www.williams-sonoma.com/shop/food-pantry/storage-containers/",
        "https://www.williams-sonoma.com/shop/food-pantry/canisters/",
        "https://www.williams-sonoma.com/shop/entertaining/decorative-accessories/",
        "https://www.williams-sonoma.com/shop/entertaining/vases/",
        "https://www.williams-sonoma.com/shop/entertaining/candles/",
    ],
    "pottery-barn-au": [
        # Category-scoped best sellers — avoids the sitewide /shop/best-sellers/ page
        # which includes furniture, bedding, rugs, and other out-of-scope categories
        "https://www.potterybarn.com.au/shop/best-sellers/storage/",             # best-seller flag auto-applied
        "https://www.potterybarn.com.au/shop/best-sellers/decorating/",          # best-seller flag auto-applied
        "https://www.potterybarn.com.au/shop/best-sellers/vases/",               # best-seller flag auto-applied
        "https://www.potterybarn.com.au/shop/best-sellers/candles-and-holders/", # best-seller flag auto-applied
        "https://www.potterybarn.com.au/shop/storage/",
        "https://www.potterybarn.com.au/shop/decorating/",
    ],
}


class WilliamsSonomaGroupAdapter(BaseAdapter):
    RETAILER_SLUG = "ws-group"  # overridden per-site by registry

    def __init__(self, rc):
        super().__init__(rc)
        self._playwright = None
        self._browser = None
        self._context = None

    async def before_scrape(self):
        self._playwright = await async_playwright().start()
        slug = self.config.get("slug", self.RETAILER_SLUG)
        is_au = "au" in slug
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
            locale="en-AU" if is_au else "en-US",
            timezone_id="Australia/Sydney" if is_au else "America/Los_Angeles",
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
        slug = self.config.get("slug", self.RETAILER_SLUG)
        return CATEGORY_PATHS_BY_SLUG.get(slug, [])

    async def get_product_urls(self, category_url: str) -> list[str]:
        page = await self._context.new_page()
        urls: list[str] = []
        try:
            for page_num in range(1, 6):
                url = f"{category_url}?pageNumber={page_num}" if page_num > 1 else category_url
                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await page.wait_for_timeout(3000)
                except PwTimeout:
                    log.warning("ws_group_timeout", url=url, slug=self.config.get("slug", self.RETAILER_SLUG))
                    break

                # Scroll to load lazy items
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
                await page.wait_for_timeout(2000)

                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href*="/shop/"], a[class*="product"]'))
                         .map(a => a.href)
                         .filter(h => h && !h.endsWith('/shop/') && !h.includes('#') && h.split('/').length > 6)
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
                        '[class*="pagination"] [class*="next"]:not([disabled]):not([aria-disabled="true"]), a[aria-label="Next page"]'
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
            await page.wait_for_timeout(2000)

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
                        for key in ("price", "lowPrice"):
                            if raw := offers.get(key):
                                try:
                                    price = float(str(raw).replace(",", ""))
                                    break
                                except (ValueError, TypeError):
                                    pass
                        imgs = d.get("image", [])
                        if isinstance(imgs, str):
                            imgs = [imgs]
                        slug = self.config.get("slug", self.RETAILER_SLUG)
                        currency = "AUD" if "au" in slug else offers.get("priceCurrency", "USD")
                        return RawProduct(
                            url=product_url,
                            name=d.get("name", ""),
                            retailer_slug=self.RETAILER_SLUG,
                            external_id=d.get("sku"),
                            description=d.get("description"),
                            price=price,
                            currency=currency,
                            image_urls=imgs,
                            raw_attributes={},
                        )
                except (json.JSONDecodeError, TypeError, StopIteration):
                    pass

            name = await page.text_content("h1") or ""
            if not name.strip():
                return None

            price = None
            price_text = await page.text_content("[data-testid*='price'], [class*='pip-price']") or ""
            m = re.search(r"[\d,]+\.?\d*", price_text.replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass

            slug = self.config.get("slug", self.RETAILER_SLUG)
            currency = "AUD" if "au" in slug else "USD"
            return RawProduct(
                url=product_url,
                name=name.strip(),
                retailer_slug=self.RETAILER_SLUG,
                price=price,
                currency=currency,
                raw_attributes={},
            )

        except PwTimeout:
            log.warning("ws_group_product_timeout", url=product_url, slug=self.config.get("slug", self.RETAILER_SLUG))
            return None
        finally:
            await page.close()
