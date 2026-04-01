"""Howards Storage World — Shopify adapter (howardsstorage.com.au)."""
from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter


class HowardsStorageAdapter(ShopifyAdapter):
    """
    Howards Storage World is an Australian storage specialist running on Shopify.
    """
    RETAILER_SLUG = "howards-storage"
    COLLECTION_HANDLES = [
        "kitchen-pantry",
        "bathroom",
        "bedroom",
        "laundry",
        "baskets",
        "storage-boxes",
        "containers",
        "home-organisation",
        "office",
        "garage",
        "all",
    ]
