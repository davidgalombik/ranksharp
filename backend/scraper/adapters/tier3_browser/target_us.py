"""
Target US adapter — uses Smartproxy Universal Scraping API to bypass Akamai.
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
        "https://www.target.com/c/storage-organization/-/N-5xu1z",
        "https://www.target.com/c/kitchen-storage-organization/-/N-kkmcz",
    ],
    "home-decor": [
        "https://www.target.com/c/decorative-accents-home-decor/-/N-55hty",
        "https://www.target.com/c/candles-home-fragrance/-/N-55hv5",
    ],
}


class TargetUSAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "target-us"
    REQUIRES_SCRAPING_API = True

    async def get_category_urls(self) -> list[str]:
        urls = []
        for cat_key in self.categories.values():
            urls.extend(CATEGORY_URLS.get(cat_key, []))
        return urls

    async def get_product_urls(self, category_url: str) -> list[str]:
        html = await self._fetch_rendered(category_url)
        if not html:
            log.warning("target_us_category_empty", url=category_url)
            return []

        soup = BeautifulSoup(html, "lxml")

        # Target product links: /p/<name>/-/A-<tcin>
        links = []
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            full = href if href.startswith("http") else "https://www.target.com" + href
            if "/p/" in full and "/-/A-" in full and full not in seen:
                clean = full.split("?")[0]
                seen.add(clean)
                links.append(clean)

        log.info("target_us_links_found", url=category_url, count=len(links))
        return links

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        html = await self._fetch_rendered(product_url)
        if not html:
            return None

        soup = BeautifulSoup(html, "lxml")

        # Try __NEXT_DATA__ JSON
        next_data_el = soup.find("script", id="__NEXT_DATA__")
        if next_data_el and next_data_el.string:
            try:
                state = json.loads(next_data_el.string)
                result = self._parse_from_next_data(state, product_url)
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

        return self._parse_from_dom(soup, product_url)

    def _parse_from_next_data(self, state: dict, url: str) -> Optional[RawProduct]:
        try:
            page_props = state.get("props", {}).get("pageProps", {})
            initial_data = page_props.get("initialData", {})
            product = (
                initial_data.get("product")
                or initial_data.get("data", {}).get("product")
            )
            if not product:
                return None
            name = product.get("title") or product.get("name", "")
            if not name:
                return None
            price = None
            price_info = product.get("price") or product.get("price_info", {})
            if isinstance(price_info, dict):
                raw = price_info.get("current_retail") or price_info.get("formatted_current_price_type")
                if isinstance(raw, str):
                    m = re.search(r"[\d.]+", raw.replace(",", ""))
                    price = float(m.group()) if m else None
                elif raw:
                    try:
                        price = float(raw)
                    except (ValueError, TypeError):
                        pass
            images = []
            for img in product.get("images", []):
                if isinstance(img, dict):
                    images.append(img.get("base_url") or img.get("url") or "")
                elif isinstance(img, str):
                    images.append(img)
            images = [i for i in images if i]
            desc = product.get("description") or ""
            if isinstance(desc, dict):
                desc = desc.get("downstream_description") or ""
            return RawProduct(
                url=url,
                name=name.strip(),
                retailer_slug=self.RETAILER_SLUG,
                external_id=str(product.get("tcin") or ""),
                sku=str(product.get("tcin", "")),
                description=desc.strip() if isinstance(desc, str) else "",
                price=price,
                currency="USD",
                image_urls=images,
                raw_attributes={
                    "brand": (
                        product.get("brand", {}).get("name")
                        if isinstance(product.get("brand"), dict)
                        else product.get("brand")
                    ),
                },
            )
        except (KeyError, TypeError, AttributeError):
            return None

    def _from_json_ld(self, d: dict, url: str) -> RawProduct:
        offers = d.get("offers", {})
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        price = None
        if raw := offers.get("price"):
            try:
                price = float(str(raw).replace(",", ""))
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
            currency="USD",
            image_urls=images,
            raw_attributes={},
        )

    def _parse_from_dom(self, soup: BeautifulSoup, url: str) -> Optional[RawProduct]:
        name_el = soup.select_one("[data-test='product-title'], h1")
        if not name_el or not name_el.get_text(strip=True):
            return None
        price = None
        price_el = soup.select_one("[data-test='product-price'], [data-test='current-price']")
        if price_el:
            m = re.search(r"[\d.]+", price_el.get_text().replace(",", ""))
            if m:
                try:
                    price = float(m.group())
                except ValueError:
                    pass
        images = [
            img.get("src") for img in soup.select("picture img, [data-test='product-image'] img")
            if img.get("src") and not img["src"].startswith("data:")
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
