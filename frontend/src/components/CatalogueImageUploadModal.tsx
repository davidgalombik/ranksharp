"use client";

import { useRef, useState } from "react";
import { api, type RanksharpImageUploadSummary } from "@/lib/api";

type Mode = "zip" | "single";
type Phase = "idle" | "uploading" | "done";

/**
 * Ranksharp Catalogue image upload — two modes:
 *
 *   - **ZIP**: filenames match SKUs (e.g. RS-1234.jpg). One drop uploads
 *     every image. Filenames that don't match any SKU are reported.
 *   - **Single**: one image file + a SKU field. For corrections or
 *     one-off adds.
 *
 * Server transcodes HEIC → JPEG and renders PDF page 1 → JPEG so the
 * browser gets something it can display.
 */
export default function CatalogueImageUploadModal({
  onClose,
  onUploaded,
}: {
  onClose: () => void;
  onUploaded: () => void;
}) {
  const [mode, setMode] = useState<Mode>("zip");
  const [file, setFile] = useState<File | null>(null);
  const [sku, setSku] = useState("");
  const [phase, setPhase] = useState<Phase>("idle");
  const [result, setResult] = useState<RanksharpImageUploadSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleUpload() {
    if (!file) return;
    if (mode === "single" && !sku.trim()) {
      setError("SKU is required for single-image upload");
      return;
    }
    setError(null); setPhase("uploading");
    try {
      const r = await api.ranksharp.uploadImages(file, mode === "single" ? sku.trim() : undefined);
      setResult(r); setPhase("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Upload failed");
      setPhase("idle");
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-stone-200">
          <div>
            <h2 className="text-lg font-bold text-stone-900">Upload product images</h2>
            <p className="text-xs text-stone-500 mt-0.5">
              JPEG · PNG · WEBP · HEIC · PDF (page 1 rendered)
            </p>
          </div>
          <button onClick={onClose} className="text-2xl text-stone-400 hover:text-stone-900 leading-none">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {phase !== "done" && (
            <>
              {/* Mode picker */}
              <div className="flex gap-2 border border-stone-200 rounded-lg p-1 bg-stone-50 w-fit">
                <button
                  onClick={() => { setMode("zip"); setFile(null); }}
                  className={
                    mode === "zip"
                      ? "px-3 py-1 rounded-md text-xs font-semibold bg-white text-stone-900 shadow-sm"
                      : "px-3 py-1 rounded-md text-xs font-medium text-stone-600"
                  }
                >
                  📁 Bulk (ZIP)
                </button>
                <button
                  onClick={() => { setMode("single"); setFile(null); }}
                  className={
                    mode === "single"
                      ? "px-3 py-1 rounded-md text-xs font-semibold bg-white text-stone-900 shadow-sm"
                      : "px-3 py-1 rounded-md text-xs font-medium text-stone-600"
                  }
                >
                  🖼 Single image
                </button>
              </div>

              {mode === "zip" && (
                <>
                  <div
                    onClick={() => inputRef.current?.click()}
                    className="border-2 border-dashed border-stone-300 rounded-xl p-8 text-center cursor-pointer hover:bg-stone-50"
                  >
                    <div className="text-3xl mb-2">📁</div>
                    <p className="text-sm font-medium text-stone-700">
                      {file ? file.name : "Click to choose a .zip"}
                    </p>
                    {file && (
                      <p className="text-xs text-stone-400 mt-1">
                        {(file.size / 1024 / 1024).toFixed(1)} MB
                      </p>
                    )}
                    <input
                      ref={inputRef} type="file" accept=".zip,application/zip" className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) setFile(f);
                        e.target.value = "";
                      }}
                    />
                  </div>
                  <div className="bg-stone-50 rounded-lg p-3 text-xs text-stone-600 space-y-1.5">
                    <p className="font-semibold text-stone-700">Filename → SKU</p>
                    <p>Each file inside the ZIP is matched to a product by its filename stem.</p>
                    <p className="font-mono text-stone-500">RS-1234.jpg → SKU &quot;RS-1234&quot;</p>
                    <p className="font-mono text-stone-500">HANDLE_012.pdf → SKU &quot;HANDLE_012&quot;</p>
                    <p>Files whose stem doesn&apos;t match a SKU in the catalogue are skipped and listed after upload.</p>
                  </div>
                </>
              )}

              {mode === "single" && (
                <>
                  <div>
                    <label className="block text-xs font-medium text-stone-500 mb-1">SKU</label>
                    <input
                      value={sku}
                      onChange={(e) => setSku(e.target.value)}
                      placeholder="RS-1234"
                      className="w-full border border-stone-300 rounded-lg px-3 py-1.5 text-sm bg-white"
                    />
                  </div>
                  <div
                    onClick={() => inputRef.current?.click()}
                    className="border-2 border-dashed border-stone-300 rounded-xl p-8 text-center cursor-pointer hover:bg-stone-50"
                  >
                    <div className="text-3xl mb-2">🖼</div>
                    <p className="text-sm font-medium text-stone-700">
                      {file ? file.name : "Click to choose an image"}
                    </p>
                    {file && (
                      <p className="text-xs text-stone-400 mt-1">{(file.size / 1024).toFixed(1)} KB</p>
                    )}
                    <input
                      ref={inputRef} type="file"
                      accept="image/*,.pdf,.heic"
                      className="hidden"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) setFile(f);
                        e.target.value = "";
                      }}
                    />
                  </div>
                </>
              )}
            </>
          )}

          {phase === "uploading" && (
            <p className="text-sm text-stone-500 text-center py-4 animate-pulse">Uploading and processing…</p>
          )}

          {phase === "done" && result && (
            <div className="space-y-3">
              <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
                <p className="text-sm font-semibold text-emerald-800">
                  {result.uploaded} image{result.uploaded !== 1 ? "s" : ""} uploaded ✓
                </p>
              </div>
              {result.skipped_no_matching_sku.length > 0 && (
                <div className="bg-amber-50 border border-amber-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-amber-800 mb-1">
                    {result.skipped_no_matching_sku.length} file{result.skipped_no_matching_sku.length !== 1 ? "s" : ""} skipped — filename didn&apos;t match any SKU
                  </p>
                  <div className="max-h-32 overflow-y-auto text-xs text-amber-700 font-mono space-y-0.5">
                    {result.skipped_no_matching_sku.slice(0, 20).map((f) => (<div key={f}>{f}</div>))}
                    {result.skipped_no_matching_sku.length > 20 && (
                      <p className="text-amber-500 italic">…and {result.skipped_no_matching_sku.length - 20} more</p>
                    )}
                  </div>
                </div>
              )}
              {result.failed.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                  <p className="text-xs font-semibold text-red-800 mb-1">
                    {result.failed.length} file{result.failed.length !== 1 ? "s" : ""} failed
                  </p>
                  <div className="max-h-40 overflow-y-auto text-xs text-red-700 space-y-1">
                    {result.failed.slice(0, 20).map((f, i) => (
                      <div key={i}>
                        <span className="font-mono">{f.filename}</span>
                        {f.sku && <span className="text-red-500"> ({f.sku})</span>}:
                        <span className="ml-1">{f.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-lg p-3 text-xs text-red-700 whitespace-pre-wrap">
              {error}
            </div>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 p-4 border-t border-stone-200 bg-stone-50">
          {phase === "idle" && (
            <>
              <button onClick={onClose} className="px-3 py-1.5 text-sm text-stone-600 hover:text-stone-900">Cancel</button>
              <button
                onClick={handleUpload}
                disabled={!file || (mode === "single" && !sku.trim())}
                className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Upload
              </button>
            </>
          )}
          {phase === "done" && (
            <button
              onClick={onUploaded}
              className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800"
            >
              Close & refresh
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
