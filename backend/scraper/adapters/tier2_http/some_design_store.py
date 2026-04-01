from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class SomeDesignStoreAdapter(ShopifyAdapter):
    RETAILER_SLUG = "some-design-store"
    COLLECTION_HANDLES = ["homewares", "kitchen", "decor", "storage", "candles"]
