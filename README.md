# Retail Trend Tracker

Scans 40+ home décor and storage retailers weekly, analyses every product with Claude AI (vision + NLP), clusters them into trends, and presents a browsable trend report.

## Architecture

```
Retailers (42 sites)
  └─ Scraping Orchestrator  (Celery workers, weekly cron)
       ├─ Tier 1 — API adapters    (Etsy, IKEA)
       ├─ Tier 2 — HTTP adapters   (Crate & Barrel, Williams Sonoma group, etc.)
       └─ Tier 3 — Browser adapters (Wayfair, Anthropologie, Target, Temu)
              │
              ▼
     Raw Product Queue (Redis)  ←→  Raw Product Store (local / S3)
              │
              ▼
     AI Analysis Pipeline (Claude claude-opus-4-6)
       ├─ Vision model  → colours, shape, style, finish
       ├─ NLP extractor → materials, patterns, fragrance, function tags
       └─ Embedding     → 1536-dim vector per product
              │
              ▼
     PostgreSQL + pgvector  (enriched product database)
              │
              ▼
     Trend Engine  (k-means clustering + delta detection)
       ├─ Cluster similar products across retailers
       ├─ Rank by frequency + retailer spread
       ├─ Detect rising / plateau / declining vs prior week
       └─ Claude names each trend + writes rationale
              │
              ▼
     Weekly Trend Report  (Next.js web UI + API)
```

## Setup

### 1. Prerequisites
- Docker + Docker Compose
- Anthropic API key (for Claude vision + NLP analysis)
- Etsy API key (optional, for Etsy adapter)
- Proxy service credentials (optional but recommended for Tier 3 scrapers)

### 2. Configure environment
```bash
cp .env.example .env
# Edit .env and set:
#   ANTHROPIC_API_KEY=sk-ant-...
#   ETSY_KEYSTRING=...          (optional)
#   PROXY_URL=...               (optional, for Walmart/Target/Temu)
```

### 3. Start all services
```bash
docker compose up -d
```

This starts:
- PostgreSQL + pgvector (port 5432)
- Redis (port 6379)
- FastAPI backend (port 8000)
- Celery worker (scrape + analysis + report queues)
- Celery beat (weekly scheduler)
- Next.js frontend (port 3000)

### 4. Open the app
- Web UI: http://localhost:3000
- API docs: http://localhost:8000/docs

### 5. Trigger your first scrape
Either click **"Scrape all now"** in the Retailers page, or:
```bash
curl -X POST http://localhost:8000/api/retailers/scrape-all
```

Scraping runs in the background. Progress is visible in `docker compose logs worker`.

After scraping completes, analysis runs automatically. Then trigger the trend report:
```bash
curl -X POST http://localhost:8000/api/reports/generate
```

## Weekly schedule (automatic)
| Time (UTC) | Action |
|---|---|
| Sunday 01:00 | All retailers scraped |
| Sunday 08:00 | Trend analysis + report generated |

## Retailers

### US — Tier 1 (API)
- Etsy (official API v3)
- IKEA US (unofficial JSON API)

### US — Tier 2 (HTTP)
- Crate & Barrel, World Market, Pottery Barn, West Elm, Williams-Sonoma
- The Container Store, At Home, Meijer
- Hawkins New York, Lenox, Cailini Coastal, Bloomingville, S'well, Mud Pie, DW Home

### US — Tier 3 (Browser / Playwright)
- Wayfair, Anthropologie, Target, Temu

### AU — Tier 1 (API)
- IKEA AU

### AU — Tier 2 (HTTP)
- Big W, Target AU, Kmart, House, The Reject Shop, Dusk
- Pottery Barn AU, Howards Storage World, David Jones, Myer
- H&M Home, Officeworks, Bunnings, Next AU
- Design Stuff, Casa & Beyond, Some Design Store

### International
- Next UK (GB), Oliver Bonas (GB), Original Home (NL)

## Adding a new retailer

1. Create an adapter in `backend/scraper/adapters/tier2_http/my_site.py`
2. Subclass `CrateAndBarrelAdapter` (HTTP) or `WayfairAdapter` (browser)
3. Override `CATEGORY_PATHS`, `get_product_urls()`, and `parse_product()`
4. Add a row to the `seed_retailers()` function in `database/db.py`

## Trend output example

```json
{
  "name": "Warm Terracotta Revival",
  "description": "Earthy terracotta tones are appearing across storage and decor ...",
  "rationale": "Seen across 12 retailers from Wayfair to Kmart AU, terracotta ...",
  "category": "colour",
  "status": "rising",
  "momentum_pct": 34.2,
  "dominant_colours": ["terracotta", "rust", "burnt orange"],
  "dominant_materials": ["ceramic", "clay", "stoneware"],
  "product_count": 847,
  "retailer_count": 12
}
```
