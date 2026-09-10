"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams, usePathname, useRouter } from "next/navigation";
import clsx from "clsx";
import InStoreRecommendationsModal from "@/components/InStoreRecommendationsModal";

/** Small client button — opens the paginated recommendations modal. */
function InStoreViewAllButton({ trend }: { trend: InStoreTrend }) {
  const [open, setOpen] = useState(false);
  const count = trend.total_recommendation_count ?? trend.recommendations.length;
  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="mt-2 w-full text-xs font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg px-3 py-1.5 transition-colors"
      >
        View all {count.toLocaleString()} recommended products →
      </button>
      {open && (
        <InStoreRecommendationsModal
          trendId={trend.id}
          trendName={trend.name}
          trendCategory={trend.category}
          onClose={() => setOpen(false)}
        />
      )}
    </>
  );
}

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
  retailer_slug?: string | null;
  url: string;
  price: number | null;
  currency: string;
  primary_image_url: string | null;
  similarity: number;
  is_best_seller?: boolean;
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
  // Total stored recommendation count (post image gate). Powers the
  // "View all N recommended products" button label. 0 hides the button.
  total_recommendation_count?: number;
}

const CURRENCIES: Record<string, string> = { USD: "$", AUD: "A$", GBP: "£", EUR: "€" };

// Human label for a calendar-month horizon. Matches the semantics in
// backend _month_range(): 1 = previous month only; N>=2 = trailing N
// calendar months including the current one.
// Compact horizon label for a Set tab, e.g. "This month", "Last 3 mo",
// "All time". Keeps tabs from wrapping. NULL / undefined → "All time".
function shortWindow(monthsWindow: number | null | undefined): string {
  if (monthsWindow == null) return "All time";
  if (monthsWindow === 0) return "This month";
  if (monthsWindow === 1) return "Last month";
  return `Last ${monthsWindow} mo`;
}

function describeWindow(monthsWindow: number | null | undefined): string {
  if (monthsWindow == null) return "all-time shelf photos";
  const now = new Date();
  const fmt = (d: Date) =>
    d.toLocaleString(undefined, { month: "short", year: "numeric" });
  if (monthsWindow === 0) {
    return `this month's shelf photos so far (${fmt(now)})`;
  }
  if (monthsWindow === 1) {
    const prev = new Date(now.getFullYear(), now.getMonth() - 1, 1);
    return `last month's shelf photos (${fmt(prev)})`;
  }
  const start = new Date(now.getFullYear(), now.getMonth() - (monthsWindow - 1), 1);
  return `the last ${monthsWindow} months of shelf photos (${fmt(start)} – ${fmt(now)})`;
}

interface InStoreReport {
  id: number;
  week_start: string;
  title: string;
  summary: string;
  total_items_analysed: number;
  trend_count: number;
  // Time horizon used by the latest run. null = all time.
  months_window: number | null;
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

interface InStoreSet {
  generation: number;
  months_window: number | null;
  trend_count: number;
  item_count: number;
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
              Matching online products · {trend.total_recommendation_count ?? trend.recommendations.length}
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
                      {r.is_best_seller && (
                        <span className="absolute top-0.5 left-0.5 px-1 py-0 rounded-full text-[9px] font-semibold bg-amber-100 text-amber-800 border border-amber-200">
                          ★
                        </span>
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
            {/* View all N recommended products — only when more exist
                than what fits in the 6-tile preview. */}
            {(trend.total_recommendation_count ?? 0) > 6 && (
              <InStoreViewAllButton trend={trend} />
            )}
          </div>
        )}
      </div>
    </article>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

function InStoreTrendsPageInner() {
  const searchParams = useSearchParams();
  const pathname = usePathname();
  const router = useRouter();

  const [report, setReport] = useState<InStoreReport | null>(null);
  const [reports, setReports] = useState<InStoreReport[]>([]);
  const [sets, setSets] = useState<InStoreSet[]>([]);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState<TaskStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("");
  const [categoryFilter, setCategoryFilter] = useState<string>("");

  // Time horizon lives in the URL (?window=3) so refresh keeps it and
  // deep links work. Calendar semantics (see engine _month_range):
  //   0    = current month only ("This month")
  //   1    = previous full calendar month only ("Last month")
  //   3+   = trailing N calendar months including current
  //   "all"= no filter (map from null)
  // Default 3 on first visit so a fresh run reflects current shelves.
  const rawWindow = searchParams.get("window");
  const monthsWindow: number | null =
    rawWindow === "all" ? null
    : rawWindow != null && /^\d+$/.test(rawWindow) ? parseInt(rawWindow, 10)
    : 3;
  const setMonthsWindow = (v: number | null) => {
    const p = new URLSearchParams(searchParams.toString());
    p.set("window", v === null ? "all" : String(v));
    router.replace(`${pathname}?${p.toString()}`, { scroll: false });
  };

  // Which Set to display. Also URL-driven so refreshes stick and
  // deep links work. Undefined = auto-resolve to the latest Set that
  // matches the current pill horizon.
  const rawSet = searchParams.get("set");
  const activeSet: number | undefined =
    rawSet != null && /^\d+$/.test(rawSet) ? parseInt(rawSet, 10) : undefined;
  const switchSet = (gen: number) => {
    const p = new URLSearchParams(searchParams.toString());
    p.set("set", String(gen));
    router.push(`${pathname}?${p.toString()}`);
  };

  // Sets whose analysed-horizon matches the current pill. The pill
  // now filters the view: pick "Last month" and you see only Sets that
  // were actually run with Last month as the horizon.
  const matchingSets = sets.filter((s) => s.months_window === monthsWindow);
  // The Set we should fetch. If the URL points to a matching Set, honor
  // it (deep link). Otherwise, latest matching Set. If none match, undefined
  // → empty state ("no analysis for this horizon yet").
  const effectiveGen: number | undefined = (() => {
    if (activeSet != null && matchingSets.some((s) => s.generation === activeSet)) {
      return activeSet;
    }
    return matchingSets.length ? matchingSets[matchingSets.length - 1].generation : undefined;
  })();

  const loadMeta = useCallback(async () => {
    // Sets list + report list — needed to decide what to fetch below.
    // Kept separate from the specific-Set fetch so a pill click that
    // changes the horizon doesn't force re-downloading /sets.
    try {
      const [listRes, setsRes] = await Promise.all([
        fetch(`${API_BASE}/api/instore-trends/?limit=20`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/instore-trends/sets`, { cache: "no-store" }),
      ]);
      if (listRes.ok) setReports(await listRes.json());
      if (setsRes.ok) setSets(await setsRes.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  const loadReport = useCallback(async (gen: number | undefined) => {
    setLoading(true);
    setError(null);
    try {
      if (gen == null) {
        // Nothing to show — no Set at this horizon.
        setReport(null);
        return;
      }
      const res = await fetch(
        `${API_BASE}/api/instore-trends/latest?generation=${gen}`,
        { cache: "no-store" },
      );
      if (res.status === 404) setReport(null);
      else if (res.ok) setReport(await res.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  // Convenience refresh after a run / delete completes.
  const loadLatest = useCallback(async () => {
    await loadMeta();
    // loadReport will pick up automatically via the effect below once
    // sets updates, so no direct call here.
  }, [loadMeta]);

  useEffect(() => { loadMeta(); }, [loadMeta]);
  useEffect(() => { loadReport(effectiveGen); }, [loadReport, effectiveGen]);

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
      // NOTE: check for null explicitly — 0 is a valid value ("This
      // month") but falsy in JS, so a plain `monthsWindow ? …` would
      // drop the param and back-end would default to all-time.
      const qs = monthsWindow !== null ? `?months_window=${monthsWindow}` : "";
      const res = await fetch(`${API_BASE}/api/instore-trends/${endpoint}${qs}`, { method: "POST" });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setRunning({ task_id: data.task_id, state: "PENDING", pct: 2, step: "Queued…" });
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const deleteSet = async (generation: number) => {
    if (!report) return;
    // The 409 guard on the backend refuses when the delete would empty
    // the WHOLE report across horizons, so it's fine to delete a lone
    // matching Set as long as another Set exists elsewhere. Keep this
    // client-side alert only for the truly-last-Set case.
    if (sets.length <= 1) {
      alert("Can't delete the only remaining Set — run a new analysis first, or clear all.");
      return;
    }
    if (!confirm(`Delete Set ${generation}? This removes every trend + recommendation in that set. Can't be undone.`)) return;
    try {
      const res = await fetch(
        `${API_BASE}/api/instore-trends/reports/${report.id}/generations/${generation}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        let detail: string;
        try { const j = await res.json(); detail = j.detail || JSON.stringify(j); }
        catch { detail = await res.text(); }
        throw new Error(detail);
      }
      const data = await res.json() as { remaining_generations: number[] };
      // If we deleted the active set, jump to the highest remaining one.
      if (activeSet === generation) {
        const next = data.remaining_generations[data.remaining_generations.length - 1];
        if (next != null) {
          const p = new URLSearchParams(searchParams.toString());
          p.set("set", String(next));
          router.push(`${pathname}?${p.toString()}`);
          return; // loadLatest will fire from the activeSet change
        }
      }
      await loadLatest();
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
            {report
              ? <>Analysed <span className="font-medium text-stone-700">{describeWindow(report.months_window)}</span> · {report.total_items_analysed.toLocaleString()} items</>
              : <>Trends synthesised across the In-store Products catalogue</>}
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

      {/* Time-window pill selector — sets the horizon the NEXT Run /
          Try Again will analyse. Doesn't refetch on its own; buyer
          picks then hits the button. Default is Last 3 months so a
          fresh run reflects current shelves rather than every photo
          ever uploaded. Hidden while an analysis is running. */}
      {!running && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-stone-400 font-medium uppercase tracking-wider">
            Analyse:
          </span>
          {([
            // Calendar semantics — see _month_range() in the engine.
            { label: "This month",    value: 0,    title: "Current calendar month only (whatever's been uploaded this month so far)" },
            { label: "Last month",    value: 1,    title: "The previous complete calendar month (e.g. August when today is in September)" },
            { label: "Last 3 months", value: 3,    title: "Trailing 3 calendar months including this one" },
            { label: "Last 6 months", value: 6,    title: "Trailing 6 calendar months including this one" },
            { label: "All time",      value: null, title: "Every shelf photo ever uploaded" },
          ] as { label: string; value: number | null; title: string }[]).map((opt) => {
            const active = monthsWindow === opt.value;
            return (
              <button
                key={String(opt.value)}
                onClick={() => setMonthsWindow(opt.value)}
                title={opt.title}
                className={clsx(
                  "px-3 py-1 rounded-full text-xs font-semibold border transition-colors",
                  active
                    ? "border-stone-900 bg-stone-900 text-white"
                    : "border-stone-200 bg-white text-stone-600 hover:border-stone-400"
                )}
              >
                {opt.label}
              </button>
            );
          })}
          <span className="text-xs text-stone-400 ml-1">
            (applies to the next Run / Try again)
          </span>
        </div>
      )}

      {/* Set tabs — one per generation for the latest report, labelled
          with the horizon that Set was analysed with. ✨ marks the
          highest generation number; × deletes with confirm. Hidden
          when there's only one Set (nothing to switch between). */}
      {!running && matchingSets.length > 1 && report && (() => {
        const latestGen = matchingSets[matchingSets.length - 1].generation;
        const currentGen = effectiveGen ?? latestGen;
        return (
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-xs text-stone-400 font-medium uppercase tracking-wider">
              Sets ({shortWindow(monthsWindow)}):
            </span>
            {matchingSets.map((s) => {
              const active = s.generation === currentGen;
              const isLatest = s.generation === latestGen;
              return (
                <div
                  key={s.generation}
                  className={clsx(
                    "inline-flex items-stretch rounded-lg overflow-hidden border transition-colors",
                    active
                      ? "border-stone-900 bg-stone-900 text-white"
                      : "border-stone-200 bg-white text-stone-600 hover:border-stone-400"
                  )}
                  title={`${s.trend_count} trend${s.trend_count === 1 ? "" : "s"} · ${s.item_count.toLocaleString()} items analysed`}
                >
                  <button
                    type="button"
                    onClick={() => switchSet(s.generation)}
                    className="px-3 py-1 text-xs font-medium"
                  >
                    Set {s.generation}
                    <span className={clsx(
                      "ml-1.5 text-[10px] font-normal",
                      active ? "text-stone-300" : "text-stone-400",
                    )}>
                      · {shortWindow(s.months_window)}
                    </span>
                    {isLatest && " ✨"}
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteSet(s.generation)}
                    title={`Delete Set ${s.generation}`}
                    aria-label={`Delete Set ${s.generation}`}
                    className={clsx(
                      "px-1.5 text-xs border-l transition-colors",
                      active
                        ? "border-stone-700 hover:bg-stone-800"
                        : "border-stone-200 text-stone-400 hover:bg-red-50 hover:text-red-600",
                    )}
                  >×</button>
                </div>
              );
            })}
          </div>
        );
      })()}

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

      {/* Report summary — surfaces WHEN the report was generated and
          WHAT time period it analysed, since those are the two facts
          buyers most often want to verify at a glance. The report row's
          `week_start` is a bucket key (the Monday of the run's week)
          and misleading as a date, so we use `created_at` instead. */}
      {report && (
        <div className="bg-white border border-stone-200 rounded-xl p-5 space-y-2">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs">
            <span className="text-stone-500">
              <span className="text-stone-400">Date report run: </span>
              <span className="font-medium text-stone-900">
                {new Date(report.created_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })}
              </span>
            </span>
            <span className="text-stone-500">
              <span className="text-stone-400">Time period: </span>
              <span className="font-medium text-stone-900">
                {shortWindow(report.months_window)}
              </span>
            </span>
            <span className="text-stone-400">
              {report.total_items_analysed.toLocaleString()} items · {report.trend_count} trends
            </span>
          </div>
          <h2 className="text-lg font-semibold text-stone-900">{report.title}</h2>
          <p className="text-sm text-stone-600">{report.summary}</p>
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
        // Two distinct empty states: no runs at ALL vs no runs at the
        // currently-selected horizon. The second is the more common one
        // once the buyer starts flipping pills.
        sets.length === 0 ? (
          <div className="text-center py-20 text-stone-400 bg-white border border-stone-200 rounded-xl">
            <p className="text-4xl mb-3">⌂</p>
            <p className="font-medium text-stone-700">No in-store trend reports yet</p>
            <p className="text-sm mt-1">Click <em>Run analysis</em> above to generate the first one.</p>
            <p className="text-xs mt-2">
              Requires items in the In-store Products catalogue with embeddings.
              New uploads get embeddings automatically; older items need a one-time backfill (admin).
            </p>
          </div>
        ) : (
          <div className="text-center py-20 text-stone-400 bg-white border border-stone-200 rounded-xl">
            <p className="text-4xl mb-3">📅</p>
            <p className="font-medium text-stone-700">
              No analysis for <span className="text-stone-900">{shortWindow(monthsWindow)}</span> yet
            </p>
            <p className="text-sm mt-1">
              Click <em>Run new analysis</em> above to analyse{" "}
              {describeWindow(monthsWindow)}.
            </p>
            <p className="text-xs mt-3 text-stone-400">
              Runs at other horizons stay available — switch pills to view them.
            </p>
          </div>
        )
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

// useSearchParams() requires a Suspense boundary during static
// prerender, otherwise the Vercel build fails
// ("useSearchParams() should be wrapped in a suspense boundary").
export default function InStoreTrendsPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-stone-400">Loading in-store trends…</div>}>
      <InStoreTrendsPageInner />
    </Suspense>
  );
}
