from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class BloomingvilleAdapter(ShopifyAdapter):
    RETAILER_SLUG = "bloomingville"
    COLLECTION_HANDLES = ["decoration", "storage", "kitchen", "candles-and-fragrance"]
