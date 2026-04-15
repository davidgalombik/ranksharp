"""
Quick test: Firecrawl with JS rendering + wait time against Pottery Barn AU.
Run with: docker compose exec api python scripts/test_firecrawl_pb.py
"""
import requests
import json

API_KEY = "fc-41efe177ac8b4f34ab301fda7a49b871"
URL = "https://www.potterybarn.com.au/furniture/dining-room-bar-furniture#/filter:ss_instock:In%2520Stock"

payload = {
    "url": URL,
    "formats": ["markdown"],
    "waitFor": 5000,  # wait 5 seconds for JS to render product grid
    "actions": [
        {"type": "wait", "milliseconds": 5000},
        {"type": "scroll", "direction": "down", "amount": 1000},
        {"type": "wait", "milliseconds": 2000},
    ],
}

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}

print("Sending request to Firecrawl...")
resp = requests.post("https://api.firecrawl.dev/v1/scrape", json=payload, headers=headers, timeout=60)
print(f"Status: {resp.status_code}")

if resp.status_code == 200:
    data = resp.json()
    md = data.get("data", {}).get("markdown", "")
    print(f"Markdown length: {len(md)}")

    # Check for product indicators
    lines = md.split("\n")
    print(f"Total lines: {len(lines)}")

    # Look for /ip/ product URLs (individual product pages)
    ip_lines = [l for l in lines if "/ip/" in l]
    print(f"\nIndividual product URLs (/ip/): {len(ip_lines)}")
    for l in ip_lines[:5]:
        print(f"  {l[:150]}")

    # Look for price patterns
    import re
    price_lines = [l for l in lines if re.search(r'\$[\d,]+', l) and len(l) < 100]
    print(f"\nPrice lines: {len(price_lines)}")
    for l in price_lines[:5]:
        print(f"  {l[:150]}")

    # Save full output
    with open("/tmp/firecrawl_pb_test.json", "w") as f:
        json.dump(data, f, indent=2)
    print("\nFull output saved to /tmp/firecrawl_pb_test.json")
else:
    print("Error:", resp.text)
