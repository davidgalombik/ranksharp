"""Dusk AU — Shopify adapter (dusk.com.au)."""
from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter


class DuskAdapter(ShopifyAdapter):
    """
    Dusk is an Australian candles & home fragrance retailer running on Shopify.
    Base URL must be https://www.dusk.com.au (not dusk.com).
    """
    RETAILER_SLUG = "dusk"
    COLLECTION_HANDLES = [
        "candles",
        "diffusers",
        "home-fragrance",
        "homewares",
        "reed-diffusers",
        "room-sprays",
        "candle-accessories",
        "storage",
        "all",
    ]
