from scraper.adapters.tier2_http.shopify_base import ShopifyAdapter

class DWHomeAdapter(ShopifyAdapter):
    RETAILER_SLUG = "dw-home"
    COLLECTION_HANDLES = ["best-sellers", "candles", "home-fragrance", "decor"]

    def _parse_shopify_product(self, p, base_url):
        product = super()._parse_shopify_product(p, base_url)
        # DW Home is a candle brand — normalise currency
        product.currency = "USD"
        return product
