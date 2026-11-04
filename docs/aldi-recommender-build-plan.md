# ALDI web product recommendations — build plan

Status (2026-11-04): Phases 0–4 live. Legacy catalogue clustering switched off
(settings.aldi_legacy_clusters_enabled) and hidden; near misses are feedback-able
rows; Phase 5 built (distillation into learned_rules, retailer rotation, weekly
link re-check), committed locally, awaiting push. Amazon stays off until an Apify
fallback. Prompts: `docs/aldi-recommender-prompts.md`.

## Decisions taken so far

- Market: US. Prices in USD, non-US retailers converted.
- Scope: home storage and organisation; stackable / nesting / collapsible; functional, elevated, novel, proven demand; no licensed IP.
- Teams by primary material: Adrah = fabrics + metals; Sapana = plastic, glass, ceramic, wood & bamboo. Anything else is out of scope for both.
- Five picks per run; Try Again gives the next five. Near misses listed under the cards.
- Search: Claude's built-in web search + web fetch (dynamic-filtering versions), capped per run. Whole web allowed; the retailer list is a priority, not a fence.
- Retailer list from the prompts, as a table with tiers. Square brackets in the prompt mean nothing.
- Editable in the UI for now: price band only. Rating / review thresholds stay in the prompt.
- Card shows only the product link and how it relates to the report. No pitch line, buyer commentary, dimensions, rating or material list in the UI (supersedes the earlier "behind an expand" answer).
- Cost tracker: every Claude call recorded with tokens, searches and USD cost; per-call list and aggregates in the UI.
- Untouched: product trends and fragrance trends (per earlier instruction), scrapers, embeddings, the catalogue pipeline.

## Order of work

| Phase | What | Why this order | Rough size |
|---|---|---|---|
| 0 | Spike: run Prompt 2 against one real session through the API with web search + fetch, from a script on the worker | Confirms Cowork parity, shows which retailers block fetches, measures real cost per run before any code is committed | half a day |
| 1 | Cost tracker: `llm_calls` table, one wrapper around every Claude call, Costs page | Everything after this is costed; cheap to build now, painful to retrofit | 1 day |
| 2 | Screenshot inventory: per-session theme digest (T1..Tn) with per-screenshot elements and bounding boxes | The recommender cites these; both teams and every Try Again reuse them | 1 day |
| 3 | Recommender backend: tables, settings, prompt template, tool loop, rule enforcement, link verification, thumbnails, Celery task, routes | The core | 2–3 days |
| 4 | Frontend: Product Recommendations panel with team tabs, cards, Good/Bad + note, Try Again, near misses, screenshot evidence viewer, price band + memo editor, per-run cost | Makes it usable by both teams | 2 days |
| 5 | Feedback distillation, retailer rotation, dead-link re-check | Once there is feedback volume to learn from | 1 day |

Sizes are working estimates, not commitments. Phase 0 may change Phase 3 (Firecrawl fallback) and the cost table.

## Phase 0 — spike

- Script on the worker: load one session's screenshots, send Prompt 2 verbatim with `web_search_20260209` + `web_fetch_20260209`, `max_uses` 20, streaming, Opus 5.5.
- Record: searches made, fetches attempted and which failed (403 / blocked), picks returned, cost from `usage`.
- Deliverable: a short report with the output and a verdict on whether a Firecrawl-backed fallback fetch tool is needed from day one. Amazon, Walmart and Target are the expected problem sites and they are the mass/value tier.

## Phase 1 — cost tracker

Tables (guard-checked migrations in `database/db.py`, same convention as today):

- `llm_calls`: id, created_at, feature (`catalogue_vision`, `aldi_analysis`, `aldi_cluster`, `aldi_inventory`, `aldi_recommend`, …), model, ref_type + ref_id (session / run / image), input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, web_searches, web_fetches, duration_ms, status, cost_usd, rates_snapshot JSON.
- Rates live in config (`settings.llm_rates`): per-model input / output / cache-read / cache-write per MTok, plus web search per request. Each row stores the rates it was costed with, so later price changes never rewrite history.

Code:

- `analysis/llm_usage.py`: `record(feature, model, response.usage, ref, duration)`; reads `usage.server_tool_use.web_search_requests` when present. One place, called by every Claude call in the catalogue and ALDI modules. Product trends and fragrance trends are left alone for now.
- Routes: `GET /api/costs?from&to&feature` (rows), `GET /api/costs/summary` (today / this month / all time; by feature; by model; by session).
- UI: a Costs page with the summary tiles, a breakdown by feature, and the call table with a link to the session or run. Later, each recommendation run shows its own cost chip.
- Note: calls made before this ships are not recoverable. If an Admin API key exists, the Usage and Cost Admin API can reconcile monthly totals later.

## Phase 2 — screenshot inventory

- Extend `MoodBoardAnalyser` (or add a second pass) so each upload returns `elements`: `[{id, description, bbox_norm, materials, colours, kind: image|text}]`. Same bbox trick as the catalogue vision prompt.
- Session-level `trend_digest`: `[{theme_id: "T1", name, description, refs: [{upload_id, element_id}]}]` — the prompt's step 1, run once per session, stored, reused by both teams and every Try Again.
- Columns: `aldi_uploads.elements` JSON, `aldi_sessions.trend_digest` JSON.
- Back-run for the 28 existing sessions is optional and cheap (189 screenshots).
- The model for this pass: Sonnet 5.5 (vision, cheap). Recorded in `llm_calls` as `aldi_inventory`.

## Phase 3 — recommender backend

Tables:

- `aldi_recommendation_runs`: id, session_id, team, generation, status (queued / running / done / failed), model, prompt_version, searches, fetches, cost_usd, started_at, finished_at, error, search_queries JSON, near_misses JSON, raw_output JSON.
- `aldi_recommendations`: id, run_id, position, product_name, retailer, retailer_tier, url, url_hash, price_usd, material_family, theme_id, evidence JSON `[{upload_id, element_id, description}]`, how_it_matches, demand_evidence (short text, kept for debugging only), verification_status (verified / unverified_blocked / rejected), page_title, thumbnail (bytes, downsized), created_at.
  Price, retailer tier and material family are stored only so the backend can enforce the rules; the UI does not show them. Dimensions, stacking detail, rating, pitch line and buyer commentary are not collected (decision 2026-11-01).
- `aldi_recommendation_feedback`: id, recommendation_id, verdict (good / bad), note, created_at.
- `aldi_team_preferences`: team (pk), memo text, price_min, price_max (default 15 / 60), updated_at.
- `aldi_preferred_retailers`: id, name, domain, tier (mass_value / marketplace / off_price / mid_market / premium / brand / wholesale / kids / pet / lifestyle / apparel / appliance / germany), priority (high / normal / low), country, active. Seeded from the prompt. Kids, pet, apparel, appliance and Germany seeded as low priority.

Prompt (`analysis/prompts/aldi_recommender.py`, versioned):

- System (cached): role, criteria, selection preference, spread rules, Rules section, output JSON schema. One template; the material block is injected per team. The criteria stay in full so the model still vets stacking, demand and finish before choosing; only the output schema is trimmed to what the app stores.
- User turn: trend digest with element ids; team material block; retailer tiers with priorities; price band; exclusions (every URL shown in this session, any team); feedback section (this session's good / bad picks with notes, team memo); "return exactly 5, plus near misses with the failing criterion".
- Trend references are by `theme_id` + `{upload_id, element_id}`, never free-text page numbers.

Run (`analysis/aldi_recommender.py`, Celery task `recommend_aldi_products(session_id, team)` on the `aldi` queue):

1. Build prompt, call Opus 5.5 with web search + web fetch, `max_uses` from settings (start at 15), streaming, adaptive thinking.
2. Collect every URL that appeared in a fetch result during the run. A pick whose URL was never fetched is rejected outright.
3. Verify each URL with httpx (browser UA, 10 s): 200 → verified; 403 / 429 / challenge page → unverified_blocked (kept, badged); 404 / 410 → rejected. Pull og:title, og:image, price meta; store a downsized thumbnail.
4. Enforce rules in code: price inside the band, ≤3 per retailer, ≥2 mass/value, ≥2 mid-market or premium, material family (via `analysis/material_families.py`) inside the team's scope.
5. If fewer than 5 survive, one bounded repair round asking for replacements with the failures named. Then persist what passed plus near misses.
6. Record cost in `llm_calls` and on the run.
7. If the spike shows mass/value fetches mostly blocked: add a custom `fetch_product_page` tool backed by the existing Firecrawl / proxy adapters as the fallback.

Routes:

- `POST /api/aldi/sessions/{id}/recommendations` body `{team}` → 202 with run id; 409 if that team already has a run in flight.
- `GET /api/aldi/sessions/{id}/recommendations` → both reels, all generations, feedback, near misses, verification badges, cost per run.
- `POST /api/aldi/recommendations/{id}/feedback` body `{verdict, note}`.
- `GET/PUT /api/aldi/teams/{team}/preferences` (memo, price band).
- `GET/PUT /api/aldi/preferred-retailers`.
- Existing `GET /api/aldi/uploads/{id}/file` serves the screenshot for the evidence viewer.

## Phase 4 — frontend

On the session page, under the existing trend summary:

- Team tabs (Sapana / Adrah). Each tab: big "Product Recommendations" button (becomes "Try Again" once a set exists), generation switcher, run status with elapsed time, cost chip.
- Five cards, deliberately minimal (decision 2026-11-01): the product link (name as link text, retailer in small type, thumbnail when one could be fetched, verified badge) and how it relates to the report — the theme it matches, the specific screenshot element it echoes with the box drawn on the screenshot, and one or two sentences on the shared attributes. Nothing else on the card.
- Good / Bad buttons with a note field; saved immediately; state shown on the card.
- Near misses in a collapsed list with the failing criterion.
- Team settings drawer: price band (editable now), memo (read / edit).
- Costs page (from Phase 1) in the nav.

## Phase 5 — learning and upkeep

- Distillation: when a team has 20+ new feedback items, a Haiku / Sonnet call rewrites the team memo from all feedback; the buyer can still edit it.
- Retailer rotation: each Try Again weights secondary retailers the session has not searched yet, so repeated runs widen coverage instead of circling.
- Dead-link re-check: a weekly task re-verifies URLs on Good picks and badges ones that have gone.

## Open points

- Spike outcome decides the Firecrawl fallback and the real per-run cost.
- Whether to back-run the inventory over the 28 existing sessions (recommended, cheap).
- Where the Costs page sits in the nav.
