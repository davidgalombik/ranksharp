"""
Deep inspect of Pottery Barn category page HTML to find product URL patterns.
Run with: docker compose exec api python3 scripts/test_pb_category2.py
"""
import asyncio, sys, re, json
sys.path.insert(0, "/app")
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from config import settings

TEST_CAT = "https://www.potterybarn.com/shop/decorating/decorative-accessories/"

class TempAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "pottery-barn"
    async def get_category_urls(self): return []
    async def get_product_urls(self, u): return []
    async def parse_product(self, u): return None

async def main():
    adapter = TempAdapter(retailer_config={
        "slug": "pottery-barn",
        "base_url": "https://www.potterybarn.com",
        "categories": {},
    })

    html = await adapter._fetch_rendered(TEST_CAT)
    if not html:
        print("NO HTML RETURNED"); return

    print(f"HTML size: {len(html):,} bytes")

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # 1. Find ALL hrefs containing /products/ or /shop/ with depth >= 4
    print("\n=== hrefs containing /products/ ===")
    prod_hrefs = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if "/products/" in h:
            prod_hrefs.append(h)
    print(f"Count: {len(prod_hrefs)}")
    for h in prod_hrefs[:20]:
        print(f"  {h[:120]}")

    # 2. Find /shop/ hrefs with enough depth (likely product pages)
    print("\n=== /shop/ hrefs with depth >= 5 path segments ===")
    shop_hrefs = []
    for a in soup.find_all("a", href=True):
        h = a["href"]
        if h.startswith("/shop/") or "/potterybarn.com/shop/" in h:
            path = h.split("?")[0]
            segments = [s for s in path.split("/") if s]
            if len(segments) >= 4:
                shop_hrefs.append(h)
    print(f"Count: {len(shop_hrefs)}")
    for h in sorted(set(shop_hrefs))[:20]:
        print(f"  {h[:120]}")

    # 3. Look for JSON-LD or __NEXT_DATA__ with product info
    print("\n=== __NEXT_DATA__ present? ===")
    nd = soup.find("script", id="__NEXT_DATA__")
    print(f"Yes, length={len(nd.string):,}" if nd and nd.string else "No")

    # 4. Any data-product-id or data-sku attributes?
    print("\n=== Elements with data-product, data-sku, data-item ===")
    for tag in soup.find_all(attrs={"data-product-id": True})[:5]:
        print(f"  data-product-id: {tag.get('data-product-id')}")
    for tag in soup.find_all(attrs={"data-sku": True})[:5]:
        print(f"  data-sku: {tag.get('data-sku')}")

    # 5. Search raw HTML for /products/ URLs
    print("\n=== Raw HTML scan for /products/ URLs ===")
    raw_products = re.findall(r'["\'](/products/[^"\'?#\s]+)["\']', html)
    unique_raw = list(dict.fromkeys(raw_products))
    print(f"Found {len(unique_raw)} unique /products/ paths in raw HTML")
    for p in unique_raw[:20]:
        print(f"  https://www.potterybarn.com{p}")

    # 6. Search raw HTML for product URL pattern in JSON
    print("\n=== JSON product slugs in raw HTML ===")
    json_products = re.findall(r'"url"\s*:\s*"(https://www\.potterybarn\.com/(?:products|shop)/[^"]+)"', html)
    unique_json = list(dict.fromkeys(json_products))
    print(f"Found {len(unique_json)} in JSON")
    for p in unique_json[:20]:
        print(f"  {p}")

asyncio.run(main())
