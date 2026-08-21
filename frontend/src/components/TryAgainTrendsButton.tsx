"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import clsx from "clsx";

type Phase = "idle" | "running" | "done" | "error";
type SegmentChoice = "luxury" | "middle" | "mass" | "all";

/**
 * Try Again — with an optional dropdown for market-segment routing.
 * Default click runs the current-segment tier; the ▾ chevron opens a
 * menu to run just one specific tier, or 'all' (sequential Luxury →
 * Middle → Mass, ~15 min).
 */
export default function TryAgainTrendsButton({ segment }: { segment?: string }) {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState(0);
  const [step, setStep] = useState("");
  const [error, setError] = useState("");
  const [menuOpen, setMenuOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  useEffect(() => () => stopPolling(), []);

  async function handleClick(choice?: SegmentChoice) {
    setMenuOpen(false);
    // Default click uses the current segment context if provided.
    const effective = choice ?? segment ?? undefined;
    setPhase("running");
    setPct(2);
    setStep(effective === "all"
      ? "Queuing all tiers (Luxury → Middle → Mass, ~15 min)…"
      : effective
        ? `Queuing ${effective} tier…`
        : "Queuing…");
    setError("");

    let taskId: string;
    try {
      const res = await api.reports.regenerate(effective);
      taskId = res.task_id;
    } catch {
      setPhase("error");
      setError("Failed to queue — try again.");
      return;
    }

    pollRef.current = setInterval(async () => {
      try {
        const status = await api.reports.taskStatus(taskId);
        setPct(status.pct);
        setStep(status.step);

        if (status.state === "SUCCESS") {
          stopPolling();
          setPct(100);
          setStep("New set ready! Refreshing…");
          setPhase("done");
          setTimeout(() => {
            // router.refresh() only re-runs server components; the trends
            // grid moved to a client component (TrendsClient) so it needs
            // an explicit signal to re-fetch. Fire a window event that
            // TrendsClient listens for.
            router.refresh();
            if (typeof window !== "undefined") {
              window.dispatchEvent(new CustomEvent("trends-refresh"));
            }
          }, 1_500);
        } else if (status.state === "FAILURE") {
          stopPolling();
          setPhase("error");
          setError("Analysis failed — check worker logs.");
        }
      } catch {
        // Network blip — keep polling
      }
    }, 3_000);
  }

  const isRunning = phase === "running";
  const isDone = phase === "done";

  return (
    <div className="relative min-w-[220px]">
      <div className="flex">
        <button
          onClick={() => handleClick()}
          disabled={isRunning || isDone}
          className={clsx(
            "flex-1 px-4 py-2 rounded-l-lg text-sm font-medium transition-colors flex items-center justify-center gap-2",
            isRunning          ? "bg-stone-300 text-stone-500 cursor-not-allowed"
            : isDone           ? "bg-emerald-600 text-white cursor-default"
            : phase === "error" ? "bg-rose-600 text-white hover:bg-rose-700"
            :                     "bg-stone-900 text-white hover:bg-stone-700"
          )}
        >
          {isRunning ? (
            <>
              <span className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" />
              Running…
            </>
          ) : isDone ? "✓ Done"
            : phase === "error" ? "✗ Failed — retry"
            : segment ? `Try Again · ${segment.charAt(0).toUpperCase() + segment.slice(1)}`
                     : "Try Again"}
        </button>
        {!isRunning && !isDone && (
          <button
            onClick={() => setMenuOpen((v) => !v)}
            className="px-2 py-2 rounded-r-lg text-sm bg-stone-800 hover:bg-stone-600 text-white border-l border-stone-700"
            title="Choose tier(s) to run"
            aria-label="Choose tier(s)"
          >▾</button>
        )}
      </div>
      {menuOpen && (
        <div className="absolute right-0 top-full mt-1 w-52 bg-white border border-stone-200 rounded-lg shadow-lg z-50 py-1 text-sm">
          <button onClick={() => handleClick("all")}
            className="block w-full text-left px-3 py-2 hover:bg-stone-50 font-medium">
            Run all tiers <span className="text-stone-400 text-xs">· ~15 min</span>
          </button>
          <div className="border-t border-stone-100 my-1" />
          <button onClick={() => handleClick("luxury")}
            className="block w-full text-left px-3 py-2 hover:bg-amber-50 text-stone-700">
            Just Luxury
          </button>
          <button onClick={() => handleClick("middle")}
            className="block w-full text-left px-3 py-2 hover:bg-stone-100 text-stone-700">
            Just Middle
          </button>
          <button onClick={() => handleClick("mass")}
            className="block w-full text-left px-3 py-2 hover:bg-sky-50 text-stone-700">
            Just Mass
          </button>
        </div>
      )}

      {/* Progress bar — absolutely positioned so it doesn't affect button alignment */}
      {(isRunning || isDone) && (
        <div className="absolute left-0 right-0 top-full pt-1.5">
          <div className="w-full h-1.5 bg-stone-200 rounded-full overflow-hidden">
            <div
              className={clsx(
                "h-full rounded-full transition-all duration-700 ease-out",
                isDone ? "bg-emerald-500" : "bg-stone-700"
              )}
              style={{ width: `${pct}%` }}
            />
          </div>
          {step && (
            <p className="mt-1 text-xs text-stone-500 text-right truncate">{step}</p>
          )}
        </div>
      )}

      {phase === "error" && error && (
        <div className="absolute left-0 right-0 top-full pt-1.5">
          <p className="text-xs text-rose-600 text-right">{error}</p>
        </div>
      )}
    </div>
  );
}
