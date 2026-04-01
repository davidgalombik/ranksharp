from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class DesignStuffAdapter(ShopifyAdapter):
    RETAILER_SLUG = "design-stuff"
    COLLECTION_HANDLES = ["storage", "homewares", "kitchen", "decor", "organisation"]
