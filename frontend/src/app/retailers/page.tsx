"use client";
import { useEffect, useState } from "react";
import { api, type Retailer } from "@/lib/api";
import clsx from "clsx";
import ScrapeProgressPanel from "@/components/ScrapeProgressPanel";

function techLabel(adapterClass: string, tier: string): string {
  const a = (adapterClass || "").toLowerCase();
  if (a.includes("apify")) return "Apify";
  if (a.includes("firecrawl")) return "Firecrawl";
  if (a.includes("smartproxy")) return "SmartProxy";
  if (tier === "api") return "API";
  if (tier === "http") return "HTTP";
  return "Browser";
}

const TECH_COLOUR: Record<string, string> = {
  Apify:      "text-violet-700 bg-violet-50 border-violet-200",
  Firecrawl:  "text-orange-700 bg-orange-50 border-orange-200",
  SmartProxy: "text-sky-700 bg-sky-50 border-sky-200",
  API:        "text-emerald-700 bg-emerald-50 border-emerald-200",
  HTTP:       "text-amber-700 bg-amber-50 border-amber-200",
  Browser:    "text-rose-700 bg-rose-50 border-rose-200",
};

const STATUS_DOT = {
  success: "bg-emerald-500",
  running: "bg-amber-500 animate-pulse",
  failed:  "bg-rose-500",
  pending: "bg-stone-300",
} as Record<string, string>;

const COUNTRY_ORDER = ["US", "AU", "GB"];
const COUNTRY_LABEL: Record<string, string> = {
  US: "🇺🇸 United States",
  AU: "🇦🇺 Australia",
  GB: "🇬🇧 United Kingdom",
};

type Tab = "retailers" | "progress";

export default function RetailersPage() {
  const [tab, setTab] = useState<Tab>("retailers");
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

  const grouped = retailers.reduce((acc, r) => {
    if (!acc[r.country]) acc[r.country] = [];
    acc[r.country].push(r);
    return acc;
  }, {} as Record<string, Retailer[]>);

  const sortedCountries = [
    ...COUNTRY_ORDER.filter((c) => grouped[c]),
    ...Object.keys(grouped).filter((c) => !COUNTRY_ORDER.includes(c)).sort(),
  ];

  if (loading) return <div className="text-center py-20 text-stone-400">Loading...</div>;

  return (
    <div className="space-y-6">
      {/* Header + tabs */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-stone-900">Retailers</h1>
          <div className="flex rounded-lg border border-stone-200 overflow-hidden text-sm font-medium">
            {(["retailers", "progress"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={clsx(
                  "px-4 py-1.5 transition-colors",
                  tab === t
                    ? "bg-stone-900 text-white"
                    : "bg-white text-stone-600 hover:bg-stone-50"
                )}
              >
                {t === "retailers" ? "Overview" : "Scrape Progress"}
              </button>
            ))}
          </div>
        </div>
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

      {/* Scrape Progress tab */}
      {tab === "progress" && <ScrapeProgressPanel />}

      {/* Tables per country — Retailers tab only */}
      {tab === "retailers" && sortedCountries.map((country) => (
        <section key={country}>
          <h2 className="text-sm font-semibold text-stone-500 uppercase tracking-wider mb-3">
            {COUNTRY_LABEL[country] ?? `🌍 ${country}`}
            <span className="ml-2 font-normal">({grouped[country].length})</span>
          </h2>

          <div className="rounded-xl border border-stone-200 overflow-hidden">
            <table className="w-full text-sm">
              <thead>
                <tr className="bg-stone-50 border-b border-stone-200 text-xs font-semibold text-stone-500 uppercase tracking-wider">
                  <th className="text-left px-4 py-3">Retailer</th>
                  <th className="text-left px-4 py-3">Tier</th>
                  <th className="text-right px-4 py-3">Products</th>
                  <th className="text-right px-4 py-3">Unanalysed</th>
                  <th className="text-left px-4 py-3">Last Scraped</th>
                  <th className="text-left px-4 py-3">Status</th>
                  <th className="text-right px-4 py-3">Actions</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-stone-100 bg-white">
                {grouped[country].map((r) => (
                  <tr key={r.id} className="hover:bg-stone-50 transition-colors">
                    {/* Name */}
                    <td className="px-4 py-3">
                      <div className="font-medium text-stone-900">{r.name}</div>
                      <div className="text-xs text-stone-400 truncate max-w-[180px]">{r.base_url}</div>
                    </td>

                    {/* Tier */}
                    <td className="px-4 py-3">
                      {(() => {
                        const label = techLabel(r.adapter_class, r.tier);
                        return (
                          <span className={clsx("px-1.5 py-0.5 rounded border text-xs font-medium", TECH_COLOUR[label])}>
                            {label}
                          </span>
                        );
                      })()}
                    </td>

                    {/* Products */}
                    <td className="px-4 py-3 text-right text-stone-700 font-medium tabular-nums">
                      {r.product_count.toLocaleString()}
                    </td>

                    {/* Unanalysed */}
                    <td className="px-4 py-3 text-right tabular-nums">
                      {r.pending_analysis_count > 0 ? (
                        <span className="text-violet-600 font-medium">{r.pending_analysis_count.toLocaleString()}</span>
                      ) : (
                        <span className="text-stone-300">—</span>
                      )}
                    </td>

                    {/* Last scraped */}
                    <td className="px-4 py-3 text-stone-500 text-xs">
                      {r.last_scrape ? new Date(r.last_scrape).toLocaleDateString() : <span className="text-stone-300">Never</span>}
                    </td>

                    {/* Status */}
                    <td className="px-4 py-3">
                      {r.last_scrape_status ? (
                        <span className="flex items-center gap-1.5 text-xs text-stone-500">
                          <span className={clsx("w-2 h-2 rounded-full flex-shrink-0", STATUS_DOT[r.last_scrape_status] || STATUS_DOT.pending)} />
                          {r.last_scrape_status}
                        </span>
                      ) : (
                        <span className="text-stone-300 text-xs">—</span>
                      )}
                    </td>

                    {/* Actions */}
                    <td className="px-4 py-3 text-right">
                      <div className="flex items-center justify-end gap-2">
                        {r.pending_analysis_count > 0 && (
                          <button
                            onClick={() => triggerAnalyse(r)}
                            disabled={analysing[r.id]}
                            className="px-2.5 py-1 text-xs font-medium border border-violet-200 text-violet-700 bg-violet-50 rounded-lg hover:bg-violet-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                          >
                            {analysing[r.id] ? "Queuing..." : "Analyse"}
                          </button>
                        )}
                        <button
                          onClick={() => triggerScrape(r)}
                          disabled={scraping[r.id] || !r.is_active}
                          className="px-2.5 py-1 text-xs font-medium border border-stone-200 rounded-lg hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                        >
                          {scraping[r.id] ? "Queuing..." : "Scrape"}
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      ))}
    </div>
  );
}

