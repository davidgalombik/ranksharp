"""
Probe At Home internal APIs for product URL discovery.
At Home runs on SFCC (Salesforce Commerce Cloud).
Run with: docker compose exec api python scripts/debug_athome_api.py
"""
import asyncio, sys, re, json
sys.path.insert(0, "/app")
import httpx

BASE = "https://www.athome.com"

HEADERS_BROWSER = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

HEADERS_XHR = {
    **HEADERS_BROWSER,
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.athome.com/storage-organization/",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Site": "same-origin",
}

# SFCC search/browse endpoints to probe
ENDPOINTS = [
    # SFCC OCAPI browse
    ("SFCC search AJAX", "GET", "/on/demandware.store/Sites-athome-Site/default/Search-Show?q=storage+baskets&format=ajax"),
    # SFCC category browse
    ("SFCC category browse", "GET", "/on/demandware.store/Sites-athome-Site/default/Search-Show?cgid=storage&format=ajax"),
    # Common SFCC product search
    ("SFCC product show", "GET", "/on/demandware.store/Sites-athome-Site/default/Product-Show?pid=&format=ajax"),
    # Algolia proxy (sometimes at /api/search)
    ("API search", "GET", "/api/search?query=storage&hitsPerPage=20"),
    # Generic search
    ("Search JSON", "GET", "/search?q=storage+baskets&format=json"),
    # Category page as JSON
    ("Category JSON", "GET", "/storage-organization/?format=json"),
    # SFCC OCAPI (Open Commerce API)
    ("OCAPI products", "GET", "/dw/shop/v24_5/product_search?q=storage&count=20&client_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"),
]

_PRODUCT_RE = re.compile(r'https?://(?:www\.)?athome\.com/[^<"\s]+/[A-Z0-9]{6,}\.html')

async def probe(client, label, method, path):
    url = BASE + path
    headers = HEADERS_XHR if "json" in path.lower() or "ajax" in path.lower() or "api" in path.lower() else HEADERS_BROWSER
    try:
        if method == "GET":
            r = await client.get(url, headers=headers, timeout=15)
        else:
            r = await client.post(url, headers=headers, timeout=15)

        ct = r.headers.get("content-type", "")
        body_preview = r.text[:200].replace("\n", " ").strip()
        product_urls = _PRODUCT_RE.findall(r.text)

        status_icon = "✓" if r.status_code == 200 else "✗"
        print(f"\n{status_icon} [{r.status_code}] {label}")
        print(f"   URL: {url}")
        print(f"   CT: {ct[:60]}")
        print(f"   Body: {body_preview[:120]}")
        if product_urls:
            print(f"   PRODUCTS FOUND: {len(product_urls)}")
            for u in product_urls[:3]:
                print(f"     {u}")
        return r.status_code, product_urls
    except Exception as e:
        print(f"\n✗ [ERR] {label}: {e}")
        return 0, []

async def main():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        print("=== Probing At Home internal APIs ===\n")
        all_products = []
        for label, method, path in ENDPOINTS:
            status, urls = await probe(client, label, method, path)
            all_products.extend(urls)

        # Also try fetching the main page to look for Algolia config
        print("\n\n=== Scanning homepage for Algolia/search config ===")
        try:
            r = await client.get(BASE + "/", headers=HEADERS_BROWSER, timeout=20)
            text = r.text
            # Look for Algolia app ID
            algolia_app = re.search(r'["\']([A-Z0-9]{10})["\']', text)
            algolia_key = re.search(r'algolia.*?["\']([a-f0-9]{32})["\']', text, re.I)
            algolia_index = re.search(r'["\']([a-zA-Z0-9_-]*(?:athome|product|catalog)[a-zA-Z0-9_-]*)["\']', text, re.I)
            print(f"  HTTP {r.status_code}, body length: {len(text)}")

            if "algolia" in text.lower():
                print("  Algolia references found!")
                # Extract more context
                for m in re.finditer(r'.{0,50}algolia.{0,100}', text, re.I):
                    print(f"    {m.group()[:120]}")
            else:
                print("  No Algolia references in page source")

            # Check for any API base URLs
            for pattern, label2 in [
                (r'apiBaseUrl["\s:]+["\']([^"\']+)["\']', "apiBaseUrl"),
                (r'searchUrl["\s:]+["\']([^"\']+)["\']', "searchUrl"),
                (r'catalogApiUrl["\s:]+["\']([^"\']+)["\']', "catalogApiUrl"),
            ]:
                m = re.search(pattern, text, re.I)
                if m:
                    print(f"  {label2}: {m.group(1)}")

        except Exception as e:
            print(f"  Error fetching homepage: {e}")

asyncio.run(main())
