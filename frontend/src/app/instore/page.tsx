"use client";

import { useCallback, useEffect, useState } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ────────────────────────────────────────────────────────────────────

interface InStoreTrendExample {
  id: number;
  product_name: string;
  category: string | null;
  subcategory: string | null;
  product_segment: string | null;
  image_id: number;
  has_crop: boolean;
  retailer: string | null;
}

interface InStoreTrendRecommendation {
  product_id: number;
  name: string;
  retailer_name: string | null;
  url: string;
  price: number | null;
  currency: string;
  primary_image_url: string | null;
  similarity: number;
}

interface InStoreTrend {
  id: number;
  name: string;
  description: string;
  rationale: string;
  category: string;
  status: string;
  item_count: number;
  momentum_pct: number | null;
  dominant_colours: string[];
  dominant_materials: string[];
  dominant_patterns: string[];
  dominant_styles: string[];
  dominant_taxonomy: string[];
  examples: InStoreTrendExample[];
  recommendations: InStoreTrendRecommendation[];
}

const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };

interface InStoreReport {
  id: number;
  week_start: string;
  title: string;
  summary: string;
  total_items_analysed: number;
  trend_count: number;
  rising_trends: InStoreTrend[];
  new_trends: InStoreTrend[];
  declining_trends: InStoreTrend[];
  all_trends: InStoreTrend[];
  created_at: string;
}

interface TaskStatus {
  task_id: string;
  state: "PENDING" | "STARTED" | "PROGRESS" | "SUCCESS" | "FAILURE";
  pct: number;
  step: string;
}

// ── Styles ────────────────────────────────────────────────────────────────────

const STATUS_STYLES: Record<string, string> = {
  rising: "bg-emerald-100 text-emerald-800",
  new: "bg-sky-100 text-sky-800",
  plateau: "bg-stone-100 text-stone-600",
  declining: "bg-rose-100 text-rose-800",
};

const STATUS_ICONS: Record<string, string> = {
  rising: "↑",
  new: "✦",
  plateau: "→",
  declining: "↓",
};

// ── Trend card ────────────────────────────────────────────────────────────────

function TrendCard({ trend }: { trend: InStoreTrend }) {
  const examples = trend.examples.slice(0, 4);
  return (
    <article className="bg-white rounded-xl border border-stone-200 overflow-hidden hover:shadow-md transition-shadow flex flex-col">
      {/* Example image strip — uses cropped item thumbnails when available */}
      <div className="relative grid grid-cols-2 gap-px bg-stone-100 aspect-[3/2]">
        {examples.length > 0 ? (
          examples.map((ex, i) => (
            <div key={`${ex.id}-${i}`} className="relative bg-stone-50 overflow-hidden">
              {ex.has_crop ? (
                <img
                  src={`${API_BASE}/api/instore-catalogue/items/${ex.id}/image`}
                  alt={ex.product_name}
                  className="w-full h-full object-cover"
                />
              ) : (
                <div className="w-full h-full flex items-center justify-center text-stone-300 text-2xl">⌂</div>
              )}
            </div>
          ))
        ) : (
          <div className="col-span-2 row-span-2 flex items-center justify-center text-stone-300 text-2xl">⌂</div>
        )}
        {examples.length > 0 && examples.length < 4 &&
          Array.from({ length: 4 - examples.length }).map((_, i) => (
            <div key={`pad-${i}`} className="bg-stone-50" />
          ))}

        <span className={clsx(
          "absolute top-2 right-2 px-2 py-0.5 rounded-full text-xs font-semibold shadow-sm",
          STATUS_STYLES[trend.status] || "bg-stone-100 text-stone-600"
        )}>
          {STATUS_ICONS[trend.status] || ""} {trend.status.charAt(0).toUpperCase() + trend.status.slice(1)}
          {trend.momentum_pct != null && ` ${trend.momentum_pct > 0 ? "+" : ""}${trend.momentum_pct.toFixed(0)}%`}
        </span>
      </div>

      <div className="p-4 space-y-3 flex flex-col flex-1">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-medium text-stone-400 uppercase tracking-wider">
            {trend.category}
          </span>
          <span className="text-xs text-stone-400">· {trend.item_count} items</span>
        </div>

        <div>
          <h3 className="text-base font-semibold text-stone-900 leading-snug">{trend.name}</h3>
          <p className="text-sm text-stone-600 mt-1 line-clamp-2">{trend.description}</p>
        </div>

        {trend.dominant_colours.length > 0 && (
          <div className="flex flex-wrap gap-x-2.5 gap-y-1">
            {trend.dominant_colours.slice(0, 5).map((c) => (
              <span key={c} className="flex items-center gap-1 text-xs text-stone-600">
                <span
                  className="inline-block w-3 h-3 rounded-full border border-stone-200 flex-shrink-0"
                  style={{ backgroundColor: c }}
                  title={c}
                />
                {c}
              </span>
            ))}
          </div>
        )}

        {trend.dominant_materials.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {trend.dominant_materials.slice(0, 5).map((m) => (
              <span key={m} className="text-xs bg-stone-50 text-stone-600 px-1.5 py-0.5 rounded">
                {m}
              </span>
            ))}
          </div>
        )}

        {trend.dominant_taxonomy.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {trend.dominant_taxonomy.slice(0, 3).map((t) => (
              <span key={t} className="text-[10px] text-stone-500 bg-stone-50 px-1.5 py-0.5 rounded">
                {t}
              </span>
            ))}
          </div>
        )}

        {/* Matching online products */}
        {trend.recommendations && trend.recommendations.length > 0 && (
          <div className="mt-auto pt-3 border-t border-stone-100">
            <p className="text-[11px] font-medium text-stone-500 uppercase tracking-wider mb-2">
              Matching online products · {trend.recommendations.length}
            </p>
            <div className="grid grid-cols-3 gap-1.5">
              {trend.recommendations.slice(0, 6).map((r) => {
                const symbol = CURRENCIES[r.currency] || r.currency;
                return (
                  <a
                    key={r.product_id}
                    href={r.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="group block rounded-md overflow-hidden border border-stone-100 hover:border-stone-300 hover:shadow-sm transition-all bg-white"
                    title={`${r.name}${r.retailer_name ? " · " + r.retailer_name : ""} · ${(r.similarity * 100).toFixed(0)}% match`}
                  >
                    <div className="aspect-square bg-stone-50 overflow-hidden relative">
                      {r.primary_image_url ? (
                        <img
                          src={r.primary_image_url}
                          alt={r.name}
                          loading="lazy"
                          className="w-full h-full object-cover group-hover:scale-105 transition-transform"
                        />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-stone-300 text-xl">⌂</div>
                      )}
                      <span className="absolute bottom-0.5 right-0.5 text-[9px] font-semibold bg-white/85 text-stone-700 px-1 rounded">
                        {(r.similarity * 100).toFixed(0)}%
                      </span>
                    </div>
                    <div className="px-1.5 py-1">
                      <p className="text-[10px] text-stone-400 truncate">{r.retailer_name || "—"}</p>
                      <p className="text-[10px] text-stone-700 truncate leading-tight">{r.name}</p>
                      {r.price != null && (
                        <p className="text-[10px] font-semibold text-stone-800">
                          {symbol}{r.price.toFixed(2)}
                        </p>
                      )}
                    </div>
                  </a>
                );
              })}
            </div>
          </div>
        )}
      </div>
    </article>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function InStoreTrendsPage() {
  const [report, setReport] = useState<InStoreReport | null>(null);
  const [reports, setReports] = useState<InStoreReport[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<TaskStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");

  const loadLatest = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/instore-trends/latest`, { cache: "no-store" });
      if (res.status === 404) {
        setReport(null);
      } else if (res.ok) {
        setReport(await res.json());
      }
      const listRes = await fetch(`${API_BASE}/api/instore-trends/?limit=20`, { cache: "no-store" });
      if (listRes.ok) setReports(await listRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadLatest(); }, [loadLatest]);

  // Poll the running task
  useEffect(() => {
    if (!running || running.state === "SUCCESS" || running.state === "FAILURE") return;
    const t = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/instore-trends/task/${running.task_id}`);
        if (res.ok) {
          const next: TaskStatus = await res.json();
          setRunning(next);
          if (next.state === "SUCCESS") {
            setTimeout(() => { setRunning(null); loadLatest(); }, 800);
          } else if (next.state === "FAILURE") {
            setTimeout(() => setRunning(null), 2500);
          }
        }
      } catch { /* keep polling */ }
    }, 1500);
    return () => clearInterval(t);
  }, [running, loadLatest]);

  const runAnalysis = async (regenerate: boolean) => {
    setError(null);
    try {
      const endpoint = regenerate ? "regenerate" : "generate";
      const res = await fetch(`${API_BASE}/api/instore-trends/${endpoint}`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setRunning({ task_id: data.task_id, state: "PENDING", pct: 2, step: "Queued…" });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const clearAll = async () => {
    if (!confirm("Delete every in-store trend report and trend? This can't be undone.")) return;
    try {
      await fetch(`${API_BASE}/api/instore-trends/clear`, { method: "DELETE" });
      await loadLatest();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const trends = (report?.all_trends ?? []).filter((t) => {
    if (statusFilter && t.status !== statusFilter) return false;
    if (categoryFilter && t.category !== categoryFilter) return false;
    return true;
  });

  const categories = Array.from(new Set((report?.all_trends ?? []).map((t) => t.category)));

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">In-store Trends</h1>
          <p className="text-sm text-stone-500 mt-0.5">
            Trends synthesised across the In-store Products catalogue
          </p>
        </div>
        <div className="flex items-center gap-2">
          {report && !running && (
            <button
              onClick={() => runAnalysis(true)}
              className="px-3 py-1.5 rounded-lg border border-stone-200 text-sm font-medium hover:border-stone-400"
              title="Generate a fresh set of trends without deleting the previous one"
            >
              ↻ Try again
            </button>
          )}
          {!running && (
            <button
              onClick={() => runAnalysis(false)}
              className="px-3 py-1.5 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-700"
            >
              {report ? "Run new analysis" : "Run analysis"}
            </button>
          )}
          {reports.length > 0 && !running && (
            <button
              onClick={clearAll}
              className="px-3 py-1.5 rounded-lg border border-red-200 text-red-700 text-sm font-medium hover:border-red-400"
            >
              Clear all
            </button>
          )}
        </div>
      </div>

      {/* Progress */}
      {running && (
        <div className="bg-white border border-stone-200 rounded-xl p-4 space-y-2">
          <div className="flex items-center justify-between text-sm">
            <span className="text-stone-700 font-medium">
              {running.state === "FAILURE" ? "Failed" : "Analysing…"}
            </span>
            <span className="text-stone-500">{running.pct}%</span>
          </div>
          <div className="w-full bg-stone-100 rounded-full h-1.5 overflow-hidden">
            <div
              className={clsx(
                "h-full transition-all duration-500",
                running.state === "FAILURE" ? "bg-rose-500" : "bg-stone-900"
              )}
              style={{ width: `${running.pct}%` }}
            />
          </div>
          <p className="text-xs text-stone-500">{running.step}</p>
        </div>
      )}

      {error && (
        <div className="bg-rose-50 border border-rose-200 rounded-lg p-3 text-sm text-rose-700">
          {error}
        </div>
      )}

      {/* Filters */}
      {report && trends.length > 0 && (
        <div className="flex items-end gap-3 flex-wrap">
          <div>
            <label className="block text-xs font-medium text-stone-500 mb-1">Category</label>
            <select
              value={categoryFilter}
              onChange={(e) => setCategoryFilter(e.target.value)}
              className="border border-stone-200 rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none"
            >
              <option value="">All categories</option>
              {categories.map((c) => (
                <option key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</option>
              ))}
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-stone-500 mb-1">Status</label>
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="border border-stone-200 rounded-lg px-3 py-1.5 text-sm bg-white focus:outline-none"
            >
              <option value="">All statuses</option>
              <option value="rising">Rising</option>
              <option value="new">New</option>
              <option value="plateau">Plateau</option>
              <option value="declining">Declining</option>
            </select>
          </div>
        </div>
      )}

      {/* Report summary */}
      {report && (
        <div className="bg-white border border-stone-200 rounded-xl p-5">
          <p className="text-xs text-stone-400 mb-1">
            Report · {new Date(report.week_start).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}
            {" · "}{report.total_items_analysed.toLocaleString()} items analysed · {report.trend_count} trends
          </p>
          <h2 className="text-lg font-semibold text-stone-900">{report.title}</h2>
          <p className="text-sm text-stone-600 mt-1">{report.summary}</p>
        </div>
      )}

      {/* Trends */}
      {loading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="bg-white border border-stone-200 rounded-xl overflow-hidden animate-pulse">
              <div className="aspect-[3/2] bg-stone-100" />
              <div className="p-4 space-y-2">
                <div className="h-3 bg-stone-100 rounded w-1/3" />
                <div className="h-4 bg-stone-100 rounded w-2/3" />
                <div className="h-3 bg-stone-100 rounded w-full" />
              </div>
            </div>
          ))}
        </div>
      ) : !report ? (
        <div className="text-center py-20 text-stone-400 bg-white border border-stone-200 rounded-xl">
          <p className="text-4xl mb-3">⌂</p>
          <p className="font-medium text-stone-700">No in-store trend reports yet</p>
          <p className="text-sm mt-1">Click <em>Run analysis</em> above to generate the first one.</p>
          <p className="text-xs mt-2">
            Requires items in the In-store Products catalogue with embeddings.
            New uploads get embeddings automatically; older items need a one-time backfill (admin).
          </p>
        </div>
      ) : trends.length === 0 ? (
        <div className="text-center py-20 text-stone-400">
          <p>No trends match your filters.</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {trends.map((t) => (
            <TrendCard key={t.id} trend={t} />
          ))}
        </div>
      )}

      {/* Previous reports */}
      {reports.length > 1 && (
        <div className="pt-6 border-t border-stone-200">
          <h3 className="text-sm font-semibold text-stone-700 mb-3">Previous reports</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
            {reports.slice(1).map((r) => (
              <div key={r.id} className="bg-white border border-stone-200 rounded-lg p-3 hover:shadow-sm transition-shadow">
                <p className="text-xs text-stone-400">
                  {new Date(r.week_start).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}
                  {" · "}{r.trend_count} trends
                </p>
                <p className="text-sm font-medium text-stone-900 mt-0.5 line-clamp-2">{r.title}</p>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
