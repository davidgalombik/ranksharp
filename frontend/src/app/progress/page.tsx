"use client";

import { useEffect, useState, useCallback, useRef } from "react";
import clsx from "clsx";
import { api, CsvPreviewResult, CsvCommitResult } from "@/lib/api";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const POLL_INTERVAL = 4000; // refresh every 4 seconds

// ── Types ──────────────────────────────────────────────────────────────────

interface ScrapeJob {
  job_id: number;
  retailer_id: number;
  retailer_name: string;
  retailer_slug: string;
  retailer_country: string;
  tier: string;
  adapter_class: string;
  status: string;
  started_at: string | null;
  finished_at: string | null;
  products_found: number;
  products_new: number;
  products_updated: number;
  total_products_in_db: number;
  error_message: string | null;
  duration_seconds: number | null;
}

interface OverallProgress {
  total_retailers: number;
  retailers_pending: number;
  retailers_running: number;
  retailers_done: number;
  retailers_failed: number;
  total_products_found: number;
  total_products_new: number;
  jobs: ScrapeJob[];
}

// ── Status config ──────────────────────────────────────────────────────────

const STATUS = {
  running: {
    label: "Running",
    dot: "bg-blue-500 animate-pulse",
    row: "border-blue-200 bg-blue-50",
    badge: "bg-blue-100 text-blue-800",
  },
  success: {
    label: "Done",
    dot: "bg-emerald-500",
    row: "border-stone-200 bg-white",
    badge: "bg-emerald-100 text-emerald-800",
  },
  failed: {
    label: "Failed",
    dot: "bg-rose-500",
    row: "border-rose-200 bg-rose-50",
    badge: "bg-rose-100 text-rose-800",
  },
  pending: {
    label: "Queued",
    dot: "bg-amber-400 animate-pulse",
    row: "border-amber-100 bg-amber-50",
    badge: "bg-amber-100 text-amber-800",
  },
  never_run: {
    label: "Not started",
    dot: "bg-stone-300",
    row: "border-stone-100 bg-stone-50",
    badge: "bg-stone-100 text-stone-500",
  },
  skipped: {
    label: "Skipped",
    dot: "bg-stone-300",
    row: "border-stone-100 bg-stone-50",
    badge: "bg-stone-100 text-stone-500",
  },
} as Record<string, { label: string; dot: string; row: string; badge: string }>;

function techLabel(adapterClass: string, tier: string): string {
  const a = adapterClass.toLowerCase();
  if (a.includes("apify")) return "Apify";
  if (a.includes("firecrawl")) return "Firecrawl";
  if (a.includes("smartproxy")) return "SmartProxy";
  if (tier === "api") return "API";
  if (tier === "http") return "HTTP";
  return "Browser";
}

const TECH_COLOUR: Record<string, string> = {
  Apify: "text-violet-700 bg-violet-50 border-violet-200",
  Firecrawl: "text-orange-700 bg-orange-50 border-orange-200",
  SmartProxy: "text-sky-700 bg-sky-50 border-sky-200",
  API: "text-emerald-700 bg-emerald-50 border-emerald-200",
  HTTP: "text-amber-700 bg-amber-50 border-amber-200",
  Browser: "text-rose-700 bg-rose-50 border-rose-200",
};

const COUNTRY_FLAG = (c: string) =>
  c === "US" ? "🇺🇸" : c === "AU" ? "🇦🇺" : c === "GB" ? "🇬🇧" : c === "NL" ? "🇳🇱" : "🌍";

function fmt(s: number): string {
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  return `${(s / 3600).toFixed(1)}h`;
}

function elapsed(startedAt: string | null): string {
  if (!startedAt) return "";
  const secs = (Date.now() - new Date(startedAt + "Z").getTime()) / 1000;
  return fmt(secs);
}

// ── Components ─────────────────────────────────────────────────────────────

function OverallBar({ data }: { data: OverallProgress }) {
  const done = data.retailers_done;
  const running = data.retailers_running;
  const failed = data.retailers_failed;
  const total = data.total_retailers;
  const donePct = (done / total) * 100;
  const runningPct = (running / total) * 100;
  const failedPct = (failed / total) * 100;

  return (
    <div className="bg-white rounded-xl border border-stone-200 p-6 space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="font-semibold text-stone-900">Overall Progress</h2>
        {running > 0 && (
          <span className="flex items-center gap-1.5 text-sm text-blue-700 font-medium">
            <span className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
            {running} running
          </span>
        )}
      </div>

      {/* Progress bar */}
      <div className="h-4 w-full rounded-full bg-stone-100 overflow-hidden flex">
        <div
          className="h-full bg-emerald-500 transition-all duration-700"
          style={{ width: `${donePct}%` }}
        />
        <div
          className="h-full bg-blue-400 animate-pulse transition-all duration-700"
          style={{ width: `${runningPct}%` }}
        />
        <div
          className="h-full bg-rose-400 transition-all duration-700"
          style={{ width: `${failedPct}%` }}
        />
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3 text-center">
        {[
          { label: "Done", value: done, colour: "text-emerald-700" },
          { label: "Running", value: running, colour: "text-blue-700" },
          { label: "Queued", value: data.retailers_pending, colour: "text-amber-700" },
          { label: "Failed", value: failed, colour: "text-rose-700" },
          { label: "Total retailers", value: total, colour: "text-stone-700" },
        ].map(({ label, value, colour }) => (
          <div key={label} className="bg-stone-50 rounded-lg p-2">
            <p className={clsx("text-xl font-bold", colour)}>{value}</p>
            <p className="text-xs text-stone-500">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex gap-6 text-sm text-stone-600 pt-1 border-t border-stone-100">
        <span>
          <strong className="text-stone-900">
            {data.jobs.reduce((s, j) => s + j.total_products_in_db, 0).toLocaleString()}
          </strong>{" "}
          total products in DB
        </span>
        {data.total_products_new > 0 && (
          <span>
            <strong className="text-stone-900">
              +{data.total_products_new.toLocaleString()}
            </strong>{" "}
            new this run
          </span>
        )}
        {data.retailers_running > 0 && (
          <span className="text-blue-600 font-medium animate-pulse">
            ● {data.retailers_running} scrape{data.retailers_running > 1 ? "s" : ""} in progress
          </span>
        )}
      </div>
    </div>
  );
}

function RetailerRow({
  job,
  onScrape,
  onCancel,
  onUploadCsv,
  scraping,
  cancelling,
}: {
  job: ScrapeJob;
  onScrape: (id: number) => void;
  onCancel: (jobId: number) => void;
  onUploadCsv: (retailerSlug: string) => void;
  scraping: boolean;
  cancelling: boolean;
}) {
  const cfg = STATUS[job.status] || STATUS.never_run;
  const canCancel = job.status === "running" || job.status === "pending";

  return (
    <div
      className={clsx(
        "flex flex-col sm:flex-row sm:items-center gap-3 px-4 py-3 rounded-xl border transition-colors",
        cfg.row
      )}
    >
      {/* Status dot + name */}
      <div className="flex items-center gap-3 min-w-0 flex-1">
        <span className={clsx("w-2.5 h-2.5 rounded-full flex-shrink-0", cfg.dot)} />
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium text-stone-900 text-sm truncate">
              {COUNTRY_FLAG(job.retailer_country)} {job.retailer_name}
            </span>
            {(() => {
              const label = techLabel(job.adapter_class, job.tier);
              return (
                <span
                  className={clsx(
                    "px-1.5 py-0.5 rounded border text-xs font-medium",
                    TECH_COLOUR[label] || ""
                  )}
                >
                  {label}
                </span>
              );
            })()}
            <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", cfg.badge)}>
              {cfg.label}
              {job.status === "running" && job.started_at && (
                <> · {elapsed(job.started_at)}</>
              )}
              {job.status === "success" && job.duration_seconds != null && (
                <> · {fmt(job.duration_seconds)}</>
              )}
            </span>
          </div>

          {/* Progress detail */}
          {(job.status === "running" || job.status === "success" || job.status === "never_run") && (
              <p className="text-xs text-stone-500 mt-0.5">
                {job.status === "running" && job.total_products_in_db > 0 && (
                  <span className="text-blue-700 font-medium">
                    {job.total_products_in_db.toLocaleString()} saved so far…{" "}
                  </span>
                )}
                {job.status === "success" && job.products_found > 0 && (
                  <>
                    {job.products_found.toLocaleString()} scraped
                    {job.products_new > 0 && (
                      <> · <span className="text-emerald-700">+{job.products_new.toLocaleString()} new</span></>
                    )}
                    {job.products_updated > 0 && (
                      <> · {job.products_updated.toLocaleString()} updated</>
                    )}
                    {" · "}
                  </>
                )}
                {job.total_products_in_db > 0 && (
                  <span>{job.total_products_in_db.toLocaleString()} total in DB</span>
                )}
              </p>
            )}

          {/* Running progress bar (animated) */}
          {job.status === "running" && (
            <div className="mt-1.5 h-1.5 w-48 rounded-full bg-stone-200 overflow-hidden">
              <div className="h-full bg-blue-400 rounded-full animate-[progress_2s_ease-in-out_infinite]"
                style={{ width: "60%" }} />
            </div>
          )}

          {/* Error message */}
          {job.status === "failed" && job.error_message && (
            <p className="text-xs text-rose-600 mt-0.5 truncate max-w-xs" title={job.error_message}>
              {job.error_message}
            </p>
          )}
        </div>
      </div>

      {/* Action buttons */}
      <div className="flex gap-2 flex-shrink-0">
        {canCancel && (
          <button
            onClick={() => onCancel(job.job_id)}
            disabled={cancelling}
            className="px-3 py-1.5 text-xs font-medium border border-rose-200 bg-rose-50 text-rose-700 rounded-lg hover:bg-rose-100 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            {cancelling ? "Cancelling…" : "Cancel"}
          </button>
        )}
        <button
          onClick={() => onUploadCsv(job.retailer_slug)}
          className="px-2 py-1.5 text-xs font-medium border border-stone-200 bg-white rounded-lg hover:bg-stone-50 transition-colors"
          title="Upload products from a CSV file for this retailer"
          aria-label="Upload CSV"
        >
          📤
        </button>
        <button
          onClick={() => onScrape(job.retailer_id)}
          disabled={scraping || job.status === "running" || job.status === "pending"}
          className="px-3 py-1.5 text-xs font-medium border border-stone-200 bg-white rounded-lg hover:bg-stone-50 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {scraping ? "Queuing…" : job.status === "running" ? "Running…" : "Scrape now"}
        </button>
      </div>
    </div>
  );
}

// ── CSV Upload modal ───────────────────────────────────────────────────────

type UploadPhase = "idle" | "previewing" | "previewed" | "committing" | "done";

function CsvUploadModal({
  retailerSlug,
  onClose,
}: {
  retailerSlug: string;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [preview, setPreview] = useState<CsvPreviewResult | null>(null);
  const [commit, setCommit] = useState<CsvCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handlePreview = async () => {
    if (!file) return;
    setError(null);
    setPhase("previewing");
    try {
      const result = await api.retailers.csvPreview(file);
      setPreview(result);
      setPhase("previewed");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Preview failed");
      setPhase("idle");
    }
  };

  const handleCommit = async () => {
    if (!file) return;
    setError(null);
    setPhase("committing");
    try {
      const result = await api.retailers.csvCommit(file);
      setCommit(result);
      setPhase("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Commit failed");
      setPhase("previewed");
    }
  };

  const downloadRejects = () => {
    const rejects = commit?.rejects || preview?.rejects || [];
    if (!rejects.length) return;
    const header = "row_number,url,reason\n";
    const rows = rejects
      .map((r) => {
        const url = (r.url || "").replace(/"/g, '""');
        const reason = r.reason.replace(/"/g, '""');
        return `${r.row_number},"${url}","${reason}"`;
      })
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `rejects-${retailerSlug}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-stone-200">
          <div>
            <h2 className="text-lg font-bold text-stone-900">Upload products via CSV</h2>
            <p className="text-xs text-stone-500 mt-0.5">
              Retailer: <strong className="text-stone-700">{retailerSlug}</strong> · the CSV&apos;s
              retailer_slug column must match this value
            </p>
          </div>
          <button onClick={onClose} className="text-2xl text-stone-400 hover:text-stone-900 leading-none">
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {/* File picker */}
          {phase === "idle" && (
            <div className="space-y-3">
              <div
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-stone-300 rounded-xl p-8 text-center cursor-pointer hover:bg-stone-50"
              >
                <div className="text-3xl mb-2">📄</div>
                <p className="text-sm font-medium text-stone-700">
                  {file ? file.name : "Click to choose a CSV file"}
                </p>
                {file && (
                  <p className="text-xs text-stone-400 mt-1">
                    {(file.size / 1024).toFixed(1)} KB
                  </p>
                )}
                {!file && (
                  <p className="text-xs text-stone-400 mt-1">
                    Max 5,000 rows · required columns: url, name, primary_image_url, retailer_slug
                  </p>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setFile(f);
                    e.target.value = "";
                  }}
                />
              </div>

              <div className="bg-stone-50 rounded-lg p-3 text-xs text-stone-600 space-y-1.5">
                <p className="font-semibold text-stone-700">Required columns</p>
                <p className="font-mono">url · name · primary_image_url · retailer_slug</p>
                <p className="font-semibold text-stone-700 mt-2">Optional columns</p>
                <p className="font-mono">price · currency · category · is_best_seller · is_new · has_patent · description · sku · brand</p>
                <p className="text-stone-400 mt-1">
                  Leave colours / materials / season / room out — they&apos;re filled in by the analysis pipeline.
                </p>
              </div>
            </div>
          )}

          {/* Previewing */}
          {phase === "previewing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Parsing and validating…</p>
          )}

          {/* Previewed */}
          {phase === "previewed" && preview && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Counter label="Total rows" value={preview.total_rows} />
                <Counter label="Will insert" value={preview.new_count} className="text-emerald-600" />
                <Counter label="Will update" value={preview.update_count} className="text-amber-600" />
                <Counter label="Rejects" value={preview.rejects.length} className="text-red-500" />
              </div>
              {preview.retailers_referenced.length > 0 && (
                <p className="text-xs text-stone-500">
                  Retailers referenced: <span className="font-medium text-stone-700">{preview.retailers_referenced.join(", ")}</span>
                </p>
              )}

              {preview.rejects.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-red-700">{preview.rejects.length} rejected row{preview.rejects.length !== 1 ? "s" : ""}</p>
                    <button onClick={downloadRejects} className="text-xs text-red-700 hover:text-red-900 underline">
                      Download rejects.csv
                    </button>
                  </div>
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {preview.rejects.slice(0, 20).map((r) => (
                      <div key={r.row_number} className="text-xs text-red-800">
                        <span className="font-mono text-red-500">row {r.row_number}:</span> {r.reason}
                        {r.url && <span className="text-red-400"> — {r.url.slice(0, 60)}{r.url.length > 60 ? "…" : ""}</span>}
                      </div>
                    ))}
                    {preview.rejects.length > 20 && (
                      <p className="text-xs text-red-500 italic">…and {preview.rejects.length - 20} more (download CSV to see all)</p>
                    )}
                  </div>
                </div>
              )}

              {preview.valid_rows === 0 && (
                <p className="text-xs text-stone-500">No valid rows to import.</p>
              )}
            </div>
          )}

          {/* Committing */}
          {phase === "committing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Saving products and queuing analysis…</p>
          )}

          {/* Done */}
          {phase === "done" && commit && (
            <div className="space-y-3">
              <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
                <p className="text-sm font-semibold text-emerald-800">Upload complete ✓</p>
                <p className="text-xs text-emerald-700 mt-1">
                  {commit.inserted} inserted · {commit.updated} updated · {commit.analysis_queued} queued for analysis
                </p>
              </div>
              {commit.rejects.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-red-700">{commit.rejects.length} row{commit.rejects.length !== 1 ? "s" : ""} rejected</p>
                    <button onClick={downloadRejects} className="text-xs text-red-700 hover:text-red-900 underline">
                      Download rejects.csv
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 whitespace-pre-wrap">
              {error}
            </div>
          )}
        </div>

        {/* Footer buttons */}
        <div className="flex items-center justify-end gap-2 p-4 border-t border-stone-200 bg-stone-50">
          {phase === "idle" && (
            <>
              <button onClick={onClose} className="px-3 py-1.5 text-sm text-stone-600 hover:text-stone-900">Cancel</button>
              <button
                onClick={handlePreview}
                disabled={!file}
                className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Preview
              </button>
            </>
          )}
          {phase === "previewed" && preview && (
            <>
              <button
                onClick={() => { setPhase("idle"); setPreview(null); setFile(null); }}
                className="px-3 py-1.5 text-sm text-stone-600 hover:text-stone-900"
              >
                Back
              </button>
              <button
                onClick={handleCommit}
                disabled={preview.valid_rows === 0}
                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Commit {preview.valid_rows} row{preview.valid_rows !== 1 ? "s" : ""}
              </button>
            </>
          )}
          {phase === "done" && (
            <button onClick={onClose} className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800">
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Counter({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="bg-stone-50 rounded-lg p-2 text-center">
      <p className="text-[10px] text-stone-400 uppercase tracking-wide">{label}</p>
      <p className={clsx("text-lg font-semibold", className || "text-stone-800")}>{value.toLocaleString()}</p>
    </div>
  );
}

// ── Page ───────────────────────────────────────────────────────────────────

export default function ProgressPage() {
  const [data, setData] = useState<OverallProgress | null>(null);
  const [scraping, setScraping] = useState<Record<number, boolean>>({});
  const [cancelling, setCancelling] = useState<Record<number, boolean>>({});
  const [stoppingAll, setStoppingAll] = useState(false);
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const [filter, setFilter] = useState<string>("all");
  const [skipAnalysis, setSkipAnalysis] = useState(false);
  const [uploadRetailerSlug, setUploadRetailerSlug] = useState<string | null>(null);

  const fetchProgress = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/scrape-jobs/active`, { cache: "no-store" });
      if (res.ok) {
        setData(await res.json());
        setLastUpdated(new Date());
      }
    } catch {}
  }, []);

  // Initial load + polling
  useEffect(() => {
    fetchProgress();
    const interval = setInterval(fetchProgress, POLL_INTERVAL);
    return () => clearInterval(interval);
  }, [fetchProgress]);

  async function triggerScrape(retailerId: number) {
    setScraping((s) => ({ ...s, [retailerId]: true }));
    try {
      await fetch(`${API_BASE}/api/retailers/${retailerId}/scrape?skip_analysis=${skipAnalysis}`, { method: "POST" });
      setTimeout(fetchProgress, 1000);
    } finally {
      setScraping((s) => ({ ...s, [retailerId]: false }));
    }
  }

  async function triggerAll() {
    if (!confirm(`Queue all active retailers for scraping?${skipAnalysis ? "\n\nℹ️ Claude analysis will be skipped." : ""}`)) return;
    await fetch(`${API_BASE}/api/retailers/scrape-all?skip_analysis=${skipAnalysis}`, { method: "POST" });
    setTimeout(fetchProgress, 1000);
  }

  async function stopAll() {
    if (!confirm("Stop all running and queued scrapes? This will cancel pending jobs and clear the scrape queue.")) return;
    setStoppingAll(true);
    try {
      await fetch(`${API_BASE}/api/scrape-jobs/stop-all`, { method: "POST" });
      setTimeout(fetchProgress, 800);
    } finally {
      setStoppingAll(false);
    }
  }

  async function cancelJob(jobId: number) {
    setCancelling((c) => ({ ...c, [jobId]: true }));
    try {
      await fetch(`${API_BASE}/api/scrape-jobs/${jobId}/cancel`, { method: "POST" });
      setTimeout(fetchProgress, 800);
    } finally {
      setCancelling((c) => ({ ...c, [jobId]: false }));
    }
  }

  if (!data) {
    return (
      <div className="flex items-center justify-center py-32 text-stone-400">
        <div className="text-center space-y-2">
          <div className="w-8 h-8 border-2 border-stone-300 border-t-stone-600 rounded-full animate-spin mx-auto" />
          <p className="text-sm">Loading progress…</p>
        </div>
      </div>
    );
  }

  const hasRunning = data.retailers_running > 0;

  // Filter jobs
  const filteredJobs = data.jobs.filter((j) => {
    if (filter === "all") return true;
    if (filter === "running") return j.status === "running";
    if (filter === "done") return j.status === "success";
    if (filter === "pending") return j.status === "pending" || j.status === "never_run";
    if (filter === "failed") return j.status === "failed";
    return true;
  });

  // Group by country
  const grouped = filteredJobs.reduce((acc, j) => {
    if (!acc[j.retailer_country]) acc[j.retailer_country] = [];
    acc[j.retailer_country].push(j);
    return acc;
  }, {} as Record<string, ScrapeJob[]>);

  return (
    <div className="space-y-6 max-w-4xl mx-auto">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-stone-900">Scrape Progress</h1>
          {lastUpdated && (
            <p className="text-xs text-stone-400 mt-0.5">
              {hasRunning ? (
                <span className="text-blue-600">● Live — refreshes every 4s</span>
              ) : (
                <>Last updated {lastUpdated.toLocaleTimeString()}</>
              )}
            </p>
          )}
        </div>
        <div className="flex items-center gap-3 flex-wrap">
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
          {(data.retailers_running > 0 || data.retailers_pending > 0) && (
            <button
              onClick={stopAll}
              disabled={stoppingAll}
              className="bg-rose-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-rose-700 disabled:opacity-50 transition-colors"
            >
              {stoppingAll ? "Stopping…" : `Stop all scraping`}
            </button>
          )}
          <button
            onClick={triggerAll}
            className="bg-stone-900 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-stone-700 transition-colors"
          >
            Scrape all retailers
          </button>
        </div>
      </div>

      {/* Overall bar */}
      <OverallBar data={data} />

      {/* Filter pills */}
      <div className="flex gap-2 flex-wrap">
        {[
          { key: "all", label: `All (${data.total_retailers})` },
          { key: "running", label: `Running (${data.retailers_running})` },
          { key: "pending", label: `Pending (${data.retailers_pending})` },
          { key: "done", label: `Done (${data.retailers_done})` },
          { key: "failed", label: `Failed (${data.retailers_failed})` },
        ].map(({ key, label }) => (
          <button
            key={key}
            onClick={() => setFilter(key)}
            className={clsx(
              "px-3 py-1.5 rounded-full text-sm font-medium border transition-colors",
              filter === key
                ? "bg-stone-900 text-white border-stone-900"
                : "bg-white text-stone-600 border-stone-200 hover:bg-stone-50"
            )}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Retailer rows grouped by country */}
      {Object.entries(grouped)
        .sort(([a], [b]) => a.localeCompare(b))
        .map(([country, jobs]) => (
          <section key={country} className="space-y-2">
            <h2 className="text-xs font-semibold text-stone-400 uppercase tracking-wider">
              {COUNTRY_FLAG(country)}{" "}
              {country === "US" ? "United States" : country === "AU" ? "Australia" : country === "GB" ? "United Kingdom" : country === "NL" ? "Netherlands" : country}
              <span className="ml-2 font-normal">({jobs.length})</span>
            </h2>
            <div className="space-y-2">
              {jobs.map((job) => (
                <RetailerRow
                  key={job.retailer_id}
                  job={job}
                  onScrape={triggerScrape}
                  onCancel={cancelJob}
                  onUploadCsv={setUploadRetailerSlug}
                  scraping={!!scraping[job.retailer_id]}
                  cancelling={!!cancelling[job.job_id]}
                />
              ))}
            </div>
          </section>
        ))}

      {filteredJobs.length === 0 && (
        <div className="text-center py-12 text-stone-400">No retailers match this filter.</div>
      )}

      {uploadRetailerSlug && (
        <CsvUploadModal
          retailerSlug={uploadRetailerSlug}
          onClose={() => setUploadRetailerSlug(null)}
        />
      )}
    </div>
  );
}
