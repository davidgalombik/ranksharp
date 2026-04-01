"""
Wayfair adapter — uses Smartproxy Universal Scraping API to bypass Akamai.

Wayfair uses Akamai Bot Manager which blocks all datacenter IPs. This adapter
delegates page fetching to the scraping API (which uses residential IPs +
Akamai challenge solving) and parses the returned HTML with BeautifulSoup.

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

CATEGORY_URLS = {
    "storage-organization": [
        "https://www.wayfair.com/storage-organization/cat/bins-baskets-c45272.html",
        "https://www.wayfair.com/storage-organization/cat/shelf-organizers-c1862684.html",
        "https://www.wayfair.com/kitchen-tabletop/cat/food-storage-containers-c47067.html",
    ],
    "decorative-accessories": [
        "https://www.wayfair.com/decor-pillows/cat/decorative-accessories-c215090.html",
        "https://www.wayfair.com/lighting/cat/candles-holders-c215337.html",
        "https://www.wayfair.com/decor-pillows/cat/vases-c215335.html",
    ],
}


class WayfairAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "wayfair"
    REQUIRES_SCRAPING_API = True

    async def get_category_urls(self) -> list[str]:
        urls = []
        for cat_key in self.categories.values():
            urls.extend(CATEGORY_URLS.get(cat_key, []))
        return urls

    async def get_product_urls(self, category_url: str) -> list[str]:
        html = await self._fetch_rendered(category_url)
        if not html:
            log.warning("wayfair_category_empty", url=category_url)
            return []

        soup = BeautifulSoup(html, "lxml")

        # Wayfair product URLs contain /p/
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if "/p/" in href and "wayfair.com" in href and href not in seen:
                seen.add(href)
                links.append(href)
            elif "/p/" in href and href.startswith("/") and href not in seen:
                seen.add(href)
                links.append("https://www.wayfair.com" + href)

        log.info("wayfair_links_found", url=category_url, count=len(links))
        return links

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(product_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Try __NEXT_DATA__ JSON first
        next_data_el = soup.find("script", id="__NEXT_DATA__")
        if next_data_el and next_data_el.string:
            try:
                state = json.loads(next_data_el.string)
                result = self._parse_from_state(state, product_url)
                if result:
                    return result
            except (json.JSONDecodeError, KeyError):
                pass

        # Try JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                d = json.loads(script.string or "")
                if isinstance(d, list):
                    d = next((x for x in d if x.get("@type") == "Product"), None)
                if d and d.get("@type") == "Product":
                    return self._from_json_ld(d, product_url)
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        # DOM fallback
        return self._parse_from_dom(soup, product_url)

    def _parse_from_state(self, state: dict, url: str) -> Optional[RawProduct]:
        try:
            props = state.get("props", {}).get("pageProps", {})
            product = props.get("product") or props.get("initialData", {}).get("product")
            if not product:
                return None
            price_val = (
                product.get("price", {}).get("value")
                or product.get("salePrice", {}).get("value")
            )
            images = [img["url"] for img in product.get("images", []) if img.get("url")]
            return RawProduct(
                url=url,
                name=product.get("name", ""),
                retailer_slug=self.RETAILER_SLUG,
                external_id=str(product.get("sku") or product.get("id", "")),
                sku=str(product.get("sku", "")),
                description=product.get("description"),
                price=float(price_val) if price_val else None,
                currency="USD",
                image_urls=images,
                raw_attributes={
                    "manufacturer": product.get("manufacturer"),
                    "color": product.get("color"),
                    "material": product.get("material"),
                },
            )
        except (KeyError, TypeError):
            return None

    def _from_json_ld(self, d: dict, url: str) -> RawProduct:
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
            url=url,
            name=d.get("name", ""),
            retailer_slug=self.RETAILER_SLUG,
            external_id=d.get("sku"),
            sku=d.get("sku"),
            description=d.get("description"),
            price=price,
            currency=offers.get("priceCurrency", "USD"),
            image_urls=images,
            raw_attributes={},
        )

    def _parse_from_dom(self, soup: BeautifulSoup, url: str) -> Optional[RawProduct]:
        name_el = soup.find("h1")
        if not name_el or not name_el.get_text(strip=True):
            return None
        price = None
        price_el = soup.select_one("[data-hb-id='price-block'] .Price, [data-testid='price']")
        if price_el:
            m = re.search(r"[\d,]+\.?\d*", price_el.get_text().replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass
        images = [
            img.get("src") or img.get("data-src")
            for img in soup.select("img[data-hb-id='product-image'], .MediaGallery img")
            if img.get("src") or img.get("data-src")
        ]
        return RawProduct(
            url=url,
            name=name_el.get_text(strip=True),
            retailer_slug=self.RETAILER_SLUG,
            price=price,
            currency="USD",
            image_urls=images,
            raw_attributes={},
        )
