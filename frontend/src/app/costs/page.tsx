"use client";
/**
 * Costs — the Claude usage ledger (2026-11-01).
 *
 * Every tracked Claude call (catalogue vision, ALDI analysis and clustering,
 * in-store analysis, and the ALDI web recommender once it ships) writes one
 * row with its tokens, server-tool counts and a USD cost computed at call
 * time. This page shows the headline totals, a breakdown by feature and by
 * model for a chosen date range, and the per-call list.
 *
 * Product trends and fragrance trends are not tracked yet (standing
 * instruction), so their calls do not appear here. All dates are UTC.
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type CostBucket, type CostCall, type CostSummary } from "@/lib/api";
import clsx from "clsx";

const PAGE_SIZE = 50;

const FEATURE_LABEL: Record<string, string> = {
  catalogue_vision: "In-store catalogue — photo analysis",
  catalogue_reclassify: "In-store catalogue — reclassify",
  aldi_analysis: "ALDI — mood board analysis",
  aldi_cluster: "ALDI — product clustering",
  aldi_ideas: "ALDI — product ideas (legacy)",
  aldi_inventory: "ALDI — screenshot inventory",
  aldi_recommend: "ALDI — web recommendations",
  instore_photo: "In-store sessions — photo analysis",
  instore_trend_report: "In-store sessions — trend report",
  instore_trends: "In-store trends — clustering",
  instore_trend_summary: "In-store trends — report summary",
};

const REF_LINK: Record<string, (id: number) => string> = {
  aldi_session: (id) => `/aldi?session=${id}`,
  aldi_upload: () => `/aldi`,
  catalogue_image: () => `/instore-products`,
};

function usd(n: number, digits = 2): string {
  return `$${n.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits })}`;
}

function tokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

function featureLabel(f: string): string {
  return FEATURE_LABEL[f] ?? f;
}

// ── Stat tile ─────────────────────────────────────────────────────────────────

function StatTile({ label, bucket, hint }: { label: string; bucket?: CostBucket; hint?: string }) {
  return (
    <div className="bg-white border border-stone-200 rounded-xl p-5">
      <div className="text-xs font-medium uppercase tracking-wide text-stone-500">{label}</div>
      <div className="mt-2 text-3xl font-semibold tabular-nums text-stone-900">
        {bucket ? usd(bucket.cost_usd) : "—"}
      </div>
      <div className="mt-1 text-sm text-stone-500 tabular-nums">
        {bucket ? `${bucket.calls.toLocaleString()} calls` : ""}
        {bucket && bucket.errors > 0 && (
          <span className="ml-2 text-rose-700">{bucket.errors} failed</span>
        )}
      </div>
      {hint && <div className="mt-1 text-xs text-stone-400">{hint}</div>}
    </div>
  );
}

// ── Breakdown table (feature or model) ────────────────────────────────────────

function BreakdownTable<T extends CostBucket>({
  title, rows, keyOf, labelOf, onPick, picked,
}: {
  title: string;
  rows: T[];
  keyOf: (r: T) => string;
  labelOf: (r: T) => string;
  onPick?: (key: string) => void;
  picked?: string | null;
}) {
  const total = rows.reduce((s, r) => s + r.cost_usd, 0);
  return (
    <div className="bg-white border border-stone-200 rounded-xl overflow-hidden">
      <div className="px-5 py-3 border-b border-stone-200 flex items-baseline justify-between">
        <h2 className="text-sm font-semibold text-stone-900">{title}</h2>
        <span className="text-sm text-stone-500 tabular-nums">{usd(total)}</span>
      </div>
      {rows.length === 0 ? (
        <div className="px-5 py-6 text-sm text-stone-500">No calls in this range.</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-xs text-stone-500 uppercase tracking-wide">
            <tr className="border-b border-stone-100">
              <th className="text-left font-medium px-5 py-2">{title.replace("By ", "")}</th>
              <th className="text-right font-medium px-3 py-2">Calls</th>
              <th className="text-right font-medium px-3 py-2">In</th>
              <th className="text-right font-medium px-3 py-2">Out</th>
              <th className="text-right font-medium px-3 py-2">Cache</th>
              <th className="text-right font-medium px-3 py-2">Searches</th>
              <th className="text-right font-medium px-5 py-2">Cost</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const k = keyOf(r);
              const share = total > 0 ? r.cost_usd / total : 0;
              return (
                <tr
                  key={k}
                  onClick={onPick ? () => onPick(k) : undefined}
                  className={clsx(
                    "border-b border-stone-100 last:border-0",
                    onPick && "cursor-pointer hover:bg-stone-50",
                    picked === k && "bg-stone-100",
                  )}
                >
                  <td className="px-5 py-2 text-stone-900">
                    <div>{labelOf(r)}</div>
                    <div className="mt-1 h-1 rounded bg-stone-100 overflow-hidden">
                      <div className="h-1 bg-stone-400" style={{ width: `${Math.max(2, share * 100)}%` }} />
                    </div>
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums text-stone-700">{r.calls.toLocaleString()}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-stone-700">{tokens(r.input_tokens)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-stone-700">{tokens(r.output_tokens)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-stone-700">{tokens(r.cache_read_tokens + r.cache_write_tokens)}</td>
                  <td className="px-3 py-2 text-right tabular-nums text-stone-700">{r.web_searches.toLocaleString()}</td>
                  <td className="px-5 py-2 text-right tabular-nums font-medium text-stone-900">{usd(r.cost_usd)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function CostsPage() {
  const today = useMemo(() => new Date(), []);
  const [from, setFrom] = useState<string>(() => {
    const d = new Date(today); d.setUTCDate(d.getUTCDate() - 29); return isoDay(d);
  });
  const [to, setTo] = useState<string>(() => isoDay(today));
  const [feature, setFeature] = useState<string | null>(null);
  const [summary, setSummary] = useState<CostSummary | null>(null);
  const [calls, setCalls] = useState<CostCall[]>([]);
  const [callsTotal, setCallsTotal] = useState(0);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const loadSummary = useCallback(async () => {
    try {
      setSummary(await api.costs.summary({ from, to }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load costs");
    }
  }, [from, to]);

  const loadCalls = useCallback(async () => {
    try {
      const res = await api.costs.calls({ from, to, feature: feature ?? undefined, limit: PAGE_SIZE, offset: page * PAGE_SIZE });
      setCalls(res.rows);
      setCallsTotal(res.total);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load calls");
    }
  }, [from, to, feature, page]);

  useEffect(() => {
    setLoading(true);
    Promise.all([loadSummary(), loadCalls()]).finally(() => setLoading(false));
  }, [loadSummary, loadCalls]);

  // Reset paging when the filter changes.
  useEffect(() => { setPage(0); }, [from, to, feature]);

  function preset(days: number | "month" | "all") {
    const end = new Date();
    if (days === "all") { setFrom("2026-01-01"); setTo(isoDay(end)); return; }
    if (days === "month") { setFrom(`${end.getUTCFullYear()}-${String(end.getUTCMonth() + 1).padStart(2, "0")}-01`); setTo(isoDay(end)); return; }
    const start = new Date(end); start.setUTCDate(start.getUTCDate() - (days - 1));
    setFrom(isoDay(start)); setTo(isoDay(end));
  }

  const pageCount = Math.max(1, Math.ceil(callsTotal / PAGE_SIZE));

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-stone-900">Costs</h1>
          <p className="mt-1 text-sm text-stone-500">
            Every Claude call, with its tokens and USD cost at the time of the call. Dates are UTC.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-sm">
          <label className="text-stone-500">From</label>
          <input type="date" value={from} max={to} onChange={(e) => setFrom(e.target.value)}
                 className="border border-stone-300 rounded-md px-2 py-1 bg-white" />
          <label className="text-stone-500">To</label>
          <input type="date" value={to} min={from} onChange={(e) => setTo(e.target.value)}
                 className="border border-stone-300 rounded-md px-2 py-1 bg-white" />
          {([["7d", 7], ["30d", 30], ["This month", "month"], ["All", "all"]] as const).map(([label, v]) => (
            <button key={label} type="button" onClick={() => preset(v)}
                    className="px-2.5 py-1 rounded-md border border-stone-300 bg-white text-stone-700 hover:bg-stone-100">
              {label}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 text-sm rounded-xl px-4 py-3">{error}</div>
      )}

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        <StatTile label="Today" bucket={summary?.totals.today} />
        <StatTile label="This month" bucket={summary?.totals.this_month} />
        <StatTile label="All time" bucket={summary?.totals.all_time} hint="Since tracking began on 1 Nov 2026" />
        <StatTile label="Selected range" bucket={summary?.totals.range} hint={`${from} to ${to}`} />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <BreakdownTable
          title="By feature"
          rows={summary?.by_feature ?? []}
          keyOf={(r) => r.feature}
          labelOf={(r) => featureLabel(r.feature)}
          onPick={(k) => setFeature(feature === k ? null : k)}
          picked={feature}
        />
        <BreakdownTable
          title="By model"
          rows={summary?.by_model ?? []}
          keyOf={(r) => r.model}
          labelOf={(r) => r.model}
        />
      </div>

      <div className="bg-white border border-stone-200 rounded-xl overflow-hidden">
        <div className="px-5 py-3 border-b border-stone-200 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-stone-900">
            Calls
            <span className="ml-2 font-normal text-stone-500 tabular-nums">{callsTotal.toLocaleString()}</span>
            {feature && (
              <button type="button" onClick={() => setFeature(null)}
                      className="ml-3 text-xs font-medium text-stone-600 border border-stone-300 rounded-full px-2 py-0.5 hover:bg-stone-100">
                {featureLabel(feature)} ×
              </button>
            )}
          </h2>
          <div className="flex items-center gap-2 text-sm text-stone-600">
            <button type="button" disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}
                    className="px-2 py-1 rounded-md border border-stone-300 disabled:opacity-40 hover:bg-stone-100">‹</button>
            <span className="tabular-nums">{page + 1} / {pageCount}</span>
            <button type="button" disabled={page + 1 >= pageCount} onClick={() => setPage((p) => p + 1)}
                    className="px-2 py-1 rounded-md border border-stone-300 disabled:opacity-40 hover:bg-stone-100">›</button>
          </div>
        </div>
        {loading && calls.length === 0 ? (
          <div className="px-5 py-6 text-sm text-stone-500">Loading…</div>
        ) : calls.length === 0 ? (
          <div className="px-5 py-6 text-sm text-stone-500">No calls in this range.</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm whitespace-nowrap">
              <thead className="text-xs text-stone-500 uppercase tracking-wide">
                <tr className="border-b border-stone-100">
                  <th className="text-left font-medium px-5 py-2">When (UTC)</th>
                  <th className="text-left font-medium px-3 py-2">Feature</th>
                  <th className="text-left font-medium px-3 py-2">Model</th>
                  <th className="text-left font-medium px-3 py-2">For</th>
                  <th className="text-right font-medium px-3 py-2">In</th>
                  <th className="text-right font-medium px-3 py-2">Out</th>
                  <th className="text-right font-medium px-3 py-2">Cache r/w</th>
                  <th className="text-right font-medium px-3 py-2">Search / fetch</th>
                  <th className="text-right font-medium px-3 py-2">Time</th>
                  <th className="text-right font-medium px-5 py-2">Cost</th>
                </tr>
              </thead>
              <tbody>
                {calls.map((c) => {
                  const link = c.ref_type && c.ref_id != null ? REF_LINK[c.ref_type]?.(c.ref_id) : undefined;
                  const refText = c.ref_type && c.ref_id != null ? `${c.ref_type.replace(/_/g, " ")} #${c.ref_id}` : "";
                  return (
                    <tr key={c.id} className={clsx("border-b border-stone-100 last:border-0", c.status !== "ok" && "bg-rose-50/60")}>
                      <td className="px-5 py-2 tabular-nums text-stone-700">
                        {c.created_at ? c.created_at.replace("T", " ").slice(0, 19) : ""}
                      </td>
                      <td className="px-3 py-2 text-stone-900">
                        {featureLabel(c.feature)}
                        {c.status !== "ok" && (
                          <span className="ml-2 inline-flex items-center gap-1 text-xs text-rose-700" title={c.error ?? ""}>
                            <span aria-hidden>●</span> failed
                          </span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-stone-700">
                        {c.model}
                        {c.rates_source && c.rates_source !== "exact" && (
                          <span className="ml-1 text-xs text-amber-700" title={`Rates matched by ${c.rates_source}`}>~</span>
                        )}
                      </td>
                      <td className="px-3 py-2 text-stone-700">
                        {link ? <a href={link} className="underline decoration-stone-300 hover:decoration-stone-700">{refText}</a> : refText}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-stone-700">{tokens(c.input_tokens)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-stone-700">{tokens(c.output_tokens)}</td>
                      <td className="px-3 py-2 text-right tabular-nums text-stone-700">
                        {c.cache_read_tokens || c.cache_write_tokens ? `${tokens(c.cache_read_tokens)} / ${tokens(c.cache_write_tokens)}` : "–"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-stone-700">
                        {c.web_searches || c.web_fetches ? `${c.web_searches} / ${c.web_fetches}` : "–"}
                      </td>
                      <td className="px-3 py-2 text-right tabular-nums text-stone-700">
                        {c.duration_ms != null ? `${(c.duration_ms / 1000).toFixed(1)}s` : "–"}
                      </td>
                      <td className="px-5 py-2 text-right tabular-nums font-medium text-stone-900">
                        {usd(c.cost_usd, c.cost_usd < 0.01 ? 4 : 2)}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
