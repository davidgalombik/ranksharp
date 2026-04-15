"""
Quick live test for Firecrawl and HTTP adapters.
Tests one category URL each and prints the first 10 products found.

Run with:
  docker compose exec api python scripts/test_firecrawl_adapters.py tjmaxx
  docker compose exec api python scripts/test_firecrawl_adapters.py container_store
  docker compose exec api python scripts/test_firecrawl_adapters.py williams_sonoma
  docker compose exec api python scripts/test_firecrawl_adapters.py at_home
  docker compose exec api python scripts/test_firecrawl_adapters.py all
"""
import asyncio
import sys

# Bootstrap path so imports work when run inside the container
sys.path.insert(0, "/app")

from scraper.adapters.tier2_http.tjmaxx import TJMaxxAdapter
from scraper.adapters.tier2_http.container_store_firecrawl import ContainerStoreFirecrawlAdapter
from scraper.adapters.tier2_http.williams_sonoma_firecrawl import WilliamsSonomaFirecrawlAdapter
from scraper.adapters.tier2_http.at_home_firecrawl import AtHomeFirecrawlAdapter
from scraper.adapters.tier2_http.crate_barrel_firecrawl import CrateBarrelFirecrawlAdapter
from scraper.adapters.tier2_http.west_elm_firecrawl import WestElmFirecrawlAdapter


async def test_adapter(adapter_cls, category_url: str, label: str,
                       parse_product_limit: int = 10):
    print(f"\n{'='*60}")
    print(f"Testing {label}")
    print(f"URL: {category_url}")
    print("="*60)

    adapter = adapter_cls(rc={"base_url": category_url, "categories": {}})
    await adapter.before_scrape()

    try:
        urls = await adapter.get_product_urls(category_url)
        print(f"\nTotal product URLs found: {len(urls)}")

        if not urls:
            print("  ⚠️  No URLs — scraping may be blocked or regex not matching")
            return

        products = []
        for u in urls[:parse_product_limit]:
            p = await adapter.parse_product(u)
            if p:
                products.append(p)

        print(f"Sample products (first {len(products)}):")
        for i, p in enumerate(products, 1):
            name_preview = (p.name or "(no name)")[:60]
            price_str = f"${p.price:.2f}" if p.price else "(no price)"
            image_str = "✓" if p.image_urls else "✗"
            cat = getattr(p, "category", "") or ""
            print(f"  {i:>2}. [{price_str:>10}] [img:{image_str}] [{cat}] {name_preview}")

        with_price = sum(1 for p in products if p.price)
        with_image = sum(1 for p in products if p.image_urls)
        print(f"\nStats for first {len(products)}: "
              f"{with_price}/{len(products)} have price, "
              f"{with_image}/{len(products)} have image")
    finally:
        await adapter.after_scrape()


async def main():
    target = sys.argv[1].lower() if len(sys.argv) > 1 else "all"

    if target in ("tjmaxx", "all"):
        await test_adapter(
            TJMaxxAdapter,
            "https://tjmaxx.tjx.com/store/shop/home-shop-by-category-pillows-decor-baskets-storage/_/N-2832378117",
            "TJ Maxx — Baskets & Storage",
        )

    if target in ("container_store", "cs", "all"):
        await test_adapter(
            ContainerStoreFirecrawlAdapter,
            "https://www.containerstore.com/s/kitchen/pantry-organizers/12?",
            "Container Store — Pantry Organizers",
        )

    if target in ("williams_sonoma", "ws", "all"):
        # WS uses /v1/map for URL discovery then fetches each product page (1 credit each).
        # Limit to 3 product page fetches in the test to keep credit usage low.
        await test_adapter(
            WilliamsSonomaFirecrawlAdapter,
            "https://www.williams-sonoma.com/shop/entertaining/serveware/",
            "Williams-Sonoma — Serveware (map-based)",
            parse_product_limit=3,
        )

    if target in ("at_home", "athome", "all"):
        await test_adapter(
            AtHomeFirecrawlAdapter,
            "https://www.athome.com/storage-organization/",
            "At Home — Storage & Organization (map-based)",
            parse_product_limit=5,
        )

    if target in ("crate_barrel", "cnb", "crate", "all"):
        await test_adapter(
            CrateBarrelFirecrawlAdapter,
            "https://www.crateandbarrel.com/dining/dinnerware/1",
            "Crate & Barrel — Dinnerware (map-based, stealth)",
            parse_product_limit=5,
        )

    if target in ("west_elm", "westelm", "we", "all"):
        await test_adapter(
            WestElmFirecrawlAdapter,
            "https://www.westelm.com/shop/dining-kitchen/all-dinnerware-collections/",
            "West Elm — Dinnerware (stealth category + product)",
            parse_product_limit=5,
        )


if __name__ == "__main__":
    asyncio.run(main())
