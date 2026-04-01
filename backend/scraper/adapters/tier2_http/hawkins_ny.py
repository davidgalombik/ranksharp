from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class HawkinsNYAdapter(ShopifyAdapter):
    RETAILER_SLUG = "hawkins-ny"
    COLLECTION_HANDLES = ["kitchen", "dining", "storage", "bar-and-entertaining", "home"]
