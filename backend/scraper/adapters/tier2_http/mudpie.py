from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class MudPieAdapter(ShopifyAdapter):
    RETAILER_SLUG = "mudpie"
    COLLECTION_HANDLES = ["home-decor", "kitchen", "entertaining", "storage", "candles"]
