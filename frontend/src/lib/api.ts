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

export interface Retailer {
  id: number;
  slug: string;
  name: string;
  base_url: string;
  country: string;
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
    list: (params?: { week_start?: string; category?: string; status?: string; generation?: string }) =>
      apiFetch<Trend[]>("/api/trends/", params as Record<string, string>),
    latest: () => apiFetch<Trend[]>("/api/trends/latest"),
    get: (id: number) => apiFetch<Trend>(`/api/trends/${id}`),
    weeks: () => apiFetch<{ week: string; generation_count: number }[]>("/api/trends/weeks/"),
  },
  reports: {
    list: () => apiFetch<Report[]>("/api/reports/"),
    latest: () => apiFetch<Report>("/api/reports/latest"),
    get: (id: number) => apiFetch<Report>(`/api/reports/${id}`),
    generate: () =>
      fetch(`${API_BASE}/api/reports/generate`, { method: "POST" }).then((r) => r.json()),
    regenerate: () =>
      fetch(`${API_BASE}/api/reports/regenerate`, { method: "POST" }).then((r) => r.json()),
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
  fragranceTrends: {
    latestReport: (generation?: number) =>
      apiFetch<FragranceTrendReport>("/api/fragrance-trends/latest", generation ? { generation: String(generation) } : undefined),
    listReports: () => apiFetch<FragranceTrendReport[]>("/api/fragrance-trends/"),
    getTrend: (id: number) => apiFetch<FragranceTrend>(`/api/fragrance-trends/trend/${id}`),
    weeks: () => apiFetch<{ week: string; generation_count: number }[]>("/api/fragrance-trends/weeks/"),
    generate: () =>
      fetch(`${API_BASE}/api/fragrance-trends/generate`, { method: "POST" }).then((r) => r.json()),
    regenerate: () =>
      fetch(`${API_BASE}/api/fragrance-trends/regenerate`, { method: "POST" }).then((r) => r.json()),
    clear: () =>
      fetch(`${API_BASE}/api/fragrance-trends/clear`, { method: "DELETE" }).then((r) => r.json()),
    taskStatus: (taskId: string) =>
      apiFetch<{ task_id: string; state: string; pct: number; step: string }>(
        `/api/fragrance-trends/task/${taskId}`
      ),
  },
  retailers: {
    list: () => apiFetch<Retailer[]>("/api/retailers/"),
    scrapeAll: (skipAnalysis = false) =>
      fetch(`${API_BASE}/api/retailers/scrape-all?skip_analysis=${skipAnalysis}`, { method: "POST" }).then((r) => r.json()),
    scrape: (id: number, skipAnalysis = false) =>
      fetch(`${API_BASE}/api/retailers/${id}/scrape?skip_analysis=${skipAnalysis}`, { method: "POST" }).then((r) => r.json()),
    analyse: (id: number) =>
      fetch(`${API_BASE}/api/retailers/${id}/analyse`, { method: "POST" }).then((r) => r.json()),
    analyseAll: () =>
      fetch(`${API_BASE}/api/retailers/analyse-all`, { method: "POST" }).then((r) => r.json()),
  },
};
