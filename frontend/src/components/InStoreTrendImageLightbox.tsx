"use client";

import { useEffect } from "react";
import ZoomableImage from "./ZoomableImage";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export interface LightboxExample {
  id: number;
  image_id: number;
  product_name: string;
  retailer: string | null;
}

/**
 * Lightbox for an In-store trend's example items (2026-10-12).
 *
 * The card tiles are per-item CROPS; this shows the FULL shelf photo the
 * item was cut from, which is what a buyer actually wants to study.
 * Wheel/pinch zoom + drag pan + double-click reset via ZoomableImage.
 * ← / → step through every example on the trend (not just the four
 * tiles); Esc or a backdrop click closes.
 */
export default function InStoreTrendImageLightbox({
  examples,
  index,
  onClose,
  onNavigate,
}: {
  examples: LightboxExample[];
  index: number;
  onClose: () => void;
  onNavigate: (index: number) => void;
}) {
  const ex = examples[index];
  const hasPrev = index > 0;
  const hasNext = index < examples.length - 1;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
      else if (e.key === "ArrowLeft" && hasPrev) onNavigate(index - 1);
      else if (e.key === "ArrowRight" && hasNext) onNavigate(index + 1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose, onNavigate, index, hasPrev, hasNext]);

  if (!ex) return null;

  const navBtn =
    "absolute top-1/2 -translate-y-1/2 w-10 h-10 rounded-full bg-white/90 hover:bg-white text-stone-900 text-xl leading-none flex items-center justify-center shadow-lg transition-colors z-30";

  return (
    <div
      className="fixed inset-0 bg-black/80 z-50 flex items-center justify-center p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label={`${ex.product_name} — full shelf photo`}
    >
      <div
        className="relative w-full max-w-6xl h-[88vh] bg-stone-900 rounded-xl overflow-hidden flex flex-col shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex-1 relative overflow-hidden flex items-center justify-center p-3">
          <ZoomableImage
            key={ex.image_id}
            src={`${API_BASE}/api/instore-catalogue/images/${ex.image_id}/file`}
            alt={ex.product_name}
          />
          {hasPrev && (
            <button
              type="button"
              onClick={() => onNavigate(index - 1)}
              title="Previous example (←)"
              aria-label="Previous example"
              className={`${navBtn} left-3`}
            >‹</button>
          )}
          {hasNext && (
            <button
              type="button"
              onClick={() => onNavigate(index + 1)}
              title="Next example (→)"
              aria-label="Next example"
              className={`${navBtn} right-3`}
            >›</button>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 px-4 py-2.5 bg-stone-950 text-stone-200 text-sm">
          <div className="min-w-0">
            <p className="font-medium text-white truncate">{ex.product_name}</p>
            <p className="text-xs text-stone-400 truncate">
              {ex.retailer ?? "Unknown retailer"} · full shelf photo · scroll to zoom, drag to pan, double-click to reset
            </p>
          </div>
          <div className="flex items-center gap-3 shrink-0">
            {examples.length > 1 && (
              <span className="text-xs text-stone-400">{index + 1} / {examples.length}</span>
            )}
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1 rounded-lg border border-stone-600 text-xs font-medium hover:bg-stone-800"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
