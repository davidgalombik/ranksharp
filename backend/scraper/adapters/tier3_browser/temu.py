"""TemuAdapter — Browser adapter stub. Inherits WayfairAdapter Playwright pattern."""
from scraper.adapters.tier3_browser.wayfair import WayfairAdapter


class TemuAdapter(WayfairAdapter):
    """
    Stub adapter. Override CATEGORY_URLS, get_product_urls(),
    and parse_product() to match this site's DOM/XHR structure.
    """
    RETAILER_SLUG = "temu"
    REQUIRES_PROXY = True
