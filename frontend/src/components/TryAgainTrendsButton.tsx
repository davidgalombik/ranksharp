"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";
import clsx from "clsx";

type Phase = "idle" | "running" | "done" | "error";

export default function TryAgainTrendsButton() {
  const router = useRouter();
  const [phase, setPhase] = useState<Phase>("idle");
  const [pct, setPct] = useState(0);
  const [step, setStep] = useState("");
  const [error, setError] = useState("");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  useEffect(() => () => stopPolling(), []);

  async function handleClick() {
    setPhase("running");
    setPct(2);
    setStep("Queuing…");
    setError("");

    let taskId: string;
    try {
      const res = await api.reports.regenerate();
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
          setTimeout(() => router.refresh(), 1_500);
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
      <button
        onClick={handleClick}
        disabled={isRunning || isDone}
        className={clsx(
          "w-full px-4 py-2 rounded-lg text-sm font-medium transition-colors flex items-center justify-center gap-2",
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
          : "Try Again"}
      </button>

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
