"""
Bunnings AU adapter — Firecrawl listing scrape + per-product image fetch.

Bunnings category pages render fine through standard Firecrawl (no stealth
proxy needed). The listing markdown contains every product's name, price,
SKU, and URL — but ~80% of items on any given category use a
`static/images/noImage.svg` placeholder on the listing grid (common for
"Special Order" stock) and only expose the real hero image on the product
detail page.

Strategy:
  1. Listing scrape (one call per page via base FirecrawlAdapter pagination).
     Parses name/price/SKU/URL into a RawProduct stub. If the listing card
     already contains a real CDN image, we take it and skip step 2 for
     that product.
  2. Per-product PDP fetch — for stubs that ended up with no image, fetch
     the product page and pull the first media.bunnings.com.au CDN image
     (the hero). Done concurrently in batches to keep wall-clock sane.

Pagination: Bunnings supports the standard ?page=N scheme, so the base
FirecrawlAdapter._paginate_url() default works unchanged.

URL pattern  : https://www.bunnings.com.au/<slug>_p<7-digit-sku>
Price format : AUD, '$205' or '$376.48'
Image CDN    : media.bunnings.com.au/api/public/content/<uuid>?v=<hash>&t=<size>
"""
import re
import asyncio
from typing import Optional, AsyncIterator
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct, _BEST_SELLER_KEYWORDS


# Product URL: path ends with _p<6-8 digits>. Everything after is optional
# (trailing slash, query string, anchor).
_PRODUCT_URL_RE = re.compile(
    r'https://www\.bunnings\.com\.au/[^)\s"\'<>]+?_p\d{6,8}'
)

# Image URL inside the product card — Bunnings CDN, w150 thumbnail first
_IMG_URL_RE = re.compile(
    r'https://media\.bunnings\.com\.au/api/public/content/[a-f0-9]{32}'
    r'\?v=[a-f0-9]+&t=w\d+dpr\d+'
)

# "$205" or "$376.48" — amount only, integer or 2dp
_PRICE_RE = re.compile(r'\$(\d+(?:\.\d{1,2})?)')

# SKU — the _p<digits> suffix (without the underscore+p prefix)
_SKU_RE = re.compile(r'_p(\d{6,8})$')


# Category listing URLs → human-readable category label.
# Add more home & laundry verticals as needed.
CATEGORIES: dict[str, str] = {
    "https://www.bunnings.com.au/products/storage-cleaning/laundry/laundry-cabinets": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage/modular-storage-cabinets": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage/containers/storage-baskets": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage/containers/storage-tubs-crates": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage/modular-storage-cabinets/modular-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/office-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/kitchen-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/laundry-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/cube-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/garage-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/storage-cleaning/storage-cleaning-inspiration/wardrobe-storage-inspiration": "storage",
    "https://www.bunnings.com.au/products/kitchen/kitchen-storage-organisation/kitchen-cleaning-accessories": "storage",
    "https://www.bunnings.com.au/products/kitchen/kitchen-storage-organisation/kitchen-drawer-organiser": "storage",
    "https://www.bunnings.com.au/products/kitchen/kitchen-storage-organisation/kitchen-storage-solutions": "storage",
    "https://www.bunnings.com.au/products/kitchen/kitchen-storage-organisation/kitchen-cabinet-bins": "storage",
}


_PDP_BATCH_SIZE = 5


class BunningsFirecrawlAdapter(FirecrawlAdapter):
    """Bunnings AU — listing parse + targeted PDP fetch for missing images."""

    RETAILER_SLUG = "bunnings"
    WAIT_MS = 3000  # Bunnings renders fast; 3s is enough for the grid

    async def get_category_urls(self) -> list[str]:
        return list(CATEGORIES.keys())

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """Parse one Bunnings category-listing markdown page into RawProducts.

        Strategy: scan the markdown once, finding each
        `[$<price>](<product-url>)` marker — each one anchors a product.
        Then look backward within a small window for the immediately
        preceding `[![<name>](<img>)...](<same-url>)` block and pull the
        name + primary image from there.
        """
        # Resolve category label from the page URL (strip ?page=N)
        base = url.split("?", 1)[0]
        label = CATEGORIES.get(base, base.rstrip("/").split("/")[-1])

        products: list[RawProduct] = []
        seen: set[str] = set()

        # Gather every [$price](product-url) marker in source order.
        # These are the per-card anchors — the product card (image + name)
        # always appears in the markdown chunk BETWEEN consecutive anchors.
        price_matches = list(re.finditer(
            r'\[\$(\d+(?:\.\d{1,2})?)\]\((https://www\.bunnings\.com\.au/[^)\s]+?_p\d{6,8})\)',
            markdown,
        ))

        # Card regex — the outer image link whose close is `](<url> "<title>")`
        # or just `](<url>)`. The inner image URL can be either the CDN
        # (media.bunnings.com.au) OR a placeholder (static/images/noImage.svg)
        # for special-order products without a photo. We post-filter
        # placeholders below so name extraction still succeeds either way.
        def _find_card(window: str, url: str):
            pattern = re.compile(
                r'\[!\[(?P<alt>[^\]]*)\]\((?P<img>https://[^)\s]+)\)'
                r'[\s\S]*?\]\('
                + re.escape(url)
                + r'(?:\s+"(?P<title>[^"]+)")?\)',
                re.DOTALL,
            )
            return pattern.search(window)

        for idx, price_match in enumerate(price_matches):
            price_str, product_url = price_match.group(1), price_match.group(2)
            product_url = product_url.split("?", 1)[0]
            if product_url in seen:
                continue
            seen.add(product_url)

            try:
                price = float(price_str)
            except ValueError:
                price = None

            # SKU
            sku_m = _SKU_RE.search(product_url)
            sku = sku_m.group(1) if sku_m else None

            # Window: from the end of the previous price marker to the
            # start of this one. This is where this product's card lives.
            window_start = price_matches[idx - 1].end() if idx > 0 else 0
            window = markdown[window_start:price_match.start()]

            card_m = _find_card(window, product_url)

            primary_image: Optional[str] = None
            name: Optional[str] = None

            if card_m:
                img_raw = card_m.group("img")
                # Drop placeholder "no image" SVGs — they aren't real photos.
                if img_raw and "noImage" not in img_raw and img_raw.startswith(
                    "https://media.bunnings.com.au/"
                ):
                    primary_image = img_raw
                # Prefer the title attribute, fall back to the image alt
                name = (card_m.group("title") or card_m.group("alt") or "").strip()

            # Bump thumbnail → larger size for Claude Vision analysis later
            if primary_image:
                primary_image = re.sub(r"t=w\d+dpr\d+", "t=w600dpr2", primary_image)

            if not name:
                # Last resort — humanise the slug
                slug = product_url.rstrip("/").split("/")[-1]
                slug = re.sub(r"_p\d+$", "", slug)
                name = slug.replace("-", " ").title()

            products.append(
                RawProduct(
                    url=product_url,
                    name=name,
                    retailer_slug=self.RETAILER_SLUG,
                    external_id=sku,
                    sku=sku,
                    price=price,
                    currency="AUD",
                    category=label,
                    image_urls=[primary_image] if primary_image else [],
                    raw_attributes={},
                )
            )

        return products

    # ── Per-product image enrichment ─────────────────────────────────────────

    async def _fetch_pdp_image(self, product_url: str) -> Optional[str]:
        """Fetch the product detail page and return the hero image URL, or None.

        The PDP markdown layout is consistent: a Skip-to-main-content link,
        then the hero image as the FIRST `![<name>](<cdn-url>)` tag. We
        pick the first `media.bunnings.com.au/api/public/content/...`
        URL and normalise it to `t=w600dpr2` for higher-res downstream use.
        """
        md = await self._fetch_markdown(product_url)
        if not md:
            return None
        for m in re.finditer(
            r'https://media\.bunnings\.com\.au/api/public/content/[a-f0-9]{32}'
            r'(?:\?[^)\s")\]]*)?',
            md,
        ):
            url = m.group(0)
            # Filter out obvious non-product images (payment logos, etc.)
            # by requiring a size param — hero images have t=w700dpr1.
            if "t=w" in url:
                return re.sub(r"t=w\d+dpr\d+", "t=w600dpr2", url)
        return None

    async def parse_product(self, product_url: str) -> Optional[RawProduct]:
        """Return the cached listing stub, enriching it with a PDP image
        if the listing didn't have one."""
        stub = self._cache.get(product_url)
        if not stub:
            return None
        if stub.image_urls:
            return stub  # Already has a real listing image — no extra call.

        img = await self._fetch_pdp_image(product_url)
        if img:
            stub.image_urls = [img]
        return stub

    async def scrape(self) -> AsyncIterator[RawProduct]:
        """Listing sweep → batched concurrent PDP image fetches → yield."""
        await self.before_scrape()
        try:
            category_urls = await self.get_category_urls()
            self.log.info("categories_found", count=len(category_urls))

            for cat_url in category_urls:
                is_best_seller_cat = any(
                    kw in cat_url.lower() for kw in _BEST_SELLER_KEYWORDS
                )
                product_urls = await self.get_product_urls(cat_url)
                self.log.info(
                    "products_found",
                    category=cat_url,
                    count=len(product_urls),
                    best_seller_cat=is_best_seller_cat,
                )

                # Split URLs: ones whose listing stub already has an image
                # can be yielded immediately; the rest go through the PDP
                # enrichment pipeline in batches of _PDP_BATCH_SIZE.
                needs_image: list[str] = []
                for url in product_urls:
                    stub = self._cache.get(url)
                    if stub and stub.image_urls:
                        if is_best_seller_cat:
                            stub.is_best_seller = True
                        yield stub
                    else:
                        needs_image.append(url)

                self.log.info(
                    "bunnings_pdp_enrichment_needed",
                    category=cat_url,
                    count=len(needs_image),
                )

                for i in range(0, len(needs_image), _PDP_BATCH_SIZE):
                    batch = needs_image[i : i + _PDP_BATCH_SIZE]
                    results = await asyncio.gather(
                        *[self.parse_product(u) for u in batch],
                        return_exceptions=True,
                    )
                    for res in results:
                        if isinstance(res, Exception):
                            self.log.warning("bunnings_pdp_error", error=str(res))
                        elif res:
                            if is_best_seller_cat:
                                res.is_best_seller = True
                            yield res
        finally:
            await self.after_scrape()

    async def _polite_delay(self):
        """Delay handled inside scrape() batching."""
        pass
