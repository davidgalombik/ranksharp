"""
Crate & Barrel adapter — uses Smartproxy Universal Scraping API to bypass PerimeterX.
Requires SCRAPING_API_USERNAME + SCRAPING_API_PASSWORD in .env.
"""
import json
import re
from typing import Optional
from bs4 import BeautifulSoup
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct
import structlog

log = structlog.get_logger()

CATEGORY_URLS = [
    "https://www.crateandbarrel.com/storage-organization/",
    "https://www.crateandbarrel.com/kitchen/food-storage/",
    "https://www.crateandbarrel.com/decorative-accessories/",
    "https://www.crateandbarrel.com/vases/",
    "https://www.crateandbarrel.com/candles-and-holders/",
    "https://www.crateandbarrel.com/baskets-and-bins/",
]


class CrateAndBarrelAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "crate-and-barrel"
    REQUIRES_SCRAPING_API = True

    async def get_category_urls(self) -> list[str]:
        return CATEGORY_URLS

    async def get_product_urls(self, category_url: str) -> list[str]:
        html = await self._fetch_rendered(category_url)
        if not html:
            log.warning("crate_barrel_category_empty", url=category_url)
            return []

        soup = BeautifulSoup(html, "lxml")

        # C&B product URLs contain /products/
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = href if href.startswith("http") else "https://www.crateandbarrel.com" + href
            if "/products/" in full and "crateandbarrel.com" in full and "#" not in full and full not in seen:
                seen.add(full)
                links.append(full)

        log.info("crate_barrel_links_found", url=category_url, count=len(links))
        return links

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(product_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Try JSON-LD — C&B includes it on product pages
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(script.string or "")
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
                    images = d.get("image", [])
                    if isinstance(images, str):
                        images = [images]
                    return RawProduct(
                        url=product_url,
                        name=d.get("name", ""),
                        retailer_slug=self.RETAILER_SLUG,
                        external_id=d.get("sku"),
                        sku=d.get("sku"),
                        description=d.get("description"),
                        price=price,
                        currency=offers.get("priceCurrency", "USD"),
                        image_urls=images,
                        raw_attributes={
                            "color": d.get("color"),
                            "material": d.get("material"),
                        },
                    )
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # DOM fallback
        name_el = soup.find("h1")
        if not name_el or not name_el.get_text(strip=True):
            return None

        price = None
        price_el = soup.select_one("[class*='price']:not([class*='was']), [data-testid*='price']")
        if price_el:
            m = re.search(r"[\d,]+\.?\d*", price_el.get_text().replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass

        images = [
            img.get("src") or img.get("data-src")
            for img in soup.select("[class*='product'] img, [class*='gallery'] img")
            if img.get("src") or img.get("data-src")
        ]

        return RawProduct(
            url=product_url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="USD",
            image_urls=images,
            raw_attributes={},
        )
