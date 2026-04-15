"""
TJ Maxx adapter (tjmaxx.tjx.com) — Firecrawl-powered.

TJMaxx uses a JS-rendered product grid. Firecrawl renders the page and
returns markdown containing product names, prices, images, and URLs from
the category listing page — no per-product page requests needed.

Markdown structure per product:
  [![{name}]({img_url})](...)
  [quick look](...)
  [{BRAND}{name}\\
  ${price} \\
  Compare At\\
  ${compare}]({product_url})
"""
import re
from typing import Optional
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct

# Category listing URLs to scrape
CATEGORY_URLS = [
    "https://tjmaxx.tjx.com/store/shop/home-shop-by-category-pillows-decor-baskets-storage/_/N-2832378117",
    "https://tjmaxx.tjx.com/store/shop/home-decorative-accessories/_/N-2091818342",
    "https://tjmaxx.tjx.com/store/shop/home-candles/_/N-221260407",
    "https://tjmaxx.tjx.com/store/shop/home-frames/_/N-106123771",
    "https://tjmaxx.tjx.com/store/shop/home-mirrors-wall-art/_/N-3940881343",
]

# Image line: [![Name](img_url)](any)  — old format (frames, candles)
#          or: [![Name](img_url)![ ](img2_url)](product_url) — new format (rugs)
# The trailing )\] is NOT required; stop after capturing the URL.
_IMG_RE = re.compile(
    r'\[!\[([^\]]*)\]\((https://img\.tjmaxx\.com/[^)]+)\)'
)

# Product URL line (last line of multi-line product link block)
# e.g.  $21](https://tjmaxx.tjx.com/store/jump/product/.../1001141625)
# Must be preceded by a price ($xx.xx) to exclude the image link line which
# also ends with the same product URL but is wrapped as )](url).
_PRODUCT_URL_RE = re.compile(
    r'\$[0-9.,]+\]\((https://tjmaxx\.tjx\.com/store/jump/product/[^)]+)\)'
)

# Price line: $14.99 \
_PRICE_LINE_RE = re.compile(r'^\$([0-9.,]+)\s*\\?$')

# Brand prefix: all-caps word at start of name line (e.g. ENCHANTE, DWELL)
_BRAND_RE = re.compile(r'^([A-Z]{3,12})([A-Z][a-z].+)$')

# Pagination: TJMaxx uses &No= offset (48 per page)
_PAGE_SIZE = 48


class TJMaxxAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "tjmaxx"
    WAIT_MS = 5000

    async def get_category_urls(self) -> list[str]:
        return CATEGORY_URLS

    def _paginate_url(self, base_url: str, page: int) -> str:
        if page == 1:
            return base_url
        offset = (page - 1) * _PAGE_SIZE
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}No={offset}"

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        """
        Line-by-line parser. Each product block in TJMaxx markdown:
          [![Name](img_url)](...)          ← image line
          [quick look](...)
          [{BRAND?}{Name}\\               ← name line (start of product link)
          ${price} \\                      ← price line
          Compare At\\
          ${compare}]({product_url})       ← URL line (end of product link)
        """
        products: list[RawProduct] = []
        category = _category_from_url(url)

        lines = markdown.splitlines()
        last_img_url: Optional[str] = None
        last_img_line: int = -1

        for i, line in enumerate(lines):
            # Track the most recently seen image URL
            img_m = _IMG_RE.search(line)
            if img_m:
                last_img_url = img_m.group(2)
                last_img_line = i
                continue

            url_match = _PRODUCT_URL_RE.search(line)
            if not url_match:
                continue

            full_product_url = url_match.group(1)
            product_url = full_product_url.split("?")[0]
            color_id = re.search(r'colorId=([A-Z0-9]+)', full_product_url)

            # The compare-at price appears on the URL line itself: $19.99](url)
            # Use it as a fallback price; the sale price is on the line before.
            url_line_price_m = re.match(r'^\s*\$([0-9.,]+)\]', line)
            url_line_price = None
            if url_line_price_m:
                try:
                    url_line_price = float(url_line_price_m.group(1).replace(",", ""))
                except ValueError:
                    pass

            # Look backwards up to 6 lines to find price and name
            # (exclude the URL line itself from the backward scan)
            window = lines[max(0, i - 6): i]
            price = None
            name = None
            brand = ""

            for wline in reversed(window):
                stripped = wline.strip().rstrip("\\").strip()
                if not stripped:
                    continue

                # Price line
                pm = _PRICE_LINE_RE.match(stripped)
                if pm and price is None:
                    try:
                        price = float(pm.group(1).replace(",", ""))
                    except ValueError:
                        pass
                    continue

                # Skip "Compare At" and numeric-only lines
                if stripped.lower() == "compare at" or re.match(r'^\$?[\d.,]+$', stripped):
                    continue

                # Name line — starts with [ (opening of the link block)
                if stripped.startswith("[") and name is None:
                    raw = stripped.lstrip("[")
                    # Detect brand prefix (e.g. "ENCHANTETapered Paper...")
                    bm = _BRAND_RE.match(raw)
                    if bm:
                        brand = bm.group(1)
                        name = bm.group(2).strip()
                    else:
                        name = raw.strip()
                    break

            # Fall back to the URL-line price if no separate price line found
            if price is None:
                price = url_line_price

            if not name:
                continue

            name = self._clean_name(name)
            if not name or len(name) < 3:
                continue

            # Use the most recently seen image if within 15 lines
            image_url = last_img_url if last_img_line >= 0 and (i - last_img_line) <= 15 else None

            # Extract product ID and colorId
            id_match = re.search(r'/(\d{7,12})(?:\?|$)', product_url)
            external_id = id_match.group(1) if id_match else None

            # Fallback: construct image URL from productId + colorId when markdown
            # proximity matching fails (loading placeholders, lazy-loaded images, etc.)
            if not image_url and external_id and color_id:
                cid = color_id.group(1)
                image_url = (
                    f"https://img.tjmaxx.com/tjx?set=DisplayName[f2_v2],"
                    f"prd[{external_id}_{cid}],ag[no]&call=url[file:tjxrPRDv3.chain]"
                )

            products.append(RawProduct(
                url=product_url,
                name=name,
                retailer_slug=self.RETAILER_SLUG,
                external_id=external_id,
                price=price,
                currency="USD",
                category=category,
                image_urls=[image_url] if image_url else [],
                raw_attributes={"brand": brand} if brand else {},
            ))

        return products


def _category_from_url(url: str) -> str:
    """Derive a human-readable category name from a TJMaxx URL."""
    mapping = {
        "baskets-storage": "Baskets & Storage",
        "decorative-accessories": "Decorative Accessories",
        "candles": "Candles",
        "frames": "Frames",
        "mirrors-wall-art": "Mirrors & Wall Art",
        "throw-pillows": "Throw Pillows",
        "blankets-throws": "Blankets & Throws",
        "rugs": "Rugs",
    }
    for key, label in mapping.items():
        if key in url:
            return label
    return "Home"
