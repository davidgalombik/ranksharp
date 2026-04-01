from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class CailiniCoastalAdapter(ShopifyAdapter):
    RETAILER_SLUG = "cailini-coastal"
    COLLECTION_HANDLES = ["best-sellers", "storage", "home-decor", "kitchen", "entertaining", "decor"]
