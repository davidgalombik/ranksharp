"use client";

import { useRef, useState } from "react";
import clsx from "clsx";
import { api, CsvPreviewResult, CsvCommitResult } from "@/lib/api";

type UploadPhase = "idle" | "previewing" | "previewed" | "committing" | "done";

export function CsvUploadModal({
  retailerSlug,
  onClose,
}: {
  retailerSlug: string;
  onClose: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<UploadPhase>("idle");
  const [preview, setPreview] = useState<CsvPreviewResult | null>(null);
  const [commit, setCommit] = useState<CsvCommitResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handlePreview = async () => {
    if (!file) return;
    setError(null);
    setPhase("previewing");
    try {
      const result = await api.retailers.csvPreview(file);
      setPreview(result);
      setPhase("previewed");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Preview failed");
      setPhase("idle");
    }
  };

  const handleCommit = async () => {
    if (!file) return;
    setError(null);
    setPhase("committing");
    try {
      const result = await api.retailers.csvCommit(file);
      setCommit(result);
      setPhase("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Commit failed");
      setPhase("previewed");
    }
  };

  const downloadRejects = () => {
    const rejects = commit?.rejects || preview?.rejects || [];
    if (!rejects.length) return;
    const header = "row_number,url,reason\n";
    const rows = rejects
      .map((r) => {
        const url = (r.url || "").replace(/"/g, '""');
        const reason = r.reason.replace(/"/g, '""');
        return `${r.row_number},"${url}","${reason}"`;
      })
      .join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `rejects-${retailerSlug}-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  };

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-stone-200">
          <div>
            <h2 className="text-lg font-bold text-stone-900">Upload products via CSV</h2>
            <p className="text-xs text-stone-500 mt-0.5">
              Retailer: <strong className="text-stone-700">{retailerSlug}</strong> · the CSV&apos;s
              retailer_slug column must match this value
            </p>
          </div>
          <button onClick={onClose} className="text-2xl text-stone-400 hover:text-stone-900 leading-none">
            ×
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {phase === "idle" && (
            <div className="space-y-3">
              <div
                onClick={() => fileInputRef.current?.click()}
                className="border-2 border-dashed border-stone-300 rounded-xl p-8 text-center cursor-pointer hover:bg-stone-50"
              >
                <div className="text-3xl mb-2">📄</div>
                <p className="text-sm font-medium text-stone-700">
                  {file ? file.name : "Click to choose a CSV file"}
                </p>
                {file && (
                  <p className="text-xs text-stone-400 mt-1">
                    {(file.size / 1024).toFixed(1)} KB
                  </p>
                )}
                {!file && (
                  <p className="text-xs text-stone-400 mt-1">
                    Max 5,000 rows · required columns: url, name, primary_image_url, retailer_slug
                  </p>
                )}
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".csv,text/csv"
                  className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setFile(f);
                    e.target.value = "";
                  }}
                />
              </div>

              <div className="bg-stone-50 rounded-lg p-3 text-xs text-stone-600 space-y-1.5">
                <p className="font-semibold text-stone-700">Required columns</p>
                <p className="font-mono">url · name · primary_image_url · retailer_slug</p>
                <p className="font-semibold text-stone-700 mt-2">Optional columns</p>
                <p className="font-mono">price · currency · category · is_best_seller · is_new · has_patent · description · sku · brand</p>
                <p className="text-stone-400 mt-1">
                  Leave colours / materials / season / room out — they&apos;re filled in by the analysis pipeline.
                </p>
              </div>
            </div>
          )}

          {phase === "previewing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Parsing and validating…</p>
          )}

          {phase === "previewed" && preview && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Counter label="Total rows" value={preview.total_rows} />
                <Counter label="Will insert" value={preview.new_count} className="text-emerald-600" />
                <Counter label="Will update" value={preview.update_count} className="text-amber-600" />
                <Counter label="Rejects" value={preview.rejects.length} className="text-red-500" />
              </div>
              {preview.retailers_referenced.length > 0 && (
                <p className="text-xs text-stone-500">
                  Retailers referenced: <span className="font-medium text-stone-700">{preview.retailers_referenced.join(", ")}</span>
                </p>
              )}
              {preview.rejects.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-xs font-semibold text-red-700">{preview.rejects.length} rejected row{preview.rejects.length !== 1 ? "s" : ""}</p>
                    <button onClick={downloadRejects} className="text-xs text-red-700 hover:text-red-900 underline">
                      Download rejects.csv
                    </button>
                  </div>
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {preview.rejects.slice(0, 20).map((r) => (
                      <div key={r.row_number} className="text-xs text-red-800">
                        <span className="font-mono text-red-500">row {r.row_number}:</span> {r.reason}
                        {r.url && <span className="text-red-400"> — {r.url.slice(0, 60)}{r.url.length > 60 ? "…" : ""}</span>}
                      </div>
                    ))}
                    {preview.rejects.length > 20 && (
                      <p className="text-xs text-red-500 italic">…and {preview.rejects.length - 20} more (download CSV to see all)</p>
                    )}
                  </div>
                </div>
              )}
              {preview.valid_rows === 0 && (
                <p className="text-xs text-stone-500">No valid rows to import.</p>
              )}
            </div>
          )}

          {phase === "committing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Saving products and queuing analysis…</p>
          )}

          {phase === "done" && commit && (
            <div className="space-y-3">
              <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
                <p className="text-sm font-semibold text-emerald-800">Upload complete ✓</p>
                <p className="text-xs text-emerald-700 mt-1">
                  {commit.inserted} inserted · {commit.updated} updated · {commit.analysis_queued} queued for analysis
                </p>
              </div>
              {commit.rejects.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-red-700">{commit.rejects.length} row{commit.rejects.length !== 1 ? "s" : ""} rejected</p>
                    <button onClick={downloadRejects} className="text-xs text-red-700 hover:text-red-900 underline">
                      Download rejects.csv
                    </button>
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
                onClick={handlePreview}
                disabled={!file}
                className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Preview
              </button>
            </>
          )}
          {phase === "previewed" && preview && (
            <>
              <button
                onClick={() => { setPhase("idle"); setPreview(null); setFile(null); }}
                className="px-3 py-1.5 text-sm text-stone-600 hover:text-stone-900"
              >
                Back
              </button>
              <button
                onClick={handleCommit}
                disabled={preview.valid_rows === 0}
                className="px-4 py-2 rounded-lg bg-emerald-600 text-white text-sm font-medium hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Commit {preview.valid_rows} row{preview.valid_rows !== 1 ? "s" : ""}
              </button>
            </>
          )}
          {phase === "done" && (
            <button onClick={onClose} className="px-4 py-2 rounded-lg bg-stone-900 text-white text-sm font-medium hover:bg-stone-800">
              Close
            </button>
          )}
        </div>
      </div>
    </div>
  );
}

function Counter({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="bg-stone-50 rounded-lg p-2 text-center">
      <p className="text-[10px] text-stone-400 uppercase tracking-wide">{label}</p>
      <p className={clsx("text-lg font-semibold", className || "text-stone-800")}>{value.toLocaleString()}</p>
    </div>
  );
}
