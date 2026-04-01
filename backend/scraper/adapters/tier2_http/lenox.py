"""Lenox — Shopify adapter (lenox.com)."""
from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter


class LenoxAdapter(ShopifyAdapter):
    """
    Lenox is a US luxury tableware & home decor brand running on Shopify.
    """
    RETAILER_SLUG = "lenox"
    COLLECTION_HANDLES = [
        "best-sellers",  # best-seller flag auto-applied
        "serveware",
        "bowls",
        "vases-decorative-accents",
        "kitchen",
        "home-decor",
        "holiday",
        "frames-albums",
        "entertaining",
        "gifts",
        "dinnerware",
        "all",
    ]
