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
 * buyer marks each pick Good or Bad with a note, and Try Again feeds that
 * back into the next five. Runs happen in the worker; this panel polls
 * while one is in flight.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import clsx from "clsx";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const TEAM_ORDER = ["sapana", "adrah"];

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
interface Run {
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

const STAGE_LABEL: Record<string, string> = {
  planning: "Planning the searches…",
  discovering: "Searching the web…",
  ranking: "Ranking what came back…",
  verifying: "Opening product pages…",
  selecting: "Choosing the five…",
  repairing: "Filling the gaps…",
  saving: "Saving…",
};

const FAMILY_LABEL: Record<string, string> = {
  fabric: "Fabric", metal: "Metal", ceramic: "Ceramic", glass: "Glass", wood: "Wood & bamboo", plastic: "Plastic",
};

function elapsed(run: Run): string {
  const start = run.started_at ?? run.created_at;
  if (!start) return "";
  const s = Math.max(0, Math.round((Date.now() - new Date(start + "Z").getTime()) / 1000));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

function thumbSrc(rec: Rec): string | null {
  if (!rec.thumbnail_url) return null;
  return rec.thumbnail_url.startsWith("/") ? `${API_BASE}${rec.thumbnail_url}` : rec.thumbnail_url;
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
                     placeholder="Would you take it anyway? Say why, and the next set learns from it…" />
      )}
    </li>
  );
}

// ── Team reel ─────────────────────────────────────────────────────────────────

function TeamReel({
  teamKey, block, uploads, onStart, onFeedback, onUndoFeedback, onSavePrefs, onDistill, onShowEvidence,
}: {
  teamKey: string;
  block: TeamBlock;
  uploads: RecUpload[];
  onStart: (team: string) => Promise<void>;
  onFeedback: (recId: number, verdict: "good" | "bad", note: string) => Promise<Feedback | null>;
  onUndoFeedback: (feedbackId: number) => Promise<void>;
  onSavePrefs: (team: string, prefs: Prefs) => Promise<void>;
  onDistill: (team: string) => Promise<void>;
  onShowEvidence: (ev: Evidence) => void;
}) {
  const runs = block.runs;
  const latest = block.latest;
  const inFlight = latest && (latest.status === "queued" || latest.status === "running");
  const doneRuns = runs.filter((r) => r.status === "done");
  const [activeRunId, setActiveRunId] = useState<number | null>(doneRuns[0]?.id ?? null);
  const [showPrefs, setShowPrefs] = useState(false);
  const [starting, setStarting] = useState(false);
  const [prefs, setPrefs] = useState(block.preferences);
  const [tick, setTick] = useState(0);
  // Bad hides a card: a short undo line first, then a "hidden" toggle.
  const [showHidden, setShowHidden] = useState(false);
  const [justHidden, setJustHidden] = useState<{ name: string; feedbackId: number } | null>(null);
  useEffect(() => {
    if (!justHidden) return;
    const t = setTimeout(() => setJustHidden(null), 10000);
    return () => clearTimeout(t);
  }, [justHidden]);
  const handleFeedback = useCallback(async (recId: number, verdict: "good" | "bad", note: string, name: string) => {
    const saved = await onFeedback(recId, verdict, note);
    if (saved && verdict === "bad") setJustHidden({ name, feedbackId: saved.id });
  }, [onFeedback]);

  useEffect(() => { setPrefs(block.preferences); }, [block.preferences]);
  useEffect(() => {
    if (doneRuns.length && (activeRunId == null || !doneRuns.some((r) => r.id === activeRunId))) {
      setActiveRunId(doneRuns[0].id);
    }
  }, [doneRuns, activeRunId]);
  useEffect(() => {
    if (!inFlight) return;
    const t = setInterval(() => setTick((x) => x + 1), 1000);
    return () => clearInterval(t);
  }, [inFlight]);

  const active = doneRuns.find((r) => r.id === activeRunId) ?? doneRuns[0] ?? null;
  void tick;

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
        <div className="flex items-center gap-2">
          <button type="button" onClick={() => setShowPrefs((v) => !v)}
                  className="text-xs px-2.5 py-1.5 rounded-md border border-stone-300 bg-white text-stone-700 hover:bg-stone-100">
            Team settings
          </button>
          {!inFlight && (
            <button type="button" disabled={starting} onClick={start}
                    className={clsx("px-4 py-2 rounded-lg text-sm font-semibold transition-colors",
                      runs.length === 0 ? "bg-stone-900 text-white hover:bg-stone-700" : "bg-amber-50 border border-amber-300 text-amber-900 hover:bg-amber-100")}>
              {runs.length === 0 ? "✨ Product Recommendations" : "🔄 Try Again"}
            </button>
          )}
        </div>
      </div>

      {showPrefs && (
        <div className="bg-stone-50 border border-stone-200 rounded-xl p-4 space-y-3">
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <label className="text-stone-600">Price band (USD)</label>
            <input type="number" min={0} value={prefs.price_min}
                   onChange={(e) => setPrefs({ ...prefs, price_min: Number(e.target.value) })}
                   className="w-20 border border-stone-300 rounded-md px-2 py-1 bg-white" />
            <span className="text-stone-400">to</span>
            <input type="number" min={0} value={prefs.price_max}
                   onChange={(e) => setPrefs({ ...prefs, price_max: Number(e.target.value) })}
                   className="w-20 border border-stone-300 rounded-md px-2 py-1 bg-white" />
          </div>
          <div>
            <label className="text-sm text-stone-600">Standing preferences for {block.label}&apos;s team (read by every run)</label>
            <textarea value={prefs.memo} onChange={(e) => setPrefs({ ...prefs, memo: e.target.value })} rows={3}
                      placeholder="e.g. Prefer products with a clear functional story; avoid purely decorative pieces."
                      className="mt-1 w-full text-sm border border-stone-300 rounded-md px-2 py-1.5 bg-white" />
          </div>
          <div>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <label className="text-sm text-stone-600">
                Learned from feedback
                <span className="ml-2 text-xs text-stone-400">
                  {prefs.distilled_at
                    ? `updated ${new Date(prefs.distilled_at + "Z").toLocaleString()} from ${prefs.distilled_count} judgment${prefs.distilled_count === 1 ? "" : "s"}`
                    : "nothing distilled yet — it updates itself after a few Good / Bad marks"}
                </span>
              </label>
              <button type="button" onClick={() => onDistill(teamKey)}
                      className="text-xs px-2.5 py-1 rounded-md border border-stone-300 bg-white text-stone-700 hover:bg-stone-100">
                Update now
              </button>
            </div>
            <textarea value={prefs.learned_rules} onChange={(e) => setPrefs({ ...prefs, learned_rules: e.target.value })} rows={5}
                      placeholder="Standing rules the recommender has distilled from this team's Good / Bad marks appear here. You can edit them."
                      className="mt-1 w-full text-sm border border-stone-300 rounded-md px-2 py-1.5 bg-white font-mono" />
          </div>
          <div className="flex justify-end">
            <button type="button" onClick={() => onSavePrefs(teamKey, prefs)}
                    className="px-3 py-1.5 rounded-md text-sm font-medium bg-stone-900 text-white hover:bg-stone-700">Save</button>
          </div>
        </div>
      )}

      {inFlight && latest && (
        <div className="bg-white border border-stone-200 rounded-xl p-4 flex items-center gap-3">
          <span className="inline-block w-3 h-3 rounded-full bg-amber-400 animate-pulse" />
          <div className="text-sm text-stone-700">
            {STAGE_LABEL[latest.stage ?? ""] ?? (latest.status === "queued" ? "Waiting for a worker…" : "Working…")}
            <span className="ml-2 text-stone-400 tabular-nums">{elapsed(latest)}</span>
          </div>
          <div className="ml-auto text-xs text-stone-400">Set {latest.generation} · usually 1–3 minutes</div>
        </div>
      )}

      {latest && latest.status === "failed" && !inFlight && (
        <div className="bg-rose-50 border border-rose-200 text-rose-800 text-sm rounded-xl px-4 py-3">
          The last run failed{latest.error ? `: ${latest.error}` : "."} Try Again starts a fresh run.
        </div>
      )}

      {doneRuns.length > 1 && (
        <div className="flex gap-1.5 flex-wrap">
          {doneRuns.slice().sort((a, b) => a.generation - b.generation).map((r) => (
            <button key={r.id} type="button" onClick={() => setActiveRunId(r.id)}
                    className={clsx("px-3 py-1 rounded-lg text-xs font-medium border",
                      r.id === active?.id ? "bg-stone-900 text-white border-stone-900" : "bg-white border-stone-200 text-stone-600 hover:bg-stone-50")}>
              Set {r.generation}
            </button>
          ))}
        </div>
      )}

      {active && (() => {
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
              Set {active.generation} · {active.recommendations.length} product{active.recommendations.length === 1 ? "" : "s"}
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
              No product met every criterion this time. The closest misses are listed below — Try Again searches different angles.
            </p>
          ) : visiblePicks.length === 0 ? (
            <p className="text-sm text-stone-500 bg-white border border-stone-200 rounded-xl p-4">
              Every product in this set is hidden. Try Again for a fresh set.
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
          {visibleMisses.length > 0 && (
            <div className="space-y-2">
              <div className="text-sm font-semibold text-stone-800">
                {visibleMisses.length} near miss{visibleMisses.length === 1 ? "" : "es"}
                <span className="ml-2 font-normal text-stone-500">
                  Close calls. Mark them too — a Good tells the next set you&apos;d take it anyway.
                </span>
              </div>
              <ul className="space-y-2">
                {visibleMisses.map((m, i) => (
                  <NearMissRow key={m.id ?? i} miss={m} onFeedback={handleFeedback} />
                ))}
              </ul>
            </div>
          )}

          {hiddenCount > 0 && (
            <div className="space-y-2">
              <button type="button" onClick={() => setShowHidden((v) => !v)}
                      className="text-xs font-medium text-stone-600 hover:text-stone-900">
                {showHidden ? "▾" : "▸"} {hiddenCount} hidden{showHidden ? "" : " · show"}
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
                </ul>
              )}
            </div>
          )}
        </>
        );
      })()}

      {!active && !inFlight && runs.length === 0 && (
        <p className="text-sm text-stone-500">
          Five products from the web for {block.label}&apos;s materials, each tied to the screenshot it echoes. Mark each one Good or Bad and the next set learns from it.
        </p>
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
    alert("Queued. The rules rewrite themselves within a minute — close and reopen Team settings to see them.");
    setTimeout(load, 45000);
  }, [load]);

  const onSavePrefs = useCallback(async (team: string, prefs: Prefs) => {
    const res = await fetch(`${API_BASE}/api/aldi/teams/${team}/preferences`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(prefs),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      alert(err.detail || "Could not save settings");
      return;
    }
    await load();
  }, [load]);

  return (
    <div className="mt-4 bg-stone-50 border border-stone-200 rounded-2xl p-5">
      <div className="flex flex-wrap items-end justify-between gap-3 mb-4">
        <div>
          <h3 className="font-semibold text-stone-900 text-lg">Product Recommendations</h3>
          <p className="text-xs text-stone-500 mt-0.5">Five products from the web per set, each tied to the report. Dates in your local time.</p>
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
