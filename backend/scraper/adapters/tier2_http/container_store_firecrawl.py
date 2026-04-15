"""
The Container Store adapter (containerstore.com) — Firecrawl-powered.

Replaces the previous httpx/BeautifulSoup adapter. Firecrawl renders the
JS-loaded product grid and returns markdown with names, prices, images,
and product URLs — all from the listing page (no per-product requests).

Markdown structure per product card:
  [![{name}]({img_url})](?)
  ![Product Badge](...)     ← optional sale badge
  [${price} – ${price_high}\\
  \\
  {name}\\
  \\
  {review_count}]({product_url})
  Choose Options
"""
import re
from typing import Optional
from scraper.firecrawl_adapter import FirecrawlAdapter
from scraper.base_adapter import RawProduct

CATEGORY_URLS = [
    # Kitchen categories — these reliably render the product grid via Firecrawl.
    # Non-kitchen paths (closet, bathroom, office) return bot-detection pages.
    "https://www.containerstore.com/s/kitchen/pantry-organizers/12?",
    "https://www.containerstore.com/s/kitchen/food-storage/12?",
    "https://www.containerstore.com/s/kitchen/cabinet-organizers/12?",
]

# Image line: [![{name}]({img_url})]({product_url_with_productId})
# We key by productId so image lookup is reliable regardless of alt-text vs name differences.
_IMG_RE = re.compile(
    r'\[!\[([^\]]+)\]\((https://(?:images|www)\.containerstore\.com/[^)]+)\)\]'
    r'\([^)]*productId=(\d+)[^)]*\)'
)

# Product link block:
# [$price\\ \n ...\n name\\ \n ... \n review_count](product_url)
# Firecrawl returns markdown line-breaks as \\ (two backslashes), so allow 1-2.
_PRODUCT_LINK_RE = re.compile(
    r'\[\$([0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?)\s*\\{1,2}\s*\n'  # price line
    r'(.*?)'                                                         # middle content
    r'\]\((https://www\.containerstore\.com/s/[^)]+productId=[^)]+)\)',
    re.DOTALL,
)

# Was-price to strip out (e.g. "Was$19.99" or "Was $19.99 – $29.99")
_WAS_PRICE_RE = re.compile(r'Was\s*\$[0-9.,]+(?:\s*[–\-]\s*\$[0-9.,]+)?')

# CS uses page size of 60–62 per listing page
_PAGE_SIZE = 62


class ContainerStoreFirecrawlAdapter(FirecrawlAdapter):
    RETAILER_SLUG = "container-store"
    WAIT_MS = 4000

    async def get_category_urls(self) -> list[str]:
        return CATEGORY_URLS

    def _paginate_url(self, base_url: str, page: int) -> str:
        """Container Store uses startIndex for pagination."""
        if page == 1:
            return base_url
        offset = (page - 1) * _PAGE_SIZE
        sep = "&" if "?" in base_url else "?"
        return f"{base_url}{sep}startIndex={offset}"

    async def _parse_listing(self, url: str, markdown: str) -> list[RawProduct]:
        products: list[RawProduct] = []
        category = _category_from_url(url)

        # Build image map: productId → image URL (both image link and product block
        # share the same productId URL, so this is more reliable than alt-text matching)
        images: dict[str, str] = {}
        for m in _IMG_RE.finditer(markdown):
            product_id_key = m.group(3)   # productId from the image link target
            images[product_id_key] = m.group(2)

        for m in _PRODUCT_LINK_RE.finditer(markdown):
            raw_price = m.group(1)
            middle = m.group(2)
            product_url = m.group(3)

            # Parse lowest price from the price range string
            price = self._extract_price("$" + raw_price)

            # Extract name from middle content (last non-empty non-number line)
            name = _extract_name_from_middle(middle)
            if not name:
                continue

            # Extract productId as external_id; use it to look up the image
            pid_m = re.search(r'productId=(\d+)', product_url)
            external_id = pid_m.group(1) if pid_m else None
            image_url = images.get(external_id) if external_id else None

            products.append(RawProduct(
                url=product_url,
                name=name,
                retailer_slug=self.RETAILER_SLUG,
                external_id=external_id,
                price=price,
                currency="USD",
                category=category,
                image_urls=[image_url] if image_url else [],
                raw_attributes={},
            ))

        return products


def _extract_name_from_middle(text: str) -> str:
    """
    Extract product name from the link body text.
    The body contains: price, blank lines, name, blank lines, review count.
    Name is the first substantial non-price non-numeric line.
    """
    text = _WAS_PRICE_RE.sub('', text)   # strip "Was$xx" lines
    lines = [l.strip().rstrip('\\').strip() for l in text.splitlines()]
    for line in lines:
        # Skip blank, pure numbers, price lines, short fragments
        if not line:
            continue
        if re.match(r'^[\d,.\s$–\-]+$', line):
            continue
        if len(line) < 3:
            continue
        return line
    return ""


def _category_from_url(url: str) -> str:
    mapping = {
        "pantry-organizers": "Pantry Organizers",
        "food-storage": "Food Storage",
        "cabinet-organizers": "Cabinet Organizers",
        "drawer-organizers": "Drawer Organizers",
        "bins-baskets": "Bins & Baskets",
        "bathroom-storage": "Bathroom Storage",
        "desktop-organization": "Desktop Organization",
    }
    for key, label in mapping.items():
        if key in url:
            return label
    return "Storage"
