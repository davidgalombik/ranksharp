"use client";

import { useEffect, useRef, useState } from "react";
import { TransformWrapper, TransformComponent, ReactZoomPanPinchRef } from "react-zoom-pan-pinch";

/**
 * Google Maps-style zoom + pan for a single image.
 *
 * UX affordances layered so the feature is discoverable:
 *   1. Cursor changes: zoom-in when idle, grab / grabbing when panning.
 *   2. Persistent zoom controls (+ / − / ⤢) bottom-right corner —
 *      obvious for buyers who don't reach for the mouse wheel.
 *   3. First-open hint pill (Scroll to zoom · Drag to pan · Double-click
 *      to reset) that fades after 3s. Only shows once per session
 *      (localStorage) so repeat users don't get nagged.
 *   4. Live zoom-percentage indicator so buyers see the wheel IS working.
 *
 * Touch: pinch-to-zoom + two-finger pan work out of the box via
 * react-zoom-pan-pinch. Hint text swaps to touch language when applicable.
 *
 * When the parent replaces the image (prev/next arrows), pass a new
 * `key` so this component remounts and zoom resets to fit.
 */
export default function ZoomableImage({
  src,
  alt,
}: {
  src: string;
  alt: string;
}) {
  const wrapperRef = useRef<ReactZoomPanPinchRef | null>(null);
  const [scale, setScale] = useState(1);
  const [panning, setPanning] = useState(false);
  const [showHint, setShowHint] = useState(false);
  const [isTouch, setIsTouch] = useState(false);

  useEffect(() => {
    setIsTouch(
      typeof window !== "undefined" &&
      ("ontouchstart" in window || navigator.maxTouchPoints > 0),
    );
    // Show the hint once per session — repeat users have already learned.
    try {
      if (!localStorage.getItem("instore_zoom_hint_seen")) {
        setShowHint(true);
        localStorage.setItem("instore_zoom_hint_seen", "1");
        const t = setTimeout(() => setShowHint(false), 3500);
        return () => clearTimeout(t);
      }
    } catch {
      // localStorage blocked (private browsing) — show and dismiss anyway
      setShowHint(true);
      const t = setTimeout(() => setShowHint(false), 3500);
      return () => clearTimeout(t);
    }
  }, []);

  const zoomed = scale > 1.01;
  const cursorClass = panning
    ? "cursor-grabbing"
    : zoomed
      ? "cursor-grab"
      : "cursor-zoom-in";

  return (
    <div className="relative w-full h-full flex items-center justify-center">
      <TransformWrapper
        ref={wrapperRef}
        initialScale={1}
        minScale={1}
        maxScale={8}
        centerOnInit
        doubleClick={{ mode: "reset" }}
        wheel={{ step: 0.15 }}
        pinch={{ step: 5 }}
        panning={{
          disabled: !zoomed,          // only pan once zoomed in
          velocityDisabled: true,
        }}
        onZoom={(ref) => setScale(ref.state.scale)}
        onPanningStart={() => setPanning(true)}
        onPanningStop={() => setPanning(false)}
      >
        {({ zoomIn, zoomOut, resetTransform }) => (
          <>
            <TransformComponent
              wrapperClass={`!w-full !h-full ${cursorClass}`}
              contentClass="!w-full !h-full flex items-center justify-center"
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={src}
                alt={alt}
                draggable={false}
                className="max-w-full max-h-[85vh] object-contain rounded-lg select-none"
              />
            </TransformComponent>

            {/* Zoom controls — always visible so wheel-averse users have
                an obvious affordance. z-30 so they sit above the pan area. */}
            <div className="absolute bottom-3 right-3 flex flex-col gap-1 z-30">
              <ZoomButton onClick={() => zoomIn(0.4)} label="Zoom in" symbol="+" />
              <ZoomButton onClick={() => zoomOut(0.4)} label="Zoom out" symbol="−" />
              <ZoomButton onClick={() => resetTransform()} label="Reset" symbol="⤢" />
            </div>

            {/* Live zoom-level readout — confirms scrolling IS working. */}
            {zoomed && (
              <span className="absolute top-3 right-3 px-2 py-0.5 rounded-full bg-white/85 text-xs text-stone-700 font-medium z-30 tabular-nums">
                {Math.round(scale * 100)}%
              </span>
            )}

            {/* First-open discovery hint — fades after 3s. Text swaps
                for touch devices where pinch is the primary gesture. */}
            {showHint && (
              <div className="absolute bottom-14 left-1/2 -translate-x-1/2 z-30 px-3 py-1.5 rounded-full bg-stone-900/90 text-white text-xs whitespace-nowrap animate-pulse pointer-events-none">
                {isTouch
                  ? "Pinch to zoom · Drag to pan · Double-tap to reset"
                  : "Scroll to zoom · Drag to pan · Double-click to reset"}
              </div>
            )}
          </>
        )}
      </TransformWrapper>
    </div>
  );
}

function ZoomButton({
  onClick,
  label,
  symbol,
}: {
  onClick: () => void;
  label: string;
  symbol: string;
}) {
  return (
    <button
      onClick={onClick}
      title={label}
      aria-label={label}
      className="w-9 h-9 rounded-full bg-white/90 hover:bg-white text-stone-900 text-lg leading-none flex items-center justify-center shadow-lg transition-colors"
    >
      {symbol}
    </button>
  );
}
