from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class OliverBonasAdapter(ShopifyAdapter):
    RETAILER_SLUG = "oliver-bonas"
    COLLECTION_HANDLES = ["home", "storage", "kitchen", "candles-diffusers", "vases-pots"]

    def _parse_shopify_product(self, p, base_url):
        product = super()._parse_shopify_product(p, base_url)
        product.currency = "GBP"
        return product
