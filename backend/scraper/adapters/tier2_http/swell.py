from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class SwellAdapter(ShopifyAdapter):
    RETAILER_SLUG = "swell"
    COLLECTION_HANDLES = ["best-sellers", "bottles", "bags", "food-bowls", "accessories"]
