"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "@/lib/api";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

interface InStoreProduct {
  id: number;
  filename: string;
  file_type: string;
  status: "pending" | "analysing" | "done" | "failed";
  product_name: string | null;
  category: string | null;
  price: string | null;
  colours: string[] | null;
  materials: string[] | null;
  style_tags: string[] | null;
  patterns: string[] | null;
  mood: string[] | null;
  error_message: string | null;
  created_at: string;
}

interface SuggestedProduct {
  id: number;
  name: string;
  url: string | null;
  price: number | null;
  primary_image_url: string | null;
  is_best_seller: boolean;
  retailer_name: string;
  colours: string[];
  materials: string[];
  style_tags: string[];
  patterns: string[];
}

interface TrendInReport {
  name: string;
  description: string;
  colours: string[];
  materials: string[];
  style_tags: string[];
  product_ids: number[];
  products: { id: number; product_name: string | null; category: string | null; filename: string; file_type: string }[];
  suggested_products?: SuggestedProduct[];
}

interface GenerationEntry {
  generation: number;
  lens: string;
  created_at: string;
  trends: TrendInReport[];
}

interface InStoreSession {
  id: number;
  name: string | null;
  status: "uploading" | "pending" | "analysing" | "generating" | "done" | "failed";
  product_count: number;
  done_count: number;
  trend_report: TrendInReport[] | null;
  generation_count?: number;
  trend_report_all?: GenerationEntry[];
  has_trend_report?: boolean;
  error_message: string | null;
  created_at: string;
  products?: InStoreProduct[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const STATUS_LABEL: Record<string, string> = {
  uploading: "Uploading…",
  pending: "Queued",
  analysing: "Analysing…",
  generating: "Generating trends…",
  done: "Done",
  failed: "Failed",
};

const STATUS_CLASS: Record<string, string> = {
  uploading: "bg-sky-100 text-sky-700",
  pending: "bg-stone-100 text-stone-500",
  analysing: "bg-amber-100 text-amber-700 animate-pulse",
  generating: "bg-amber-100 text-amber-700 animate-pulse",
  done: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

const CHIP_COLOURS = [
  "bg-stone-100 text-stone-700",
  "bg-amber-100 text-amber-700",
  "bg-emerald-100 text-emerald-700",
  "bg-sky-100 text-sky-700",
  "bg-violet-100 text-violet-700",
  "bg-rose-100 text-rose-700",
];

function Chip({ label, colourIdx = 0 }: { label: string; colourIdx?: number }) {
  return (
    <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", CHIP_COLOURS[colourIdx % CHIP_COLOURS.length])}>
      {label}
    </span>
  );
}

function imageUrl(sessionId: number, productId: number) {
  return `${API_BASE}/api/instore/sessions/${sessionId}/products/${productId}/image`;
}

// ── Upload Zone ───────────────────────────────────────────────────────────────

function UploadZone({ onUpload }: { onUpload: (files: File[]) => void }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const files = Array.from(e.dataTransfer.files).filter((f) =>
      ["image/jpeg", "image/jpg", "image/png", "image/heic", "image/heif", "application/pdf"].includes(f.type)
        || /\.(heic|heif)$/i.test(f.name)
    );
    if (files.length) onUpload(files);
  }, [onUpload]);

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={handleDrop}
      onClick={() => inputRef.current?.click()}
      className={clsx(
        "border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors",
        dragging ? "border-stone-500 bg-stone-50" : "border-stone-300 hover:border-stone-400 hover:bg-stone-50"
      )}
    >
      <input
        ref={inputRef}
        type="file"
        multiple
        accept="image/jpeg,image/jpg,image/png,image/heic,image/heif,application/pdf,.heic,.heif"
        className="hidden"
        onChange={(e) => {
          const files = Array.from(e.target.files || []);
          if (files.length) onUpload(files);
          e.target.value = "";
        }}
      />
      <div className="text-3xl mb-2">📸</div>
      <p className="text-sm font-medium text-stone-700">Drop product photos here</p>
      <p className="text-xs text-stone-400 mt-1">JPG, PNG, HEIC or PDF · 20 MB each</p>
    </div>
  );
}

// ── Product Card ──────────────────────────────────────────────────────────────

function ProductCard({ product, sessionId }: { product: InStoreProduct; sessionId: number }) {
  const isPdf = product.file_type === "pdf";
  return (
    <div className="bg-white rounded-lg border border-stone-200 overflow-hidden">
      <div className="aspect-square bg-stone-100 relative">
        {isPdf ? (
          <div className="w-full h-full flex items-center justify-center text-stone-400 text-4xl">📄</div>
        ) : (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={imageUrl(sessionId, product.id)}
            alt={product.product_name || product.filename}
            className="w-full h-full object-cover"
          />
        )}
        <span className={clsx("absolute top-1.5 right-1.5 px-2 py-0.5 rounded-full text-xs font-medium", STATUS_CLASS[product.status])}>
          {STATUS_LABEL[product.status]}
        </span>
      </div>
      <div className="p-2.5">
        <p className="text-xs font-semibold text-stone-800 truncate">
          {product.product_name || product.filename}
        </p>
        {product.category && (
          <p className="text-xs text-stone-500 mt-0.5">{product.category}</p>
        )}
        {product.price && (
          <p className="text-xs text-stone-600 font-medium mt-0.5">{product.price}</p>
        )}
        {product.colours && product.colours.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1.5">
            {product.colours.slice(0, 3).map((c, i) => (
              <Chip key={i} label={c} colourIdx={0} />
            ))}
          </div>
        )}
        {product.materials && product.materials.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {product.materials.slice(0, 2).map((m, i) => (
              <Chip key={i} label={m} colourIdx={2} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Trend Card ────────────────────────────────────────────────────────────────

function TrendCard({ trend, sessionId, index }: { trend: TrendInReport; sessionId: number; index: number }) {
  return (
    <div className="bg-white rounded-xl border border-stone-200 p-5">
      <div className="flex items-start justify-between gap-2 mb-3">
        <h3 className="text-base font-bold text-stone-900">{trend.name}</h3>
        <span className="text-xs text-stone-400 whitespace-nowrap">{trend.products?.length || 0} photos</span>
      </div>
      <p className="text-sm text-stone-600 mb-4 leading-relaxed">{trend.description}</p>

      {/* Attribute chips */}
      <div className="space-y-2 mb-4">
        {trend.colours?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {trend.colours.map((c, i) => <Chip key={i} label={c} colourIdx={0} />)}
          </div>
        )}
        {trend.materials?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {trend.materials.map((m, i) => <Chip key={i} label={m} colourIdx={2} />)}
          </div>
        )}
        {trend.style_tags?.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {trend.style_tags.map((s, i) => <Chip key={i} label={s} colourIdx={4} />)}
          </div>
        )}
      </div>

      {/* In-store photo thumbnails */}
      {trend.products && trend.products.length > 0 && (
        <div className="flex gap-2 flex-wrap mb-5">
          {trend.products.slice(0, 8).map((p) => (
            <div key={p.id} className="w-14 h-14 rounded-md overflow-hidden bg-stone-100 flex-shrink-0">
              {p.file_type === "pdf" ? (
                <div className="w-full h-full flex items-center justify-center text-stone-400 text-lg">📄</div>
              ) : (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={imageUrl(sessionId, p.id)}
                  alt={p.product_name || p.filename}
                  className="w-full h-full object-cover"
                  title={p.product_name || p.filename}
                />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Suggested products from database */}
      {trend.suggested_products && trend.suggested_products.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-3">
            Matching products from our database ({trend.suggested_products.length})
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 xl:grid-cols-5 gap-3">
            {trend.suggested_products.map((p) => (
              <a
                key={p.id}
                href={p.url || "#"}
                target="_blank"
                rel="noopener noreferrer"
                className="group block bg-stone-50 rounded-lg border border-stone-200 overflow-hidden hover:border-stone-400 hover:shadow-sm transition-all"
              >
                <div className="aspect-square bg-stone-100 overflow-hidden relative">
                  {p.primary_image_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={p.primary_image_url}
                      alt={p.name}
                      className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-200"
                    />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-stone-300 text-2xl">🛍️</div>
                  )}
                  {p.is_best_seller && (
                    <span className="absolute top-1 left-1 bg-amber-400 text-amber-900 text-[10px] font-bold px-1.5 py-0.5 rounded-full">
                      ★ Best Seller
                    </span>
                  )}
                </div>
                <div className="p-2">
                  <p className="text-xs font-semibold text-stone-800 line-clamp-2 leading-snug">{p.name}</p>
                  <p className="text-xs text-stone-400 mt-0.5">{p.retailer_name}</p>
                  {p.price != null && (
                    <p className="text-xs font-medium text-stone-700 mt-0.5">
                      ${p.price.toFixed(2)}
                    </p>
                  )}
                </div>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Progress Bar ──────────────────────────────────────────────────────────────

function ProgressBar({ done, total, status }: { done: number; total: number; status: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  const isGenerating = status === "generating";
  return (
    <div className="space-y-1">
      <div className="flex justify-between text-xs text-stone-500">
        <span>{isGenerating ? "Generating trend report…" : `Analysing photos (${done}/${total})`}</span>
        <span>{pct}%</span>
      </div>
      <div className="w-full bg-stone-100 rounded-full h-1.5">
        <div
          className={clsx("h-1.5 rounded-full transition-all duration-500", isGenerating ? "bg-violet-500 animate-pulse" : "bg-amber-500")}
          style={{ width: `${isGenerating ? 100 : pct}%` }}
        />
      </div>
    </div>
  );
}

// ── Main Page ─────────────────────────────────────────────────────────────────

export default function InStorePage() {
  const [sessions, setSessions] = useState<InStoreSession[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [detail, setDetail] = useState<InStoreSession | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [activeGen, setActiveGen] = useState<number>(1);
  const pollRef = useRef<NodeJS.Timeout | null>(null);

  const fetchSessions = useCallback(async () => {
    try {
      const data = await api.instore.listSessions();
      setSessions(data);
      if (!selectedId && data.length > 0) setSelectedId(data[0].id);
    } catch { /* ignore */ }
  }, [selectedId]);

  const fetchDetail = useCallback(async (id: number) => {
    try {
      const data = await api.instore.getSession(id);
      setDetail(data);
      // Auto-advance to newest generation
      const latestGen = data.generation_count ?? 1;
      setActiveGen(latestGen);
      return data.status;
    } catch { return "failed"; }
  }, []);

  const startPolling = useCallback((id: number) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      const status = await fetchDetail(id);
      await fetchSessions();
      if (status === "done" || status === "failed") clearInterval(pollRef.current!);
    }, 3000);
  }, [fetchDetail, fetchSessions]);

  // Initial load
  useEffect(() => { fetchSessions(); }, [fetchSessions]);

  // Poll while active session is in progress
  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!selectedId) return;

    fetchDetail(selectedId);

    const shouldPoll = detail
      ? ["pending", "analysing", "generating"].includes(detail.status)
      : true;

    if (shouldPoll) startPolling(selectedId);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId, detail?.status]);

  const handleTryAgain = useCallback(async (sessionId: number) => {
    try {
      const res = await fetch(`${API_BASE}/api/instore/sessions/${sessionId}/regenerate`, { method: "POST" });
      if (!res.ok) return;
      setDetail((prev) => prev ? { ...prev, status: "generating" } : prev);
      setSessions((prev) => prev.map((s) => s.id === sessionId ? { ...s, status: "generating" } : s));
      startPolling(sessionId);
    } catch { /* ignore */ }
  }, [startPolling]);

  const handleUpload = useCallback(async (files: File[]) => {
    setUploading(true);
    setUploadError(null);
    try {
      // Start a new UPLOADING session (finalise=false) so the user can batch more
      const result = await api.instore.createSession(files, { finalise: false });
      await fetchSessions();
      setSelectedId(result.id);
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [fetchSessions]);

  const handleAddMore = useCallback(async (sessionId: number, files: File[]) => {
    setUploading(true);
    setUploadError(null);
    try {
      await api.instore.addUploads(sessionId, files);
      await fetchDetail(sessionId);
      await fetchSessions();
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
    }
  }, [fetchDetail, fetchSessions]);

  const handleFinalise = useCallback(async (sessionId: number) => {
    if (!confirm("Done uploading? Claude will start analysing your photos and generating trends.")) return;
    try {
      await api.instore.finaliseSession(sessionId);
      await fetchDetail(sessionId);
      await fetchSessions();
      startPolling(sessionId);
    } catch (e: unknown) {
      setUploadError(e instanceof Error ? e.message : "Finalise failed");
    }
  }, [fetchDetail, fetchSessions, startPolling]);

  const handleDelete = useCallback(async (id: number) => {
    if (!confirm("Delete this session and all its photos?")) return;
    await api.instore.deleteSession(id);
    setSessions((s) => s.filter((x) => x.id !== id));
    if (selectedId === id) {
      const remaining = sessions.filter((x) => x.id !== id);
      setSelectedId(remaining.length > 0 ? remaining[0].id : null);
      setDetail(null);
    }
  }, [selectedId, sessions]);

  const handleDeletePrevious = useCallback(async () => {
    // Keep the most recent session (index 0, sorted by created_at desc), delete all others
    const toDelete = sessions.slice(1);
    if (toDelete.length === 0) return;
    if (!confirm(`Delete ${toDelete.length} previous session${toDelete.length !== 1 ? "s" : ""} and all their photos?`)) return;
    await Promise.all(toDelete.map((s) => api.instore.deleteSession(s.id)));
    setSessions((prev) => prev.slice(0, 1));
    if (selectedId && toDelete.some((s) => s.id === selectedId)) {
      setSelectedId(sessions[0]?.id ?? null);
      setDetail(null);
    }
  }, [sessions, selectedId]);

  const isActive = detail && ["uploading", "pending", "analysing", "generating"].includes(detail.status);
  const isUploadingMode = detail?.status === "uploading";

  return (
    <div className="min-h-screen bg-stone-50">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">
        <div className="flex items-center justify-between mb-6">
          <div>
            <h1 className="text-2xl font-bold text-stone-900">In-store Products</h1>
            <p className="text-sm text-stone-500 mt-0.5">Upload product photos — Claude analyses each one and identifies trends</p>
          </div>
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-[300px_1fr] gap-6">
          {/* ── Left sidebar: sessions + upload ── */}
          <div className="space-y-4">
            {!detail || detail.status !== "uploading" ? (
              <UploadZone onUpload={handleUpload} />
            ) : (
              <div className="bg-stone-100 rounded-xl p-4 text-center text-xs text-stone-500">
                Add more photos to your current session in the main panel →
              </div>
            )}

            {uploading && (
              <div className="text-xs text-amber-600 animate-pulse text-center">Uploading…</div>
            )}
            {uploadError && (
              <div className="text-xs text-red-600 text-center">{uploadError}</div>
            )}

            {sessions.length > 0 && (
              <div className="bg-white rounded-xl border border-stone-200 overflow-hidden">
                <div className="px-3 py-2 border-b border-stone-100 flex items-center justify-between gap-2">
                  <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide">Sessions</p>
                  {sessions.length > 1 && (
                    <button
                      onClick={handleDeletePrevious}
                      className="text-xs text-red-400 hover:text-red-600 transition-colors whitespace-nowrap"
                    >
                      Delete Previous Sessions
                    </button>
                  )}
                </div>
                <div className="divide-y divide-stone-100 max-h-[400px] overflow-y-auto">
                  {sessions.map((s) => (
                    <button
                      key={s.id}
                      onClick={() => setSelectedId(s.id)}
                      className={clsx(
                        "w-full text-left px-3 py-2.5 hover:bg-stone-50 transition-colors",
                        selectedId === s.id && "bg-stone-50"
                      )}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-sm font-medium text-stone-800 truncate">
                          {s.name || new Date(s.created_at).toLocaleDateString("en-AU", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" })}
                        </span>
                        <span className={clsx("text-xs px-1.5 py-0.5 rounded-full flex-shrink-0", STATUS_CLASS[s.status])}>
                          {STATUS_LABEL[s.status]}
                        </span>
                      </div>
                      <p className="text-xs text-stone-400 mt-0.5">{s.product_count} photo{s.product_count !== 1 ? "s" : ""}</p>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* ── Right panel: session detail ── */}
          <div>
            {!selectedId ? (
              <div className="bg-white rounded-xl border border-stone-200 p-12 text-center">
                <div className="text-4xl mb-3">🛍️</div>
                <h2 className="text-lg font-semibold text-stone-700 mb-1">No sessions yet</h2>
                <p className="text-sm text-stone-400">Upload product photos to get started</p>
              </div>
            ) : !detail ? (
              <div className="bg-white rounded-xl border border-stone-200 p-12 text-center">
                <div className="text-sm text-stone-400 animate-pulse">Loading…</div>
              </div>
            ) : (
              <div className="space-y-6">
                {/* Session header */}
                <div className="bg-white rounded-xl border border-stone-200 p-4 flex items-center justify-between gap-4">
                  <div>
                    <h2 className="text-base font-bold text-stone-900">
                      {detail.name || new Date(detail.created_at).toLocaleDateString("en-AU", { weekday: "long", day: "numeric", month: "long", year: "numeric" })}
                    </h2>
                    <p className="text-xs text-stone-500 mt-0.5">
                      {detail.product_count} photo{detail.product_count !== 1 ? "s" : ""} · {detail.done_count} analysed
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={clsx("px-2.5 py-1 rounded-full text-xs font-medium", STATUS_CLASS[detail.status])}>
                      {STATUS_LABEL[detail.status]}
                    </span>
                    <button
                      onClick={() => handleDelete(detail.id)}
                      className="text-xs text-stone-400 hover:text-red-500 transition-colors"
                    >
                      Delete
                    </button>
                  </div>
                </div>

                {isUploadingMode && (
                  <div className="bg-sky-50 border border-sky-200 rounded-xl p-4 space-y-3">
                    <div className="flex items-center justify-between gap-2">
                      <div>
                        <p className="text-sm font-semibold text-sky-900">Batch upload mode</p>
                        <p className="text-xs text-sky-700 mt-0.5">
                          Add more photos in batches to avoid upload timeouts. Click &ldquo;Finish uploading&rdquo; when done.
                        </p>
                      </div>
                    </div>

                    <UploadZone onUpload={(files) => handleAddMore(detail.id, files)} />

                    <div className="flex gap-2 justify-end">
                      <button
                        onClick={() => handleFinalise(detail.id)}
                        disabled={uploading || detail.product_count === 0}
                        className={clsx(
                          "px-4 py-2 rounded-lg text-sm font-medium transition-colors",
                          uploading || detail.product_count === 0
                            ? "bg-stone-200 text-stone-400 cursor-not-allowed"
                            : "bg-emerald-600 text-white hover:bg-emerald-700"
                        )}
                      >
                        ✓ Finish uploading ({detail.product_count} photo{detail.product_count !== 1 ? "s" : ""})
                      </button>
                    </div>
                  </div>
                )}

                {/* Progress bar while running */}
                {isActive && !isUploadingMode && (
                  <div className="bg-white rounded-xl border border-stone-200 p-4">
                    <ProgressBar done={detail.done_count} total={detail.product_count} status={detail.status} />
                  </div>
                )}

                {/* Error */}
                {detail.status === "failed" && detail.error_message && (
                  <div className="bg-red-50 border border-red-200 rounded-xl p-4 text-sm text-red-700">
                    {detail.error_message}
                  </div>
                )}

                {/* Trend report */}
                {detail.trend_report && detail.trend_report.length > 0 && (() => {
                  const allReports = detail.trend_report_all || [];
                  const genNums = allReports.map((r) => r.generation).sort((a, b) => a - b);
                  const latestGen = detail.generation_count ?? 1;
                  // Active generation's trends + lens
                  const activeEntry = allReports.find((r) => r.generation === activeGen);
                  const visibleTrends = activeEntry ? activeEntry.trends : detail.trend_report;
                  const activeLens = activeEntry?.lens;

                  return (
                    <div>
                      {/* Header row */}
                      <div className="flex items-center justify-between gap-2 mb-3">
                        <h3 className="text-sm font-bold text-stone-700 uppercase tracking-wide">
                          Trend Report — {visibleTrends.length} trends identified
                        </h3>
                        {detail.status === "done" && (
                          <button
                            onClick={() => handleTryAgain(detail.id)}
                            className="flex items-center gap-1.5 px-3 py-1.5 bg-stone-50 border border-stone-300 hover:bg-stone-100 text-stone-700 rounded-lg text-xs font-medium transition-colors whitespace-nowrap"
                          >
                            <span>🔄</span>
                            <span>Try Again</span>
                          </button>
                        )}
                      </div>

                      {/* Generation tabs */}
                      {genNums.length > 1 && (
                        <div className="flex gap-1.5 flex-wrap mb-3">
                          {genNums.map((gen) => (
                            <button
                              key={gen}
                              onClick={() => setActiveGen(gen)}
                              className={clsx(
                                "px-3 py-1 rounded-lg text-xs font-medium transition-colors border",
                                activeGen === gen
                                  ? "bg-stone-800 border-stone-800 text-white"
                                  : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
                              )}
                            >
                              {gen === latestGen ? `Set ${gen} ✨` : `Set ${gen}`}
                            </button>
                          ))}
                        </div>
                      )}

                      {/* Active lens label */}
                      {activeLens && (
                        <p className="text-xs text-stone-400 italic mb-3">
                          Lens: {activeLens.split(" — ")[0]}
                        </p>
                      )}

                      <div className="space-y-4">
                        {visibleTrends.map((trend, i) => (
                          <TrendCard key={i} trend={trend} sessionId={detail.id} index={i} />
                        ))}
                      </div>
                    </div>
                  );
                })()}

              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
