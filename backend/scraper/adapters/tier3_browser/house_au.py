"""
House AU browser adapter (house.com.au).

house.com.au is a Shopify store but the products.json API is disabled and product
listings are rendered via JavaScript. Playwright is used to load the category pages,
extract product links, then parse product JSON-LD from each page.
"""
import json
import re
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from scraper.base_adapter import BaseAdapter, RawProduct
import structlog

log = structlog.get_logger()

CATEGORY_URLS = [
    "https://www.house.com.au/collections/best-sellers",  # best-seller flag auto-applied
    "https://www.house.com.au/collections/kitchen-storage",
    "https://www.house.com.au/collections/home-decor",
    "https://www.house.com.au/collections/storage",
    "https://www.house.com.au/collections/bathroom",
    "https://www.house.com.au/collections/homewares",
    "https://www.house.com.au/collections/organisation",
    "https://www.house.com.au/collections/kitchen",
]


class HouseAUAdapter(BaseAdapter):
    RETAILER_SLUG = "house-au"

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
            locale="en-AU",
            timezone_id="Australia/Sydney",
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
                url = f"{category_url}?page={page_num}" if page_num > 1 else category_url
                try:
                    await page.goto(url, wait_until="networkidle", timeout=60_000)
                    await page.wait_for_timeout(2000)
                except PwTimeout:
                    log.warning("house_au_timeout", url=url)
                    break

                # Shopify product links on collection pages
                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href*="/products/"]'))
                         .map(a => a.href)
                         .filter(h => h && !h.includes('?') && !h.includes('#'))
                """)
                if not links:
                    break
                for href in links:
                    if href not in urls:
                        urls.append(href)

                has_next = await page.evaluate("""
                    () => !!document.querySelector(
                        'a[aria-label="Next"], .pagination__next, [class*="next"]:not([disabled])'
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
                        imgs = d.get("image", [])
                        if isinstance(imgs, str):
                            imgs = [imgs]
                        return RawProduct(
                            url=product_url,
                            name=d.get("name", ""),
                            retailer_slug=self.RETAILER_SLUG,
                            external_id=d.get("sku"),
                            description=d.get("description"),
                            price=price,
                            currency="AUD",
                            image_urls=imgs,
                            raw_attributes={},
                        )
                except (json.JSONDecodeError, TypeError, StopIteration):
                    pass

            # Shopify __NEXT_DATA__ or window.ShopifyAnalytics
            shopify_data = await page.evaluate("""
                () => {
                    if (window.ShopifyAnalytics && window.ShopifyAnalytics.meta && window.ShopifyAnalytics.meta.product) {
                        return window.ShopifyAnalytics.meta.product;
                    }
                    return null;
                }
            """)
            if shopify_data:
                price = None
                variants = shopify_data.get("variants", [])
                if variants:
                    cents = variants[0].get("price")
                    if cents:
                        price = cents / 100.0
                imgs = [
                    img.get("src", "")
                    for img in shopify_data.get("media", [])
                    if img.get("src")
                ]
                return RawProduct(
                    url=product_url,
                    name=shopify_data.get("title", ""),
                    retailer_slug=self.RETAILER_SLUG,
                    external_id=str(shopify_data.get("id", "")),
                    description=shopify_data.get("description"),
                    price=price,
                    currency="AUD",
                    image_urls=imgs,
                    raw_attributes={},
                )

            # DOM fallback
            name = await page.text_content("h1") or ""
            if not name.strip():
                return None
            return RawProduct(
                url=product_url,
                name=name.strip(),
                retailer_slug=self.RETAILER_SLUG,
                currency="AUD",
                raw_attributes={},
            )

        except PwTimeout:
            log.warning("house_au_product_timeout", url=product_url)
            return None
        finally:
            await page.close()
