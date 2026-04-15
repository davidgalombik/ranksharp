from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Database
    database_url: str = "postgresql+asyncpg://trendtracker:trendtracker@localhost:5432/trendtracker"
    database_url_sync: str = "postgresql://trendtracker:trendtracker@localhost:5432/trendtracker"

    # Redis
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"

    # Anthropic
    anthropic_api_key: str = ""

    # Etsy
    etsy_api_key: str = ""
    etsy_keystring: str = ""

    # Residential proxy (for REQUIRES_PROXY adapters)
    proxy_url: str = ""
    proxy_username: str = ""
    proxy_password: str = ""

    # Smartproxy Universal Scraping API (for Akamai-protected sites)
    # Sign up at smartproxy.com → Scraping → Universal Scraping API
    # Use the sub-account username + password from your Smartproxy dashboard
    scraping_api_username: str = ""
    scraping_api_password: str = ""

    # Apify (for Wayfair — PerimeterX/Akamai blocks all proxy-based scraping)
    # Copy token from apify.com/account/integrations
    apify_api_token: str = ""

    # Firecrawl (for JS-heavy sites: TJMaxx, Container Store, Williams-Sonoma)
    # Get API key from firecrawl.dev/app
    firecrawl_api_key: str = ""

    # Storage
    raw_data_path: str = "/app/raw_data"
    aldi_upload_dir: str = "/app/raw_data/aldi_uploads"
    instore_upload_dir: str = "/app/raw_data/instore_uploads"
    aws_s3_bucket: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "ap-southeast-2"

    # CORS
    allowed_origins: str = "http://localhost:3000"

    # Scraping
    scrape_concurrency: int = 5
    request_delay_min: float = 1.5
    request_delay_max: float = 4.0
    max_retries: int = 3

    # AI Models
    vision_model: str = "claude-opus-4-6"
    nlp_model: str = "claude-opus-4-6"

    # Trend engine
    trend_cluster_min_size: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
