"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ── Types ─────────────────────────────────────────────────────────────────────

interface InspiredProduct {
  id: number;
  name: string;
  retailer_name: string;
  url: string;
  image_url: string | null;
  // Only present on new-shape clusters (2026-08-06). Ranked to the front
  // so the card's hero mosaic promotes best-sellers.
  is_best_seller?: boolean;
}

interface AldiIdea {
  id: number;
  generation: number;
  position: number;
  name: string;
  description: string;
  category: string;
  price_point: string;
  rationale: string;
  inspired_by_products: InspiredProduct[];
  // New shape (2026-08-06):
  //   kind = 'cluster'      → sub-theme of real catalogue products (new UI)
  //   kind = 'out_of_scope'  → single "mood board out of scope" explainer card
  //   kind = null / other    → legacy synthesized idea (old IdeaCard UI)
  kind?: string | null;
  filter_keywords?: string[];
}

interface AldiUploadDoc {
  id: number;
  filename: string;
  file_type: string;
  status: string;
  themes: string[];
  colour_palette: string[];
  colour_hex: string[];
  key_materials: string[];
  key_prints: string[];
  product_categories: string[];
  season_occasion: string | null;
  mood_descriptors: string[];
  error_message: string | null;
}

interface AldiSession {
  id: number;
  status: "uploading" | "pending" | "analysing" | "generating" | "done" | "failed";
  created_at: string;
  upload_count: number;
  idea_count: number;
  // Detail fields
  themes?: string[];
  colour_palette?: string[];
  colour_hex?: string[];
  key_materials?: string[];
  key_prints?: string[];
  product_categories?: string[];
  season_occasion?: string | null;
  mood_descriptors?: string[];
  // Claude-decided keywords used to pre-filter the catalogue for a
  // thematic mood board. Empty for stylistic boards.
  filter_keywords?: string[];
  // How many products matched the theme in the whole 156k catalogue on
  // the last generation. Drives the low-match warning banner.
  similar_products_count?: number | null;
  error_message?: string | null;
  uploads?: AldiUploadDoc[];
  ideas?: AldiIdea[];
  generation_count?: number;
  latest_generation?: number;
}

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  uploading: "Uploading…",
  pending: "Queued",
  analysing: "Analysing…",
  generating: "Generating ideas…",
  done: "Done",
  failed: "Failed",
};

const STATUS_COLOURS: Record<string, string> = {
  uploading: "bg-sky-100 text-sky-700",
  pending: "bg-stone-100 text-stone-600",
  analysing: "bg-amber-100 text-amber-700 animate-pulse",
  generating: "bg-amber-100 text-amber-700 animate-pulse",
  done: "bg-green-100 text-green-700",
  failed: "bg-red-100 text-red-700",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <span className={clsx("px-2 py-0.5 rounded-full text-xs font-medium", STATUS_COLOURS[status] ?? "bg-stone-100 text-stone-600")}>
      {STATUS_LABELS[status] ?? status}
    </span>
  );
}

// ── Upload zone ───────────────────────────────────────────────────────────────

function UploadZone({ onSessionCreated }: { onSessionCreated: (s: AldiSession) => void }) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = async (files: File[]) => {
    if (!files.length) return;
    setError(null);
    setUploading(true);
    try {
      const form = new FormData();
      for (const file of files) form.append("files", file);
      const res = await fetch(`${API_BASE}/api/aldi/sessions?finalise=false`, { method: "POST", body: form });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || `Upload failed (${res.status})`);
      }
      const session: AldiSession = await res.json();
      onSessionCreated(session);
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(Array.from(e.dataTransfer.files));
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => !uploading && inputRef.current?.click()}
      className={clsx(
        "border-2 border-dashed rounded-xl p-10 text-center cursor-pointer transition-colors",
        dragging ? "border-amber-400 bg-amber-50" : "border-stone-300 hover:border-stone-400 bg-white",
        uploading && "pointer-events-none opacity-60",
      )}
    >
      <input
        ref={inputRef}
        type="file"
        accept=".pdf,.jpg,.jpeg,.png"
        multiple
        className="hidden"
        onChange={(e) => handleFiles(Array.from(e.target.files || []))}
      />
      <div className="text-4xl mb-3">{uploading ? "⏳" : "📂"}</div>
      <p className="font-medium text-stone-700">
        {uploading ? "Uploading documents…" : "Drop trend documents here or click to browse"}
      </p>
      <p className="text-sm text-stone-400 mt-1">
        Select multiple files to analyse them together as one session · PDF, JPEG or PNG · max 20 MB each
      </p>
      {error && <p className="mt-3 text-xs text-red-500">{error}</p>}
    </div>
  );
}

// ── Session card (sidebar) ─────────────────────────────────────────────────────

function SessionCard({
  session,
  selected,
  onClick,
  onDelete,
}: {
  session: AldiSession;
  selected: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  const docLabel = session.upload_count === 1 ? "1 document" : `${session.upload_count} documents`;
  return (
    <div
      onClick={onClick}
      className={clsx(
        "bg-white rounded-xl border p-4 cursor-pointer transition-all hover:shadow-md",
        selected ? "border-amber-400 ring-2 ring-amber-200" : "border-stone-200",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-stone-800">{docLabel}</p>
          <p className="text-xs text-stone-400 mt-0.5">
            {new Date(session.created_at).toLocaleDateString("en-AU", {
              day: "numeric", month: "short", year: "numeric",
            })}
          </p>
          <div className="mt-2">
            <StatusBadge status={session.status} />
          </div>
          {session.status === "done" && (
            <p className="text-xs text-stone-500 mt-1">{session.idea_count} ideas generated</p>
          )}
          {session.status === "analysing" && session.upload_count > 1 && (
            <p className="text-xs text-amber-600 mt-1">Analysing documents…</p>
          )}
          {session.status === "generating" && (
            <p className="text-xs text-amber-600 mt-1">Generating ideas…</p>
          )}
        </div>
        <button
          onClick={(e) => { e.stopPropagation(); onDelete(); }}
          className="text-stone-300 hover:text-red-400 transition-colors text-lg flex-shrink-0"
          title="Delete session"
        >
          ×
        </button>
      </div>
    </div>
  );
}

// ── Colour swatch ─────────────────────────────────────────────────────────────

function ColourSwatch({ hex, label }: { hex?: string; label: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <div
        className="w-4 h-4 rounded-full border border-stone-200 flex-shrink-0"
        style={{ backgroundColor: hex || "#e5e7eb" }}
      />
      <span className="text-xs text-stone-600 capitalize">{label}</span>
    </div>
  );
}

// ── Analysis panel (per-document) ─────────────────────────────────────────────

function DocAnalysisPanel({ doc }: { doc: AldiUploadDoc }) {
  const fileUrl = `${API_BASE}/api/aldi/uploads/${doc.id}/file`;
  const isProcessing = ["pending", "analysing"].includes(doc.status);

  return (
    <div className="space-y-4">
      {/* Document preview */}
      <div className="bg-white rounded-xl border border-stone-200 overflow-hidden">
        {doc.file_type === "pdf" ? (
          <iframe src={fileUrl} className="w-full h-72" title={doc.filename} />
        ) : (
          <img src={fileUrl} alt={doc.filename} className="w-full max-h-72 object-contain bg-stone-50" />
        )}
      </div>

      {isProcessing && (
        <div className="text-center py-4 text-stone-400 text-sm animate-pulse">Analysing document…</div>
      )}

      {doc.status === "failed" && doc.error_message && (
        <div className="bg-red-50 border border-red-200 rounded-xl p-3">
          <p className="text-xs text-red-600">{doc.error_message}</p>
        </div>
      )}

      {!isProcessing && doc.status !== "failed" && (
        <div className="bg-white rounded-xl border border-stone-200 p-4 space-y-3">
          {doc.season_occasion && (
            <span className="inline-block px-2.5 py-1 bg-amber-100 text-amber-800 rounded-full text-xs font-semibold">
              {doc.season_occasion}
            </span>
          )}
          {doc.themes.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Themes</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.themes.map((t) => (
                  <span key={t} className="px-2 py-0.5 bg-stone-100 text-stone-700 rounded-full text-xs">{t}</span>
                ))}
              </div>
            </div>
          )}
          {doc.colour_palette.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Colour Palette</p>
              <div className="flex flex-wrap gap-2">
                {doc.colour_palette.map((c, i) => (
                  <ColourSwatch key={c} hex={doc.colour_hex?.[i]} label={c} />
                ))}
              </div>
            </div>
          )}
          {doc.key_materials.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Materials</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.key_materials.map((m) => (
                  <span key={m} className="px-2 py-0.5 bg-stone-100 text-stone-700 rounded text-xs capitalize">{m}</span>
                ))}
              </div>
            </div>
          )}
          {doc.key_prints.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Prints & Patterns</p>
              <div className="flex flex-wrap gap-1.5">
                {doc.key_prints.map((p) => (
                  <span key={p} className="px-2 py-0.5 bg-amber-50 text-amber-800 border border-amber-200 rounded text-xs">{p}</span>
                ))}
              </div>
            </div>
          )}
          {doc.mood_descriptors.length > 0 && (
            <p className="text-xs text-stone-500 italic">{doc.mood_descriptors.join(" · ")}</p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Idea card ─────────────────────────────────────────────────────────────────

// ── New (2026-08-06) cluster shape ────────────────────────────────────────────

interface ClusterProduct {
  id: number;
  name: string;
  url: string;
  price: number | null;
  primary_image_url: string | null;
  retailer_name: string;
  retailer_slug: string;
  is_best_seller: boolean;
}

/**
 * AldiClusterCard — new sub-theme-of-real-products card. Shape mirrors
 * Product Trends' TrendCard: image mosaic (best-sellers first) + name +
 * description + "View all N products" button that opens the paginated
 * modal against the live catalogue.
 */
function AldiClusterCard({ idea }: { idea: AldiIdea }) {
  const [modalOpen, setModalOpen] = useState(false);
  const products = idea.inspired_by_products || [];
  const heroes = products.slice(0, 3);
  const bestSellerBadge = products.some((p) => p.is_best_seller);
  return (
    <>
      <article className="bg-white rounded-xl border border-stone-200 overflow-hidden flex flex-col">
        <div className="h-40 flex gap-0.5 overflow-hidden bg-stone-100">
          {heroes.length === 0 ? (
            <div className="flex-1 flex items-center justify-center text-stone-300 text-4xl">📦</div>
          ) : heroes.length === 1 ? (
            <img src={heroes[0].image_url ?? ""} alt={heroes[0].name}
                 className="w-full h-full object-cover" title={`${heroes[0].retailer_name}: ${heroes[0].name}`} />
          ) : (
            <>
              <div className="flex-[3] overflow-hidden">
                <img src={heroes[0].image_url ?? ""} alt={heroes[0].name}
                     className="w-full h-full object-cover" title={`${heroes[0].retailer_name}: ${heroes[0].name}`} />
              </div>
              <div className="flex-[2] flex flex-col gap-0.5">
                {heroes.slice(1, 3).map((p) => (
                  <div key={p.id} className="flex-1 overflow-hidden">
                    <img src={p.image_url ?? ""} alt={p.name}
                         className="w-full h-full object-cover" title={`${p.retailer_name}: ${p.name}`} />
                  </div>
                ))}
                {heroes.length < 3 && <div className="flex-1 bg-stone-200" />}
              </div>
            </>
          )}
        </div>
        <div className="p-4 space-y-2 flex-1 flex flex-col">
          <div className="flex items-start justify-between gap-2">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-stone-400">{idea.category}</p>
              <h3 className="text-sm font-semibold text-stone-900 leading-snug mt-0.5">{idea.name}</h3>
            </div>
            {bestSellerBadge && (
              <span className="flex-shrink-0 px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded text-[10px] font-semibold">★</span>
            )}
          </div>
          <p className="text-xs text-stone-600 leading-relaxed line-clamp-3">{idea.description}</p>
          <div className="flex-1" />
          <button
            onClick={() => setModalOpen(true)}
            className="mt-2 w-full text-xs font-medium text-amber-800 bg-amber-50 hover:bg-amber-100 border border-amber-200 rounded-lg px-3 py-2 transition-colors"
          >
            View all matching products →
          </button>
        </div>
      </article>
      {modalOpen && (
        <AldiClusterProductsModal idea={idea} onClose={() => setModalOpen(false)} />
      )}
    </>
  );
}

/**
 * Single-card "mood board out of scope" explainer. Shown when the
 * semantic search matched fewer than 5 products in the whole catalogue —
 * clustering would invent nonsense, so we serve an honest summary instead.
 */
function OutOfScopeCard({ idea }: { idea: AldiIdea }) {
  return (
    <div className="col-span-full bg-red-50 border border-red-200 rounded-xl p-5 space-y-2">
      <p className="text-sm font-semibold text-red-800">⚠️ {idea.name}</p>
      <p className="text-xs text-red-700 leading-relaxed">{idea.description}</p>
    </div>
  );
}

/**
 * Paginated modal listing every catalogue product matching the cluster's
 * keywords. Best-sellers first, "Only best sellers" toggle, Load More.
 * Same shape as Product Trends' TrendProductsModal.
 */
function AldiClusterProductsModal({ idea, onClose }: { idea: AldiIdea; onClose: () => void }) {
  const PAGE_SIZE = 48;
  const [products, setProducts] = useState<ClusterProduct[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [onlyBestSellers, setOnlyBestSellers] = useState(false);

  const loadFirstPage = useCallback(async () => {
    setLoading(true);
    setOffset(0);
    try {
      const qs = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: "0",
        ...(onlyBestSellers ? { only_best_sellers: "true" } : {}),
      });
      const res = await fetch(`${API_BASE}/api/aldi/ideas/${idea.id}/products?${qs}`);
      const data = await res.json();
      setProducts(data.items || []);
      setTotal(data.total ?? 0);
    } catch {
      setProducts([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [idea.id, onlyBestSellers]);

  useEffect(() => { loadFirstPage(); }, [loadFirstPage]);

  async function loadMore() {
    if (loadingMore || total === null || products.length >= total) return;
    setLoadingMore(true);
    const next = offset + PAGE_SIZE;
    try {
      const qs = new URLSearchParams({
        limit: String(PAGE_SIZE),
        offset: String(next),
        ...(onlyBestSellers ? { only_best_sellers: "true" } : {}),
      });
      const res = await fetch(`${API_BASE}/api/aldi/ideas/${idea.id}/products?${qs}`);
      const data = await res.json();
      setProducts((prev) => [...prev, ...(data.items || [])]);
      setOffset(next);
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const showLoadMore = total !== null && products.length < total && !loading;

  return (
    <div
      className="fixed inset-0 z-50 bg-stone-900/60 flex items-center justify-center p-4"
      onClick={onClose} role="dialog" aria-modal="true"
    >
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-6xl max-h-[90vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between p-6 border-b border-stone-200 gap-4">
          <div className="min-w-0">
            <p className="text-xs text-stone-400 uppercase tracking-wider">{idea.category}</p>
            <h2 className="text-xl font-bold text-stone-900 mt-0.5">{idea.name}</h2>
            <p className="text-sm text-stone-500 mt-1">
              {loading
                ? "Loading matching products…"
                : total === null
                  ? ""
                  : `Showing ${products.length.toLocaleString()} of ${total.toLocaleString()} matching products`}
            </p>
          </div>
          <button onClick={onClose} className="text-stone-400 hover:text-stone-900 text-2xl leading-none px-2" aria-label="Close">×</button>
        </div>
        <div className="px-6 py-3 border-b border-stone-100 flex items-center gap-4 flex-wrap">
          <label className="flex items-center gap-2 text-sm text-stone-700 cursor-pointer">
            <input type="checkbox" checked={onlyBestSellers} onChange={(e) => setOnlyBestSellers(e.target.checked)} className="rounded" />
            Only best sellers
          </label>
          {onlyBestSellers && (
            <p className="text-xs text-stone-400">Note: not every retailer publishes best-seller flags.</p>
          )}
        </div>
        <div className="flex-1 overflow-y-auto p-6">
          {loading ? (
            <p className="text-center text-stone-400 py-16">Loading products…</p>
          ) : products.length === 0 ? (
            <p className="text-center text-stone-400 py-16">
              {onlyBestSellers ? "No best-seller products in this cluster." : "No products match this cluster."}
            </p>
          ) : (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {products.map((p) => (
                  <a key={p.id} href={p.url} target="_blank" rel="noopener noreferrer"
                     className="group block bg-white border border-stone-200 hover:border-amber-300 rounded-lg overflow-hidden transition-colors">
                    <div className="aspect-square bg-stone-100 relative">
                      {p.primary_image_url ? (
                        <img src={p.primary_image_url} alt={p.name} className="w-full h-full object-cover" />
                      ) : (
                        <div className="w-full h-full flex items-center justify-center text-stone-300 text-3xl">📦</div>
                      )}
                      {p.is_best_seller && (
                        <span className="absolute top-1 right-1 px-1.5 py-0.5 bg-amber-100 text-amber-800 rounded text-[10px] font-semibold">★</span>
                      )}
                    </div>
                    <div className="p-2 space-y-0.5">
                      <p className="text-xs text-stone-500 truncate">{p.retailer_name}</p>
                      <p className="text-xs text-stone-800 leading-snug line-clamp-2">{p.name}</p>
                      {p.price != null && <p className="text-xs text-stone-500">${p.price.toFixed(2)}</p>}
                    </div>
                  </a>
                ))}
              </div>
              {showLoadMore && (
                <div className="pt-6 text-center">
                  <button
                    onClick={loadMore}
                    disabled={loadingMore}
                    className={clsx(
                      "px-5 py-2 rounded-lg text-sm font-medium transition-colors",
                      loadingMore
                        ? "bg-stone-100 text-stone-400 cursor-not-allowed"
                        : "bg-stone-900 text-white hover:bg-stone-700",
                    )}
                  >
                    {loadingMore ? "Loading…" : `Load ${Math.min(PAGE_SIZE, (total ?? 0) - products.length)} more`}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Legacy synthesized-idea card (kept for backward compat) ───────────────────

function IdeaCard({ idea }: { idea: AldiIdea }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="bg-white rounded-xl border border-stone-200 p-4 space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <span className="text-xs font-medium text-stone-400 uppercase tracking-wide">{idea.category}</span>
          <h3 className="text-sm font-semibold text-stone-900 mt-0.5 leading-snug">{idea.name}</h3>
        </div>
        <span className="flex-shrink-0 px-2 py-0.5 bg-amber-50 border border-amber-200 rounded-full text-xs font-semibold text-amber-700">
          {idea.price_point}
        </span>
      </div>
      <p className="text-xs text-stone-600 leading-relaxed">{idea.description}</p>
      <button
        onClick={() => setExpanded((v) => !v)}
        className="text-xs text-stone-400 hover:text-stone-700 underline"
      >
        {expanded ? "Hide rationale" : "Show rationale"}
      </button>
      {expanded && (
        <p className="text-xs text-stone-500 italic leading-relaxed border-l-2 border-amber-200 pl-3">
          {idea.rationale}
        </p>
      )}
      {idea.inspired_by_products.length > 0 && (
        <div>
          <p className="text-xs text-stone-400 mb-1.5">Inspired by</p>
          <div className="flex gap-2 flex-wrap">
            {idea.inspired_by_products.map((p) => (
              <a key={p.id} href={p.url} target="_blank" rel="noopener noreferrer" title={`${p.name} — ${p.retailer_name}`} className="group flex flex-col items-center gap-1">
                {p.image_url ? (
                  <img src={p.image_url} alt={p.name} className="w-12 h-12 object-cover rounded-lg border border-stone-200 group-hover:border-amber-300 transition-colors" />
                ) : (
                  <div className="w-12 h-12 bg-stone-100 rounded-lg border border-stone-200 flex items-center justify-center text-stone-300 text-lg">⌂</div>
                )}
                <span className="text-xs text-stone-400 text-center max-w-12 truncate">{p.retailer_name}</span>
              </a>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Batch upload panel (shown while session is UPLOADING) ─────────────────────

function BatchUploadPanel({
  session,
  onAddMore,
  onFinalise,
}: {
  session: AldiSession;
  onAddMore: (files: File[]) => Promise<void>;
  onFinalise: () => void;
}) {
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const handleFiles = async (files: File[]) => {
    if (!files.length) return;
    setError(null);
    setUploading(true);
    try {
      await onAddMore(files);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Upload failed");
    } finally {
      setUploading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    handleFiles(Array.from(e.dataTransfer.files));
  };

  return (
    <div className="bg-sky-50 border border-sky-200 rounded-xl p-4 space-y-3 mt-4">
      <div>
        <p className="text-sm font-semibold text-sky-900">Batch upload mode</p>
        <p className="text-xs text-sky-700 mt-0.5">
          Add more documents in batches to avoid upload timeouts. Click &ldquo;Finish uploading&rdquo; when done.
        </p>
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        onClick={() => !uploading && inputRef.current?.click()}
        className={clsx(
          "border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-colors bg-white",
          dragging ? "border-amber-400 bg-amber-50" : "border-stone-300 hover:border-stone-400",
          uploading && "pointer-events-none opacity-60",
        )}
      >
        <input
          ref={inputRef}
          type="file"
          accept=".pdf,.jpg,.jpeg,.png"
          multiple
          className="hidden"
          onChange={(e) => handleFiles(Array.from(e.target.files || []))}
        />
        <div className="text-2xl mb-1">{uploading ? "⏳" : "📂"}</div>
        <p className="text-sm font-medium text-stone-700">
          {uploading ? "Uploading…" : "Drop more documents here or click to browse"}
        </p>
        <p className="text-xs text-stone-400 mt-0.5">PDF, JPEG or PNG · max 20 MB each</p>
        {error && <p className="mt-2 text-xs text-red-500">{error}</p>}
      </div>

      <div className="flex justify-end">
        <button
          onClick={onFinalise}
          disabled={uploading || session.upload_count === 0}
          className={clsx(
            "px-4 py-2 rounded-lg text-sm font-medium transition-colors",
            uploading || session.upload_count === 0
              ? "bg-stone-200 text-stone-400 cursor-not-allowed"
              : "bg-emerald-600 text-white hover:bg-emerald-700",
          )}
        >
          ✓ Finish uploading ({session.upload_count} document{session.upload_count !== 1 ? "s" : ""})
        </button>
      </div>
    </div>
  );
}

// ── Session detail view ────────────────────────────────────────────────────────

function SessionDetailView({
  session,
  onTryAgain,
  onAddMore,
  onFinalise,
  onDeleteGeneration,
}: {
  session: AldiSession;
  onTryAgain: () => void;
  onAddMore: (files: File[]) => Promise<void>;
  onFinalise: () => void;
  onDeleteGeneration: (generation: number) => Promise<void>;
}) {
  const [activeDocIdx, setActiveDocIdx] = useState(0);
  const uploads = session.uploads || [];
  const allIdeas = session.ideas || [];
  const isProcessing = ["pending", "analysing", "generating"].includes(session.status);
  const isGenerating = session.status === "generating";
  const activeDoc = uploads[activeDocIdx];

  // Generation state
  const latestGen = session.latest_generation ?? (allIdeas.length > 0 ? Math.max(...allIdeas.map((i) => i.generation)) : 1);
  const [activeGen, setActiveGen] = useState<number>(latestGen);

  // When a new generation arrives, auto-switch to it
  useEffect(() => {
    setActiveGen(latestGen);
  }, [latestGen]);

  const ideas = isProcessing ? allIdeas : allIdeas.filter((i) => i.generation === activeGen);
  const genNums = Array.from(new Set(allIdeas.map((i) => i.generation))).sort((a, b) => a - b);

  // If the active tab was deleted (its ideas are gone), fall back to
  // the highest remaining generation. Otherwise the right column shows
  // "No ideas were generated." on a tab that no longer exists.
  useEffect(() => {
    if (genNums.length > 0 && !genNums.includes(activeGen)) {
      setActiveGen(genNums[genNums.length - 1]);
    }
  }, [genNums, activeGen]);

  // Progress tracking
  const total = uploads.length;
  const analysed = uploads.filter((u) => ["done", "failed"].includes(u.status)).length;
  const failed = uploads.filter((u) => u.status === "failed").length;
  const progressPct = total > 0 ? Math.round((analysed / total) * 100) : 0;
  const isAnalysing = session.status === "analysing" || session.status === "pending";

  const docStatusIcon = (status: string) => {
    if (status === "done") return "✓";
    if (status === "failed") return "✗";
    if (status === "analysing") return "⋯";
    return "·";
  };
  const docStatusColour = (status: string, active: boolean) => {
    if (active) return "bg-stone-900 text-white";
    if (status === "done") return "bg-green-50 border-green-200 text-green-700 hover:bg-green-100";
    if (status === "failed") return "bg-red-50 border-red-200 text-red-600 hover:bg-red-100";
    if (status === "analysing") return "bg-amber-50 border-amber-200 text-amber-700 animate-pulse";
    return "bg-white border-stone-200 text-stone-500 hover:bg-stone-50";
  };

  const isUploadingMode = session.status === "uploading";

  return (
    <>
      {isUploadingMode && (
        <BatchUploadPanel
          session={session}
          onAddMore={onAddMore}
          onFinalise={onFinalise}
        />
      )}
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 mt-4">
      {/* Left: document tabs + per-doc analysis */}
      <div className="space-y-4">
        {/* Progress bar (shown while analysing) */}
        {(isAnalysing || isGenerating) && total > 1 && (
          <div className="bg-white rounded-xl border border-stone-200 p-4 space-y-2">
            <div className="flex items-center justify-between text-xs">
              <span className="font-medium text-stone-700">
                {isGenerating
                  ? "Generating combined ideas…"
                  : `Analysing documents — ${analysed} of ${total} done`}
              </span>
              <span className="text-stone-400">{isGenerating ? "100%" : `${progressPct}%`}</span>
            </div>
            <div className="h-2 bg-stone-100 rounded-full overflow-hidden">
              <div
                className={clsx(
                  "h-full rounded-full transition-all duration-700",
                  isGenerating ? "bg-amber-400 animate-pulse w-full" : "bg-amber-400"
                )}
                style={{ width: isGenerating ? "100%" : `${progressPct}%` }}
              />
            </div>
            {failed > 0 && (
              <p className="text-xs text-red-500">{failed} document{failed > 1 ? "s" : ""} failed to analyse</p>
            )}
          </div>
        )}

        {/* Document tabs */}
        {uploads.length > 1 && (
          <div className="flex gap-1 flex-wrap">
            {uploads.map((doc, idx) => (
              <button
                key={doc.id}
                onClick={() => setActiveDocIdx(idx)}
                className={clsx(
                  "px-3 py-1.5 rounded-lg text-xs font-medium transition-colors border flex items-center gap-1.5 max-w-44",
                  docStatusColour(doc.status, activeDocIdx === idx)
                )}
                title={doc.filename}
              >
                <span className="flex-shrink-0 text-[10px]">{docStatusIcon(doc.status)}</span>
                <span className="truncate">{doc.filename.length > 18 ? doc.filename.slice(0, 16) + "…" : doc.filename}</span>
              </button>
            ))}
          </div>
        )}

        {/* Active document analysis */}
        {activeDoc ? (
          <DocAnalysisPanel doc={activeDoc} />
        ) : (
          <div className="text-center py-8 text-stone-400 text-sm animate-pulse">Loading documents…</div>
        )}

        {/* Combined trend summary (shown once all docs analysed) */}
        {session.status === "done" && (session.themes || []).length > 0 && (
          <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 space-y-3">
            <p className="text-xs font-semibold text-amber-800 uppercase tracking-wide">Combined Trend Summary</p>
            {session.season_occasion && (
              <span className="inline-block px-2.5 py-1 bg-amber-200 text-amber-900 rounded-full text-xs font-semibold">
                {session.season_occasion}
              </span>
            )}
            {(session.themes || []).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">All Themes</p>
                <div className="flex flex-wrap gap-1.5">
                  {session.themes!.map((t) => (
                    <span key={t} className="px-2 py-0.5 bg-white text-stone-700 rounded-full text-xs border border-amber-200">{t}</span>
                  ))}
                </div>
              </div>
            )}
            {(session.colour_palette || []).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Colour Palette</p>
                <div className="flex flex-wrap gap-2">
                  {session.colour_palette!.map((c, i) => (
                    <ColourSwatch key={c} hex={session.colour_hex?.[i]} label={c} />
                  ))}
                </div>
              </div>
            )}
            {(session.key_materials || []).length > 0 && (
              <div>
                <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-1.5">Key Materials</p>
                <div className="flex flex-wrap gap-1.5">
                  {session.key_materials!.map((m) => (
                    <span key={m} className="px-2 py-0.5 bg-white text-stone-700 rounded text-xs border border-amber-200 capitalize">{m}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}

        {session.status === "failed" && session.error_message && (
          <div className="bg-red-50 border border-red-200 rounded-xl p-4">
            <p className="text-sm font-medium text-red-700">Session failed</p>
            <p className="text-xs text-red-500 mt-1">{session.error_message}</p>
          </div>
        )}
      </div>

      {/* Right: combined ideas */}
      <div>
        {/* Low-match warning banner — shown when the mood board didn't
            match many products in the catalogue, so buyers understand
            why the recommendations look thin / off-theme rather than
            assuming the tool is broken. */}
        {session.status === "done" &&
          typeof session.similar_products_count === "number" &&
          session.similar_products_count < 50 && (
          <div
            className={clsx(
              "mb-3 rounded-xl border p-3.5 text-xs space-y-1.5",
              session.similar_products_count < 5
                ? "bg-red-50 border-red-200 text-red-800"
                : "bg-amber-50 border-amber-200 text-amber-900"
            )}
          >
            <p className="font-semibold flex items-center gap-1.5">
              <span>{session.similar_products_count < 5 ? "⚠️" : "ℹ️"}</span>
              <span>
                {session.similar_products_count < 5
                  ? "Mood board may be out of scope"
                  : "Limited catalogue coverage"}
              </span>
            </p>
            <p>
              Only <b>{session.similar_products_count}</b>{" "}
              product{session.similar_products_count === 1 ? "" : "s"} in the
              catalogue matched this mood board&apos;s theme
              {(session.filter_keywords || []).length > 0 && (
                <>
                  {" "}(searched for{" "}
                  <i>
                    {session.filter_keywords!.slice(0, 6).join(", ")}
                    {session.filter_keywords!.length > 6 ? ", …" : ""}
                  </i>
                  )
                </>
              )}
              .{" "}
              {session.similar_products_count < 5
                ? "The theme likely falls outside our home-décor, storage, tabletop and kitchenware coverage. Try a mood board focused on those categories."
                : "Recommendations may not fully align with the mood board — try a mood board with a more concrete theme, or add more images."}
            </p>
          </div>
        )}
        {/* Header row: title + Try Again button. Label switches on the
            shape of the visible ideas — new-shape clusters read as "N
            recommendations", legacy synthesized ideas as "N Product Ideas". */}
        <div className="flex items-center justify-between mb-1 gap-2">
          <h3 className="font-semibold text-stone-800">
            {(() => {
              if (isProcessing) {
                return isGenerating
                  ? "Generating recommendations…"
                  : `${allIdeas.length > 0 ? allIdeas.length + " " : ""}Recommendations`;
              }
              const isCluster = ideas.some((i) => i.kind === "cluster");
              const isOOS = ideas.some((i) => i.kind === "out_of_scope");
              if (isOOS) return "Recommendations";
              if (isCluster) return `${ideas.length} Recommendation${ideas.length !== 1 ? "s" : ""}`;
              return `${ideas.length} Product Ideas`;
            })()}
          </h3>
          {/* Try Again is available on both DONE and FAILED — the backend
              already allows regeneration from either terminal state, and
              hiding it on FAILED left the user stuck with no way to retry
              a transient Claude / parsing hiccup. */}
          {(session.status === "done" || session.status === "failed") && (
            <button
              onClick={onTryAgain}
              className={clsx(
                "flex items-center gap-1.5 px-3 py-1.5 border rounded-lg text-xs font-medium transition-colors",
                session.status === "failed"
                  ? "bg-red-50 border-red-300 hover:bg-red-100 text-red-800"
                  : "bg-amber-50 border-amber-300 hover:bg-amber-100 text-amber-800",
              )}
            >
              <span>🔄</span>
              <span>{session.status === "failed" ? "Retry" : "Try Again"}</span>
            </button>
          )}
        </div>

        {isProcessing && (
          <p className="text-xs text-stone-400 mb-3">
            {isGenerating
              ? "Claude is reviewing all documents and generating 10 tailored product ideas…"
              : `Waiting for all ${total} document${total > 1 ? "s" : ""} to finish before generating ideas. ${analysed} of ${total} done so far.`}
          </p>
        )}

        {/* Generation tabs (shown when multiple generations exist).
            Each tab is a two-part control: main label selects the set,
            the small × next to it deletes just that set (with confirm). */}
        {!isProcessing && genNums.length > 1 && (
          <div className="flex gap-1.5 flex-wrap mb-3">
            {genNums.map((gen) => {
              const active = activeGen === gen;
              return (
                <div
                  key={gen}
                  className={clsx(
                    "inline-flex items-stretch rounded-lg overflow-hidden border transition-colors",
                    active
                      ? "border-amber-500 bg-amber-500 text-white"
                      : "border-stone-200 bg-white text-stone-600 hover:border-amber-300 hover:text-amber-700"
                  )}
                >
                  <button
                    onClick={() => setActiveGen(gen)}
                    className="px-3 py-1 text-xs font-medium"
                  >
                    {gen === latestGen ? `Set ${gen} ✨` : `Set ${gen}`}
                  </button>
                  <button
                    onClick={(e) => {
                      e.stopPropagation();
                      onDeleteGeneration(gen);
                    }}
                    title={`Delete Set ${gen}`}
                    aria-label={`Delete Set ${gen}`}
                    className={clsx(
                      "px-1.5 text-xs border-l transition-colors",
                      active
                        ? "border-amber-400 hover:bg-amber-600"
                        : "border-stone-200 text-stone-400 hover:bg-red-50 hover:text-red-600"
                    )}
                  >
                    ×
                  </button>
                </div>
              );
            })}
          </div>
        )}

        {isProcessing ? (
          <div className="space-y-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className={clsx("bg-white rounded-xl border border-stone-200 p-4 space-y-2", isGenerating ? "animate-pulse" : "opacity-40")}>
                <div className="h-3 bg-stone-100 rounded w-1/3" />
                <div className="h-4 bg-stone-100 rounded w-3/4" />
                <div className="h-3 bg-stone-100 rounded w-full" />
                <div className="h-3 bg-stone-100 rounded w-5/6" />
              </div>
            ))}
          </div>
        ) : (
          <>
            {/* out-of-scope card spans the full width regardless of grid */}
            {ideas.some((i) => i.kind === "out_of_scope") && (
              <div className="mb-3">
                {ideas
                  .filter((i) => i.kind === "out_of_scope")
                  .map((idea) => <OutOfScopeCard key={idea.id} idea={idea} />)}
              </div>
            )}
            {/* New-shape clusters render in a 2-col grid like Product Trends */}
            {ideas.some((i) => i.kind === "cluster") && (
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {ideas
                  .filter((i) => i.kind === "cluster")
                  .map((idea) => <AldiClusterCard key={idea.id} idea={idea} />)}
              </div>
            )}
            {/* Legacy synthesized ideas — historical sessions still work */}
            {ideas.some((i) => !i.kind) && (
              <div className="space-y-3">
                {ideas
                  .filter((i) => !i.kind)
                  .map((idea) => <IdeaCard key={idea.id} idea={idea} />)}
              </div>
            )}
            {session.status === "done" && ideas.length === 0 && (
              <p className="text-sm text-stone-400 text-center py-8">No ideas were generated.</p>
            )}
          </>
        )}
      </div>
    </div>
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AldiPage() {
  const [sessions, setSessions] = useState<AldiSession[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [selectedDetail, setSelectedDetail] = useState<AldiSession | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const fetchList = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/aldi/sessions`);
      const data: AldiSession[] = await res.json();
      setSessions(data);
    } catch {
      setSessions([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchList(); }, [fetchList]);

  const fetchDetail = useCallback(async (id: number) => {
    try {
      const res = await fetch(`${API_BASE}/api/aldi/sessions/${id}`);
      const data: AldiSession = await res.json();
      setSelectedDetail(data);
      setSessions((prev) => prev.map((s) => s.id === id ? { ...s, status: data.status, idea_count: data.idea_count } : s));
      return data.status;
    } catch {
      return "failed";
    }
  }, []);

  const startPolling = useCallback((id: number) => {
    if (pollRef.current) clearInterval(pollRef.current);
    let attempts = 0;
    pollRef.current = setInterval(async () => {
      attempts++;
      const status = await fetchDetail(id);
      if (status === "done" || status === "failed" || attempts > 60) {
        clearInterval(pollRef.current!);
      }
    }, 3000);
  }, [fetchDetail]);

  useEffect(() => {
    if (selectedId === null) {
      setSelectedDetail(null);
      if (pollRef.current) clearInterval(pollRef.current);
      return;
    }

    fetchDetail(selectedId);
    startPolling(selectedId);

    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [selectedId, fetchDetail, startPolling]);

  const handleSessionCreated = (s: AldiSession) => {
    setSessions((prev) => [s, ...prev]);
    setSelectedId(s.id);
  };

  const handleTryAgain = useCallback(async (sessionId: number) => {
    try {
      const res = await fetch(`${API_BASE}/api/aldi/sessions/${sessionId}/regenerate`, { method: "POST" });
      if (!res.ok) return;
      // Optimistically update session status to generating
      setSelectedDetail((prev) => prev ? { ...prev, status: "generating" } : prev);
      setSessions((prev) => prev.map((s) => s.id === sessionId ? { ...s, status: "generating" } : s));
      // Restart polling to pick up the new generation
      startPolling(sessionId);
    } catch {
      // ignore
    }
  }, [startPolling]);

  const handleDelete = async (id: number) => {
    await fetch(`${API_BASE}/api/aldi/sessions/${id}`, { method: "DELETE" });
    setSessions((prev) => prev.filter((s) => s.id !== id));
    if (selectedId === id) setSelectedId(null);
  };

  // Delete one generation (Set N) from a session. Backend refuses if
  // this would leave the session empty — in that case surface the
  // reason so the user can choose to delete the whole session instead.
  const handleDeleteGeneration = useCallback(
    async (sessionId: number, generation: number) => {
      if (!confirm(`Delete Set ${generation}? This can't be undone.`)) return;
      const res = await fetch(
        `${API_BASE}/api/aldi/sessions/${sessionId}/generations/${generation}`,
        { method: "DELETE" }
      );
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        alert(err.detail || `Delete failed (${res.status})`);
        return;
      }
      // Refetch to pick up the new tab list + idea_count
      await fetchDetail(sessionId);
      await fetchList();
    },
    [fetchDetail, fetchList]
  );

  const handleAddMore = useCallback(async (sessionId: number, files: File[]) => {
    if (!files.length) return;
    const form = new FormData();
    for (const file of files) form.append("files", file);
    const res = await fetch(`${API_BASE}/api/aldi/sessions/${sessionId}/uploads`, { method: "POST", body: form });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || `Upload failed (${res.status})`);
    }
    await fetchDetail(sessionId);
    await fetchList();
  }, [fetchDetail, fetchList]);

  const handleFinalise = useCallback(async (sessionId: number) => {
    if (!confirm("Done uploading? Claude will analyse the documents and generate product ideas.")) return;
    const res = await fetch(`${API_BASE}/api/aldi/sessions/${sessionId}/finalise`, { method: "POST" });
    if (!res.ok) return;
    await fetchDetail(sessionId);
    await fetchList();
    startPolling(sessionId);
  }, [fetchDetail, fetchList, startPolling]);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-stone-900">Aldi Trends</h1>
        <p className="text-sm text-stone-500 mt-1">
          Upload one or more trend mood boards together to extract insights and generate combined Aldi product ideas
        </p>
      </div>

      {/* Upload zone — hidden while an existing session is still being batched up */}
      {selectedDetail?.status === "uploading" ? (
        <div className="bg-stone-100 rounded-xl p-4 text-center text-xs text-stone-500">
          Add more documents to your current session below, or finish uploading to start a new one.
        </div>
      ) : (
        <UploadZone onSessionCreated={handleSessionCreated} />
      )}

      {/* Content */}
      {loading ? (
        <div className="text-center py-12 text-stone-400 text-sm">Loading…</div>
      ) : sessions.length === 0 ? (
        <div className="text-center py-12 text-stone-400">
          <p className="text-4xl mb-3">📋</p>
          <p className="font-medium">No sessions yet</p>
          <p className="text-sm mt-1">Upload trend mood boards above to get started</p>
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
          {/* Sidebar */}
          <div className="lg:col-span-1 space-y-2">
            <p className="text-xs font-semibold text-stone-500 uppercase tracking-wide mb-3">
              Sessions ({sessions.length})
            </p>
            {sessions.map((s) => (
              <SessionCard
                key={s.id}
                session={s}
                selected={selectedId === s.id}
                onClick={() => setSelectedId(s.id)}
                onDelete={() => handleDelete(s.id)}
              />
            ))}
          </div>

          {/* Main detail */}
          <div className="lg:col-span-3">
            {selectedDetail ? (
              <>
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-3">
                    <h2 className="font-semibold text-stone-800">
                      {selectedDetail.upload_count === 1 ? "1 Document" : `${selectedDetail.upload_count} Documents`}
                    </h2>
                    <StatusBadge status={selectedDetail.status} />
                  </div>
                </div>
                <SessionDetailView
                  session={selectedDetail}
                  onTryAgain={() => handleTryAgain(selectedDetail.id)}
                  onAddMore={(files) => handleAddMore(selectedDetail.id, files)}
                  onFinalise={() => handleFinalise(selectedDetail.id)}
                  onDeleteGeneration={(gen) => handleDeleteGeneration(selectedDetail.id, gen)}
                />
              </>
            ) : (
              <div className="bg-white rounded-xl border border-stone-200 p-12 text-center text-stone-400">
                <p className="text-3xl mb-3">👈</p>
                <p className="font-medium">Select a session to view insights and ideas</p>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
