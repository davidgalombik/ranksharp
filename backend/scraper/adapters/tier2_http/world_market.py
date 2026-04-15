"""
World Market adapter (worldmarket.com — Cost Plus World Market).

World Market uses server-side rendered pages with JSON-LD product data.
Category URLs follow /c/{category}/ pattern.
"""
import json
import httpx
from bs4 import BeautifulSoup
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

CATEGORY_PATHS = [
    "/c/best-sellers/",
    "/c/decor-and-pillows/decorative-accessories/",
    "/c/new-and-trending/new-decor-and-pillows/",
    "/c/new-and-trending/new-kitchen/",
    "/c/kitchen/kitchen-storage-and-organization/",
    "/c/kitchen/kitchen-storage-and-organization/food-storage/",
    "/c/kitchen/cookware/",
    "/c/kitchen/bakeware/",
]

# Human-readable label derived from the last meaningful path segment
CATEGORY_LABELS: dict[str, str] = {
    "/c/best-sellers/": "Best Sellers",
    "/c/decor-and-pillows/decorative-accessories/": "Decorative Accessories",
    "/c/new-and-trending/new-decor-and-pillows/": "New Decor",
    "/c/new-and-trending/new-kitchen/": "New Kitchen",
    "/c/kitchen/kitchen-storage-and-organization/": "Kitchen Storage",
    "/c/kitchen/kitchen-storage-and-organization/food-storage/": "Food Storage",
    "/c/kitchen/cookware/": "Cookware",
    "/c/kitchen/bakeware/": "Bakeware",
}


class WorldMarketAdapter(BaseAdapter):
    RETAILER_SLUG = "world-market"

    def __init__(self, rc):
        super().__init__(rc)
        self._client = None
        self._cat_cache: dict[str, str] = {}

    async def before_scrape(self):
        self._client = httpx.AsyncClient(
            headers=HEADERS, follow_redirects=True, timeout=30
        )

    async def after_scrape(self):
        if self._client:
            await self._client.aclose()

    async def get_category_urls(self):
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url: str) -> list[str]:
        import re as _re
        path = category_url.replace(self.base_url, "")
        cat_label = CATEGORY_LABELS.get(path, path.rstrip("/").split("/")[-1].replace("-", " ").title())

        urls = []
        for page in range(1, 8):
            params = {"start": (page - 1) * 60, "sz": 60} if page > 1 else {}
            resp = await self._client.get(category_url, params=params)
            if resp.status_code != 200:
                break
            # Product links are in the format /p/{slug}-{id}.html
            found = _re.findall(r'/p/[a-z0-9-]+-\d+\.html', resp.text)
            added = 0
            for href in dict.fromkeys(found):
                full = self.base_url + href
                if full not in urls:
                    urls.append(full)
                    added += 1
                self._cat_cache.setdefault(full, cat_label)
            if added == 0:
                break
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url: str) -> Optional[RawProduct]:
        resp = await self._client.get(url)
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, "lxml")

        # og:image is the most reliable product image source on worldmarket.com.
        # JSON-LD schema does not include the image field on most product pages.
        og_img = soup.find("meta", property="og:image")
        og_img_url = og_img.get("content", "").strip() if og_img else ""
        # Exclude nav/library assets (not product photos) and .tif files (browsers can't render them)
        if og_img_url and (
            any(x in og_img_url for x in ["MegaNavigation", "nav-flyout", "World_Market-Library"])
            or og_img_url.lower().endswith(".tif")
        ):
            og_img_url = ""

        name = ""
        sku = None
        price = None
        description = None

        for s in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(s.string or "")
                if isinstance(d, list):
                    d = next((x for x in d if x.get("@type") == "Product"), None)
                if d and d.get("@type") == "Product":
                    name = d.get("name", "")
                    sku = d.get("sku")
                    description = d.get("description")
                    offers = d.get("offers", {})
                    if isinstance(offers, list):
                        offers = offers[0] if offers else {}
                    if raw := offers.get("price"):
                        try:
                            price = float(str(raw).replace(",", ""))
                        except (ValueError, TypeError):
                            pass
                    # JSON-LD image uses the dw/image/v2 service (proper JPEG).
                    # Prefer it over og:image; bump sw= to 800 for full-size.
                    jld_imgs = d.get("image", [])
                    if isinstance(jld_imgs, str):
                        jld_imgs = [jld_imgs]
                    for img in jld_imgs:
                        if img and not any(x in img for x in ["MegaNavigation", "nav-flyout", "World_Market-Library"]):
                            # Increase image size: replace any sw=N with sw=800
                            import re as _re
                            img = _re.sub(r'([\?&])sw=\d+', r'\1sw=800', img)
                            if 'sw=' not in img:
                                img += ('&' if '?' in img else '?') + 'sw=800'
                            og_img_url = img  # always prefer JSON-LD image
                            break
                    break
            except (json.JSONDecodeError, TypeError, StopIteration):
                pass

        if not name:
            name_el = soup.select_one("h1.product-name, h1[itemprop='name'], h1")
            if not name_el:
                return None
            name = name_el.get_text(strip=True)

        img_urls = [og_img_url] if og_img_url else []

        return RawProduct(
            url=url,
            name=name,
            retailer_slug=self.RETAILER_SLUG,
            external_id=sku,
            description=description,
            price=price,
            currency="USD",
            category=self._cat_cache.get(url),
            image_urls=img_urls,
            raw_attributes={},
        )
