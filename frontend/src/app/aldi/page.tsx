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
  status: "pending" | "analysing" | "generating" | "done" | "failed";
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
  error_message?: string | null;
  uploads?: AldiUploadDoc[];
  ideas?: AldiIdea[];
  generation_count?: number;
  latest_generation?: number;
}

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_LABELS: Record<string, string> = {
  pending: "Queued",
  analysing: "Analysing…",
  generating: "Generating ideas…",
  done: "Done",
  failed: "Failed",
};

const STATUS_COLOURS: Record<string, string> = {
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
      const res = await fetch(`${API_BASE}/api/aldi/sessions`, { method: "POST", body: form });
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

// ── Session detail view ────────────────────────────────────────────────────────

function SessionDetailView({
  session,
  onTryAgain,
}: {
  session: AldiSession;
  onTryAgain: () => void;
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

  return (
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
        {/* Header row: title + Try Again button */}
        <div className="flex items-center justify-between mb-1 gap-2">
          <h3 className="font-semibold text-stone-800">
            {isProcessing
              ? isGenerating
                ? "Generating ideas…"
                : `${allIdeas.length > 0 ? allIdeas.length + " " : ""}Product Ideas`
              : `${ideas.length} Product Ideas`}
          </h3>
          {session.status === "done" && (
            <button
              onClick={onTryAgain}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-amber-50 border border-amber-300 hover:bg-amber-100 text-amber-800 rounded-lg text-xs font-medium transition-colors"
            >
              <span>🔄</span>
              <span>Try Again</span>
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

        {/* Generation tabs (shown when multiple generations exist) */}
        {!isProcessing && genNums.length > 1 && (
          <div className="flex gap-1.5 flex-wrap mb-3">
            {genNums.map((gen) => (
              <button
                key={gen}
                onClick={() => setActiveGen(gen)}
                className={clsx(
                  "px-3 py-1 rounded-lg text-xs font-medium transition-colors border",
                  activeGen === gen
                    ? "bg-amber-500 border-amber-500 text-white"
                    : "bg-white border-stone-200 text-stone-600 hover:border-amber-300 hover:text-amber-700"
                )}
              >
                {gen === latestGen ? `Set ${gen} ✨` : `Set ${gen}`}
              </button>
            ))}
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
          <div className="space-y-3">
            {ideas.map((idea) => <IdeaCard key={idea.id} idea={idea} />)}
            {session.status === "done" && ideas.length === 0 && (
              <p className="text-sm text-stone-400 text-center py-8">No ideas were generated.</p>
            )}
          </div>
        )}
      </div>
    </div>
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

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold text-stone-900">Aldi Trends</h1>
        <p className="text-sm text-stone-500 mt-1">
          Upload one or more trend mood boards together to extract insights and generate combined Aldi product ideas
        </p>
      </div>

      {/* Upload zone */}
      <UploadZone onSessionCreated={handleSessionCreated} />

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
                <SessionDetailView session={selectedDetail} onTryAgain={() => handleTryAgain(selectedDetail.id)} />
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
