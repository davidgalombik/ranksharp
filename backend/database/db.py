"""Database engine and session management."""
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine, text
from config import settings
from database.models import Base

# Async engine for FastAPI
async_engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

# Sync engine for Celery tasks / Alembic
sync_engine = create_engine(settings.database_url_sync, echo=False)


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    """Create all tables and enable pgvector extension."""
    async with async_engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        await conn.run_sync(Base.metadata.create_all)
        # Guard-checked migrations: each ADD COLUMN / ALTER COLUMN checks
        # information_schema first so we never attempt to acquire a lock on a
        # table that is already up-to-date. CREATE INDEX/TABLE use IF NOT EXISTS
        # which is safe on its own (no heavy lock on no-op).
        # Format: (sql, guard_sql) — only run sql if guard returns a row.
        # Plain strings are always run (CREATE INDEX/TABLE with IF NOT EXISTS).
        def _col(table, col):
            """Guard: returns a row if the column already exists (skip ADD COLUMN)."""
            return (
                f"SELECT 1 FROM information_schema.columns "
                f"WHERE table_name='{table}' AND column_name='{col}'"
            )

        def _idx(index_name):
            """Guard: returns a row if the index already exists (skip CREATE INDEX)."""
            return f"SELECT 1 FROM pg_indexes WHERE indexname='{index_name}'"

        migrations = [
            ("ALTER TABLE trends ADD COLUMN IF NOT EXISTS markets JSONB DEFAULT '[]'",
             _col("trends", "markets")),
            ("ALTER TABLE trends ADD COLUMN IF NOT EXISTS price_tier VARCHAR(20)",
             _col("trends", "price_tier")),
            ("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_best_seller BOOLEAN DEFAULT FALSE",
             _col("products", "is_best_seller")),
            ("CREATE INDEX IF NOT EXISTS ix_product_is_best_seller ON products (is_best_seller)",
             _idx("ix_product_is_best_seller")),
            ("ALTER TABLE products ADD COLUMN IF NOT EXISTS has_patent BOOLEAN DEFAULT FALSE",
             _col("products", "has_patent")),
            ("ALTER TABLE products ADD COLUMN IF NOT EXISTS is_new BOOLEAN DEFAULT TRUE",
             _col("products", "is_new")),
            ("ALTER TABLE aldi_uploads ADD COLUMN IF NOT EXISTS mood_descriptors JSONB DEFAULT '[]'",
             _col("aldi_uploads", "mood_descriptors")),
            ("ALTER TABLE aldi_uploads ADD COLUMN IF NOT EXISTS raw_analysis JSONB DEFAULT '{}'",
             _col("aldi_uploads", "raw_analysis")),
            "CREATE TABLE IF NOT EXISTS aldi_sessions (id SERIAL PRIMARY KEY, status VARCHAR(20) NOT NULL DEFAULT 'pending', error_message TEXT, themes JSONB DEFAULT '[]', colour_palette JSONB DEFAULT '[]', colour_hex JSONB DEFAULT '[]', key_materials JSONB DEFAULT '[]', key_prints JSONB DEFAULT '[]', product_categories JSONB DEFAULT '[]', season_occasion VARCHAR(200), mood_descriptors JSONB DEFAULT '[]', created_at TIMESTAMP DEFAULT NOW(), updated_at TIMESTAMP)",
            ("CREATE INDEX IF NOT EXISTS ix_aldi_sessions_status ON aldi_sessions (status)",
             _idx("ix_aldi_sessions_status")),
            ("ALTER TABLE aldi_uploads ADD COLUMN IF NOT EXISTS session_id INTEGER REFERENCES aldi_sessions(id) ON DELETE CASCADE",
             _col("aldi_uploads", "session_id")),
            ("CREATE INDEX IF NOT EXISTS ix_aldi_uploads_session_id ON aldi_uploads (session_id)",
             _idx("ix_aldi_uploads_session_id")),
            ("ALTER TABLE aldi_product_ideas ADD COLUMN IF NOT EXISTS session_id INTEGER REFERENCES aldi_sessions(id) ON DELETE CASCADE",
             _col("aldi_product_ideas", "session_id")),
            ("CREATE INDEX IF NOT EXISTS ix_aldi_ideas_session_id ON aldi_product_ideas (session_id)",
             _idx("ix_aldi_ideas_session_id")),
            ("ALTER TABLE aldi_product_ideas ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT 1",
             _col("aldi_product_ideas", "generation")),
            ("ALTER TABLE aldi_product_ideas ADD COLUMN IF NOT EXISTS inspired_by_product_ids JSONB DEFAULT '[]'",
             _col("aldi_product_ideas", "inspired_by_product_ids")),
            ("ALTER TABLE aldi_product_ideas ADD COLUMN IF NOT EXISTS inspired_by_products JSONB DEFAULT '[]'",
             _col("aldi_product_ideas", "inspired_by_products")),
            # Guard: skip if upload_id is ALREADY nullable (migration already applied)
            (
                "ALTER TABLE aldi_product_ideas ALTER COLUMN upload_id DROP NOT NULL",
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name='aldi_product_ideas' AND column_name='upload_id' AND is_nullable='YES'",
            ),
            "CREATE TABLE IF NOT EXISTS fragrance_trends (id SERIAL PRIMARY KEY, week_start TIMESTAMP NOT NULL, name VARCHAR(500) NOT NULL, description TEXT NOT NULL, rationale TEXT NOT NULL, category VARCHAR(100) NOT NULL, status VARCHAR(20) DEFAULT 'new', product_count INTEGER DEFAULT 0, retailer_count INTEGER DEFAULT 0, retailer_names JSONB DEFAULT '[]', avg_price FLOAT, momentum_pct FLOAT, prev_trend_id INTEGER REFERENCES fragrance_trends(id), dominant_colours JSONB DEFAULT '[]', dominant_materials JSONB DEFAULT '[]', container_styles JSONB DEFAULT '[]', scent_families JSONB DEFAULT '[]', sustainability_signals JSONB DEFAULT '[]', markets JSONB DEFAULT '[]', price_tier VARCHAR(20), created_at TIMESTAMP DEFAULT NOW())",
            ("CREATE INDEX IF NOT EXISTS ix_fragrance_trend_week_start ON fragrance_trends (week_start)",
             _idx("ix_fragrance_trend_week_start")),
            "CREATE TABLE IF NOT EXISTS fragrance_trend_examples (id SERIAL PRIMARY KEY, trend_id INTEGER NOT NULL REFERENCES fragrance_trends(id), product_id INTEGER NOT NULL REFERENCES products(id), relevance_score FLOAT DEFAULT 1.0, is_hero BOOLEAN DEFAULT FALSE, UNIQUE(trend_id, product_id))",
            "CREATE TABLE IF NOT EXISTS fragrance_trend_reports (id SERIAL PRIMARY KEY, week_start TIMESTAMP NOT NULL UNIQUE, title VARCHAR(500) NOT NULL, summary TEXT NOT NULL, trend_ids JSONB DEFAULT '[]', total_products_analysed INTEGER DEFAULT 0, retailers_covered INTEGER DEFAULT 0, created_at TIMESTAMP DEFAULT NOW())",
            ("ALTER TABLE trends ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT 1",
             _col("trends", "generation")),
            ("CREATE INDEX IF NOT EXISTS ix_trend_generation ON trends (week_start, generation)",
             _idx("ix_trend_generation")),
            ("ALTER TABLE trend_reports ADD COLUMN IF NOT EXISTS generation_count INTEGER DEFAULT 1",
             _col("trend_reports", "generation_count")),
            ("ALTER TABLE fragrance_trends ADD COLUMN IF NOT EXISTS generation INTEGER DEFAULT 1",
             _col("fragrance_trends", "generation")),
            ("CREATE INDEX IF NOT EXISTS ix_fragrance_trend_generation ON fragrance_trends (week_start, generation)",
             _idx("ix_fragrance_trend_generation")),
            ("ALTER TABLE fragrance_trend_reports ADD COLUMN IF NOT EXISTS generation_count INTEGER DEFAULT 1",
             _col("fragrance_trend_reports", "generation_count")),
        ]
        for item in migrations:
            if isinstance(item, tuple):
                stmt, guard = item
                # Skip entirely if guard finds it already applied — zero lock acquired
                row = await conn.execute(text(guard))
                if row.fetchone():
                    continue
            else:
                stmt = item
            try:
                await conn.execute(text("SAVEPOINT mig"))
                await conn.execute(text(stmt))
                await conn.execute(text("RELEASE SAVEPOINT mig"))
            except Exception:
                await conn.execute(text("ROLLBACK TO SAVEPOINT mig"))


async def seed_retailers():
    """Seed the retailers table with all configured sites."""
    from database.models import Retailer, ScrapeTier
    from sqlalchemy import select

    retailers_config = [
        # ── Tier 1: API ─────────────────────────────────────────────────────
        dict(slug="etsy", name="Etsy", base_url="https://www.etsy.com", country="US",
             tier=ScrapeTier.API, adapter_class="scraper.adapters.tier1_api.etsy.EtsyAdapter",
             categories={"storage": "Baskets & Bins", "decor": "Home Decor"}),
        dict(slug="ikea-us", name="IKEA US", base_url="https://www.ikea.com/us/en", country="US",
             tier=ScrapeTier.API, adapter_class="scraper.adapters.tier1_api.ikea.IkeaAdapter",
             categories={"storage": "storage-organisation", "decor": "decoration"}),
        dict(slug="ikea-au", name="IKEA AU", base_url="https://www.ikea.com/au/en", country="AU",
             tier=ScrapeTier.API, adapter_class="scraper.adapters.tier1_api.ikea.IkeaAdapter",
             categories={"storage": "storage-organisation", "decor": "decoration"}),

        # ── Tier 2: HTTP ─────────────────────────────────────────────────────
        dict(slug="crate-and-barrel", name="Crate & Barrel", base_url="https://www.crateandbarrel.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.crate_barrel_firecrawl.CrateBarrelFirecrawlAdapter",
             categories={"storage": "storage-organization", "decor": "decorative-accessories"}),
        dict(slug="world-market", name="World Market", base_url="https://www.worldmarket.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.world_market.WorldMarketAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="pottery-barn", name="Pottery Barn", base_url="https://www.potterybarn.com", country="US",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.pottery_barn_smartproxy.PotteryBarnSmartproxyAdapter",
             categories={"storage": "storage-organization", "decor": "decorating-accessories"}),
        dict(slug="west-elm", name="West Elm", base_url="https://www.westelm.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.west_elm_firecrawl.WestElmFirecrawlAdapter",
             categories={"storage": "storage-organization", "decor": "decorative-accessories"}),
        dict(slug="williams-sonoma", name="Williams Sonoma", base_url="https://www.williams-sonoma.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.williams_sonoma_firecrawl.WilliamsSonomaFirecrawlAdapter",
             categories={"storage": "kitchen-storage", "decor": "home-decor"}),
        dict(slug="container-store", name="The Container Store", base_url="https://www.containerstore.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.container_store_firecrawl.ContainerStoreFirecrawlAdapter",
             categories={"storage": "kitchen", "decor": "office"}),
        dict(slug="tjmaxx", name="TJ Maxx", base_url="https://tjmaxx.tjx.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.tjmaxx.TJMaxxAdapter",
             categories={"storage": "baskets-storage", "decor": "decorative-accessories"}),
        dict(slug="at-home", name="At Home", base_url="https://www.athome.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.at_home_firecrawl.AtHomeFirecrawlAdapter",
             categories={"storage": "storage-organization", "decor": "home-decor"}),
        dict(slug="meijer", name="Meijer", base_url="https://www.meijer.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.meijer.MeijerAdapter",
             categories={"storage": "home-storage", "decor": "home-decor"}),
        dict(slug="next-uk", name="Next UK", base_url="https://www.next.co.uk", country="GB",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.next_uk.NextUKAdapter",
             categories={"storage": "storage-boxes", "decor": "home-accessories"}),
        dict(slug="next-au", name="Next AU", base_url="https://www.next.com.au/en", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.next_au.NextAUAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="big-w", name="Big W", base_url="https://www.bigw.com.au", country="AU",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.big_w.BigWAdapter",
             categories={"storage": "kitchen-storage", "decor": "home-decor"}),
        dict(slug="target-au", name="Target AU", base_url="https://www.target.com.au", country="AU",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.target_au.TargetAUAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="kmart-au", name="Kmart AU", base_url="https://www.kmart.com.au", country="AU",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.kmart_au.KmartAUAdapter",
             categories={"storage": "storage", "decor": "homewares"}),
        dict(slug="house-au", name="House", base_url="https://www.house.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.house_au.HouseAUAdapter",
             categories={"storage": "kitchen-storage", "decor": "home-decor"}),
        dict(slug="reject-shop", name="The Reject Shop", base_url="https://www.rejectshop.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.reject_shop.RejectShopAdapter",
             categories={"storage": "storage", "decor": "homewares"}),
        dict(slug="dusk", name="Dusk", base_url="https://www.dusk.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.dusk.DuskAdapter",
             categories={"decor": "candles"}),
        dict(slug="pottery-barn-au", name="Pottery Barn AU", base_url="https://www.potterybarn.com.au", country="AU",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.williams_sonoma_group.WilliamsSonomaGroupAdapter",
             categories={"storage": "storage", "decor": "decorating"}),
        # Howards Storage World closed down ~2020 — domain NXDOMAIN, keeping record but inactive
        dict(slug="howards-storage", name="Howards Storage World", base_url="https://www.howardsstorage.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.howards_storage.HowardsStorageAdapter",
             categories={"storage": "kitchen-pantry", "decor": "home-decor"}),
        dict(slug="david-jones", name="David Jones", base_url="https://www.davidjones.com", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.david_jones.DavidJonesAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="myer", name="Myer", base_url="https://www.myer.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.myer_firecrawl.MyerFirecrawlAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="hm-home", name="H&M Home", base_url="https://www2.hm.com", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.hm_home.HMHomeAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="officeworks", name="Officeworks", base_url="https://www.officeworks.com.au", country="AU",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.officeworks.OfficeworksAdapter",
             categories={"storage": "storage", "decor": "home"}),
        dict(slug="bunnings", name="Bunnings", base_url="https://www.bunnings.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.bunnings.BunningsAdapter",
             categories={"storage": "storage", "decor": "home-decor"}),
        dict(slug="hawkins-ny", name="Hawkins New York", base_url="https://www.hawkinsnewyork.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.hawkins_ny.HawkinsNYAdapter",
             categories={"storage": "kitchen", "decor": "home"}),
        dict(slug="design-stuff", name="Design Stuff", base_url="https://designstuff.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.design_stuff.DesignStuffAdapter",
             categories={"decor": "homewares"}),
        dict(slug="casa-and-beyond", name="Casa and Beyond", base_url="https://casaandbeyond.com.au", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.casa_and_beyond.CasaAndBeyondAdapter",
             categories={"decor": "homewares"}),
        dict(slug="oliver-bonas", name="Oliver Bonas", base_url="https://www.oliverbonas.com", country="GB",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.oliver_bonas.OliverBonasAdapter",
             categories={"storage": "storage", "decor": "home"}),
        dict(slug="some-design-store", name="Some Design Store", base_url="https://www.somedesignstore.com", country="AU",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.some_design_store.SomeDesignStoreAdapter",
             categories={"decor": "homewares"}),
        dict(slug="original-home", name="Original Home", base_url="https://originalhome.nl", country="NL",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.original_home.OriginalHomeAdapter",
             categories={"decor": "homewares"}),
        dict(slug="lenox", name="Lenox", base_url="https://www.lenox.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.lenox.LenoxAdapter",
             categories={"storage": "kitchen-storage", "decor": "home-decor"}),
        dict(slug="cailini-coastal", name="Cailini Coastal", base_url="https://cailinicoastal.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.cailini_coastal.CailiniCoastalAdapter",
             categories={"decor": "home-decor"}),
        dict(slug="bloomingville", name="Bloomingville", base_url="https://www.bloomingville.us", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.bloomingville.BloomingvilleAdapter",
             categories={"storage": "storage", "decor": "decoration"}),
        dict(slug="swell", name="S'well", base_url="https://www.swell.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.swell.SwellAdapter",
             categories={"storage": "bottles-bags"}),
        dict(slug="mudpie-usa-store", name="Mud Pie USA Store", base_url="https://mudpieusastore.shop", country="US",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.mudpie_usa_store_smartproxy.MudPieUSAStoreAdapter",
             categories={"decor": "home-decor", "storage": "kitchen"}),
        dict(slug="dw-home", name="DW Home Candles", base_url="https://www.dwhome.com", country="US",
             tier=ScrapeTier.HTTP, adapter_class="scraper.adapters.tier2_http.dw_home.DWHomeAdapter",
             categories={"decor": "candles"}),

        # ── Tier 3: Browser ──────────────────────────────────────────────────
        dict(slug="wayfair", name="Wayfair", base_url="https://www.wayfair.com", country="US",
             tier=ScrapeTier.API, adapter_class="scraper.adapters.tier1_api.wayfair_apify.WayfairApifyAdapter",
             categories={"storage": "storage-organization", "decor": "decorative-accessories"}),
        dict(slug="anthropologie", name="Anthropologie", base_url="https://www.anthropologie.com", country="US",
             tier=ScrapeTier.API,
             adapter_class="scraper.adapters.tier1_api.anthropologie_apify.AnthropologieApifyAdapter",
             categories={"decor": "home-catalog", "new": "new-home"}),
        dict(slug="target-us", name="Target US", base_url="https://www.target.com", country="US",
             tier=ScrapeTier.API,
             adapter_class="scraper.adapters.tier1_api.target_apify.TargetApifyAdapter",
             categories={"decor": "home-decor", "storage": "storage-organization"}),
        dict(slug="amazon-us", name="Amazon US", base_url="https://www.amazon.com", country="US",
             tier=ScrapeTier.API,
             adapter_class="scraper.adapters.tier1_api.amazon_apify.AmazonApifyAdapter",
             categories={"decor": "home-decor", "storage": "storage-organization"}),
        dict(slug="walmart-us", name="Walmart US", base_url="https://www.walmart.com", country="US",
             tier=ScrapeTier.API,
             adapter_class="scraper.adapters.tier1_api.walmart_apify.WalmartApifyAdapter",
             categories={"decor": "home-decor", "storage": "storage-organization"}),
        dict(slug="temu", name="Temu", base_url="https://www.temu.com", country="US",
             tier=ScrapeTier.BROWSER, adapter_class="scraper.adapters.tier3_browser.temu.TemuAdapter",
             categories={"storage": "home-storage", "decor": "home-decor"}),
    ]

    async with AsyncSessionLocal() as session:
        for config in retailers_config:
            result = await session.execute(
                select(Retailer).where(Retailer.slug == config["slug"])
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                session.add(Retailer(**config))
            else:
                # Always sync mutable fields so fixes to db.py take effect on restart
                for key in ("base_url", "tier", "adapter_class", "categories", "name", "country"):
                    if key in config:
                        setattr(existing, key, config[key])
        await session.commit()
