"""
Debug Pottery Barn product page HTML - find price and image patterns.
Run with: docker compose exec api python3 scripts/test_pb_product_debug.py
"""
import asyncio, sys, re, json
sys.path.insert(0, "/app")
from scraper.scraping_api_adapter import ScrapingAPIAdapter
from scraper.base_adapter import RawProduct

class TempAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "pottery-barn"
    async def get_category_urls(self): return []
    async def get_product_urls(self, u): return []
    async def parse_product(self, u): return None

TEST_URL = "https://www.potterybarn.com/products/monique-lhuillier-glass-bud-vases-set-of-3/"

async def main():
    adapter = TempAdapter(retailer_config={"slug": "pottery-barn", "base_url": "https://www.potterybarn.com", "categories": {}})
    html = await adapter._fetch_rendered(TEST_URL)
    if not html:
        print("NO HTML"); return

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # 1. JSON-LD dump
    print("=== JSON-LD scripts ===")
    for i, script in enumerate(soup.find_all("script", type="application/ld+json")):
        try:
            d = json.loads(script.string or "")
            t = d.get("@type") if isinstance(d, dict) else [x.get("@type") for x in d if isinstance(x, dict)]
            print(f"[{i}] @type={t}")
            if isinstance(d, dict) and d.get("@type") == "Product":
                print(f"     name={d.get('name')}")
                print(f"     offers={json.dumps(d.get('offers', {}))[:200]}")
                print(f"     image count={len(d.get('image', []))}")
                print(f"     first image={d.get('image', [''])[0] if d.get('image') else 'none'}")
            if isinstance(d, list):
                for x in d:
                    if isinstance(x, dict) and x.get("@type") == "Product":
                        print(f"     [list] name={x.get('name')}")
                        print(f"     [list] offers={json.dumps(x.get('offers', {}))[:200]}")
                        print(f"     [list] image count={len(x.get('image', []))}")
        except Exception as e:
            print(f"[{i}] parse error: {e}")

    # 2. Price search in raw HTML
    print("\n=== Price patterns in HTML ===")
    price_hits = re.findall(r'.{0,30}\$\s*[\d,]+\.?\d*.{0,30}', html)
    for h in list(dict.fromkeys(price_hits))[:10]:
        print(f"  {h.strip()[:100]}")

    # 3. pbimgs.com image URLs
    print("\n=== pbimgs.com image URLs in HTML ===")
    pb_imgs = re.findall(r'https://assets\.pbimgs\.com/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', html)
    unique_imgs = list(dict.fromkeys(pb_imgs))
    print(f"Count: {len(unique_imgs)}")
    for img in unique_imgs[:10]:
        print(f"  {img}")

    # 4. All <img> src attrs
    print("\n=== All <img> src (first 20 non-data:, non-svg icon) ===")
    count = 0
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src", "")
        if src and not src.startswith("data:") and count < 20:
            print(f"  {src[:120]}")
            count += 1

asyncio.run(main())
