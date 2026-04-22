"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";
import { api } from "@/lib/api";

// ── Constants ────────────────────────────────────────────────────────────────

const CATEGORIES = ["Kitchen & Dining", "Home & Decor", "Candles", "Other"] as const;
type Category = typeof CATEGORIES[number];

const BATCH_SIZE = 200;
const DOWNSCALE_MAX = 2048;
const DOWNSCALE_QUALITY = 0.82;
// Claude Sonnet 4 vision pricing rough: ~$0.003/image at this resolution
const COST_PER_IMAGE = 0.003;

const CATEGORY_COLOURS: Record<string, string> = {
  "Kitchen & Dining": "bg-amber-100 text-amber-800 border-amber-200",
  "Home & Decor": "bg-emerald-100 text-emerald-800 border-emerald-200",
  "Candles": "bg-rose-100 text-rose-800 border-rose-200",
  "Other": "bg-stone-100 text-stone-700 border-stone-200",
};

const PROMINENCE_COLOURS: Record<string, string> = {
  hero: "bg-indigo-100 text-indigo-700",
  main: "bg-sky-100 text-sky-700",
  peripheral: "bg-stone-100 text-stone-500",
  background: "bg-stone-100 text-stone-400",
};

const PROMINENCE_LABEL: Record<string, string> = {
  hero: "Hero",
  main: "Main",
  peripheral: "Peripheral",
  background: "Background",
};

// ── Types ────────────────────────────────────────────────────────────────────

interface CatalogueItem {
  id: number;
  image_id: number;
  product_name: string;
  category: string;
  prominence: string | null;
  has_crop?: boolean;
  colours: string[];
  materials: string[];
  patterns: string[];
  style_tags: string[];
  confidence: string | null;
  source_filename: string;
  retailer: string | null;
  created_at: string;
}

interface ImagePreviewItem {
  id: number;
  product_name: string;
  category: string;
  prominence: string | null;
}

interface ImageRow {
  id: number;
  filename: string;
  file_type: string;
  status: string;
  retailer: string | null;
  item_count: number;       // count matching active filter
  total_item_count: number; // total detected (from DB column)
  by_category: Record<string, number>;
  error_message: string | null;
  created_at: string;
  preview: ImagePreviewItem[];
}

interface ImageDetailItem {
  id: number;
  product_name: string;
  category: string;
  prominence: string | null;
  has_crop?: boolean;
  colours: string[];
  materials: string[];
  patterns: string[];
  style_tags: string[];
  confidence: string | null;
}

interface ImageDetail {
  id: number;
  filename: string;
  file_type: string;
  status: string;
  retailer: string | null;
  error_message: string | null;
  created_at: string;
  items: ImageDetailItem[];
}

interface Retailer {
  name: string;
  count: number;
}

const RETAILER_STORAGE_KEY = "instore-products-last-retailer";
const RETAILER_NONE = "__none__";

interface CatalogueImage {
  id: number;
  filename: string;
  file_type: string;
  status: string;
  item_count: number;
  error_message: string | null;
  created_at: string;
}

interface Stats {
  images_total: number;
  images_by_status: Record<string, number>;
  items_total: number;
  items_by_category: Record<string, number>;
  items_by_prominence?: Record<string, number>;
}

interface UploadProgress {
  totalFiles: number;
  processed: number;
  added: number;
  dupes: number;
  invalid: number;
  failed: number;
  currentBatch: number;
  totalBatches: number;
  currentPhase: "idle" | "hashing" | "downscaling" | "uploading" | "done" | "cancelled";
  error: string | null;
}

const INITIAL_PROGRESS: UploadProgress = {
  totalFiles: 0,
  processed: 0,
  added: 0,
  dupes: 0,
  invalid: 0,
  failed: 0,
  currentBatch: 0,
  totalBatches: 0,
  currentPhase: "idle",
  error: null,
};

// ── File helpers ─────────────────────────────────────────────────────────────

const IMAGE_EXT_RE = /\.(jpe?g|png|heic|heif|pdf)$/i;

function isAcceptedFile(f: File) {
  if (f.type.startsWith("image/") && /(jpeg|jpg|png|heic|heif)/i.test(f.type)) return true;
  if (f.type === "application/pdf") return true;
  return IMAGE_EXT_RE.test(f.name);
}

async function sha256Hex(data: ArrayBuffer): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Downscale JPEG/PNG to max 2048px at ~82% quality.
 * HEIC/PDF pass through untouched (browser can't canvas-decode them).
 */
async function maybeDownscale(file: File): Promise<File> {
  const name = file.name.toLowerCase();
  const isJpg = /\.(jpe?g)$/i.test(name) || file.type === "image/jpeg" || file.type === "image/jpg";
  const isPng = /\.png$/i.test(name) || file.type === "image/png";
  if (!isJpg && !isPng) return file;   // HEIC/PDF untouched

  try {
    const bitmap = await createImageBitmap(file);
    const { width, height } = bitmap;
    const longEdge = Math.max(width, height);
    if (longEdge <= DOWNSCALE_MAX) {
      bitmap.close();
      return file;   // already small enough
    }
    const scale = DOWNSCALE_MAX / longEdge;
    const targetW = Math.round(width * scale);
    const targetH = Math.round(height * scale);
    const canvas = document.createElement("canvas");
    canvas.width = targetW;
    canvas.height = targetH;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      bitmap.close();
      return file;
    }
    ctx.drawImage(bitmap, 0, 0, targetW, targetH);
    bitmap.close();
    const blob: Blob | null = await new Promise((resolve) =>
      canvas.toBlob(resolve, "image/jpeg", DOWNSCALE_QUALITY)
    );
    if (!blob) return file;
    // Rename to .jpg so backend content-type sniff is predictable
    const newName = name.replace(/\.(png|jpe?g)$/i, ".jpg");
    return new File([blob], newName, { type: "image/jpeg" });
  } catch {
    return file;
  }
}

// ── Upload Zone ──────────────────────────────────────────────────────────────

function UploadZone({
  retailer,
  onRetailerChange,
  retailers,
  onFilesSelected,
  disabled,
}: {
  retailer: string;
  onRetailerChange: (v: string) => void;
  retailers: Retailer[];
  onFilesSelected: (files: File[]) => void;
  disabled: boolean;
}) {
  const [dragging, setDragging] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const folderInputRef = useRef<HTMLInputElement>(null);

  const retailerValid = retailer.trim().length > 0;

  const handleFiles = (fileList: FileList | null | File[]) => {
    if (!fileList) return;
    if (!retailerValid) return;
    const files = Array.from(fileList as ArrayLike<File>).filter(isAcceptedFile);
    if (files.length) onFilesSelected(files);
  };

  const onDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    if (disabled || !retailerValid) return;
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      handleFiles(e.dataTransfer.files);
    }
  };

  return (
    <div className="bg-white rounded-xl border border-stone-200 p-4 space-y-3">
      {/* Retailer input — required before uploading */}
      <div>
        <label className="block text-xs font-semibold text-stone-600 mb-1">
          Retailer <span className="text-red-500">*</span>
        </label>
        <input
          type="text"
          list="instore-retailer-options"
          value={retailer}
          onChange={(e) => onRetailerChange(e.target.value)}
          placeholder="e.g. World Market, Target, HomeGoods…"
          className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-stone-300"
        />
        <datalist id="instore-retailer-options">
          {retailers.map((r) => <option key={r.name} value={r.name}>{`${r.count} images`}</option>)}
        </datalist>
        {!retailerValid && (
          <p className="text-xs text-stone-400 mt-1">
            Tag these uploads with a store name so you can filter by retailer later. Autocompletes from retailers you&apos;ve used before.
          </p>
        )}
      </div>

      <div
        onDragOver={(e) => { e.preventDefault(); if (!disabled && retailerValid) setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={clsx(
          "border-2 border-dashed rounded-xl p-8 text-center transition-colors",
          dragging ? "border-stone-500 bg-stone-50" : "border-stone-300",
          (disabled || !retailerValid) && "opacity-50 pointer-events-none",
        )}
      >
        <div className="text-3xl mb-2">🏪</div>
        <p className="text-sm font-medium text-stone-700 mb-1">
          {retailerValid ? "Drag a folder, drop files, or pick below" : "Enter a retailer first ↑"}
        </p>
        <p className="text-xs text-stone-400">
          JPG, PNG, HEIC or PDF · up to 10,000+ files · dupes skipped automatically
        </p>

        <div className="flex items-center gap-2 justify-center mt-4">
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={!retailerValid}
            className="px-3 py-1.5 rounded-lg border border-stone-300 bg-white hover:bg-stone-50 text-sm font-medium text-stone-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Choose files…
          </button>
          <button
            type="button"
            onClick={() => folderInputRef.current?.click()}
            disabled={!retailerValid}
            className="px-3 py-1.5 rounded-lg border border-stone-300 bg-white hover:bg-stone-50 text-sm font-medium text-stone-700 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            Choose folder…
          </button>
        </div>

        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".jpg,.jpeg,.png,.heic,.heif,.pdf,image/jpeg,image/png,image/heic,image/heif,application/pdf"
          className="hidden"
          onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
        />
        <input
          ref={folderInputRef}
          type="file"
          multiple
          // webkitdirectory lets the user pick a whole folder tree
          // @ts-expect-error non-standard attribute
          webkitdirectory=""
          directory=""
          className="hidden"
          onChange={(e) => { handleFiles(e.target.files); e.target.value = ""; }}
        />
      </div>
    </div>
  );
}

// ── Cost Estimate Modal ──────────────────────────────────────────────────────

function CostEstimateModal({
  fileCount,
  onCancel,
  onConfirm,
}: {
  fileCount: number;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const cost = fileCount * COST_PER_IMAGE;
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
        <h2 className="text-lg font-bold text-stone-900">Confirm upload</h2>
        <div className="text-sm text-stone-600 space-y-2">
          <p>About to analyse <strong className="text-stone-900">{fileCount.toLocaleString()}</strong> file{fileCount !== 1 ? "s" : ""}.</p>
          <p>
            Estimated Claude API cost: <strong className="text-stone-900">~${cost.toFixed(2)}</strong>.
            Actual cost depends on image content (number of products per photo).
          </p>
          <p className="text-xs text-stone-400">
            Images are downscaled and deduplicated before upload. Duplicates are free — already-analysed images are skipped.
          </p>
        </div>
        <div className="flex gap-2 justify-end pt-2">
          <button
            onClick={onCancel}
            className="px-4 py-2 rounded-lg border border-stone-300 text-sm font-medium text-stone-700 hover:bg-stone-50"
          >
            Cancel
          </button>
          <button
            onClick={onConfirm}
            className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800"
          >
            Start upload
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Upload Progress ──────────────────────────────────────────────────────────

function UploadProgressPanel({ progress, onCancel }: { progress: UploadProgress; onCancel: () => void }) {
  const pct = progress.totalFiles ? Math.round((progress.processed / progress.totalFiles) * 100) : 0;
  const label =
    progress.currentPhase === "hashing" ? "Hashing & deduplicating…" :
    progress.currentPhase === "downscaling" ? "Resizing images…" :
    progress.currentPhase === "uploading" ? `Uploading batch ${progress.currentBatch}/${progress.totalBatches}…` :
    progress.currentPhase === "done" ? "Upload complete. Analysis queued." :
    progress.currentPhase === "cancelled" ? "Upload cancelled." :
    "Idle";

  return (
    <div className="bg-white rounded-xl border border-stone-200 p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-sm font-semibold text-stone-800">{label}</p>
        {progress.currentPhase !== "done" && progress.currentPhase !== "cancelled" && progress.currentPhase !== "idle" && (
          <button onClick={onCancel} className="text-xs text-stone-400 hover:text-red-500">Cancel</button>
        )}
      </div>
      <div className="w-full bg-stone-100 rounded-full h-2 overflow-hidden">
        <div
          className={clsx(
            "h-2 rounded-full transition-all duration-300",
            progress.currentPhase === "done" ? "bg-emerald-500" : "bg-amber-500"
          )}
          style={{ width: `${pct}%` }}
        />
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs">
        <Counter label="Total" value={progress.totalFiles} />
        <Counter label="Added" value={progress.added} className="text-emerald-600" />
        <Counter label="Duplicates" value={progress.dupes} className="text-stone-500" />
        <Counter label="Invalid" value={progress.invalid} className="text-amber-600" />
        <Counter label="Failed" value={progress.failed} className="text-red-500" />
      </div>
      {progress.error && (
        <p className="text-xs text-red-500">{progress.error}</p>
      )}
      {progress.currentPhase === "done" && (
        <p className="text-xs text-stone-500">
          {progress.added} image{progress.added !== 1 ? "s" : ""} queued for Claude analysis. They'll appear below as they're processed.
        </p>
      )}
    </div>
  );
}

function Counter({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="bg-stone-50 rounded-lg p-2 text-center">
      <p className="text-stone-400">{label}</p>
      <p className={clsx("text-sm font-semibold", className || "text-stone-800")}>{value.toLocaleString()}</p>
    </div>
  );
}

// ── Inline edit chip ─────────────────────────────────────────────────────────

function EditableName({ value, onSave }: { value: string; onSave: (v: string) => Promise<void> }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  if (!editing) {
    return (
      <button
        onClick={() => setEditing(true)}
        className="text-left text-sm font-semibold text-stone-900 line-clamp-2 hover:text-stone-600"
        title="Click to edit name"
      >
        {value}
      </button>
    );
  }
  return (
    <form
      onSubmit={async (e) => {
        e.preventDefault();
        const v = draft.trim();
        if (v && v !== value) await onSave(v);
        setEditing(false);
      }}
      className="flex gap-1"
    >
      <input
        autoFocus
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={async () => {
          const v = draft.trim();
          if (v && v !== value) await onSave(v);
          setEditing(false);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") { setDraft(value); setEditing(false); }
        }}
        className="w-full border border-stone-300 rounded px-1.5 py-0.5 text-sm"
      />
    </form>
  );
}

function EditableCategory({ value, onSave }: { value: string; onSave: (v: string) => Promise<void> }) {
  const colour = CATEGORY_COLOURS[value] || CATEGORY_COLOURS["Other"];
  return (
    <select
      value={value}
      onChange={(e) => onSave(e.target.value)}
      className={clsx("px-2 py-0.5 rounded-full text-xs font-semibold border cursor-pointer", colour)}
      title="Change category"
    >
      {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
    </select>
  );
}

// ── Product card (legacy — kept for potential future flat-list view) ─────────

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function _ProductCard({
  item,
  selected,
  onToggleSelect,
  onUpdate,
  onDelete,
  onOpenLightbox,
}: {
  item: CatalogueItem;
  selected: boolean;
  onToggleSelect: (id: number, e: React.MouseEvent) => void;
  onUpdate: (id: number, patch: { product_name?: string; category?: string }) => Promise<void>;
  onDelete: (id: number) => void;
  onOpenLightbox: (imageId: number, filename: string) => void;
}) {
  return (
    <div
      className={clsx(
        "bg-white rounded-xl border overflow-hidden flex flex-col transition-colors",
        selected ? "border-stone-900 ring-2 ring-stone-900" : "border-stone-200",
      )}
    >
      <div className="relative aspect-square bg-stone-100 overflow-hidden group">
        <button
          onClick={() => onOpenLightbox(item.image_id, item.source_filename)}
          className="w-full h-full"
          title="Click to view full image"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.instoreCatalogue.imageUrl(item.image_id)}
            alt={item.product_name}
            className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
            loading="lazy"
          />
        </button>
        {/* Select checkbox — top-left */}
        <label
          className={clsx(
            "absolute top-2 left-2 w-6 h-6 rounded-md flex items-center justify-center cursor-pointer transition-opacity",
            selected
              ? "bg-stone-900 text-white opacity-100"
              : "bg-white/90 text-stone-400 opacity-0 group-hover:opacity-100 hover:bg-white border border-stone-300",
          )}
          onClick={(e) => { e.stopPropagation(); onToggleSelect(item.id, e); }}
          title="Select for bulk actions (shift-click to range-select)"
        >
          <input
            type="checkbox"
            checked={selected}
            onChange={() => { /* handled by label onClick */ }}
            className="sr-only"
          />
          {selected ? "✓" : ""}
        </label>
      </div>
      <div className="p-3 space-y-2 flex-1 flex flex-col">
        <EditableName value={item.product_name} onSave={(v) => onUpdate(item.id, { product_name: v })} />
        <div className="flex items-center gap-1.5 flex-wrap">
          <EditableCategory value={item.category} onSave={(v) => onUpdate(item.id, { category: v })} />
          {item.prominence && PROMINENCE_LABEL[item.prominence] && (
            <span
              className={clsx("px-1.5 py-0.5 rounded-full text-[10px] font-medium", PROMINENCE_COLOURS[item.prominence] || "bg-stone-100 text-stone-500")}
              title="AI-assessed how prominent this product is in the frame"
            >
              {PROMINENCE_LABEL[item.prominence]}
            </span>
          )}
          {item.retailer && (
            <span
              className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-violet-100 text-violet-700 border border-violet-200"
              title={`Uploaded from ${item.retailer}`}
            >
              {item.retailer}
            </span>
          )}
        </div>
        {(item.colours?.length || 0) > 0 && (
          <div className="flex flex-wrap gap-1">
            {item.colours.slice(0, 3).map((c, i) => (
              <span key={i} className="text-xs bg-stone-50 px-1.5 py-0.5 rounded text-stone-600">{c}</span>
            ))}
          </div>
        )}
        {(item.materials?.length || 0) > 0 && (
          <p className="text-xs text-stone-400 line-clamp-1">{item.materials.slice(0, 3).join(" · ")}</p>
        )}
        <div className="flex items-center justify-between mt-auto pt-1">
          <p className="text-xs text-stone-300 truncate max-w-[10rem]" title={item.source_filename}>{item.source_filename}</p>
          <button
            onClick={() => onDelete(item.id)}
            className="text-xs text-stone-300 hover:text-red-500"
            title="Delete item"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Lightbox (legacy — kept for potential reuse) ─────────────────────────────

/* eslint-disable-next-line @typescript-eslint/no-unused-vars */
function _Lightbox({ imageId, filename, onClose }: { imageId: number; filename: string; onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 bg-black/85 z-50 flex items-center justify-center p-4 cursor-zoom-out"
      onClick={onClose}
    >
      <div className="max-w-6xl max-h-[90vh] flex flex-col items-center gap-2">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={api.instoreCatalogue.imageUrl(imageId)}
          alt={filename}
          className="max-h-[85vh] max-w-full object-contain rounded-xl"
        />
        <p className="text-xs text-stone-300">{filename}</p>
      </div>
    </div>
  );
}

// ── Failed images panel ──────────────────────────────────────────────────────

function FailedImagesPanel({ onRetryAll, reload }: { onRetryAll: () => void; reload: number }) {
  const [failed, setFailed] = useState<CatalogueImage[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.instoreCatalogue.listImages({ status: "failed", limit: 100 })
      .then((d) => { if (!cancelled) setFailed(d.images || []); })
      .catch(() => { if (!cancelled) setFailed([]); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [reload]);

  if (loading) return <p className="text-sm text-stone-400">Loading failed images…</p>;
  if (failed.length === 0) return <p className="text-sm text-stone-500 text-center py-8">No failed images 🎉</p>;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-stone-700">{failed.length} failed image{failed.length !== 1 ? "s" : ""}</p>
        <button
          onClick={onRetryAll}
          className="px-3 py-1.5 rounded-lg bg-stone-900 text-white text-xs font-medium hover:bg-stone-800"
        >
          Retry all failed
        </button>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-3">
        {failed.map((img) => (
          <div key={img.id} className="bg-white rounded-xl border border-red-200 p-2 text-xs">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={api.instoreCatalogue.imageUrl(img.id)}
              alt={img.filename}
              className="w-full aspect-square object-cover rounded mb-2"
              loading="lazy"
            />
            <p className="font-medium text-stone-800 truncate" title={img.filename}>{img.filename}</p>
            <p className="text-red-500 line-clamp-2 mt-1">{img.error_message || "Failed"}</p>
            <div className="flex gap-1 mt-2">
              <button
                onClick={async () => { await api.instoreCatalogue.retryImage(img.id); setFailed((f) => f.filter((x) => x.id !== img.id)); }}
                className="flex-1 px-2 py-1 rounded bg-stone-100 hover:bg-stone-200 text-stone-700"
              >
                Retry
              </button>
              <button
                onClick={async () => { if (confirm("Delete this image and its items?")) { await api.instoreCatalogue.deleteImage(img.id); setFailed((f) => f.filter((x) => x.id !== img.id)); } }}
                className="px-2 py-1 rounded bg-stone-100 hover:bg-red-100 hover:text-red-700 text-stone-400"
                title="Delete"
              >
                ×
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ── Product card (one per detected item, using Claude's per-product crop) ────

function CatalogueProductCard({
  item,
  selected,
  onToggleSelect,
  onUpdate,
  onDelete,
  onOpenSource,
}: {
  item: CatalogueItem;
  selected: boolean;
  onToggleSelect: (id: number, e: React.MouseEvent) => void;
  onUpdate: (id: number, patch: { product_name?: string; category?: string }) => Promise<void>;
  onDelete: (id: number) => void;
  onOpenSource: (imageId: number) => void;
}) {
  return (
    <div
      className={clsx(
        "bg-white rounded-xl border overflow-hidden flex flex-col transition-colors",
        selected ? "border-stone-900 ring-2 ring-stone-900" : "border-stone-200",
      )}
    >
      <div
        className="relative aspect-square bg-stone-100 overflow-hidden group cursor-pointer"
        onClick={() => onOpenSource(item.image_id)}
        title="Click to view source photo"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={api.instoreCatalogue.itemImageUrl(item.id)}
          alt={item.product_name}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          loading="lazy"
        />
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggleSelect(item.id, e); }}
          className={clsx(
            "absolute top-2 left-2 w-7 h-7 rounded-md flex items-center justify-center cursor-pointer transition-opacity border",
            selected
              ? "bg-stone-900 text-white border-stone-900 opacity-100"
              : "bg-white/90 text-stone-400 border-stone-300 opacity-0 group-hover:opacity-100 hover:bg-white",
          )}
          title="Select (shift-click to range-select)"
        >
          {selected ? "✓" : ""}
        </button>
        {!item.has_crop && (
          <span
            className="absolute bottom-2 left-2 px-1.5 py-0.5 rounded-full text-[9px] font-medium bg-stone-900/70 text-white"
            title="No crop available — showing full source image"
          >
            full image
          </span>
        )}
      </div>
      <div className="p-3 space-y-2 flex-1 flex flex-col">
        <EditableName value={item.product_name} onSave={(v) => onUpdate(item.id, { product_name: v })} />
        <div className="flex items-center gap-1.5 flex-wrap">
          <EditableCategory value={item.category} onSave={(v) => onUpdate(item.id, { category: v })} />
          {item.prominence && PROMINENCE_LABEL[item.prominence] && (
            <span
              className={clsx("px-1.5 py-0.5 rounded-full text-[10px] font-medium", PROMINENCE_COLOURS[item.prominence] || "bg-stone-100 text-stone-500")}
              title="AI-assessed prominence"
            >
              {PROMINENCE_LABEL[item.prominence]}
            </span>
          )}
          {item.retailer && (
            <span
              className="px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-violet-100 text-violet-700 border border-violet-200"
              title={`Uploaded from ${item.retailer}`}
            >
              {item.retailer}
            </span>
          )}
        </div>
        {(item.colours?.length || 0) > 0 && (
          <div className="flex flex-wrap gap-1">
            {item.colours.slice(0, 3).map((c, i) => (
              <span key={i} className="text-xs bg-stone-50 px-1.5 py-0.5 rounded text-stone-600">{c}</span>
            ))}
          </div>
        )}
        {(item.materials?.length || 0) > 0 && (
          <p className="text-xs text-stone-400 line-clamp-1">{item.materials.slice(0, 3).join(" · ")}</p>
        )}
        <div className="flex items-center justify-between mt-auto pt-1">
          <p className="text-[10px] text-stone-300 truncate max-w-[10rem]" title={item.source_filename}>{item.source_filename}</p>
          <button
            onClick={() => onDelete(item.id)}
            className="text-xs text-stone-300 hover:text-red-500"
            title="Delete product"
          >
            ×
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Image card (one per uploaded photo) ──────────────────────────────────────

function ImageCard({
  image,
  selected,
  onToggleSelect,
  onOpen,
}: {
  image: ImageRow;
  selected: boolean;
  onToggleSelect: (id: number, e: React.MouseEvent) => void;
  onOpen: () => void;
}) {
  const categoryEntries = Object.entries(image.by_category).sort((a, b) => b[1] - a[1]);
  const status = image.status;
  const isProcessing = status === "pending" || status === "analysing";
  const isFailed = status === "failed";

  return (
    <div
      className={clsx(
        "bg-white rounded-xl border overflow-hidden flex flex-col transition-colors",
        selected ? "border-stone-900 ring-2 ring-stone-900" : "border-stone-200",
      )}
    >
      <div
        className="relative aspect-square bg-stone-100 overflow-hidden group cursor-pointer"
        onClick={onOpen}
        title="Click to view all detected products"
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={api.instoreCatalogue.imageUrl(image.id)}
          alt={image.filename}
          className="w-full h-full object-cover group-hover:scale-105 transition-transform duration-300"
          loading="lazy"
        />

        {/* Select checkbox — button swallows its own click so onOpen doesn't fire */}
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onToggleSelect(image.id, e); }}
          className={clsx(
            "absolute top-2 left-2 w-7 h-7 rounded-md flex items-center justify-center cursor-pointer transition-opacity border",
            selected
              ? "bg-stone-900 text-white border-stone-900 opacity-100"
              : "bg-white/90 text-stone-400 border-stone-300 opacity-0 group-hover:opacity-100 hover:bg-white",
          )}
          title="Select image (shift-click to range-select)"
        >
          {selected ? "✓" : ""}
        </button>

        {/* Retailer chip */}
        {image.retailer && (
          <span className="absolute top-2 right-2 px-2 py-0.5 rounded-full text-[10px] font-semibold bg-violet-100 text-violet-800 border border-violet-200 shadow-sm">
            {image.retailer}
          </span>
        )}

        {/* Status ribbon */}
        {isProcessing && (
          <div className="absolute inset-x-0 bottom-0 bg-amber-500/90 text-white text-xs font-semibold text-center py-1 animate-pulse">
            {status === "analysing" ? "Analysing…" : "Queued"}
          </div>
        )}
        {isFailed && (
          <div className="absolute inset-x-0 bottom-0 bg-red-500/90 text-white text-xs font-semibold text-center py-1">
            Analysis failed
          </div>
        )}
      </div>

      <div className="p-3 space-y-2 flex-1 flex flex-col">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-sm font-semibold text-stone-900">
            {image.item_count} product{image.item_count !== 1 ? "s" : ""}
          </p>
          {image.total_item_count > image.item_count && (
            <p className="text-xs text-stone-400">of {image.total_item_count}</p>
          )}
        </div>

        {/* Category breakdown */}
        {categoryEntries.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {categoryEntries.map(([cat, n]) => (
              <span
                key={cat}
                className={clsx(
                  "px-1.5 py-0.5 rounded-full text-[10px] font-medium border",
                  CATEGORY_COLOURS[cat] || CATEGORY_COLOURS["Other"],
                )}
              >
                {n} {cat}
              </span>
            ))}
          </div>
        )}

        {/* Sample product names */}
        {image.preview.length > 0 && (
          <ul className="text-xs text-stone-500 space-y-0.5 list-disc list-inside">
            {image.preview.slice(0, 3).map((p) => (
              <li key={p.id} className="truncate">{p.product_name}</li>
            ))}
            {image.item_count > 3 && (
              <li className="list-none text-stone-400">… +{image.item_count - 3} more</li>
            )}
          </ul>
        )}

        <p className="text-[10px] text-stone-300 mt-auto pt-1 truncate" title={image.filename}>
          {image.filename}
        </p>
      </div>
    </div>
  );
}

// ── Image detail modal (full image + all detected products) ──────────────────

function ImageDetailModal({
  imageId,
  onClose,
  onItemUpdated,
  onItemDeleted,
}: {
  imageId: number;
  onClose: () => void;
  onItemUpdated: (id: number, patch: { product_name?: string; category?: string }) => Promise<void>;
  onItemDeleted: (id: number) => Promise<void>;
}) {
  const [detail, setDetail] = useState<ImageDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.instoreCatalogue.getImageDetail(imageId)
      .then((d) => { if (!cancelled) setDetail(d as ImageDetail); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [imageId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const handleItemPatch = async (id: number, patch: { product_name?: string; category?: string }) => {
    await onItemUpdated(id, patch);
    setDetail((prev) => prev ? { ...prev, items: prev.items.map((it) => it.id === id ? { ...it, ...patch } : it) } : prev);
  };

  const handleItemDelete = async (id: number) => {
    if (!confirm("Delete this detected product?")) return;
    await onItemDeleted(id);
    setDetail((prev) => prev ? { ...prev, items: prev.items.filter((it) => it.id !== id) } : prev);
  };

  return (
    <div className="fixed inset-0 bg-black/75 z-50 flex items-stretch justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl max-w-7xl w-full max-h-[92vh] overflow-hidden flex flex-col md:flex-row"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Left: image */}
        <div className="md:w-1/2 bg-stone-900 flex items-center justify-center p-4 md:p-6 relative">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={api.instoreCatalogue.imageUrl(imageId)}
            alt={detail?.filename || ""}
            className="max-w-full max-h-[85vh] object-contain rounded-lg"
          />
        </div>

        {/* Right: items list */}
        <div className="md:w-1/2 flex flex-col overflow-hidden">
          <div className="flex items-center justify-between p-4 border-b border-stone-200">
            <div className="min-w-0">
              <p className="text-xs text-stone-400 truncate" title={detail?.filename}>{detail?.filename}</p>
              <div className="flex items-center gap-2 mt-1">
                {detail?.retailer && (
                  <span className="px-2 py-0.5 rounded-full text-xs font-semibold bg-violet-100 text-violet-800 border border-violet-200">
                    {detail.retailer}
                  </span>
                )}
                <p className="text-sm font-semibold text-stone-900">
                  {detail ? `${detail.items.length} product${detail.items.length !== 1 ? "s" : ""} detected` : "Loading…"}
                </p>
              </div>
            </div>
            <button
              onClick={onClose}
              className="text-2xl text-stone-400 hover:text-stone-900 leading-none"
              title="Close (Esc)"
            >
              ×
            </button>
          </div>

          <div className="flex-1 overflow-y-auto p-4 space-y-3">
            {loading && <p className="text-sm text-stone-400 animate-pulse">Loading products…</p>}
            {!loading && detail?.items.length === 0 && (
              <p className="text-sm text-stone-400">No products detected in this image.</p>
            )}
            {detail?.items.map((item) => (
              <div key={item.id} className="bg-stone-50 rounded-xl border border-stone-200 p-3 space-y-2">
                <div className="flex items-start gap-3">
                  {/* Cropped thumbnail when available */}
                  {item.has_crop && (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img
                      src={api.instoreCatalogue.itemImageUrl(item.id)}
                      alt={item.product_name}
                      className="w-16 h-16 object-cover rounded-lg border border-stone-200 flex-shrink-0"
                      loading="lazy"
                    />
                  )}
                  <div className="flex-1 min-w-0">
                    <EditableName
                      value={item.product_name}
                      onSave={(v) => handleItemPatch(item.id, { product_name: v })}
                    />
                  </div>
                  <button
                    onClick={() => handleItemDelete(item.id)}
                    className="text-xs text-stone-300 hover:text-red-500 leading-none"
                    title="Delete this item"
                  >
                    ×
                  </button>
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <EditableCategory
                    value={item.category}
                    onSave={(v) => handleItemPatch(item.id, { category: v })}
                  />
                  {item.prominence && PROMINENCE_LABEL[item.prominence] && (
                    <span
                      className={clsx("px-1.5 py-0.5 rounded-full text-[10px] font-medium", PROMINENCE_COLOURS[item.prominence] || "bg-stone-100 text-stone-500")}
                    >
                      {PROMINENCE_LABEL[item.prominence]}
                    </span>
                  )}
                </div>
                {(item.colours.length > 0 || item.materials.length > 0) && (
                  <div className="text-xs text-stone-500 space-y-0.5">
                    {item.colours.length > 0 && <p>Colours: {item.colours.join(" · ")}</p>}
                    {item.materials.length > 0 && <p>Materials: {item.materials.join(" · ")}</p>}
                    {item.patterns.length > 0 && <p>Patterns: {item.patterns.join(" · ")}</p>}
                    {item.style_tags.length > 0 && <p>Style: {item.style_tags.join(" · ")}</p>}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function InStoreProductsPage() {
  const [viewMode, setViewMode] = useState<"image" | "product">("image");
  const [images, setImages] = useState<ImageRow[]>([]);
  const [products, setProducts] = useState<CatalogueItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [page, setPage] = useState(0);
  const PAGE_SIZE = 48;

  // Filters
  const [search, setSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [category, setCategory] = useState<"" | Category>("");
  const [retailerFilter, setRetailerFilter] = useState("");   // "" = all, "__none__" = untagged
  const [showAll, setShowAll] = useState(false);   // include peripheral/background
  const [mode, setMode] = useState<"catalogue" | "failed">("catalogue");

  // Retailers known to the system — for autocomplete on upload + filter dropdown
  const [retailers, setRetailers] = useState<Retailer[]>([]);
  const [untaggedCount, setUntaggedCount] = useState(0);

  // Retailer currently selected in the upload zone (remembered across sessions)
  const [uploadRetailer, setUploadRetailer] = useState<string>("");

  // Upload state
  const [pendingFiles, setPendingFiles] = useState<File[] | null>(null);
  const [progress, setProgress] = useState<UploadProgress>(INITIAL_PROGRESS);
  const cancelRef = useRef(false);

  // Stats
  const [stats, setStats] = useState<Stats | null>(null);

  // Detail modal (image-level)
  const [openImageId, setOpenImageId] = useState<number | null>(null);

  // Multi-select (image IDs)
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const lastClickedIdRef = useRef<number | null>(null);
  const [confirmingDeleteAll, setConfirmingDeleteAll] = useState(false);
  const [bulkDeleting, setBulkDeleting] = useState(false);

  // Reload nonce for failed panel
  const [failedReload, setFailedReload] = useState(0);

  // Debounce search
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(search), 300);
    return () => clearTimeout(t);
  }, [search]);

  // Reset to page 0 when filters or view mode change
  useEffect(() => {
    setPage(0);
  }, [debouncedSearch, category, retailerFilter, showAll, viewMode]);

  // Load images (image-centric grid)
  const loadImages = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.instoreCatalogue.listImages({
        q: debouncedSearch || undefined,
        category: category || undefined,
        retailer: retailerFilter || undefined,
        show_all: showAll,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setImages((data.images as ImageRow[]) || []);
      setTotal(data.total || 0);
    } catch {
      setImages([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, category, retailerFilter, showAll, page]);

  // Load products (flat item list)
  const loadProducts = useCallback(async () => {
    setLoading(true);
    try {
      const data = await api.instoreCatalogue.listItems({
        q: debouncedSearch || undefined,
        category: category || undefined,
        retailer: retailerFilter || undefined,
        show_all: showAll,
        limit: PAGE_SIZE,
        offset: page * PAGE_SIZE,
      });
      setProducts((data.items as CatalogueItem[]) || []);
      setTotal(data.total || 0);
    } catch {
      setProducts([]);
      setTotal(0);
    } finally {
      setLoading(false);
    }
  }, [debouncedSearch, category, retailerFilter, showAll, page]);

  // Refresh both the current view and stats
  const reloadCurrentView = useCallback(async () => {
    if (viewMode === "image") { await loadImages(); } else { await loadProducts(); }
  }, [viewMode, loadImages, loadProducts]);

  useEffect(() => {
    if (viewMode === "image") loadImages();
    else loadProducts();
  }, [viewMode, loadImages, loadProducts]);

  // Load stats
  const loadStats = useCallback(async () => {
    try {
      const s = await api.instoreCatalogue.stats();
      setStats(s);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { loadStats(); }, [loadStats]);

  // Load retailer list (for autocomplete + filter dropdown)
  const loadRetailers = useCallback(async () => {
    try {
      const r = await api.instoreCatalogue.listRetailers();
      setRetailers(r.retailers || []);
      setUntaggedCount(r.untagged_count || 0);
    } catch { /* ignore */ }
  }, []);
  useEffect(() => { loadRetailers(); }, [loadRetailers]);

  // Hydrate uploadRetailer from localStorage on mount
  useEffect(() => {
    if (typeof window === "undefined") return;
    const stored = window.localStorage.getItem(RETAILER_STORAGE_KEY);
    if (stored) setUploadRetailer(stored);
  }, []);

  // Poll while uploads/analysis are in flight
  useEffect(() => {
    const anyActive = stats && (
      (stats.images_by_status?.pending || 0) > 0 ||
      (stats.images_by_status?.analysing || 0) > 0
    );
    if (!anyActive) return;
    const t = setInterval(() => { loadStats(); reloadCurrentView(); }, 4000);
    return () => clearInterval(t);
  }, [stats, loadStats, reloadCurrentView]);

  // ── Upload pipeline ──────────────────────────────────────────────────────

  const onFilesSelected = useCallback((files: File[]) => {
    setPendingFiles(files);
  }, []);

  const startUpload = useCallback(async () => {
    const files = pendingFiles;
    if (!files) return;
    setPendingFiles(null);
    cancelRef.current = false;

    const totalFiles = files.length;
    const totalBatches = Math.ceil(totalFiles / BATCH_SIZE);
    setProgress({ ...INITIAL_PROGRESS, totalFiles, totalBatches, currentPhase: "hashing" });

    // Process batches one at a time to avoid browser memory blow-up on 10k files
    for (let batchIdx = 0; batchIdx < totalBatches; batchIdx++) {
      if (cancelRef.current) break;

      const slice = files.slice(batchIdx * BATCH_SIZE, (batchIdx + 1) * BATCH_SIZE);

      // Hash + downscale this batch concurrently (capped to avoid CPU thrash)
      setProgress((p) => ({ ...p, currentBatch: batchIdx + 1, currentPhase: "hashing" }));

      const prepared: { file: File; hash: string }[] = [];
      for (let i = 0; i < slice.length; i += 4) {
        if (cancelRef.current) break;
        const chunk = slice.slice(i, i + 4);
        const chunkPrepared = await Promise.all(chunk.map(async (f) => {
          const scaled = await maybeDownscale(f);
          const buf = await scaled.arrayBuffer();
          const hash = await sha256Hex(buf);
          return { file: scaled, hash };
        }));
        prepared.push(...chunkPrepared);
      }

      if (cancelRef.current) break;

      // Upload
      setProgress((p) => ({ ...p, currentPhase: "uploading" }));
      try {
        const result = await api.instoreCatalogue.upload(
          prepared.map((p) => p.file),
          prepared.map((p) => p.hash),
          uploadRetailer.trim() || undefined,
        );
        setProgress((p) => ({
          ...p,
          processed: p.processed + slice.length,
          added: p.added + (result.added || 0),
          dupes: p.dupes + (result.skipped_duplicate || 0),
          invalid: p.invalid + (result.skipped_invalid || 0),
        }));
      } catch (err) {
        setProgress((p) => ({
          ...p,
          processed: p.processed + slice.length,
          failed: p.failed + slice.length,
          error: err instanceof Error ? err.message : "Batch failed",
        }));
      }
    }

    setProgress((p) => ({
      ...p,
      currentPhase: cancelRef.current ? "cancelled" : "done",
    }));
    // Remember the retailer name for next time
    if (typeof window !== "undefined" && uploadRetailer.trim()) {
      window.localStorage.setItem(RETAILER_STORAGE_KEY, uploadRetailer.trim());
    }
    await loadStats();
    await reloadCurrentView();
    await loadRetailers();
  }, [pendingFiles, uploadRetailer, loadStats, reloadCurrentView, loadRetailers]);

  const cancelUpload = useCallback(() => {
    cancelRef.current = true;
  }, []);

  // ── Item actions ─────────────────────────────────────────────────────────

  // Item-level edit/delete (invoked from the detail modal)
  const updateItem = useCallback(async (id: number, patch: { product_name?: string; category?: string }) => {
    try {
      await api.instoreCatalogue.patchItem(id, patch);
      if (patch.category) loadStats();
    } catch { /* ignore */ }
  }, [loadStats]);

  const deleteItem = useCallback(async (id: number) => {
    try {
      await api.instoreCatalogue.deleteItem(id);
      loadStats();
      reloadCurrentView();
    } catch { /* ignore */ }
  }, [loadStats, reloadCurrentView]);

  const retryAllFailed = useCallback(async () => {
    if (!confirm("Re-queue all failed images for analysis?")) return;
    try {
      await api.instoreCatalogue.retryAllFailed();
      setFailedReload((n) => n + 1);
      loadStats();
    } catch { /* ignore */ }
  }, [loadStats]);

  // ── Selection actions ───────────────────────────────────────────────────

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  // Clear selection on filter change so old IDs don't linger after they scroll off
  useEffect(() => { clearSelection(); lastClickedIdRef.current = null; }, [clearSelection, debouncedSearch, category, retailerFilter, showAll, page, viewMode]);

  const toggleSelect = useCallback((id: number, e: React.MouseEvent) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      const visibleIds = viewMode === "image" ? images.map((img) => img.id) : products.map((p) => p.id);
      // Shift-click = range select using the last clicked anchor
      if (e.shiftKey && lastClickedIdRef.current != null && lastClickedIdRef.current !== id) {
        const a = visibleIds.indexOf(lastClickedIdRef.current);
        const b = visibleIds.indexOf(id);
        if (a !== -1 && b !== -1) {
          const [lo, hi] = [Math.min(a, b), Math.max(a, b)];
          for (let i = lo; i <= hi; i++) next.add(visibleIds[i]);
          lastClickedIdRef.current = id;
          return next;
        }
      }
      if (next.has(id)) next.delete(id); else next.add(id);
      lastClickedIdRef.current = id;
      return next;
    });
  }, [viewMode, images, products]);

  const selectAllOnPage = useCallback(() => {
    const ids = viewMode === "image" ? images.map((i) => i.id) : products.map((p) => p.id);
    setSelectedIds(new Set(ids));
  }, [viewMode, images, products]);

  const bulkDeleteSelected = useCallback(async () => {
    if (selectedIds.size === 0) return;
    const isImageView = viewMode === "image";
    const noun = isImageView ? "image" : "product";
    const cascade = isImageView ? " (and every product detected in them)" : "";
    if (!confirm(`Delete ${selectedIds.size} selected ${noun}${selectedIds.size !== 1 ? "s" : ""}${cascade}? This cannot be undone.`)) return;
    setBulkDeleting(true);
    try {
      const ids = Array.from(selectedIds);
      if (isImageView) {
        await api.instoreCatalogue.bulkDeleteImages(ids);
        setImages((prev) => prev.filter((img) => !selectedIds.has(img.id)));
      } else {
        await api.instoreCatalogue.bulkDeleteItems(ids);
        setProducts((prev) => prev.filter((it) => !selectedIds.has(it.id)));
      }
      setTotal((t) => Math.max(0, t - selectedIds.size));
      clearSelection();
      loadStats();
      loadRetailers();
    } catch (err) {
      alert(err instanceof Error ? err.message : "Bulk delete failed");
    } finally {
      setBulkDeleting(false);
    }
  }, [viewMode, selectedIds, clearSelection, loadStats, loadRetailers]);

  const deleteEverything = useCallback(async () => {
    setBulkDeleting(true);
    try {
      await api.instoreCatalogue.deleteEverything();
      clearSelection();
      setImages([]);
      setProducts([]);
      setTotal(0);
      setPage(0);
      await loadStats();
      await loadRetailers();
      setConfirmingDeleteAll(false);
    } catch (err) {
      alert(err instanceof Error ? err.message : "Delete all failed");
    } finally {
      setBulkDeleting(false);
    }
  }, [clearSelection, loadStats, loadRetailers]);

  // ── Derived ──────────────────────────────────────────────────────────────

  const processingCount = (stats?.images_by_status?.pending || 0) + (stats?.images_by_status?.analysing || 0);
  const failedCount = stats?.images_by_status?.failed || 0;

  const hasFilters = debouncedSearch || category || retailerFilter || showAll;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between flex-wrap gap-3">
        <h1 className="text-2xl font-bold text-stone-900">In-store Products</h1>
        <div className="flex items-center gap-3">
          {stats && stats.images_total > 0 && (
            <button
              onClick={() => setConfirmingDeleteAll(true)}
              className="px-3 py-1.5 rounded-lg border border-red-200 text-red-600 hover:bg-red-50 text-xs font-medium"
              title="Permanently delete every image, item, and file in the catalogue"
            >
              Delete all
            </button>
          )}
          <div className="text-sm text-stone-500 text-right">
            {loading ? "Loading…" : (
              <>
                <div>
                  {viewMode === "image"
                    ? `${images.length} of ${total.toLocaleString()} images`
                    : `${products.length} of ${total.toLocaleString()} products`}
                </div>
                {stats && (
                  <div className="text-xs text-stone-400">
                    {stats.items_total.toLocaleString()} products catalogued
                    {processingCount > 0 && ` · ${processingCount} analysing`}
                    {failedCount > 0 && ` · ${failedCount} failed`}
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      {/* Upload zone */}
      <UploadZone
        retailer={uploadRetailer}
        onRetailerChange={setUploadRetailer}
        retailers={retailers}
        onFilesSelected={onFilesSelected}
        disabled={progress.currentPhase === "hashing" || progress.currentPhase === "downscaling" || progress.currentPhase === "uploading"}
      />

      {/* Cost estimate modal */}
      {pendingFiles && (
        <CostEstimateModal
          fileCount={pendingFiles.length}
          onCancel={() => setPendingFiles(null)}
          onConfirm={startUpload}
        />
      )}

      {/* Progress */}
      {progress.currentPhase !== "idle" && (
        <UploadProgressPanel progress={progress} onCancel={cancelUpload} />
      )}

      {/* Tabs */}
      <div className="flex gap-1 border-b border-stone-200">
        <button
          onClick={() => setMode("catalogue")}
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors",
            mode === "catalogue" ? "border-stone-900 text-stone-900" : "border-transparent text-stone-500 hover:text-stone-700"
          )}
        >
          Catalogue
        </button>
        <button
          onClick={() => setMode("failed")}
          className={clsx(
            "px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors flex items-center gap-2",
            mode === "failed" ? "border-stone-900 text-stone-900" : "border-transparent text-stone-500 hover:text-stone-700"
          )}
        >
          Failed
          {failedCount > 0 && (
            <span className="px-1.5 py-0.5 rounded-full bg-red-100 text-red-700 text-xs">{failedCount}</span>
          )}
        </button>
      </div>

      {mode === "catalogue" ? (
        <>
          {/* View toggle */}
          <div className="flex items-center gap-1 bg-white rounded-xl border border-stone-200 p-1 w-fit">
            <button
              onClick={() => setViewMode("image")}
              className={clsx(
                "px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                viewMode === "image"
                  ? "bg-stone-900 text-white"
                  : "text-stone-600 hover:bg-stone-100"
              )}
            >
              By image
            </button>
            <button
              onClick={() => setViewMode("product")}
              className={clsx(
                "px-3 py-1.5 rounded-lg text-sm font-medium transition-colors",
                viewMode === "product"
                  ? "bg-stone-900 text-white"
                  : "text-stone-600 hover:bg-stone-100"
              )}
            >
              By product
            </button>
          </div>

          {/* Filter bar */}
          <div className="bg-white rounded-xl border border-stone-200 p-4">
            <div className="grid grid-cols-1 sm:grid-cols-5 gap-3 items-center">
              <div className="sm:col-span-2">
                <input
                  type="search"
                  placeholder="Search products…"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  className="w-full border border-stone-200 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-stone-300"
                />
              </div>
              <select
                value={retailerFilter}
                onChange={(e) => setRetailerFilter(e.target.value)}
                className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
              >
                <option value="">All retailers</option>
                {retailers.map((r) => (
                  <option key={r.name} value={r.name}>{r.name} ({r.count})</option>
                ))}
                {untaggedCount > 0 && (
                  <option value={RETAILER_NONE}>(no retailer) ({untaggedCount})</option>
                )}
              </select>
              <select
                value={category}
                onChange={(e) => setCategory(e.target.value as "" | Category)}
                className="border border-stone-200 rounded-lg px-3 py-2 text-sm bg-white focus:outline-none"
              >
                <option value="">All categories</option>
                {CATEGORIES.map((c) => <option key={c} value={c}>{c}</option>)}
              </select>
              <button
                onClick={() => setShowAll((v) => !v)}
                className={clsx(
                  "flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg border text-sm font-medium transition-colors",
                  showAll
                    ? "bg-stone-900 border-stone-900 text-white"
                    : "bg-white border-stone-200 text-stone-600 hover:border-stone-400"
                )}
                title="When off, only hero/main products are shown — hiding items Claude judged peripheral or background"
              >
                <span>{showAll ? "✓" : "○"}</span>
                <span>Show background items</span>
              </button>
            </div>
            {stats?.items_by_prominence && !showAll && (
              <p className="text-xs text-stone-400 mt-2">
                Hiding {((stats.items_by_prominence.peripheral || 0) + (stats.items_by_prominence.background || 0)).toLocaleString()} peripheral/background items · toggle above to show all
              </p>
            )}
            {hasFilters && (
              <button
                onClick={() => { setSearch(""); setCategory(""); setRetailerFilter(""); setShowAll(false); }}
                className="mt-2 text-xs text-stone-500 hover:text-stone-900 underline"
              >
                Clear filters
              </button>
            )}
          </div>

          {/* Grid */}
          {loading ? (
            <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-4">
              {Array.from({ length: 12 }).map((_, i) => (
                <div key={i} className="bg-white rounded-xl border border-stone-200 overflow-hidden animate-pulse">
                  <div className="aspect-square bg-stone-100" />
                  <div className="p-3 space-y-2">
                    <div className="h-3 bg-stone-100 rounded w-2/3" />
                    <div className="h-3 bg-stone-100 rounded w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : (viewMode === "image" ? images.length : products.length) > 0 ? (
            <>
              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-4">
                {viewMode === "image"
                  ? images.map((img) => (
                    <ImageCard
                      key={img.id}
                      image={img}
                      selected={selectedIds.has(img.id)}
                      onToggleSelect={toggleSelect}
                      onOpen={() => setOpenImageId(img.id)}
                    />
                  ))
                  : products.map((it) => (
                    <CatalogueProductCard
                      key={it.id}
                      item={it}
                      selected={selectedIds.has(it.id)}
                      onToggleSelect={toggleSelect}
                      onUpdate={updateItem}
                      onDelete={deleteItem}
                      onOpenSource={(imageId) => setOpenImageId(imageId)}
                    />
                  ))
                }
              </div>
              <div className="flex items-center justify-center gap-3 pt-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="px-4 py-2 rounded-lg border border-stone-200 text-sm font-medium disabled:opacity-40 hover:bg-stone-50"
                >
                  ← Previous
                </button>
                <span className="text-sm text-stone-500">Page {page + 1}</span>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={(viewMode === "image" ? images.length : products.length) < PAGE_SIZE}
                  className="px-4 py-2 rounded-lg border border-stone-200 text-sm font-medium disabled:opacity-40 hover:bg-stone-50"
                >
                  Next →
                </button>
              </div>
            </>
          ) : (
            <div className="text-center py-24 text-stone-400">
              <p className="text-4xl mb-3">🏪</p>
              {hasFilters ? (
                <>
                  <p className="font-medium">No {viewMode === "image" ? "images" : "products"} match your filters</p>
                  <button onClick={() => { setSearch(""); setCategory(""); setRetailerFilter(""); setShowAll(false); }} className="mt-2 text-sm text-stone-600 underline hover:text-stone-900">Clear filters</button>
                </>
              ) : (
                <>
                  <p className="font-medium">No {viewMode === "image" ? "images" : "products"} yet</p>
                  <p className="text-sm mt-1">Upload in-store photos above to get started</p>
                </>
              )}
            </div>
          )}
        </>
      ) : (
        <FailedImagesPanel onRetryAll={retryAllFailed} reload={failedReload} />
      )}

      {/* Image detail modal */}
      {openImageId !== null && (
        <ImageDetailModal
          imageId={openImageId}
          onClose={() => setOpenImageId(null)}
          onItemUpdated={updateItem}
          onItemDeleted={deleteItem}
        />
      )}

      {/* Floating selection action bar */}
      {selectedIds.size > 0 && (
        <div className="fixed bottom-6 left-1/2 -translate-x-1/2 z-40 bg-stone-900 text-white rounded-full shadow-xl px-2 py-1.5 flex items-center gap-1">
          <span className="px-3 py-1 text-sm font-medium">
            {selectedIds.size} selected
          </span>
          <button
            onClick={selectAllOnPage}
            className="px-3 py-1 text-xs rounded-full hover:bg-stone-800 transition-colors"
          >
            Select all on page ({viewMode === "image" ? images.length : products.length})
          </button>
          <button
            onClick={clearSelection}
            className="px-3 py-1 text-xs rounded-full hover:bg-stone-800 transition-colors"
          >
            Clear
          </button>
          <button
            onClick={bulkDeleteSelected}
            disabled={bulkDeleting}
            className="px-4 py-1.5 rounded-full bg-red-500 hover:bg-red-600 text-white text-sm font-medium disabled:opacity-60"
          >
            {bulkDeleting ? "Deleting…" : `Delete ${selectedIds.size}`}
          </button>
        </div>
      )}

      {/* Delete-all confirmation modal */}
      {confirmingDeleteAll && stats && (
        <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
          <div className="bg-white rounded-xl shadow-xl max-w-md w-full p-6 space-y-4">
            <h2 className="text-lg font-bold text-stone-900">Delete everything?</h2>
            <div className="text-sm text-stone-600 space-y-2">
              <p>
                This will permanently delete <strong className="text-stone-900">{stats.images_total.toLocaleString()} images</strong>
                {" "}and <strong className="text-stone-900">{stats.items_total.toLocaleString()} catalogued products</strong>,
                including the image files on disk.
              </p>
              <p className="text-red-600">This cannot be undone.</p>
            </div>
            <div className="flex gap-2 justify-end pt-2">
              <button
                onClick={() => setConfirmingDeleteAll(false)}
                disabled={bulkDeleting}
                className="px-4 py-2 rounded-lg border border-stone-300 text-sm font-medium text-stone-700 hover:bg-stone-50 disabled:opacity-60"
              >
                Cancel
              </button>
              <button
                onClick={deleteEverything}
                disabled={bulkDeleting}
                className="px-4 py-2 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700 disabled:opacity-60"
              >
                {bulkDeleting ? "Deleting…" : "Yes, delete everything"}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
