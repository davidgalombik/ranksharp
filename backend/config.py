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

    # Voyage AI (for semantic embeddings — recommended by Anthropic).
    # Get your key at https://www.voyageai.com/
    voyage_api_key: str = ""
    # voyage-3: 1024-dim, $0.06/M tokens, recommended general-purpose.
    voyage_model: str = "voyage-3"
    voyage_embedding_dim: int = 1024

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
    instore_catalogue_dir: str = "/app/raw_data/instore_catalogue"
    ranksharp_image_dir: str = "/app/raw_data/ranksharp_images"
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
    # ALDI screenshot inventory + session trend digest (Phase 2, 2026-11-01).
    # Sonnet 5.5: strong vision at a fifth of the Opus 4.6 token price; the
    # inventory runs once per upload and the digest once per session.
    aldi_inventory_model: str = "claude-sonnet-5-5"
    # ALDI web product recommender (Phase 3, 2026-11-01). The pipeline runs
    # query planning, discovery searches, candidate ranking and page
    # verification on the fast model, and the final selection on the strong
    # model. Budgets bound cost per run (see docs/aldi-recommender-spike.md).
    aldi_recommend_model: str = "claude-opus-5-5"
    aldi_recommend_fast_model: str = "claude-sonnet-5-5"
    # Discovery only filters a search result list down to product pages —
    # Haiku does that at a twentieth of Sonnet's token price (first run:
    # discovery was half of a $1.49 bill).
    aldi_recommend_search_model: str = "claude-haiku-5-5"
    aldi_recommend_queries: int = 16        # discovery searches per run
    aldi_recommend_verify: int = 15         # candidate pages fetched per run
    aldi_recommend_concurrency: int = 6     # parallel tool calls
    # A run re-plans around what failed and searches again while it has
    # fewer than five picks, up to this many rounds (each adds ~$0.8).
    aldi_recommend_rounds: int = 2
    # Phase 5 (2026-11-04): learning across reports and upkeep.
    aldi_distill_model: str = "claude-sonnet-5-5"
    aldi_distill_min_new: int = 5          # new feedback items before a team's rules are re-distilled
    aldi_linkcheck_model: str = "claude-haiku-5-5"
    aldi_linkcheck_days: int = 7           # re-verify a pick's link this often
    aldi_linkcheck_batch: int = 40         # links per weekly run
    aldi_recommend_amazon_fallback: bool = False   # Amazon pages need the Apify fallback (Phase 5)
    aldi_recommend_prompt_version: str = "2026-11-01.1"
    # Legacy catalogue clustering on ALDI sessions (the old "Recommendations"
    # cards: vector search over the catalogue + an Opus clustering call, and
    # its own Try Again). Off since 2026-11-04 — hidden on the page and not
    # worth the spend while the web recommender is the output. Flip to True
    # (and SHOW_LEGACY_CLUSTERS in frontend/src/app/aldi/page.tsx) to restore.
    aldi_legacy_clusters_enabled: bool = False

    # LLM cost ledger (2026-11-01) — USD per million tokens, keyed by model
    # id (a dated id like claude-opus-4-6-20260101 matches by prefix). Check
    # against anthropic.com/pricing when a model changes. Every llm_calls
    # row stores the rates it was costed with, so edits here never rewrite
    # history. Cache write = 5-minute cache (1.25x input); cache read = 0.1x
    # input except where the pricing page says otherwise.
    llm_rates: dict = {
        "claude-opus-5-5":   {"input": 4.0,  "output": 20.0, "cache_read": 0.20, "cache_write": 5.0},
        "claude-opus-5":     {"input": 5.0,  "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "claude-opus-4-6":   {"input": 5.0,  "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "claude-sonnet-5-5": {"input": 2.0,  "output": 10.0, "cache_read": 0.20, "cache_write": 2.50},
        "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
        "claude-haiku-5-5":  {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    }
    # Used when a model id is not in llm_rates; the row is marked source=family.
    llm_rate_fallbacks: dict = {
        "opus":   {"input": 5.0,  "output": 25.0, "cache_read": 0.50, "cache_write": 6.25},
        "sonnet": {"input": 3.0,  "output": 15.0, "cache_read": 0.30, "cache_write": 3.75},
        "haiku":  {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},
    }
    llm_web_search_rate_usd: float = 0.01   # $10 per 1,000 web searches; fetches are token-only

    # Trend engine
    trend_cluster_min_size: int = 5


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
