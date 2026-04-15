"""
Test sitemap-based URL discovery for At Home.
At Home uses Salesforce Commerce Cloud which typically publishes sitemaps.
Run with: docker compose exec api python scripts/debug_athome_sitemap.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx
import xml.etree.ElementTree as ET

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

BASE = "https://www.athome.com"
_PRODUCT_RE = re.compile(r'https://www\.athome\.com/[^<\s]+/[A-Z0-9]{6,}\.html')

SITEMAP_CANDIDATES = [
    "/sitemap.xml",
    "/sitemap_index.xml",
    "/sitemap-index.xml",
    "/sitemaps/sitemap_index.xml",
    "/robots.txt",
]

async def try_url(client, url):
    try:
        r = await client.get(url, timeout=20)
        return r.status_code, r.text[:500] if r.status_code == 200 else ""
    except Exception as e:
        return 0, str(e)

async def parse_sitemap(client, url, depth=0):
    """Recursively parse sitemap index → sitemap → URLs."""
    if depth > 3:
        return []
    try:
        r = await client.get(url, timeout=30)
        if r.status_code != 200:
            print(f"  {'  '*depth}HTTP {r.status_code}: {url}")
            return []
        text = r.text
        # Is it a sitemap index?
        if "<sitemapindex" in text:
            root = ET.fromstring(text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            sub_sitemaps = [loc.text for loc in root.findall(".//sm:loc", ns) if loc.text]
            print(f"  {'  '*depth}Sitemap index: {len(sub_sitemaps)} sub-sitemaps at {url}")
            all_urls = []
            for sub in sub_sitemaps[:10]:  # limit to first 10 sub-sitemaps
                print(f"  {'  '*depth}  → {sub}")
                sub_urls = await parse_sitemap(client, sub, depth + 1)
                all_urls.extend(sub_urls)
                if len(all_urls) > 500:
                    break
            return all_urls
        elif "<urlset" in text:
            root = ET.fromstring(text)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            urls = [loc.text for loc in root.findall(".//sm:loc", ns) if loc.text]
            product_urls = [u for u in urls if _PRODUCT_RE.match(u)]
            print(f"  {'  '*depth}URL set: {len(urls)} total, {len(product_urls)} products at {url}")
            return product_urls
        else:
            print(f"  {'  '*depth}Unknown format at {url}: {text[:100]}")
            return []
    except Exception as e:
        print(f"  {'  '*depth}Error parsing {url}: {e}")
        return []

async def main():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True) as client:
        print("=== Probing sitemap candidates ===")
        for path in SITEMAP_CANDIDATES:
            url = BASE + path if not path.startswith("http") else path
            status, preview = await try_url(client, url)
            print(f"  {path}: HTTP {status}  {preview[:80].replace(chr(10), ' ')}")

        print("\n=== Parsing sitemap ===")
        product_urls = await parse_sitemap(client, BASE + "/sitemap.xml")

        if not product_urls:
            # Try index variant
            print("\n  Trying sitemap_index.xml ...")
            product_urls = await parse_sitemap(client, BASE + "/sitemap_index.xml")

        print(f"\n=== Results ===")
        print(f"Total product URLs found: {len(product_urls)}")
        if product_urls:
            print("Sample URLs:")
            for u in product_urls[:10]:
                print(f"  {u}")

            # Categorize by path segment
            cats = {}
            for u in product_urls:
                path = u.replace(BASE, "").split("/")[1] if "/" in u.replace(BASE, "") else "other"
                cats[path] = cats.get(path, 0) + 1
            print("\nBy category path:")
            for cat, count in sorted(cats.items(), key=lambda x: -x[1])[:15]:
                print(f"  /{cat}/: {count}")

            # Test one product page
            print("\n=== Test product page scrape ===")
            test_url = product_urls[0]
            r = await client.get(test_url, timeout=30)
            print(f"HTTP {r.status_code} for {test_url}")
            if r.status_code == 200:
                import json
                import re as _re
                # Look for JSON-LD
                ld_matches = _re.findall(r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>', r.text, _re.S)
                for raw in ld_matches:
                    try:
                        d = json.loads(raw)
                        if isinstance(d, list):
                            d = next((x for x in d if x.get("@type") == "Product"), None)
                        if d and d.get("@type") == "Product":
                            offers = d.get("offers", {})
                            if isinstance(offers, list):
                                offers = offers[0] if offers else {}
                            print(f"  Name   : {d.get('name')}")
                            print(f"  Price  : {offers.get('price')}")
                            print(f"  SKU    : {d.get('sku')}")
                            imgs = d.get("image", [])
                            print(f"  Images : {len(imgs) if isinstance(imgs, list) else 1}")
                            break
                    except Exception:
                        pass

asyncio.run(main())
