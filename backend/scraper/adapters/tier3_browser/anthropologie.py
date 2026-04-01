"""
Anthropologie browser adapter (anthropologie.com) — Playwright.

Anthropologie blocks datacenter IPs with plain HTTP (403), but allows real
browser traffic. Product pages also 403 from non-browser clients, so we extract
all product data (name, price, image) directly from the category page cards in
a single Playwright pass per category — avoiding individual product page visits
entirely wherever the card data is complete.
"""
import json
import re
from typing import Optional
from playwright.async_api import async_playwright, TimeoutError as PwTimeout
from playwright_stealth import stealth_async
from scraper.base_adapter import BaseAdapter, RawProduct
import structlog

log = structlog.get_logger()

CATEGORY_URLS = [
    "https://www.anthropologie.com/new-home",
    "https://www.anthropologie.com/new-candles",
    "https://www.anthropologie.com/new-kitchen-dining",
    "https://www.anthropologie.com/new-room-wall-decor",
    "https://www.anthropologie.com/new-arrivals-home",
]


class AnthropologieAdapter(BaseAdapter):
    RETAILER_SLUG = "anthropologie"
    REQUIRES_PROXY = True

    def __init__(self, rc):
        super().__init__(rc)
        self._playwright = None
        self._browser = None
        self._context = None
        # Cache product data extracted from category pages so parse_product
        # can return it without a second page visit
        self._product_cache: dict[str, RawProduct] = {}

    async def before_scrape(self):
        self._playwright = await async_playwright().start()
        launch_args: dict = {
            "headless": True,
            "args": [
                "--no-sandbox",
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        }
        if proxy := self._build_playwright_proxy():
            launch_args["proxy"] = proxy

        self._browser = await self._playwright.chromium.launch(**launch_args)
        self._context = await self._browser.new_context(
            viewport={"width": 1440, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="America/New_York",
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
        """
        Load the category page with Playwright and extract both product URLs
        and full product card data (name, price, image) in one pass.
        Cached data is later returned by parse_product() without a second fetch.
        """
        page = await self._context.new_page()
        await stealth_async(page)
        urls: list[str] = []
        try:
            try:
                # Use "commit" — just wait for first byte, don't wait for DOM events
                # Akamai JS challenge prevents domcontentloaded/networkidle from firing
                await page.goto(category_url, wait_until="commit", timeout=30_000)
            except Exception:
                log.warning("anthropologie_category_timeout", url=category_url)
                return urls

            # Give Akamai challenge time to run and redirect to real page
            await page.wait_for_timeout(15_000)

            # Log page title and URL to debug what was actually loaded
            title = await page.title()
            current_url = page.url
            log.info("anthropologie_page_loaded", title=title, url=current_url)

            # Dismiss cookie/country popups if present
            for selector in [
                "button[data-testid='close-button']",
                "button[aria-label*='close' i]",
                "button[aria-label*='accept' i]",
                "#onetrust-accept-btn-handler",
                "[class*='modal'] button[class*='close']",
            ]:
                try:
                    btn = page.locator(selector).first
                    if await btn.is_visible(timeout=2000):
                        await btn.click()
                        await page.wait_for_timeout(1000)
                except Exception:
                    pass

            # Wait for product links to appear
            try:
                await page.wait_for_selector("a[href*='/shop/']", timeout=20_000)
            except Exception:
                pass

            # Scroll to trigger lazy-loaded cards
            for _ in range(5):
                try:
                    await page.evaluate(
                        "() => { const el = document.body || document.documentElement; if (el) window.scrollTo(0, el.scrollHeight); }"
                    )
                except Exception:
                    pass
                await page.wait_for_timeout(2000)

            # Debug: count all links on the page
            all_links = await page.evaluate("""
                () => {
                    const links = Array.from(document.querySelectorAll('a[href]'));
                    return {
                        total: links.length,
                        shopLinks: links.filter(a => a.href.includes('/shop/')).length,
                        sample: links.slice(0, 5).map(a => a.href)
                    };
                }
            """)
            log.info("anthropologie_page_links", url=category_url, **all_links)

            # Extract product cards in one JS evaluation
            products = await page.evaluate("""
                () => {
                    const seen = new Set();
                    const results = [];

                    // Try /shop/ links first, fall back to any product-looking link
                    const shopAnchors = document.querySelectorAll('a[href*="/shop/"]');
                    const anchors = shopAnchors.length > 0
                        ? shopAnchors
                        : document.querySelectorAll('a[href*="anthropologie.com/"]');

                    anchors.forEach(a => {
                        const href = a.href.split('?')[0];
                        if (!href.includes('anthropologie.com') || seen.has(href)) return;
                        // Skip nav/footer links
                        if (href.match(/\/(account|cart|wishlist|help|about|stores|search|category)\/?$/)) return;
                        seen.add(href);

                        // Walk up to find the card container
                        const card = a.closest('li, article, [class*="ProductCard"], [class*="product-card"], [class*="product_"]') || a;

                        const nameEl = card.querySelector(
                            'h2, h3, [class*="ProductCard__name"], [class*="product-name"], ' +
                            '[class*="display-name"], p[class*="name"], span[class*="name"]'
                        );
                        const name = nameEl ? nameEl.innerText.trim() : (a.innerText.trim() || '');

                        const priceEl = card.querySelector(
                            '[class*="Price"]:not([class*="original"]):not([class*="compare"]), ' +
                            '[class*="price"]:not([class*="original"]):not([class*="was"]), ' +
                            '[data-testid*="price"]'
                        );
                        const priceText = priceEl ? priceEl.innerText.trim() : '';

                        const img = card.querySelector('img');
                        const imgUrl = img ? (img.src || img.dataset.src || img.dataset.lazy || '') : '';

                        results.push({ href, name, priceText, imgUrl });
                    });

                    return results;
                }
            """)

            for p in products:
                href = p.get("href", "")
                if not href:
                    continue
                urls.append(href)

                price = None
                m = re.search(r"[\d,]+\.?\d*", p.get("priceText", "").replace(",", ""))
                if m:
                    try:
                        price = float(m.group())
                    except ValueError:
                        pass

                name = p.get("name", "").strip()
                img = p.get("imgUrl", "")

                if name:
                    self._product_cache[href] = RawProduct(
                        url=href,
                        name=name,
                        retailer_slug=self.RETAILER_SLUG,
                        price=price,
                        currency="USD",
                        image_urls=[img] if img else [],
                        raw_attributes={},
                    )

        finally:
            await page.close()

        return list(dict.fromkeys(urls))

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """
        Return cached card data if available (extracted during get_product_urls).
        Falls back to a direct product page visit in the established browser
        context (which carries cookies from the category page visit).
        """
        if cached := self._product_cache.get(product_url):
            if cached.name:
                return cached

        # Fallback: visit product page (same browser context = has cookies)
        page = await self._context.new_page()
        await stealth_async(page)
        try:
            await page.goto(product_url, wait_until="commit", timeout=30_000)
            await page.wait_for_timeout(10_000)
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
                            currency="USD",
                            image_urls=imgs,
                            raw_attributes={},
                        )
                except (json.JSONDecodeError, TypeError, StopIteration):
                    pass

            # DOM fallback
            name = await page.text_content("h1") or ""
            if not name.strip():
                return None

            price = None
            price_text = await page.text_content(
                "[class*='Price']:not([class*='original']), [data-testid*='price']"
            ) or ""
            m = re.search(r"[\d,]+\.?\d*", price_text.replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass

            imgs = await page.evaluate("""
                () => Array.from(document.querySelectorAll(
                    '[class*="product"] img, [class*="gallery"] img, [class*="ProductImage"] img'
                )).map(img => img.src || img.dataset.src).filter(Boolean)
            """)

            return RawProduct(
                url=product_url,
                name=name.strip(),
                retailer_slug=self.RETAILER_SLUG,
                price=price,
                currency="USD",
                image_urls=imgs,
                raw_attributes={},
            )

        except PwTimeout:
            log.warning("anthropologie_product_timeout", url=product_url)
            return None
        finally:
            await page.close()
