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
  is_active: boolean;
  product_count: number;
  pending_analysis_count: number;
  last_scrape: string | null;
  last_scrape_status: string | null;
}

// ── API calls ──────────────────────────────────────────────────────────────

export const api = {
  trends: {
    list: (params?: { week_start?: string; category?: string; status?: string }) =>
      apiFetch<Trend[]>("/api/trends/", params as Record<string, string>),
    latest: () => apiFetch<Trend[]>("/api/trends/latest"),
    get: (id: number) => apiFetch<Trend>(`/api/trends/${id}`),
    weeks: () => apiFetch<string[]>("/api/trends/weeks/"),
  },
  reports: {
    list: () => apiFetch<Report[]>("/api/reports/"),
    latest: () => apiFetch<Report>("/api/reports/latest"),
    get: (id: number) => apiFetch<Report>(`/api/reports/${id}`),
    generate: () =>
      fetch(`${API_BASE}/api/reports/generate`, { method: "POST" }).then((r) => r.json()),
  },
  products: {
    search: (params: Record<string, string>) => apiFetch<Product[]>("/api/products/", params),
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
