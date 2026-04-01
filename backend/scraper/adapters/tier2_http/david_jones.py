"""David Jones adapter — JSON-LD."""
import json, re, httpx
from bs4 import BeautifulSoup
from typing import Optional
from scraper.base_adapter import BaseAdapter, RawProduct
from tenacity import retry, stop_after_attempt, wait_exponential

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "en-AU"}
CATEGORY_PATHS = ["/c/home/kitchen-dining/food-storage/", "/c/home/home-decor/", "/c/home/storage-organisation/"]

class DavidJonesAdapter(BaseAdapter):
    RETAILER_SLUG = "david-jones"

    def __init__(self, rc):
        super().__init__(rc)
        self._client = None

    async def before_scrape(self):
        self._client = httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30)

    async def after_scrape(self):
        if self._client: await self._client.aclose()

    async def get_category_urls(self):
        return [self.base_url + p for p in CATEGORY_PATHS]

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def get_product_urls(self, category_url):
        urls = []
        for page in range(1, 6):
            resp = await self._client.get(category_url, params={"page": page})
            if resp.status_code != 200: break
            soup = BeautifulSoup(resp.text, "lxml")
            links = soup.select("a[href*='/p/']")
            if not links: break
            for a in links:
                href = a.get("href", "")
                full = href if href.startswith("http") else self.base_url + href
                if full not in urls: urls.append(full)
            if not soup.select_one("a[rel='next']"): break
        return urls

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=8))
    async def parse_product(self, url):
        resp = await self._client.get(url)
        if resp.status_code != 200: return None
        soup = BeautifulSoup(resp.text, "lxml")
        for s in soup.select('script[type="application/ld+json"]'):
            try:
                d = json.loads(s.string or "")
                if isinstance(d, list): d = next((x for x in d if x.get("@type")=="Product"), None)
                if d and d.get("@type") == "Product":
                    offers = d.get("offers", {})
                    if isinstance(offers, list): offers = offers[0] if offers else {}
                    price = None
                    if raw := offers.get("price"):
                        try: price = float(str(raw).replace(",",""))
                        except: pass
                    imgs = d.get("image", [])
                    if isinstance(imgs, str): imgs = [imgs]
                    return RawProduct(url=url, name=d.get("name",""), retailer_slug=self.RETAILER_SLUG,
                        external_id=d.get("sku"), description=d.get("description"), price=price, currency="AUD", image_urls=imgs, raw_attributes={})
            except: pass
        name = soup.select_one("h1")
        if not name: return None
        return RawProduct(url=url, name=name.get_text(strip=True), retailer_slug=self.RETAILER_SLUG, currency="AUD", raw_attributes={})
