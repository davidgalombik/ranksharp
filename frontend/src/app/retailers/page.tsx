"use client";
import { useEffect, useState } from "react";
import { api, type Retailer } from "@/lib/api";
import clsx from "clsx";

const TIER_STYLES = {
  api: "bg-emerald-50 text-emerald-700 border-emerald-200",
  http: "bg-amber-50 text-amber-700 border-amber-200",
  browser: "bg-rose-50 text-rose-700 border-rose-200",
} as Record<string, string>;

const TIER_LABELS = { api: "API", http: "HTTP", browser: "Browser" };

const STATUS_DOT = {
  success: "bg-emerald-500",
  running: "bg-amber-500 animate-pulse",
  failed: "bg-rose-500",
  pending: "bg-stone-300",
} as Record<string, string>;

export default function RetailersPage() {
  const [retailers, setRetailers] = useState<Retailer[]>([]);
  const [loading, setLoading] = useState(true);
  const [scraping, setScraping] = useState<Record<number, boolean>>({});
  const [analysing, setAnalysing] = useState<Record<number, boolean>>({});
  const [skipAnalysis, setSkipAnalysis] = useState(false);

  useEffect(() => {
    api.retailers.list().then(setRetailers).finally(() => setLoading(false));
  }, []);

  async function triggerScrape(retailer: Retailer) {
    setScraping((s) => ({ ...s, [retailer.id]: true }));
    try {
      await api.retailers.scrape(retailer.id, skipAnalysis);
      alert(`Scrape queued for ${retailer.name}${skipAnalysis ? " (analysis skipped)" : ""}`);
    } catch {
      alert("Failed to queue scrape");
    } finally {
      setScraping((s) => ({ ...s, [retailer.id]: false }));
    }
  }

  async function triggerAll() {
    if (!confirm(`Trigger scrape for all active retailers?${skipAnalysis ? "\n\nℹ️ Claude analysis will be skipped." : ""}`)) return;
    await api.retailers.scrapeAll(skipAnalysis);
    alert("All scrapes queued. Check back in a few hours.");
  }

  async function triggerAnalyse(retailer: Retailer) {
    setAnalysing((s) => ({ ...s, [retailer.id]: true }));
    try {
      const res = await api.retailers.analyse(retailer.id);
      alert(`Queued analysis for ${res.products_queued} products in ${retailer.name}`);
      api.retailers.list().then(setRetailers);
    } catch {
      alert("Failed to queue analysis");
    } finally {
      setAnalysing((s) => ({ ...s, [retailer.id]: false }));
    }
  }

  async function triggerAnalyseAll() {
    const totalPending = retailers.reduce((sum, r) => sum + (r.pending_analysis_count ?? 0), 0);
    if (!confirm(`Run Claude analysis on ${totalPending} unanalysed products across all retailers?`)) return;
    const res = await api.retailers.analyseAll();
    alert(`Queued analysis for ${res.products_queued} products.`);
    api.retailers.list().then(setRetailers);
  }

  const grouped = retailers.reduce(
    (acc, r) => {
      const key = r.country;
      if (!acc[key]) acc[key] = [];
      acc[key].push(r);
      return acc;
    },
    {} as Record<string, Retailer[]>
  );

  if (loading) return <div className="text-center py-20 text-stone-400">Loading...</div>;

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-stone-900">
          Retailers ({retailers.length})
        </h1>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-sm text-stone-600 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={skipAnalysis}
              onChange={(e) => setSkipAnalysis(e.target.checked)}
              className="w-4 h-4 rounded border-stone-300 accent-stone-700"
            />
            <span>Skip AI analysis</span>
            {skipAnalysis && (
              <span className="text-xs bg-amber-100 text-amber-700 border border-amber-200 px-1.5 py-0.5 rounded font-medium">
                No Claude tokens used
              </span>
            )}
          </label>
          {retailers.some((r) => r.pending_analysis_count > 0) && (
            <button
              onClick={triggerAnalyseAll}
              className="bg-violet-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-violet-700 transition-colors"
            >
              Analyse all unanalysed ({retailers.reduce((s, r) => s + (r.pending_analysis_count ?? 0), 0)})
            </button>
          )}
          <button
            onClick={triggerAll}
            className="bg-stone-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-stone-700 transition-colors"
          >
            Scrape all now
          </button>
        </div>
      </div>

      {/* Legend */}
      <div className="flex gap-4 text-xs">
        {Object.entries(TIER_LABELS).map(([k, v]) => (
          <span key={k} className={clsx("px-2 py-1 rounded border text-xs font-medium", TIER_STYLES[k])}>
            {v}
          </span>
        ))}
      </div>

      {Object.entries(grouped)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([country, countryRetailers]) => (
          <section key={country}>
            <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-3">
              {country === "US" ? "🇺🇸 United States" : country === "AU" ? "🇦🇺 Australia" : country === "GB" ? "🇬🇧 United Kingdom" : `🌍 ${country}`}
              <span className="ml-2 font-normal">({countryRetailers.length})</span>
            </h2>
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
              {countryRetailers.map((r) => (
                <div
                  key={r.id}
                  className="bg-white rounded-xl border border-stone-200 p-4 space-y-2"
                >
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="font-medium text-stone-900 truncate">{r.name}</h3>
                      <p className="text-xs text-stone-400 truncate">{r.base_url}</p>
                    </div>
                    <span className={clsx("px-1.5 py-0.5 rounded border text-xs font-medium flex-shrink-0", TIER_STYLES[r.tier as keyof typeof TIER_STYLES])}>
                      {TIER_LABELS[r.tier as keyof typeof TIER_LABELS]}
                    </span>
                  </div>

                  <div className="flex items-center justify-between text-sm">
                    <span className="text-stone-600">
                      {r.product_count.toLocaleString()} products
                    </span>
                    {r.last_scrape_status && (
                      <span className="flex items-center gap-1.5 text-xs text-stone-500">
                        <span className={clsx("w-2 h-2 rounded-full", STATUS_DOT[r.last_scrape_status] || STATUS_DOT.pending)} />
                        {r.last_scrape_status}
                      </span>
                    )}
                  </div>

                  {r.last_scrape && (
                    <p className="text-xs text-stone-400">
                      Last scraped: {new Date(r.last_scrape).toLocaleDateString()}
                    </p>
                  )}

                  <button
                    onClick={() => triggerScrape(r)}
                    disabled={scraping[r.id] || !r.is_active}
                    className="w-full mt-1 px-3 py-1.5 text-xs font-medium border border-stone-200 rounded-lg hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    {scraping[r.id] ? "Queuing..." : "Scrape now"}
                  </button>
                  {r.pending_analysis_count > 0 && (
                    <button
                      onClick={() => triggerAnalyse(r)}
                      disabled={analysing[r.id]}
                      className="w-full px-3 py-1.5 text-xs font-medium border border-violet-200 text-violet-700 bg-violet-50 rounded-lg hover:bg-violet-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                    >
                      {analysing[r.id]
                        ? "Queuing..."
                        : `Analyse ${r.pending_analysis_count} unanalysed`}
                    </button>
                  )}
                </div>
              ))}
            </div>
          </section>
        ))}
    </div>
  );
}
