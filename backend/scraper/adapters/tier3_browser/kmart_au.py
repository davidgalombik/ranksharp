"""
Kmart AU browser adapter (kmart.com.au) — Playwright for Akamai Bot Manager bypass.

Kmart AU is protected by Akamai Bot Manager (returns 403 to plain HTTP).
"""
import json
import re
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from scraper.base_adapter import BaseAdapter, RawProduct
import structlog

log = structlog.get_logger()

CATEGORY_URLS = [
    "https://www.kmart.com.au/category/home-living/storage-organisation/",
    "https://www.kmart.com.au/category/home-living/homewares/",
    "https://www.kmart.com.au/category/home-living/home-decor/",
    "https://www.kmart.com.au/category/kitchen/food-storage/",
    "https://www.kmart.com.au/category/kitchen/kitchen-organisation/",
]


class KmartAUAdapter(BaseAdapter):
    RETAILER_SLUG = "kmart-au"

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
                    log.warning("kmart_au_timeout", url=url)
                    break

                links = await page.evaluate("""
                    () => Array.from(document.querySelectorAll('a[href*="/product/"]'))
                         .map(a => a.href)
                         .filter(h => h && !h.includes('?'))
                """)
                if not links:
                    break
                for href in links:
                    if href not in urls:
                        urls.append(href)

                has_next = await page.evaluate("""
                    () => !!document.querySelector(
                        '[aria-label="Next page"], [aria-label="next"], .pagination__next:not([disabled])'
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

            name = await page.text_content("h1[data-testid='product-title'], h1") or ""
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
            log.warning("kmart_au_product_timeout", url=product_url)
            return None
        finally:
            await page.close()
