from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class CasaAndBeyondAdapter(ShopifyAdapter):
    RETAILER_SLUG = "casa-and-beyond"
    COLLECTION_HANDLES = ["homewares", "kitchen", "storage", "decor", "candles"]
