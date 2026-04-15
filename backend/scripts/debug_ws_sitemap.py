"""
Check if WS sitemap is accessible and whether it lists product URLs by category.
Run with: docker compose exec api python scripts/debug_ws_sitemap.py
"""
import asyncio, sys, re
sys.path.insert(0, "/app")
import httpx

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml",
}

async def main():
    async with httpx.AsyncClient(headers=HEADERS, follow_redirects=True, timeout=30) as client:
        # Check sitemap index
        for url in [
            "https://www.williams-sonoma.com/sitemap.xml",
            "https://www.williams-sonoma.com/sitemap_index.xml",
            "https://www.williams-sonoma.com/robots.txt",
        ]:
            resp = await client.get(url)
            print(f"{url} -> {resp.status_code}, {len(resp.text)} bytes")
            if resp.status_code == 200:
                # Look for sitemap references or product URLs
                sitemaps = re.findall(r'<loc>(https://[^<]+)</loc>', resp.text)
                print(f"  Found {len(sitemaps)} <loc> entries")
                for s in sitemaps[:10]:
                    print(f"    {s}")
                # If robots.txt, look for Sitemap: lines
                if "robots.txt" in url:
                    sitemap_lines = [l for l in resp.text.splitlines() if l.startswith("Sitemap:")]
                    for l in sitemap_lines:
                        print(f"  {l}")

asyncio.run(main())
