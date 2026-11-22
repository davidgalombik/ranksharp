"use client";
/**
 * AldiRecommendationsPanel — web product recommendations for one ALDI
 * session (Phase 4, 2026-11-01).
 *
 * Two reels, one per buying team (Sapana: ceramic / plastic / glass / wood;
 * Adrah: fabric / metal). Each run returns five product links from the
 * web, each tied to the screenshot element it echoes. The card shows the
 * link, a prominent retailer name and the "See in report" evidence buttons
 * (trimmed 2026-11-04: no theme chip, verified badge or match text); the
 * buyer marks each pick Good or Bad with a note, and Find More Products
 * (formerly Try Again) feeds that back into the next five. Each run is a
 * "Round" (formerly "Set"; renamed 2026-11-22, numbered 1..n in order),
 * with an "All rounds" tab listing every product. Runs happen in the
 * worker; this panel polls while one is in flight.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TEAM_ORDER = ["sapana", "adrah", "fragrance"];

export interface RecElement {
  id: string;
  kind: string;
  bbox: number[] | null;
  description: string;
}
export interface RecUpload {
  id: number;
  filename: string;
  file_type: string;
  elements?: RecElement[];
}
interface Feedback { id: number; verdict: string; note: string | null; created_at: string }
interface Evidence { upload_id: number; element_id: string; page?: number | null; description?: string }
interface Rec {
  id: number;
  position: number;
  product_name: string;
  retailer: string | null;
  retailer_tier: string | null;
  url: string;
  material_family: string | null;
  theme_id: string | null;
  theme_name: string | null;
  evidence: Evidence[];
  how_it_matches: string | null;
  verification_status: string | null;
  thumbnail_url: string | null;
  feedback: Feedback[];
  link_status?: string | null;
}
// Since 2026-11-04 near misses are rows with an id and feedback; older runs
// still serve the plain JSON list (no id, so no feedback controls).
interface NearMiss {
  id?: number;
  product_name?: string;
  retailer?: string;
  url?: string;
  fails?: string;
  note?: string;
  thumbnail_url?: string | null;
  feedback?: Feedback[];
  link_status?: string | null;
}
interface FeedItem { name: string; retailer: string; tag: string; ok: boolean; image: string | null }
// Live progress written by the worker while a run is in flight (2026-11-22).
interface Progress {
  queries_total?: number; queries_done?: number; found?: number; recent?: string[];
  retailers?: { name: string; done: boolean }[]; to_verify?: number; checked?: number; feed?: FeedItem[];
}
interface Run {
  progress?: Progress;
  id: number;
  team: string;
  generation: number;
  status: string;
  stage: string | null;
  created_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  cost_usd: number;
  searches: number;
  fetches: number;
  error: string | null;
  near_misses: NearMiss[];
  stats: Record<string, number>;
  recommendations: Rec[];
}
interface Prefs {
  memo: string;
  price_min: number;
  price_max: number;
  learned_rules: string;
  distilled_at: string | null;
  distilled_count: number;
}
interface TeamBlock {
  label: string;
  families: string[];
  preferences: Prefs;
  runs: Run[];
  latest: Run | null;
}
interface Payload { session_id: number; teams: Record<string, TeamBlock> }

// The pipeline's stages, shown as four plain-English steps (2026-11-22).
const STEPS = [
  { label: "Reading the report", detail: "Writing searches from the report and your marks" },
  { label: "Searching retailers", detail: "Your preferred retailers, then the web" },
  { label: "Opening product pages", detail: "Checking price, stock and details" },
  { label: "Picking the best 5", detail: "Matched to the report" },
];
const STAGE_STEP: Record<string, number> = {
  planning: 0, "planning again": 1, discovering: 1, ranking: 1, verifying: 2, selecting: 3, repairing: 3, saving: 3,
};

const FAMILY_LABEL: Record<string, string> = {
  fabric: "Fabric", metal: "Metal", ceramic: "Ceramic", glass: "Glass", wood: "Wood & bamboo", plastic: "Plastic",
  candles: "Candles", fragrances: "Fragrances",
};

function secsSince(iso: string | null): number {
  return iso ? Math.max(0, (Date.now() - new Date(iso + "Z").getTime()) / 1000) : 0;
}

function fmtClock(secs: number): string {
  const s = Math.round(secs);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function fmtDuration(secs: number): string {
  const s = Math.round(secs);
  return s < 60 ? `${s} sec` : `${Math.floor(s / 60)} min ${s % 60} sec`;
}

/** Typical run length for this team, from its finished rounds (default ~2 min). */
function typicalSecs(runs: Run[]): number {
  const d = runs.filter((r) => r.status === "done" && r.started_at && r.finished_at)
    .map((r) => (new Date(r.finished_at! + "Z").getTime() - new Date(r.started_at! + "Z").getTime()) / 1000)
    .filter((x) => x > 5 && x < 1800).sort((a, b) => a - b);
  return d.length ? d[Math.floor(d.length / 2)] : 110;
}

function etaText(elapsed: number, typical: number): string {
  const left = typical - elapsed;
  if (elapsed > typical * 1.3) return "Taking a little longer than usual";
  if (left > 75) return `About ${Math.round(left / 60)} min left`;
  if (left > 15) return `About ${Math.max(20, Math.round(left / 10) * 10)} sec left`;
  return "Almost done";
}

/** Where the bar should be for this stage: real counts where the worker
 *  reports them, an easing creep elsewhere. The caller keeps it monotonic. */
function targetPct(run: Run, p: Progress, stageSecs: number): number {
  if (run.status === "queued") return 2;
  const ease = (tau: number) => 1 - Math.exp(-stageSecs / tau);
  const ratio = (a?: number, b?: number) => (b ? Math.min(1, (a ?? 0) / b) : 0);
  switch (run.stage) {
    case "planning": return 3 + 20 * ease(12);
    case "discovering": return 25 + 22 * ratio(p.queries_done, p.queries_total);
    case "ranking": return 48 + 2 * ease(5);
    case "verifying": return 50 + 25 * ratio(p.checked, p.to_verify);
    case "planning again": return 50;
    default: return 76 + 22 * ease(20);   // selecting, repairing, saving
  }
}

function thumbSrc(rec: Rec): string | null {
  if (!rec.thumbnail_url) return null;
  return rec.thumbnail_url.startsWith("/") ? `${API_BASE}${rec.thumbnail_url}` : rec.thumbnail_url;
}

function plural(n: number, one: string, many = `${one}s`): string {
  return `${n} ${n === 1 ? one : many}`;
}

function Sparkle({ className = "w-4 h-4", double = false }: { className?: string; double?: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d="M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8z" />
      {double && <path d="M19 15l.8 2.2L22 18l-2.2.8L19 21l-.8-2.2L16 18l2.2-.8z" />}
    </svg>
  );
}

function Check({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className={className}>
      <path d="M5 12l5 5L20 7" />
    </svg>
  );
}

function StatusPill({ verdict }: { verdict: string | null }) {
  const [text, cls] = verdict === "good" ? ["Good", "bg-emerald-100 text-emerald-900"]
    : verdict === "bad" ? ["Bad", "bg-rose-100 text-rose-900"]
    : ["To review", "bg-blue-50 text-blue-700"];
  return <span className={clsx("px-2 py-0.5 rounded-full text-[11px] font-semibold whitespace-nowrap", cls)}>{text}</span>;
}

// ── Round tab (2026-11-22) ────────────────────────────────────────────────────
// A select button plus an always-visible × to delete the round.

function RoundTab({
  label, sub, todo, isNew, active, onSelect, onDelete, deleteLabel, glow,
}: {
  label: string; sub: string; todo?: boolean; isNew?: boolean; active: boolean; glow?: boolean;
  onSelect: () => void; onDelete?: () => void; deleteLabel?: string;
}) {
  return (
    <div className={clsx("inline-flex items-stretch min-w-[8.5rem] rounded-xl overflow-hidden border transition-colors", glow && "ring-4 ring-emerald-200",
      active ? "bg-stone-900 border-stone-900 text-white shadow-md shadow-stone-900/20"
             : "bg-white border-stone-300 text-stone-900 hover:border-stone-500")}>
      <button type="button" role="tab" aria-selected={active} onClick={onSelect}
              className="flex-1 flex flex-col items-start gap-0.5 pl-4 pr-3.5 py-2.5 text-left">
        <span className="flex items-center gap-1.5 text-sm font-semibold">
          {label}
          {isNew && <span className="px-1.5 py-px rounded-full bg-amber-100 text-amber-800 text-[11px] font-bold">New</span>}
        </span>
        <span className={clsx("text-xs", active ? "text-stone-200" : todo ? "text-blue-700 font-semibold" : "text-stone-500")}>{sub}</span>
      </button>
      {onDelete && (
        <button type="button" onClick={onDelete} aria-label={deleteLabel} title={deleteLabel}
                className={clsx("w-9 text-lg leading-none border-l transition-colors",
                  active ? "border-stone-700 text-stone-200 hover:bg-stone-800"
                         : "border-stone-200 text-stone-500 hover:bg-red-50 hover:text-red-700")}>
          ×
        </button>
      )}
    </div>
  );
}

// ── Confirm delete (2026-11-22) ───────────────────────────────────────────────

function DeleteRoundDialog({
  roundNo, products, teamLabel, busy, onConfirm, onClose,
}: {
  roundNo: number; products: number; teamLabel: string; busy: boolean;
  onConfirm: () => void; onClose: () => void;
}) {
  const keepRef = useRef<HTMLButtonElement>(null);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    keepRef.current?.focus();   // the safe choice has focus, so Enter never deletes by accident
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") closeRef.current(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="fixed inset-0 z-50 bg-stone-900/45 flex items-center justify-center p-4" onClick={() => !busy && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="delete-round-title"
           className="w-full max-w-md bg-white rounded-2xl shadow-2xl p-7 space-y-5" onClick={(e) => e.stopPropagation()}>
        <div className="flex gap-3.5 items-start">
          <span className="w-11 h-11 flex-shrink-0 rounded-full bg-red-100 text-red-700 flex items-center justify-center">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" className="w-5 h-5">
              <path d="M3 6h18" /><path d="M8 6V4h8v2" /><path d="M6 6l1 14h10l1-14" /><path d="M10 11v6" /><path d="M14 11v6" />
            </svg>
          </span>
          <div className="space-y-1.5">
            <h2 id="delete-round-title" className="text-lg font-bold text-stone-900">Delete Round {roundNo}?</h2>
            <p className="text-sm text-stone-600 leading-relaxed">
              This removes the {plural(products, "product")} in Round {roundNo} from {teamLabel}&apos;s recommendations for this report. It can&apos;t be undone.
            </p>
          </div>
        </div>
        <ul className="rounded-xl bg-stone-100 px-4 py-3.5 space-y-2 text-sm text-stone-800">
          <li className="flex gap-2 items-start"><Check className="w-4 h-4 mt-0.5 flex-shrink-0 text-emerald-600" />Your Good / Bad marks are kept and still guide future rounds.</li>
          <li className="flex gap-2 items-start"><Check className="w-4 h-4 mt-0.5 flex-shrink-0 text-emerald-600" />Products you marked Bad won&apos;t come back.</li>
        </ul>
        <div className="flex justify-end gap-2.5">
          <button ref={keepRef} type="button" disabled={busy} onClick={onClose}
                  className="h-11 px-4 rounded-xl border border-stone-300 bg-white text-sm font-semibold text-stone-800 hover:bg-stone-50">
            Keep round
          </button>
          <button type="button" disabled={busy} onClick={onConfirm}
                  className="h-11 px-4 rounded-xl bg-red-700 text-sm font-semibold text-white hover:bg-red-800 disabled:opacity-60">
            {busy ? "Deleting…" : `Delete Round ${roundNo}`}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── Evidence viewer ───────────────────────────────────────────────────────────

function EvidenceViewer({
  uploads, evidence, onClose,
}: { uploads: RecUpload[]; evidence: Evidence; onClose: () => void }) {
  const upload = uploads.find((u) => u.id === evidence.upload_id);
  const element = upload?.elements?.find((e) => e.id === evidence.element_id);
  const bbox = element?.bbox;
  const fileUrl = `${API_BASE}/api/aldi/uploads/${evidence.upload_id}/file`;
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose} role="dialog" aria-modal="true">
      <div className="bg-white rounded-2xl shadow-2xl max-w-5xl w-full max-h-[92vh] overflow-auto" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between gap-4 px-5 py-3 border-b border-stone-200">
          <div>
            <div className="text-sm font-semibold text-stone-900">
              Page {evidence.page ?? "?"} · {evidence.element_id}
              {upload && <span className="ml-2 font-normal text-stone-500">{upload.filename}</span>}
            </div>
            <div className="text-sm text-stone-600 mt-0.5">{element?.description ?? evidence.description ?? ""}</div>
          </div>
          <button onClick={onClose} className="text-stone-400 hover:text-stone-900 text-2xl leading-none px-2" aria-label="Close">×</button>
        </div>
        <div className="p-4">
          <div className="relative inline-block max-w-full">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={fileUrl} alt={upload?.filename ?? "report page"} className="max-w-full h-auto block rounded-lg" />
            {bbox && bbox.length === 4 && (
              <div
                className="absolute border-[3px] border-amber-400 rounded-sm shadow-[0_0_0_9999px_rgba(0,0,0,0.35)] pointer-events-none"
                style={{ left: `${bbox[0] * 100}%`, top: `${bbox[1] * 100}%`, width: `${bbox[2] * 100}%`, height: `${bbox[3] * 100}%` }}
              />
            )}
          </div>
          {!bbox && <p className="text-xs text-stone-400 mt-2">No box recorded for this element — showing the whole page.</p>}
        </div>
      </div>
    </div>
  );
}

// ── Feedback row (shared by picks and near misses) ─────────────────────────────
// Choose Good or Bad, add an optional note, press Submit (2026-11-04). A Bad
// hides the card (the parent derives that from the saved verdict); the parent
// shows an undo for a few seconds and a "hidden" toggle afterwards.

function FeedbackRow({
  feedback, onSubmit, placeholder,
}: {
  feedback: Feedback[];
  onSubmit: (verdict: "good" | "bad", note: string) => Promise<void>;
  placeholder: string;
}) {
  const latest = feedback.length ? feedback[feedback.length - 1] : null;
  const [verdict, setVerdict] = useState<"good" | "bad" | null>((latest?.verdict as "good" | "bad") ?? null);
  const [note, setNote] = useState(latest?.note ?? "");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    const l = feedback.length ? feedback[feedback.length - 1] : null;
    setVerdict((l?.verdict as "good" | "bad") ?? null);
    setNote(l?.note ?? "");
  }, [feedback]);

  const savedVerdict = (latest?.verdict as "good" | "bad" | undefined) ?? null;
  const savedNote = latest?.note ?? "";
  const changed = verdict !== null && (verdict !== savedVerdict || note.trim() !== savedNote.trim());

  async function submit() {
    if (!verdict) return;
    setSaving(true);
    try {
      await onSubmit(verdict, note);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" disabled={saving} onClick={() => setVerdict("good")}
              className={clsx("px-3 py-1 rounded-md text-xs font-medium border transition-colors",
                verdict === "good" ? "bg-emerald-600 border-emerald-600 text-white" : "bg-white border-stone-300 text-stone-700 hover:bg-emerald-50")}>
        👍 Good
      </button>
      <button type="button" disabled={saving} onClick={() => setVerdict("bad")}
              className={clsx("px-3 py-1 rounded-md text-xs font-medium border transition-colors",
                verdict === "bad" ? "bg-rose-600 border-rose-600 text-white" : "bg-white border-stone-300 text-stone-700 hover:bg-rose-50")}>
        👎 Bad
      </button>
      <input
        value={note}
        onChange={(e) => setNote(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter" && changed) submit(); }}
        placeholder={placeholder}
        className="flex-1 min-w-[12rem] text-xs border border-stone-300 rounded-md px-2 py-1 bg-white"
      />
      <button type="button" disabled={saving || !changed} onClick={submit}
              className={clsx("px-3 py-1 rounded-md text-xs font-semibold transition-colors",
                changed ? "bg-stone-900 text-white hover:bg-stone-700" : "bg-stone-200 text-stone-400 cursor-not-allowed")}>
        {saving ? "Saving…" : savedVerdict && !changed ? "Saved" : "Submit"}
      </button>
    </div>
  );
}

function latestVerdict(feedback: Feedback[] | undefined): string | null {
  return feedback && feedback.length ? feedback[feedback.length - 1].verdict : null;
}

function UndoLine({ feedback, onUndo }: { feedback: Feedback[]; onUndo: (feedbackId: number) => Promise<void> }) {
  const latest = feedback.length ? feedback[feedback.length - 1] : null;
  if (!latest) return null;
  return (
    <div className="text-xs text-stone-500 pl-1">
      Hidden{latest.note ? ` — “${latest.note}”` : ""} ·{" "}
      <button type="button" onClick={() => onUndo(latest.id)} className="font-semibold text-blue-700 hover:underline">Undo</button>
    </div>
  );
}

// ── Card ──────────────────────────────────────────────────────────────────────

function RecCard({
  rec, onFeedback, onShowEvidence, hidden,
}: {
  rec: Rec;
  onFeedback: (recId: number, verdict: "good" | "bad", note: string, name: string) => Promise<void>;
  hidden?: boolean;
  onShowEvidence: (ev: Evidence) => void;
}) {
  const verdict = latestVerdict(rec.feedback);
  const src = thumbSrc(rec);

  return (
    <div className={clsx(
      "bg-white rounded-xl border p-4 flex gap-4",
      verdict === "good" ? "border-emerald-300" : verdict === "bad" ? "border-rose-300" : "border-stone-200",
      hidden && "opacity-60",
    )}>
      <div className="w-28 h-28 flex-shrink-0 rounded-lg bg-stone-100 overflow-hidden flex items-center justify-center">
        {src ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img src={src} alt={rec.product_name} className="w-full h-full object-cover" />
        ) : (
          <span className="text-stone-300 text-xs">no image</span>
        )}
      </div>
      <div className="flex-1 min-w-0">
        <div className="min-w-0">
          <a href={rec.url} target="_blank" rel="noopener noreferrer"
             className="font-semibold text-blue-700 hover:underline break-words">
            {rec.position}. {rec.product_name}
          </a>
          {!verdict && <span className="ml-2 align-middle"><StatusPill verdict={null} /></span>}
          <div className="text-sm font-bold text-stone-800 mt-1">
            {rec.retailer ?? new URL(rec.url).hostname}
            {rec.link_status === "gone" && (
              <span className="ml-2 text-xs font-medium text-rose-700">link no longer works</span>
            )}
          </div>
        </div>

        {rec.evidence.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {rec.evidence.map((ev) => (
              <button key={`${ev.upload_id}-${ev.element_id}`} type="button" onClick={() => onShowEvidence(ev)}
                      title={ev.description ?? ""}
                      className="text-xs px-2 py-1 rounded-md border border-stone-300 bg-stone-50 hover:bg-stone-100 text-stone-700">
                See in report · page {ev.page ?? "?"} {ev.element_id}
              </button>
            ))}
          </div>
        )}

        <div className="mt-3">
          <FeedbackRow feedback={rec.feedback} onSubmit={(v, n) => onFeedback(rec.id, v, n, rec.product_name)}
                       placeholder="Why? e.g. not functional enough, focus on needlepoint…" />
        </div>
      </div>
    </div>
  );
}

// ── Near miss ─────────────────────────────────────────────────────────────────

function NearMissRow({
  miss, onFeedback, hidden,
}: {
  miss: NearMiss;
  onFeedback: (recId: number, verdict: "good" | "bad", note: string, name: string) => Promise<void>;
  hidden?: boolean;
}) {
  const verdict = latestVerdict(miss.feedback);
  return (
    <li className={clsx(
      "bg-white rounded-xl border px-4 py-3 space-y-2",
      verdict === "good" ? "border-emerald-300" : verdict === "bad" ? "border-rose-300" : "border-stone-200",
      hidden && "opacity-60",
    )}>
      <div className="text-sm">
        {miss.url ? (
          <a href={miss.url} target="_blank" rel="noopener noreferrer" className="font-semibold text-blue-700 hover:underline break-words">
            {miss.product_name ?? miss.url}
          </a>
        ) : (
          <span className="font-semibold text-stone-900">{miss.product_name}</span>
        )}
        {miss.retailer && <span className="ml-2 font-bold text-stone-800">{miss.retailer}</span>}
        {miss.link_status === "gone" && <span className="ml-2 text-xs font-medium text-rose-700">link no longer works</span>}
      </div>
      {miss.id != null && (
        <FeedbackRow feedback={miss.feedback ?? []} onSubmit={(v, n) => onFeedback(miss.id!, v, n, miss.product_name ?? miss.url ?? "product")}
                     placeholder="Would you take it anyway? Say why, and the next round learns from it…" />
      )}
    </li>
  );
}

// ── Compact row for the All rounds tab (2026-11-22) ───────────────────────────

function CompactRec({
  rec, onFeedback,
}: {
  rec: Rec;
  onFeedback: (recId: number, verdict: "good" | "bad", note: string, name: string) => Promise<void>;
}) {
  const verdict = latestVerdict(rec.feedback);
  const src = thumbSrc(rec);
  return (
    <li className={clsx("bg-white rounded-xl border px-3 py-2.5 space-y-2",
      verdict === "good" ? "border-emerald-300" : verdict === "bad" ? "border-rose-300" : "border-stone-200")}>
      <div className="flex items-center gap-3.5">
        <div className="w-14 h-14 flex-shrink-0 rounded-lg bg-stone-100 overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          {src && <img src={src} alt={rec.product_name} className="w-full h-full object-cover" />}
        </div>
        <div className="flex-1 min-w-0">
          <a href={rec.url} target="_blank" rel="noopener noreferrer"
             className="block text-sm font-semibold text-blue-700 hover:underline truncate">{rec.product_name}</a>
          <span className="text-xs font-semibold text-stone-700">{rec.retailer ?? new URL(rec.url).hostname}</span>
        </div>
        <StatusPill verdict={verdict} />
      </div>
      <FeedbackRow feedback={rec.feedback} onSubmit={(v, n) => onFeedback(rec.id, v, n, rec.product_name)}
                   placeholder="Why? e.g. not functional enough, focus on needlepoint…" />
    </li>
  );
}

// ── Team reel ─────────────────────────────────────────────────────────────────

function TeamReel({
  teamKey, block, uploads, onStart, onCancel, onDeleteSet, onFeedback, onUndoFeedback, onSavePrefs, onDistill, onShowEvidence,
}: {
  teamKey: string;
  block: TeamBlock;
  uploads: RecUpload[];
  onStart: (team: string) => Promise<void>;
  onCancel: (runId: number) => Promise<void>;
  onDeleteSet: (runId: number) => Promise<void>;
  onFeedback: (recId: number, verdict: "good" | "bad", note: string) => Promise<Feedback | null>;
  onUndoFeedback: (feedbackId: number) => Promise<void>;
  onSavePrefs: (team: string, prefs: Prefs) => Promise<void>;
  onDistill: (team: string) => Promise<void>;
  onShowEvidence: (ev: Evidence) => void;
}) {
  void uploads;
  const runs = block.runs;
  const latest = block.latest;
  const inFlight = !!latest && (latest.status === "queued" || latest.status === "running");
  // Rounds = finished runs, oldest first, numbered 1..n so deletions leave no gaps.
  const rounds = runs.filter((r) => r.status === "done").sort((a, b) => a.generation - b.generation);
  const roundNo = new Map(rounds.map((r, i) => [r.id, i + 1]));
  const newest = rounds.length ? rounds[rounds.length - 1] : null;
  const newestId = newest?.id ?? null;

  const [view, setView] = useState<number | "all" | null>(null);
  const [showPrefs, setShowPrefs] = useState(false);
  const [starting, setStarting] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [pendingDelete, setPendingDelete] = useState<Run | null>(null);
  const [prefs, setPrefs] = useState(block.preferences);
  const [tick, setTick] = useState(0);
  // Bad hides a card: a short undo line first, then a "hidden" toggle.
  const [showHidden, setShowHidden] = useState(false);
  const [justHidden, setJustHidden] = useState<{ name: string; feedbackId: number } | null>(null);

  const showAll = view === "all" && rounds.length > 1;
  const active = showAll ? null : (rounds.find((r) => r.id === view) ?? newest);

  useEffect(() => {
    if (!justHidden) return;
    const t = setTimeout(() => setJustHidden(null), 10000);
    return () => clearTimeout(t);
  }, [justHidden]);
  const handleFeedback = useCallback(async (recId: number, verdict: "good" | "bad", note: string, name: string) => {
    const saved = await onFeedback(recId, verdict, note);
    if (saved && verdict === "bad" && !showAll) setJustHidden({ name, feedbackId: saved.id });
  }, [onFeedback, showAll]);

  useEffect(() => { setPrefs(block.preferences); }, [block.preferences]);
  // Open the newest round on load and whenever a new round arrives.
  const lastNewest = useRef<number | null>(null);
  useEffect(() => {
    if (newestId !== lastNewest.current) {
      lastNewest.current = newestId;
      setView(newestId);
    }
  }, [newestId]);
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [inFlight]);
  void tick;

  // Live progress bar (2026-11-22): follows the worker's real counts and
  // creeps forward in between, never backwards, never past 98% until done.
  const typical = typicalSecs(runs);
  const stageRef = useRef({ key: "", since: Date.now() });
  const pctRef = useRef({ id: 0, pct: 0 });
  let livePct = 0;
  if (inFlight && latest) {
    const key = `${latest.id}:${latest.status}:${latest.stage}`;
    if (stageRef.current.key !== key) stageRef.current = { key, since: Date.now() };
    if (pctRef.current.id !== latest.id) pctRef.current = { id: latest.id, pct: 0 };
    const target = targetPct(latest, latest.progress ?? {}, (Date.now() - stageRef.current.since) / 1000);
    pctRef.current.pct = Math.min(98, Math.max(target, pctRef.current.pct + 0.15));
    livePct = Math.round(pctRef.current.pct);
  }

  // Finish moment: banner, tab glow, browser tab title, optional notification.
  const [celebrate, setCelebrate] = useState<number | null>(null);
  const [notifyOn, setNotifyOn] = useState(false);
  const canNotify = typeof window !== "undefined" && "Notification" in window && Notification.permission !== "denied";
  const prevLatest = useRef<{ id: number; status: string } | null>(null);
  const baseTitle = useRef("");
  useEffect(() => {
    baseTitle.current = document.title;
    return () => { if (baseTitle.current) document.title = baseTitle.current; };
  }, []);
  useEffect(() => {
    if (!latest) return;
    const prev = prevLatest.current;
    prevLatest.current = { id: latest.id, status: latest.status };
    if (prev && prev.id === latest.id && (prev.status === "queued" || prev.status === "running") && latest.status === "done") {
      setCelebrate(latest.id);
      if (notifyOn && document.hidden && "Notification" in window && Notification.permission === "granted") {
        try {
          new Notification(`Round ${roundNo.get(latest.id) ?? ""} is ready`,
            { body: `${plural(latest.recommendations.length, "new product")} for ${block.label}.` });
        } catch { /* some browsers only allow notifications from a service worker */ }
      }
    }
  }, [latest?.id, latest?.status]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const base = baseTitle.current || document.title;
    if (inFlight) document.title = `Finding products ${livePct}% · ${base}`;
    else if (celebrate && document.hidden) document.title = `✓ Round ${roundNo.get(celebrate) ?? ""} ready · ${base}`;
    else document.title = base;
  }, [inFlight, livePct, celebrate]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const onVisible = () => { if (!document.hidden && !inFlight && baseTitle.current) document.title = baseTitle.current; };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [inFlight]);
  async function enableNotify() {
    if (!("Notification" in window)) return;
    const perm = Notification.permission === "default" ? await Notification.requestPermission() : Notification.permission;
    setNotifyOn(perm === "granted");
  }

  const counts = (r: Run) => {
    const products = r.recommendations.length;
    const toReview = r.recommendations.filter((x) => !x.feedback.length).length;
    return { products, toReview, sub: products === 0 ? "No products" : `${plural(products, "product")} · ${toReview ? `${toReview} to review` : "all rated"}` };
  };
  const totalProducts = rounds.reduce((n, r) => n + r.recommendations.length, 0);
  const totalToReview = rounds.reduce((n, r) => n + counts(r).toReview, 0);
  const newestCounts = newest ? counts(newest) : null;
  let goodMarks = 0, badMarks = 0;
  for (const r of runs) {
    for (const item of [...r.recommendations, ...r.near_misses]) {
      const v = latestVerdict(item.feedback);
      if (v === "good") goodMarks++; else if (v === "bad") badMarks++;
    }
  }

  async function cancel(runId: number) {
    if (!confirm("Cancel this search? Anything already spent on it stays in Costs.")) return;
    setCancelling(true);
    try { await onCancel(runId); } finally { setCancelling(false); }
  }

  async function confirmDelete() {
    if (!pendingDelete) return;
    setDeleting(true);
    try { await onDeleteSet(pendingDelete.id); } finally { setDeleting(false); setPendingDelete(null); }
  }

  async function start() {
    setStarting(true);
    try { await onStart(teamKey); } finally { setStarting(false); }
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="text-sm text-stone-600">
          {block.families.map((f) => (
            <span key={f} className="inline-block mr-1.5 px-2 py-0.5 rounded-full bg-stone-100 border border-stone-200 text-xs">{FAMILY_LABEL[f] ?? f}</span>
          ))}
        </div>
        <div className="flex flex-wrap items-start gap-3">
          <button type="button" onClick={() => setShowPrefs((v) => !v)} aria-expanded={showPrefs}
                  className={clsx("flex items-center gap-2 h-11 px-4 rounded-xl border text-sm font-semibold transition-colors",
                    showPrefs ? "border-violet-600 bg-violet-50 text-violet-800" : "border-stone-300 bg-white text-stone-800 hover:bg-stone-50")}>
            <Sparkle className="w-4 h-4 text-violet-600" />
            AI prompt
          </button>
          {inFlight ? (
            <button type="button" disabled
                    className="flex items-center gap-2.5 h-12 px-6 rounded-xl bg-stone-700 text-stone-100 text-base font-bold cursor-wait">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} strokeLinecap="round" aria-hidden="true" className="w-[18px] h-[18px] text-amber-300 animate-spin">
                <path d="M21 12a9 9 0 1 1-6.2-8.6" />
              </svg>
              Finding products…
            </button>
          ) : (
            <div className="flex flex-col gap-1.5">
              <button type="button" disabled={starting} onClick={start}
                      className="flex items-center justify-center gap-2.5 h-12 px-6 rounded-xl bg-stone-900 text-white text-base font-bold shadow-lg shadow-stone-900/20 hover:bg-stone-700 disabled:opacity-60 transition-colors">
                <Sparkle double className="w-[18px] h-[18px] text-amber-300" />
                {starting ? "Starting…" : rounds.length === 0 ? "Find Products" : "Find More Products"}
              </button>
              {newestCounts && newestCounts.products > 0 && (
                <div className="flex items-center gap-2 text-[11px] text-stone-600" title="Rate the newest round to sharpen the next one">
                  <div className="flex-1 h-1.5 rounded-full bg-stone-200 overflow-hidden" aria-hidden="true">
                    <div className="h-full rounded-full bg-emerald-600 transition-all"
                         style={{ width: `${Math.round(100 * (newestCounts.products - newestCounts.toReview) / newestCounts.products)}%` }} />
                  </div>
                  <span>{newestCounts.toReview === 0 ? `All ${newestCounts.products} rated` : `${newestCounts.products - newestCounts.toReview} of ${newestCounts.products} rated`}</span>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {showPrefs && (
        <div className="bg-violet-50/60 border border-violet-200 rounded-2xl p-5 space-y-4">
          <div>
            <h4 className="text-base font-bold text-stone-900">AI prompt · {block.label}</h4>
            <p className="text-sm text-stone-600 mt-0.5">Everything here is sent to the AI on every search for {block.label}&apos;s team.</p>
          </div>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label htmlFor={`pmin-${teamKey}`} className="text-stone-700 font-medium">Price band (USD)</label>
            <input id={`pmin-${teamKey}`} type="number" min={0} value={prefs.price_min}
                   onChange={(e) => setPrefs({ ...prefs, price_min: Number(e.target.value) })}
                   className="w-20 border border-stone-300 rounded-lg px-2 py-1.5 bg-white" />
            <span className="text-stone-500">to</span>
            <input aria-label="Maximum price (USD)" type="number" min={0} value={prefs.price_max}
                   onChange={(e) => setPrefs({ ...prefs, price_max: Number(e.target.value) })}
                   className="w-20 border border-stone-300 rounded-lg px-2 py-1.5 bg-white" />
          </div>
          <div>
            <label htmlFor={`memo-${teamKey}`} className="text-sm font-semibold text-stone-900">Your instructions to the AI</label>
            <p className="text-xs text-stone-500">Written by the team. Read before every search.</p>
            <textarea id={`memo-${teamKey}`} value={prefs.memo} onChange={(e) => setPrefs({ ...prefs, memo: e.target.value })} rows={3}
                      placeholder="e.g. Prefer products with a clear functional story; avoid purely decorative pieces."
                      className="mt-1.5 w-full text-sm border border-stone-300 rounded-lg px-3 py-2 bg-white" />
          </div>
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label htmlFor={`learned-${teamKey}`} className="text-sm font-semibold text-stone-900">What the AI learned from your Good / Bad marks</label>
              <button type="button" onClick={() => onDistill(teamKey)}
                      className="text-xs px-2.5 py-1.5 rounded-lg border border-stone-300 bg-white text-stone-700 hover:bg-stone-100">
                Update now
              </button>
            </div>
            <p className="text-xs text-stone-500">
              {prefs.distilled_at
                ? `Updated ${new Date(prefs.distilled_at + "Z").toLocaleString()} from ${plural(prefs.distilled_count, "mark")}. You can edit these.`
                : "Nothing learned yet. It updates itself after a few Good / Bad marks."}
            </p>
            <textarea id={`learned-${teamKey}`} value={prefs.learned_rules} onChange={(e) => setPrefs({ ...prefs, learned_rules: e.target.value })} rows={5}
                      placeholder="Rules the AI has learned from this team's Good / Bad marks appear here. You can edit them."
                      className="mt-1.5 w-full text-sm border border-stone-300 rounded-lg px-3 py-2 bg-white font-mono" />
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={() => onSavePrefs(teamKey, prefs)}
                    className="h-10 px-4 rounded-xl text-sm font-semibold bg-stone-900 text-white hover:bg-stone-700">Save</button>
          </div>
        </div>
      )}

      {latest && latest.status === "cancelled" && !inFlight && (
        <div className="bg-stone-100 border border-stone-200 text-stone-700 text-sm rounded-xl px-4 py-3">
          The last search was cancelled{latest.error && latest.error !== "Cancelled." ? `: ${latest.error}` : "."} Find More Products starts a fresh round.
        </div>
      )}

      {latest && latest.status === "failed" && !inFlight && (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 text-sm rounded-xl px-4 py-3">
          The last search failed{latest.error ? `: ${latest.error}` : "."} Find More Products starts a fresh round.
        </div>
      )}

      {(rounds.length > 0 || inFlight) && (
        <div className="space-y-2.5 pt-1">
          {rounds.length > 0 && (
            <p className="text-sm text-stone-600">
              <strong className="text-stone-900">{plural(totalProducts, "product")} across {plural(rounds.length, "round")}.</strong>
              {rounds.length > 1 && " Click each round to see them all."}
              {totalToReview > 0 && ` ${totalToReview} still to review.`}
            </p>
          )}
          <div role="tablist" aria-label="Rounds" className="flex flex-wrap gap-2">
            {rounds.length > 1 && (
              <RoundTab label="All rounds" active={showAll} onSelect={() => setView("all")} todo={totalToReview > 0}
                        sub={`${plural(totalProducts, "product")}${totalToReview ? ` · ${totalToReview} to review` : ""}`} />
            )}
            {rounds.map((r) => {
              const n = roundNo.get(r.id)!;
              const c = counts(r);
              return (
                <RoundTab key={r.id} label={`Round ${n}`} sub={c.sub} todo={c.toReview > 0}
                          isNew={r.id === newestId && rounds.length > 1} active={!showAll && active?.id === r.id}
                          onSelect={() => setView(r.id)} onDelete={() => setPendingDelete(r)} deleteLabel={`Delete Round ${n}`}
                          glow={celebrate === r.id} />
              );
            })}
            {inFlight && (
              <div className="inline-flex flex-col items-start justify-center gap-0.5 min-w-[8.5rem] pl-4 pr-3.5 py-2.5 rounded-xl border-2 border-dashed border-amber-400 bg-amber-50">
                <span className="text-sm font-semibold text-stone-900">Round {rounds.length + 1}</span>
                <span className="text-xs font-semibold text-amber-800">{livePct}% · Finding…</span>
              </div>
            )}
          </div>
        </div>
      )}

      {inFlight && latest && (() => {
        const p = latest.progress ?? {};
        const step = latest.status === "queued" ? -1 : (STAGE_STEP[latest.stage ?? ""] ?? 0);
        const el = secsSince(latest.started_at ?? latest.created_at);
        const recent = p.recent ?? [];
        const retailers = p.retailers ?? [];
        const feed = p.feed ?? [];
        const toVerify = p.to_verify ?? 0;
        const nudge = rounds.slice().reverse().find((r) => counts(r).toReview > 0);
        const card = (on: boolean) => clsx("bg-white rounded-xl px-4 py-3 flex flex-col gap-0.5", on ? "border-2 border-amber-500" : "border border-amber-200");
        return (
          <div className="rounded-2xl border border-amber-200 bg-amber-50 p-5 space-y-4">
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <h4 className="text-base font-bold text-stone-900">Finding Round {rounds.length + 1} for {block.label}</h4>
              <span className="text-sm text-stone-700 tabular-nums">
                <strong className="text-stone-900">{step < 0 ? "Starting…" : etaText(el, typical)}</strong> · {fmtClock(el)} so far
              </span>
            </div>

            <div className="space-y-2">
              <div className="relative h-2.5 rounded-full bg-amber-200 overflow-hidden" role="progressbar" aria-valuenow={livePct} aria-valuemin={0} aria-valuemax={100} aria-label="Search progress">
                <div className="absolute inset-y-0 left-0 rounded-full bg-amber-600 transition-all duration-1000 ease-out" style={{ width: `${livePct}%` }} />
                <div className="absolute inset-y-0 w-[8%] rounded-full bg-white/50 animate-pulse transition-all duration-1000" style={{ left: `${Math.max(0, livePct - 8)}%` }} />
              </div>
              <ol className="grid grid-cols-2 sm:grid-cols-4 gap-1 text-xs">
                {STEPS.map((st, i) => (
                  <li key={st.label} className={clsx(i < step ? "text-emerald-800 font-semibold" : i === step ? "text-amber-900 font-bold" : "text-stone-500")}>
                    {i < step ? "✓ " : i === step ? "● " : ""}{st.label}
                  </li>
                ))}
              </ol>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              <div className={card(step === 0)}>
                <span className="text-2xl font-extrabold tabular-nums">{p.queries_total || "…"}</span>
                <span className="text-xs text-stone-600">{p.queries_total ? `searches written · ${p.queries_done ?? 0} run` : "searches being written from your report"}</span>
              </div>
              <div className={card(step === 1)}>
                <span className="text-2xl font-extrabold tabular-nums">{p.found ?? 0}</span>
                <span className="text-xs text-stone-600">{toVerify ? `products found, shortlisted to ${toVerify}` : "products found so far"}</span>
              </div>
              <div className={card(step === 2)}>
                <span className={clsx("text-2xl font-extrabold tabular-nums", !toVerify && "text-stone-400")}>
                  {p.checked ?? 0} <span className="text-stone-400 font-bold">/ {toVerify || 15}</span>
                </span>
                {toVerify > 0 && (
                  <div className="flex gap-0.5 my-1" aria-hidden="true">
                    {Array.from({ length: toVerify }, (_, i) => (
                      <span key={i} className={clsx("flex-1 h-1.5 rounded-full", i < (p.checked ?? 0) ? "bg-amber-600" : "bg-stone-200")} />
                    ))}
                  </div>
                )}
                <span className="text-xs text-stone-600">product pages checked</span>
              </div>
            </div>

            {step >= 2 && feed.length > 0 ? (
              <div className="bg-white border border-amber-200 rounded-xl px-4 py-3.5 space-y-2">
                <span className="text-[11px] font-bold tracking-wider text-amber-800">JUST CHECKED</span>
                <ul className="space-y-2">
                  {feed.slice(0, 4).map((f, i) => (
                    <li key={`${f.name}-${i}`} className={clsx("flex items-center gap-3", i === 1 && "opacity-85", i >= 2 && "opacity-60")}>
                      <span className="w-10 h-10 flex-shrink-0 rounded-lg bg-stone-100 overflow-hidden">
                        {/* eslint-disable-next-line @next/next/no-img-element */}
                        {f.image && <img src={f.image} alt="" className="w-full h-full object-cover"
                                         onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = "none"; }} />}
                      </span>
                      <span className="flex-1 min-w-0">
                        <span className="block text-sm font-semibold text-stone-900 truncate">{f.name}</span>
                        <span className="text-xs text-stone-500">{f.retailer}</span>
                      </span>
                      <span className={clsx("px-2 py-0.5 rounded-full text-xs whitespace-nowrap", f.ok ? "bg-emerald-100 text-emerald-900 font-semibold" : "bg-stone-100 text-stone-600")}>
                        {f.ok ? "✓ " : ""}{f.tag}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            ) : (
              <div className="bg-white border border-amber-200 rounded-xl px-4 py-3.5 space-y-2.5">
                <span className="text-[11px] font-bold tracking-wider text-amber-800">NOW SEARCHING</span>
                {recent.length === 0 ? (
                  <p className="text-sm text-stone-600">{step < 0 ? "Waiting for a worker to pick this up…" : "Writing searches from the report and your marks…"}</p>
                ) : (
                  <div className="space-y-1.5 text-sm">
                    {recent.map((q, i) => i === recent.length - 1 ? (
                      <p key={`${q}-${i}`} className="font-semibold text-stone-900">“{q}”<span className="text-amber-600 animate-pulse"> ▍</span></p>
                    ) : (
                      <p key={`${q}-${i}`} className={i === recent.length - 2 ? "text-stone-500" : "text-stone-400"}>✓ “{q}”</p>
                    ))}
                  </div>
                )}
                {retailers.length > 0 && (
                  <div className="flex flex-wrap items-center gap-1.5">
                    <span className="text-xs text-stone-500 mr-0.5">Retailers:</span>
                    {retailers.slice(0, 10).map((r) => (
                      <span key={r.name} className={clsx("px-2 py-0.5 rounded-full text-xs", r.done ? "bg-emerald-100 text-emerald-900 font-semibold" : "bg-stone-100 text-stone-600")}>
                        {r.name}{r.done ? " ✓" : ""}
                      </span>
                    ))}
                    <span className="px-2 py-0.5 rounded-full bg-stone-100 text-stone-600 text-xs">+ the open web</span>
                  </div>
                )}
              </div>
            )}

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-amber-200 pt-3">
              <span className="text-sm text-stone-700">
                {nudge ? (
                  <>While you wait: <strong className="text-stone-900">{plural(counts(nudge).toReview, "product")} in Round {roundNo.get(nudge.id)}</strong> {counts(nudge).toReview === 1 ? "still needs" : "still need"} a Good / Bad.{" "}
                    <button type="button" onClick={() => setView(nudge.id)} className="font-semibold text-blue-700 hover:underline">Review {counts(nudge).toReview === 1 ? "it" : "them"}</button></>
                ) : goodMarks + badMarks > 0 ? (
                  `Using your ${[goodMarks ? plural(goodMarks, "Good mark") : "", badMarks ? plural(badMarks, "Bad mark") : ""].filter(Boolean).join(" and ")} to steer this round.`
                ) : "Mark products Good or Bad and the next round learns from it."}
              </span>
              <span className="flex items-center gap-2">
                {canNotify && (
                  <button type="button" onClick={enableNotify} disabled={notifyOn}
                          className="h-9 px-3.5 rounded-lg border border-stone-300 bg-white text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:text-emerald-800 disabled:border-emerald-300 disabled:bg-emerald-50">
                    {notifyOn ? "✓ We'll notify you" : "Notify me when it's done"}
                  </button>
                )}
                <button type="button" disabled={cancelling} onClick={() => cancel(latest.id)}
                        className="h-9 px-3.5 rounded-lg border border-stone-300 bg-white text-sm font-medium text-stone-700 hover:bg-stone-100 disabled:opacity-50">
                  {cancelling ? "Cancelling…" : "Cancel"}
                </button>
              </span>
            </div>
          </div>
        );
      })()}

      {celebrate && !inFlight && (() => {
        const r = rounds.find((x) => x.id === celebrate);
        if (!r) return null;
        const n = roundNo.get(r.id)!;
        const k = r.recommendations.length;
        const took = r.started_at && r.finished_at
          ? (new Date(r.finished_at + "Z").getTime() - new Date(r.started_at + "Z").getTime()) / 1000 : null;
        const st = r.stats || {};
        return (
          <div className="rounded-2xl border border-emerald-300 bg-emerald-50 p-5 flex flex-wrap items-center gap-4" role="status">
            <span className="w-12 h-12 flex-shrink-0 rounded-full bg-emerald-600 text-white flex items-center justify-center"><Check className="w-7 h-7" /></span>
            <div className="flex-1 min-w-[16rem]">
              <h4 className="text-lg font-extrabold text-stone-900">{k ? `${plural(k, "new product")} found for ${block.label}` : `Round ${n} finished`}</h4>
              <p className="text-sm text-stone-700">
                {k ? `Round ${n} is ready` : "No product met every criterion this time; its near misses are listed below"}
                {took != null && ` · took ${fmtDuration(took)}`}
                {st.found ? ` · ${st.found} products considered, ${st.to_verify ?? 0} checked${k ? `, the best ${k} kept` : ""}` : ""}.
              </p>
            </div>
            {k > 0 && (
              <div className="flex gap-1.5">
                {r.recommendations.slice(0, 5).map((x) => {
                  const src = thumbSrc(x);
                  return (
                    <span key={x.id} className="w-12 h-12 rounded-lg bg-white border-2 border-white shadow overflow-hidden">
                      {/* eslint-disable-next-line @next/next/no-img-element */}
                      {src && <img src={src} alt="" className="w-full h-full object-cover" />}
                    </span>
                  );
                })}
              </div>
            )}
            <button type="button" onClick={() => { setView(r.id); setCelebrate(null); }}
                    className="h-11 px-4 rounded-xl bg-emerald-700 text-white text-sm font-bold hover:bg-emerald-800">See Round {n}</button>
            <button type="button" aria-label="Dismiss" onClick={() => setCelebrate(null)} className="text-stone-500 hover:text-stone-900 text-xl leading-none px-1">×</button>
          </div>
        );
      })()}

      {showAll && (
        <div className="space-y-5">
          {rounds.slice().reverse().map((r) => {
            const n = roundNo.get(r.id)!;
            return (
              <section key={r.id} className="space-y-2" aria-label={`Round ${n}`}>
                <div className="flex items-center gap-2.5 pt-1">
                  <h4 className="text-[15px] font-bold text-stone-900">Round {n}</h4>
                  {r.id === newestId && <span className="px-1.5 py-px rounded-full bg-amber-100 text-amber-800 text-[11px] font-bold">New</span>}
                  <span className="text-xs text-stone-500">
                    {plural(r.recommendations.length, "product")}{r.finished_at && ` · ${new Date(r.finished_at + "Z").toLocaleString()}`}
                  </span>
                  <div className="flex-1 h-px bg-stone-200" />
                </div>
                {r.recommendations.length === 0 ? (
                  <p className="text-xs text-stone-500">No product met every criterion in this round. Its near misses are on the Round {n} tab.</p>
                ) : (
                  <ul className="space-y-2">
                    {r.recommendations.map((rec) => <CompactRec key={rec.id} rec={rec} onFeedback={handleFeedback} />)}
                  </ul>
                )}
              </section>
            );
          })}
          <p className="text-xs text-stone-500">Near misses are listed on each round&apos;s own tab.</p>
        </div>
      )}

      {active && (() => {
        const n = roundNo.get(active.id)!;
        const isHidden = (fb: Feedback[] | undefined) => latestVerdict(fb) === "bad";
        const visiblePicks = active.recommendations.filter((r) => !isHidden(r.feedback));
        const hiddenPicks = active.recommendations.filter((r) => isHidden(r.feedback));
        const visibleMisses = active.near_misses.filter((m) => !isHidden(m.feedback));
        const hiddenMisses = active.near_misses.filter((m) => isHidden(m.feedback));
        const hiddenCount = hiddenPicks.length + hiddenMisses.length;
        return (
        <>
          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-stone-500">
            <span>
              Round {n} · {plural(active.recommendations.length, "product")}
              {hiddenPicks.length > 0 && ` · ${hiddenPicks.length} hidden`}
              {active.finished_at && ` · ${new Date(active.finished_at + "Z").toLocaleString()}`}
            </span>
            <span className="tabular-nums">
              ${active.cost_usd.toFixed(2)} · {active.searches} searches · {active.fetches} pages opened
            </span>
          </div>

          {justHidden && (
            <div className="flex items-center justify-between gap-3 text-sm bg-stone-100 border border-stone-200 rounded-xl px-4 py-2">
              <span className="text-stone-700 truncate">Hidden: {justHidden.name}</span>
              <button type="button" onClick={() => { onUndoFeedback(justHidden.feedbackId); setJustHidden(null); }}
                      className="text-xs font-semibold text-blue-700 hover:underline flex-shrink-0">Undo</button>
            </div>
          )}

          {active.recommendations.length === 0 ? (
            <p className="text-sm text-stone-500 bg-white border border-stone-200 rounded-xl p-4">
              No product met every criterion this round. The closest misses are listed below. Find More Products searches different angles.
            </p>
          ) : visiblePicks.length === 0 ? (
            <p className="text-sm text-stone-500 bg-white border border-stone-200 rounded-xl p-4">
              {hiddenPicks.length === 1 ? "The product in this round was marked Bad and is hidden." : `All ${hiddenPicks.length} products in this round were marked Bad and are hidden.`}{" "}
              <button type="button" onClick={() => setShowHidden((v) => !v)} className="font-semibold text-blue-700 hover:underline">{showHidden ? "Hide hidden" : "Show hidden"}</button>
              {" "}· Find More Products for a fresh round.
            </p>
          ) : (
            <div className="space-y-3">
              {visiblePicks.map((rec) => (
                <RecCard key={rec.id} rec={rec} onFeedback={handleFeedback} onShowEvidence={onShowEvidence} />
              ))}
            </div>
          )}
          {active.recommendations.length < 5 && active.recommendations.length > 0 && (
            <p className="text-xs text-stone-500">Only {active.recommendations.length} of 5 met every criterion; the bar was not lowered.</p>
          )}
          {hiddenCount > 0 && (
            <div className="space-y-2">
              <button type="button" onClick={() => setShowHidden((v) => !v)}
                      className="text-xs font-medium text-stone-600 hover:text-stone-900">
                {showHidden ? "▾" : "▸"} {hiddenCount} hidden{hiddenPicks.length ? ` (${plural(hiddenPicks.length, "product")}${hiddenMisses.length ? `, ${plural(hiddenMisses.length, "near miss", "near misses")}` : ""})` : ""}{showHidden ? " · hide" : " · show"}
              </button>
              {showHidden && (
                <ul className="space-y-2">
                  {hiddenPicks.map((rec) => (
                    <li key={`p${rec.id}`} className="space-y-1">
                      <RecCard rec={rec} onFeedback={handleFeedback} onShowEvidence={onShowEvidence} hidden />
                      <UndoLine feedback={rec.feedback} onUndo={onUndoFeedback} />
                    </li>
                  ))}
                  {hiddenMisses.map((m, i) => (
                    <li key={`m${m.id ?? i}`} className="space-y-1">
                      <ul><NearMissRow miss={m} onFeedback={handleFeedback} hidden /></ul>
                      <UndoLine feedback={m.feedback ?? []} onUndo={onUndoFeedback} />
                    </li>
                  ))}
                  <li>
                    <button type="button" onClick={() => setShowHidden(false)} className="text-xs font-semibold text-blue-700 hover:underline">▴ Hide these again</button>
                  </li>
                </ul>
              )}
            </div>
          )}
          {visibleMisses.length > 0 && (
            <div className="space-y-2">
              <div className="text-sm font-semibold text-stone-800">
                {plural(visibleMisses.length, "near miss", "near misses")}
                <span className="ml-2 font-normal text-stone-500">
                  Close calls. Mark them too: a Good tells the next round you&apos;d take it anyway.
                </span>
              </div>
              <ul className="space-y-2">
                {visibleMisses.map((m, i) => (
                  <NearMissRow key={m.id ?? i} miss={m} onFeedback={handleFeedback} />
                ))}
              </ul>
            </div>
          )}

        </>
        );
      })()}

      {rounds.length === 0 && !inFlight && runs.length === 0 && (
        <p className="text-sm text-stone-500">
          Five products from the web for {block.label}&apos;s scope, each tied to the screenshot it echoes. Mark each one Good or Bad and the next round learns from it.
        </p>
      )}

      {pendingDelete && (
        <DeleteRoundDialog roundNo={roundNo.get(pendingDelete.id) ?? 0} products={pendingDelete.recommendations.length}
                           teamLabel={block.label} busy={deleting} onConfirm={confirmDelete} onClose={() => setPendingDelete(null)} />
      )}
    </div>
  );
}

// ── Panel ─────────────────────────────────────────────────────────────────────

export default function AldiRecommendationsPanel({ sessionId, uploads, ready }: {
  sessionId: number;
  uploads: RecUpload[];
  /** The report has finished analysing (at least one upload done). */
  ready: boolean;
}) {
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<string>(TEAM_ORDER[0]);
  const [evidence, setEvidence] = useState<Evidence | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/aldi/sessions/${sessionId}/recommendations`, { cache: "no-store" });
      if (!res.ok) throw new Error(await res.text());
      const payload: Payload = await res.json();
      setData(payload);
      setError(null);
      return payload;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load recommendations");
      return null;
    }
  }, [sessionId]);

  const anyInFlight = useMemo(
    () => !!data && Object.values(data.teams).some((t) => t.runs.some((r) => r.status === "queued" || r.status === "running")),
    [data],
  );

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    if (!anyInFlight) return;
    pollRef.current = setInterval(load, 4000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [anyInFlight, load]);

  const onStart = useCallback(async (team: string) => {
    const res = await fetch(`${API_BASE}/api/aldi/sessions/${sessionId}/recommendations`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ team }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Could not start (${res.status})`);
      return;
    }
    await load();
  }, [sessionId, load]);

  const onCancel = useCallback(async (runId: number) => {
    const res = await fetch(`${API_BASE}/api/aldi/recommendation-runs/${runId}/cancel`, { method: "POST" });
    if (!res.ok && res.status !== 409) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Could not cancel (${res.status})`);
    }
    await load();   // 409 = it finished or failed meanwhile; reload shows that
  }, [load]);

  const onDeleteSet = useCallback(async (runId: number) => {
    const res = await fetch(`${API_BASE}/api/aldi/recommendation-runs/${runId}`, { method: "DELETE" });
    if (!res.ok && res.status !== 404) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Could not delete (${res.status})`);
    }
    await load();
  }, [load]);

  const onFeedback = useCallback(async (recId: number, verdict: "good" | "bad", note: string): Promise<Feedback | null> => {
    const res = await fetch(`${API_BASE}/api/aldi/recommendations/${recId}/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ verdict, note }),
    });
    if (!res.ok) { alert("Could not save feedback"); return null; }
    const rec: Rec = await res.json();
    await load();
    return rec.feedback.length ? rec.feedback[rec.feedback.length - 1] : null;
  }, [load]);

  const onUndoFeedback = useCallback(async (feedbackId: number) => {
    const res = await fetch(`${API_BASE}/api/aldi/recommendation-feedback/${feedbackId}`, { method: "DELETE" });
    if (!res.ok) { alert("Could not undo"); return; }
    await load();
  }, [load]);

  const onDistill = useCallback(async (team: string) => {
    const res = await fetch(`${API_BASE}/api/aldi/teams/${team}/distill`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || `Could not queue (${res.status})`);
      return;
    }
    alert("Queued. The rules rewrite themselves within a minute — close and reopen AI prompt to see them.");
    setTimeout(load, 45000);
  }, [load]);

  const onSavePrefs = useCallback(async (team: string, prefs: Prefs) => {
    const res = await fetch(`${API_BASE}/api/aldi/teams/${team}/preferences`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(prefs),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || "Could not save the AI prompt");
      return;
    }
    await load();
  }, [load]);

  return (
    <div className="mt-4 bg-stone-50 border border-stone-200 rounded-2xl p-5">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold text-stone-900 text-lg">Product Recommendations</h3>
          <p className="text-xs text-stone-500 mt-0.5">Five products from the web per round, each tied to the report. Dates in your local time.</p>
        </div>
        <div className="flex gap-1 bg-white border border-stone-200 rounded-lg p-1">
          {TEAM_ORDER.map((key) => {
            const block = data?.teams[key];
            const busy = block?.runs.some((r) => r.status === "queued" || r.status === "running");
            return (
              <button key={key} type="button" onClick={() => setTab(key)}
                      className={clsx("px-3 py-1.5 rounded-md text-sm font-medium transition-colors flex items-center gap-1.5",
                        tab === key ? "bg-stone-900 text-white" : "text-stone-600 hover:bg-stone-100")}>
                {block?.label ?? key}
                {busy && <span className="inline-block w-2 h-2 rounded-full bg-amber-400 animate-pulse" />}
              </button>
            );
          })}
        </div>
      </div>

      {error && <div className="bg-rose-50 border border-rose-200 text-rose-800 text-sm rounded-xl px-4 py-3 mb-3">{error}</div>}

      {!ready ? (
        <p className="text-sm text-stone-500">Available once the report has finished analysing.</p>
      ) : !data ? (
        <p className="text-sm text-stone-500">Loading…</p>
      ) : (
        data.teams[tab] && (
          <TeamReel
            key={tab}
            teamKey={tab}
            block={data.teams[tab]}
            uploads={uploads}
            onStart={onStart}
            onCancel={onCancel}
            onDeleteSet={onDeleteSet}
            onFeedback={onFeedback}
            onUndoFeedback={onUndoFeedback}
            onSavePrefs={onSavePrefs}
            onDistill={onDistill}
            onShowEvidence={setEvidence}
          />
        )
      )}

      {evidence && <EvidenceViewer uploads={uploads} evidence={evidence} onClose={() => setEvidence(null)} />}
    </div>
  );
}
