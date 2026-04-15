"""Lenox — Shopify adapter (lenox.com)."""
from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter


class LenoxAdapter(ShopifyAdapter):
    """
    Lenox is a US luxury tableware & home decor brand running on Shopify.
    """
    RETAILER_SLUG = "lenox"
    COLLECTION_HANDLES = [
        "best-sellers",   # best-seller flag auto-applied
        "serveware",
        "bowls",
        "kitchen",
        "home-decor",
        "holiday",
        "dinnerware",
        # "vases-decorative-accents", "frames-albums", "entertaining", "gifts" — empty collections
        # "all" removed — it has no label, causing 92% of products to get category=NULL
    ]
