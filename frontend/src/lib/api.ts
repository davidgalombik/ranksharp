// Server-side (Docker): use internal service name. Client-side: use public URL.
const API_BASE =
  typeof window === "undefined"
    ? process.env.API_URL || "http://api:8000"
    : process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function apiFetch<T>(path: string, params?: Record<string, string>): Promise<T> {
  const url = new URL(`${API_BASE}${path}`);
  if (params) {
    Object.entries(params).forEach(([k, v]) => v && url.searchParams.set(k, v));
  }
  const res = await fetch(url.toString(), { cache: "no-store" });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json();
}

// ── Types ──────────────────────────────────────────────────────────────────

export interface TrendExample {
  product_id: number;
  name: string;
  url: string;
  price: number | null;
  currency: string;
  primary_image_url: string | null;
  retailer_name: string;
  retailer_slug: string;
  retailer_country: string;
  colours: string[];
  materials: string[];
  style_tags: string[];
  is_hero: boolean;
  is_best_seller: boolean;
}

export interface Trend {
  id: number;
  week_start: string;
  name: string;
  description: string;
  rationale: string;
  category: string;
  status: "rising" | "plateau" | "declining" | "new";
  product_count: number;
  retailer_count: number;
  retailer_names: string[];
  avg_price: number | null;
  momentum_pct: number | null;
  dominant_colours: string[];
  dominant_materials: string[];
  dominant_patterns: string[];
  dominant_styles: string[];
  markets: string[];
  price_tier: string | null;
  examples: TrendExample[];
}

export interface TrendSummary {
  id: number;
  name: string;
  category: string;
  status: string;
  product_count: number;
  retailer_count: number;
  dominant_colours: string[];
  dominant_materials: string[];
  momentum_pct: number | null;
}

export interface Report {
  id: number;
  week_start: string;
  title: string;
  summary: string;
  total_products_analysed: number;
  retailers_covered: number;
  trend_count: number;
  rising_trends: TrendSummary[];
  new_trends: TrendSummary[];
  declining_trends: TrendSummary[];
  all_trends: TrendSummary[];
  created_at: string;
}

export interface FragranceTrendExample {
  product_id: number;
  name: string;
  url: string;
  price: number | null;
  currency: string;
  primary_image_url: string | null;
  retailer_name: string;
  retailer_slug: string;
  retailer_country: string;
  colours: string[];
  materials: string[];
  is_hero: boolean;
  is_best_seller?: boolean;
}

export interface FragranceTrend {
  id: number;
  week_start: string;
  generation: number;
  name: string;
  description: string;
  rationale: string;
  category: string;
  status: "rising" | "plateau" | "declining" | "new";
  product_count: number;
  retailer_count: number;
  retailer_names: string[];
  avg_price: number | null;
  momentum_pct: number | null;
  dominant_colours: string[];
  dominant_materials: string[];
  container_styles: string[];
  scent_families: string[];
  sustainability_signals: string[];
  markets: string[];
  price_tier: string | null;
  examples: FragranceTrendExample[];
  // New (2026-08-06): keyword list backing the "View all N products"
  // modal. Empty on legacy trends → modal falls back to stored examples.
  filter_keywords?: string[];
  // Live count of every fragrance product matching this trend's
  // keywords. Populates the button label. Null on legacy trends.
  matching_product_count?: number | null;
}

export interface FragranceTrendReport {
  id: number;
  week_start: string;
  title: string;
  summary: string;
  total_products_analysed: number;
  retailers_covered: number;
  trend_count: number;
  generation_count: number;
  trends: FragranceTrend[];
  created_at: string;
}

export interface Product {
  id: number;
  retailer_name: string;
  retailer_slug: string;
  name: string;
  url: string;
  price: number | null;
  currency: string;
  category: string | null;
  primary_image_url: string | null;
  colours: string[];
  materials: string[];
  style_tags: string[];
  patterns: string[];
  shape: string | null;
  finish: string | null;
  season: string | null;
  room: string | null;
  last_seen_at: string;
}

export interface CsvRejectRow {
  row_number: number;
  url: string | null;
  reason: string;
}

export interface CsvPreviewResult {
  total_rows: number;
  valid_rows: number;
  new_count: number;
  update_count: number;
  would_deactivate: number;
  rejects: CsvRejectRow[];
  retailers_referenced: string[];
}

export interface CsvCommitResult extends CsvPreviewResult {
  inserted: number;
  updated: number;
  deactivated: number;
  analysis_queued: number;
}

export interface Retailer {
  id: number;
  slug: string;
  name: string;
  base_url: string;
  country: string;
  market_segment?: "luxury" | "middle" | "mass" | null;
  tier: string;
  adapter_class: string;
  is_active: boolean;
  product_count: number;
  pending_analysis_count: number;
  last_scrape: string | null;
  last_scrape_status: string | null;
}

// ── API calls ──────────────────────────────────────────────────────────────

export const api = {
  trends: {
    list: (params?: { week_start?: string; category?: string; status?: string; generation?: string; market_segment?: string }) =>
      apiFetch<Trend[]>("/api/trends/", params as Record<string, string>),
    latest: () => apiFetch<Trend[]>("/api/trends/latest"),
    get: (id: number) => apiFetch<Trend>(`/api/trends/${id}`),
    // `generations` is the actual list of set numbers present that week
    // (e.g. [1, 3, 4] after Set 2 was deleted). Frontend renders tabs
    // from this list rather than assuming 1..generation_count.
    // Segment (2026-08-08): omit for legacy unsegmented weeks; pass
    // 'luxury'/'middle'/'mass' for the segmented view.
    weeks: (market_segment?: string) =>
      apiFetch<{ week: string; generation_count: number; generations?: number[] }[]>(
        "/api/trends/weeks/",
        market_segment ? { market_segment } : undefined,
      ),
    // Cross-tier diffusion — rows are trend names merged across
    // Luxury / Middle / Mass so buyers see where a trend has spread.
    compare: (week_start?: string) =>
      apiFetch<{
        week: string | null;
        rows: Array<{
          name: string;
          category: string;
          luxury: { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null } | null;
          middle: { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null } | null;
          mass:   { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null } | null;
        }>;
      }>("/api/trends/compare", week_start ? { week_start } : undefined),
    // Paginated live query of every product matching a trend — used by
    // the "View all N products" modal so the buyer can browse the full
    // set, not just the ~100 stored TrendExample rows.
    products: (id: number, params?: { limit?: number; offset?: number; only_best_sellers?: boolean }) =>
      apiFetch<{ total: number; items: TrendExample[] }>(
        `/api/trends/${id}/products`,
        {
          limit: String(params?.limit ?? 48),
          offset: String(params?.offset ?? 0),
          ...(params?.only_best_sellers ? { only_best_sellers: "true" } : {}),
        }
      ),
    // Hard-delete a single Set N for the given week + segment. Server returns
    // {deleted_trends, deleted_examples, unlinked_backlinks, remaining_generations}.
    // Omit market_segment for legacy unsegmented weeks.
    deleteGeneration: async (week: string, generation: number, market_segment?: string) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      const res = await fetch(
        `${API_BASE}/api/trends/week/${week}/generations/${generation}${qs}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<{
        week: string;
        generation: number;
        deleted_trends: number;
        deleted_examples: number;
        unlinked_backlinks: number;
        remaining_generations: number[];
      }>;
    },
  },
  reports: {
    list: () => apiFetch<Report[]>("/api/reports/"),
    latest: () => apiFetch<Report>("/api/reports/latest"),
    get: (id: number) => apiFetch<Report>(`/api/reports/${id}`),
    // market_segment: 'luxury'|'middle'|'mass'|'all' or omit for legacy unsegmented.
    generate: (market_segment?: string) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      return fetch(`${API_BASE}/api/reports/generate${qs}`, { method: "POST" }).then((r) => r.json());
    },
    regenerate: (market_segment?: string) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      return fetch(`${API_BASE}/api/reports/regenerate${qs}`, { method: "POST" }).then((r) => r.json());
    },
    clear: () =>
      fetch(`${API_BASE}/api/reports/clear`, { method: "DELETE" }).then((r) => r.json()),
    taskStatus: (taskId: string) =>
      apiFetch<{ task_id: string; state: string; pct: number; step: string }>(
        `/api/reports/task/${taskId}`
      ),
  },
  products: {
    search: (params: Record<string, string>) => apiFetch<Product[]>("/api/products/", params),
    historical: (params: Record<string, string>) => apiFetch<Product[]>("/api/products/historical", params),
  },
  instore: {
    createSession: async (files: File[], opts?: { name?: string; finalise?: boolean }) => {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      if (opts?.name) formData.append("name", opts.name);
      formData.append("finalise", String(opts?.finalise ?? false));
      const res = await fetch(`${API_BASE}/api/instore/sessions`, { method: "POST", body: formData });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    addUploads: async (sessionId: number, files: File[]) => {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      const res = await fetch(`${API_BASE}/api/instore/sessions/${sessionId}/uploads`, {
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    finaliseSession: async (sessionId: number) => {
      const res = await fetch(`${API_BASE}/api/instore/sessions/${sessionId}/finalise`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    listSessions: async () => {
      const res = await fetch(`${API_BASE}/api/instore/sessions`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    getSession: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore/sessions/${id}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    deleteSession: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore/sessions/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    getImageUrl: (sessionId: number, productId: number) =>
      `${API_BASE}/api/instore/sessions/${sessionId}/products/${productId}/image`,
  },
  instoreCatalogue: {
    upload: async (files: File[], hashes: string[], retailer?: string, country?: string) => {
      const formData = new FormData();
      files.forEach((f) => formData.append("files", f));
      hashes.forEach((h) => formData.append("hashes", h));
      if (retailer) formData.append("retailer", retailer);
      if (country) formData.append("country", country);
      const res = await fetch(`${API_BASE}/api/instore-catalogue/upload`, { method: "POST", body: formData });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{
        added: number;
        skipped_duplicate: number;
        skipped_invalid: number;
        image_ids: number[];
      }>;
    },
    listItems: async (params: { q?: string; category?: string; subcategory?: string; product_segment?: string; uncategorised_only?: boolean; country?: string; retailer?: string; show_all?: boolean; prominence?: string; month?: string; limit?: number; offset?: number } = {}) => {
      const qs = new URLSearchParams();
      if (params.q) qs.set("q", params.q);
      if (params.category) qs.set("category", params.category);
      if (params.subcategory) qs.set("subcategory", params.subcategory);
      if (params.product_segment) qs.set("product_segment", params.product_segment);
      if (params.uncategorised_only) qs.set("uncategorised_only", "true");
      if (params.country) qs.set("country", params.country);
      if (params.retailer) qs.set("retailer", params.retailer);
      if (params.show_all) qs.set("show_all", "true");
      if (params.prominence) qs.set("prominence", params.prominence);
      if (params.month) qs.set("month", params.month);
      qs.set("limit", String(params.limit ?? 60));
      qs.set("offset", String(params.offset ?? 0));
      const res = await fetch(`${API_BASE}/api/instore-catalogue/?${qs}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    // Distinct upload months for the Month filter dropdown.
    listMonths: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/months`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{ months: { month: string; count: number }[] }>;
    },
    listRetailers: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/retailers`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{ retailers: { name: string; count: number }[]; untagged_count: number }>;
    },
    listImages: async (params: {
      q?: string;
      category?: string;
      subcategory?: string;
      product_segment?: string;
      uncategorised_only?: boolean;
      country?: string;
      retailer?: string;
      prominence?: string;
      show_all?: boolean;
      status?: string;
      month?: string;
      limit?: number;
      offset?: number;
    } = {}) => {
      const qs = new URLSearchParams();
      if (params.q) qs.set("q", params.q);
      if (params.category) qs.set("category", params.category);
      if (params.subcategory) qs.set("subcategory", params.subcategory);
      if (params.product_segment) qs.set("product_segment", params.product_segment);
      if (params.uncategorised_only) qs.set("uncategorised_only", "true");
      if (params.country) qs.set("country", params.country);
      if (params.retailer) qs.set("retailer", params.retailer);
      if (params.prominence) qs.set("prominence", params.prominence);
      if (params.show_all) qs.set("show_all", "true");
      if (params.status) qs.set("status", params.status);
      if (params.month) qs.set("month", params.month);
      qs.set("limit", String(params.limit ?? 60));
      qs.set("offset", String(params.offset ?? 0));
      const res = await fetch(`${API_BASE}/api/instore-catalogue/images?${qs}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    getImageDetail: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/images/${id}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    bulkDeleteImages: async (ids: number[]) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/images/bulk-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ image_ids: ids }),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{ deleted: number; files_unlinked: number }>;
    },
    stats: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/stats`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    facets: async (params: { q?: string; category?: string; subcategory?: string; product_segment?: string; uncategorised_only?: boolean; retailer?: string; show_all?: boolean; month?: string } = {}) => {
      const qs = new URLSearchParams();
      if (params.q) qs.set("q", params.q);
      if (params.category) qs.set("category", params.category);
      if (params.subcategory) qs.set("subcategory", params.subcategory);
      if (params.product_segment) qs.set("product_segment", params.product_segment);
      if (params.uncategorised_only) qs.set("uncategorised_only", "true");
      if (params.retailer) qs.set("retailer", params.retailer);
      if (params.show_all) qs.set("show_all", "true");
      if (params.month) qs.set("month", params.month);
      const res = await fetch(`${API_BASE}/api/instore-catalogue/facets?${qs}`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{
        categories: Record<string, number>;
        subcategories: Record<string, number>;
        product_segments: Record<string, number>;
        uncategorised: number;
      }>;
    },
    taxonomy: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/taxonomy`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{
        tree: {
          category: string;
          category_slug: string;
          subcategories: {
            label: string;
            slug: string;
            product_segments: { label: string; slug: string }[];
          }[];
        }[];
      }>;
    },
    patchItem: async (id: number, body: { product_name?: string; category?: string; subcategory?: string; product_segment?: string }) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/items/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    deleteItem: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/items/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    bulkDeleteItems: async (ids: number[]) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/items/bulk-delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ item_ids: ids }),
      });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{ deleted: number }>;
    },
    deleteEverything: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/everything?confirm=YES`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      return res.json() as Promise<{ deleted_images: number; files_unlinked: number }>;
    },
    retryImage: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/images/${id}/retry`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    retryAllFailed: async () => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/retry-all-failed`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    deleteImage: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/instore-catalogue/images/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    imageUrl: (id: number) => `${API_BASE}/api/instore-catalogue/images/${id}/file`,
    itemImageUrl: (itemId: number) => `${API_BASE}/api/instore-catalogue/items/${itemId}/image`,
  },
  fragranceTrends: {
    latestReport: (generation?: number, market_segment?: string) => {
      const qs: Record<string, string> = {};
      if (generation !== undefined) qs.generation = String(generation);
      if (market_segment) qs.market_segment = market_segment;
      return apiFetch<FragranceTrendReport>("/api/fragrance-trends/latest", Object.keys(qs).length ? qs : undefined);
    },
    listReports: () => apiFetch<FragranceTrendReport[]>("/api/fragrance-trends/"),
    getTrend: (id: number) => apiFetch<FragranceTrend>(`/api/fragrance-trends/trend/${id}`),
    // Live paginated query of every fragrance product matching a trend's
    // keywords. Powers the "View all N products" modal. Legacy trends
    // (empty filter_keywords) fall back to the stored examples server-side.
    trendProducts: (
      id: number,
      params?: { limit?: number; offset?: number; only_best_sellers?: boolean },
    ) =>
      apiFetch<{
        total: number;
        items: Array<{
          id: number; name: string; url: string; price: number | null;
          currency: string; primary_image_url: string | null;
          retailer_name: string; retailer_slug: string; is_best_seller: boolean;
        }>;
      }>(
        `/api/fragrance-trends/trend/${id}/products`,
        {
          limit: String(params?.limit ?? 48),
          offset: String(params?.offset ?? 0),
          ...(params?.only_best_sellers ? { only_best_sellers: "true" } : {}),
        },
      ),
    // Fragrance is run-based since 2026-08-06. `week` is the run's ISO
    // datetime string (misnamed for backward compat). Now segment-scoped
    // (2026-08-08) — pass 'luxury'/'middle'/'mass' or omit for legacy.
    weeks: (market_segment?: string) =>
      apiFetch<{ week: string; generation_count: number; generations?: number[] }[]>(
        "/api/fragrance-trends/weeks/",
        market_segment ? { market_segment } : undefined,
      ),
    generate: (market_segment?: string) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      return fetch(`${API_BASE}/api/fragrance-trends/generate${qs}`, { method: "POST" }).then((r) => r.json());
    },
    regenerate: (market_segment?: string) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      return fetch(`${API_BASE}/api/fragrance-trends/regenerate${qs}`, { method: "POST" }).then((r) => r.json());
    },
    clear: () =>
      fetch(`${API_BASE}/api/fragrance-trends/clear`, { method: "DELETE" }).then((r) => r.json()),
    // Diffusion matrix — same-name trends aligned across Luxury / Middle
    // / Mass. Each cell is {trend_id, product_count, retailer_count,
    // momentum_pct} or null.
    compare: () =>
      apiFetch<{
        week: string | null;
        rows: Array<{
          name: string;
          category: string;
          luxury: null | { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null };
          middle: null | { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null };
          mass:   null | { trend_id: number; product_count: number; retailer_count: number; momentum_pct: number | null };
        }>;
      }>("/api/fragrance-trends/compare"),
    // Hard-delete one Set within a specific run. runId is the
    // FragranceTrendReport.id (there is exactly one report per run).
    // Segment-scoped (2026-08-22) — the same (run, generation) can
    // exist in Luxury/Middle/Mass and we want to delete only one.
    // 409 if it would leave the whole run with zero trends across
    // every segment.
    deleteGeneration: async (
      runId: number,
      generation: number,
      market_segment?: string,
    ) => {
      const qs = market_segment ? `?market_segment=${market_segment}` : "";
      const res = await fetch(
        `${API_BASE}/api/fragrance-trends/runs/${runId}/generations/${generation}${qs}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<{
        run_id: number;
        generation: number;
        deleted_trends: number;
        deleted_examples: number;
        unlinked_backlinks: number;
        remaining_generations: number[];
      }>;
    },
    taskStatus: (taskId: string) =>
      apiFetch<{ task_id: string; state: string; pct: number; step: string }>(
        `/api/fragrance-trends/task/${taskId}`
      ),
  },
  retailers: {
    list: () => apiFetch<Retailer[]>("/api/retailers/"),
    // Market segmentation (2026-08-08). Pass null to un-classify.
    setSegment: async (retailerId: number, market_segment: "luxury" | "middle" | "mass" | null) => {
      const res = await fetch(`${API_BASE}/api/retailers/${retailerId}/segment`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market_segment }),
      });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<{ id: number; market_segment: string | null }>;
    },
    scrapeAll: (skipAnalysis = false) =>
      fetch(`${API_BASE}/api/retailers/scrape-all?skip_analysis=${skipAnalysis}`, { method: "POST" }).then((r) => r.json()),
    scrape: (id: number, skipAnalysis = false) =>
      fetch(`${API_BASE}/api/retailers/${id}/scrape?skip_analysis=${skipAnalysis}`, { method: "POST" }).then((r) => r.json()),
    analyse: (id: number) =>
      fetch(`${API_BASE}/api/retailers/${id}/analyse`, { method: "POST" }).then((r) => r.json()),
    analyseAll: () =>
      fetch(`${API_BASE}/api/retailers/analyse-all`, { method: "POST" }).then((r) => r.json()),
    // skipDeactivate: merge mode — part of a multi-file upload. When true,
    // absent products are NOT moved to Historical. Preview reflects that.
    csvPreview: async (file: File, skipDeactivate = false) => {
      const fd = new FormData();
      fd.append("file", file);
      const url = `${API_BASE}/api/retailers/csv-upload/preview${skipDeactivate ? "?skip_deactivate=true" : ""}`;
      const res = await fetch(url, { method: "POST", body: fd });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<CsvPreviewResult>;
    },
    csvCommit: async (file: File, skipDeactivate = false) => {
      const fd = new FormData();
      fd.append("file", file);
      const url = `${API_BASE}/api/retailers/csv-upload/commit${skipDeactivate ? "?skip_deactivate=true" : ""}`;
      const res = await fetch(url, { method: "POST", body: fd });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<CsvCommitResult>;
    },
  },

  // ── Ranksharp Catalogue ──────────────────────────────────────────────────
  ranksharp: {
    listProducts: (params: { q?: string; category?: string; limit?: number; offset?: number } = {}) => {
      const qs: Record<string, string> = {
        limit: String(params.limit ?? 48),
        offset: String(params.offset ?? 0),
      };
      if (params.q) qs.q = params.q;
      if (params.category) qs.category = params.category;
      return apiFetch<RanksharpProductListPage>("/api/ranksharp/products", qs);
    },
    getProduct: (id: number) =>
      apiFetch<RanksharpProductDetail>(`/api/ranksharp/products/${id}`),
    listCategories: () =>
      apiFetch<{ categories: string[] }>("/api/ranksharp/categories"),
    imageUrl: (productId: number) =>
      `${API_BASE}/api/ranksharp/products/${productId}/image`,
    csvPreview: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/ranksharp/csv/preview`, {
        method: "POST", body: fd,
      });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<RanksharpCsvSummary>;
    },
    csvCommit: async (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      const res = await fetch(`${API_BASE}/api/ranksharp/csv/commit`, {
        method: "POST", body: fd,
      });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<RanksharpCsvCommitSummary>;
    },
    uploadImages: async (file: File, sku?: string) => {
      const fd = new FormData();
      fd.append("file", file);
      if (sku) fd.append("sku", sku);
      const res = await fetch(`${API_BASE}/api/ranksharp/images/upload`, {
        method: "POST", body: fd,
      });
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); } catch { detail = await res.text(); }
        throw new Error(detail);
      }
      return res.json() as Promise<RanksharpImageUploadSummary>;
    },
    deleteProduct: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/ranksharp/products/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      return res.json();
    },
    deleteSale: async (id: number) => {
      const res = await fetch(`${API_BASE}/api/ranksharp/sales/${id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`Delete failed (${res.status})`);
      return res.json();
    },
  },
};

// ── Ranksharp types ─────────────────────────────────────────────────────────

export interface RanksharpProductListItem {
  id: number;
  sku: string;
  name: string;
  description: string | null;
  category: string | null;
  subcategory: string | null;
  has_image: boolean;
  sale_count: number;
  total_units: number;
  latest_sale_date: string | null;
  latest_currency: string | null;
  latest_price_wholesale: number | null;
  latest_price_retail: number | null;
}

export interface RanksharpProductListPage {
  total: number;
  items: RanksharpProductListItem[];
}

export interface RanksharpSale {
  id: number;
  customer: string;
  price_wholesale: number | null;
  price_retail: number | null;
  currency: string | null;
  units_purchased: number | null;
  on_sale_date: string | null;
  notes: string | null;
  created_at: string;
}

export interface RanksharpProductDetail {
  id: number;
  sku: string;
  name: string;
  description: string | null;
  category: string | null;
  subcategory: string | null;
  has_image: boolean;
  created_at: string;
  updated_at: string;
  sales: RanksharpSale[];
}

export interface RanksharpCsvReject {
  row_number: number;
  sku: string | null;
  reason: string;
}

export interface RanksharpCsvSummary {
  total_rows: number;
  valid_rows: number;
  new_products: number;
  existing_products: number;
  sale_records: number;
  rejects: RanksharpCsvReject[];
}

export interface RanksharpCsvCommitSummary extends RanksharpCsvSummary {
  products_created: number;
  sales_created: number;
}

export interface RanksharpImageUploadSummary {
  uploaded: number;
  skipped_no_matching_sku: string[];
  failed: { filename: string; sku: string | null; reason: string }[];
}
