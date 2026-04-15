"""
Find price patterns in PB product page JS/HTML.
Run with: docker compose exec api python3 scripts/test_pb_price.py
"""
import asyncio, sys, re, json
sys.path.insert(0, "/app")
from scraper.scraping_api_adapter import ScrapingAPIAdapter

class TempAdapter(ScrapingAPIAdapter):
    RETAILER_SLUG = "pottery-barn"
    async def get_category_urls(self): return []
    async def get_product_urls(self, u): return []
    async def parse_product(self, u): return None

TEST_URL = "https://www.potterybarn.com/products/monique-lhuillier-glass-bud-vases-set-of-3/"

async def main():
    adapter = TempAdapter(retailer_config={"slug": "pottery-barn", "base_url": "https://www.potterybarn.com", "categories": {}})
    html = await adapter._fetch_rendered(TEST_URL)
    if not html: print("NO HTML"); return

    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "lxml")

    # 1. Look for inline script data with price
    print("=== Script tags with 'price' (first 5 matches) ===")
    count = 0
    for script in soup.find_all("script"):
        text = script.string or ""
        if '"price"' in text.lower() and count < 5:
            # Find JSON price patterns
            matches = re.findall(r'"price"\s*:\s*"?([0-9]+(?:\.[0-9]{1,2})?)"?', text)
            if matches:
                print(f"  prices found: {matches[:10]}")
                # Show surrounding context
                idx = text.find('"price"')
                print(f"  context: ...{text[max(0,idx-30):idx+60].strip()}...")
                count += 1

    # 2. Look for window.__STORE__ or window.__STATE__ or similar
    print("\n=== Global state variables ===")
    for var in ["__NEXT_DATA__", "__STORE__", "__STATE__", "__PB_INITIAL", "window.PB", "pbGlobal", "initialState"]:
        if var in html:
            idx = html.find(var)
            print(f"  Found '{var}' at pos {idx}")
            print(f"  Context: ...{html[idx:idx+200].strip()[:200]}...")

    # 3. Raw price patterns near product name
    print("\n=== Raw price patterns ===")
    name_idx = html.find("Monique Lhuillier")
    if name_idx >= 0:
        surrounding = html[name_idx:name_idx+3000]
        prices = re.findall(r'[\$£€]\s*([0-9,]+(?:\.[0-9]{1,2})?)', surrounding)
        print(f"  Prices near product name: {prices[:10]}")

    # 4. itemprop=price or data-price attributes
    print("\n=== itemprop/data-price elements ===")
    for el in soup.find_all(attrs={"itemprop": "price"}):
        print(f"  itemprop=price: content={el.get('content')} text={el.get_text(strip=True)[:50]}")
    for el in soup.find_all(attrs={"data-price": True}):
        print(f"  data-price: {el.get('data-price')}")
    for el in soup.find_all(attrs={"data-product-price": True}):
        print(f"  data-product-price: {el.get('data-product-price')}")

    # 5. Look for "listPrice" or "salePrice"
    print("\n=== listPrice / salePrice / regularPrice ===")
    for pattern in [r'"listPrice"\s*:\s*"?([0-9.]+)"?', r'"salePrice"\s*:\s*"?([0-9.]+)"?', r'"regularPrice"\s*:\s*"?([0-9.]+)"?', r'"retailPrice"\s*:\s*"?([0-9.]+)"?']:
        matches = re.findall(pattern, html)
        if matches:
            print(f"  {pattern[:30]}: {list(set(matches))[:5]}")

    # 6. Show wcm product images specifically
    print("\n=== pbimgs wcm product images ===")
    wcm_imgs = re.findall(r'https://assets\.pbimgs\.com/pbimgs/[a-z]+/images/dp/wcm/[^\s"\'<>]+\.(?:jpg|jpeg|png|webp)', html)
    unique_wcm = list(dict.fromkeys(wcm_imgs))
    print(f"Count: {len(unique_wcm)}")
    for img in unique_wcm[:5]:
        print(f"  {img}")

asyncio.run(main())
