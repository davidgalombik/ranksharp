"use client";

import { useRef, useState } from "react";
import clsx from "clsx";
import { api, type RanksharpCsvSummary, type RanksharpCsvCommitSummary } from "@/lib/api";

type Phase = "idle" | "previewing" | "previewed" | "committing" | "done";

/**
 * CSV upload for the Ranksharp Catalogue.
 *
 * Append-only semantics: existing SKUs are never overwritten by a re-upload,
 * and existing products not in the new CSV are never deleted. Every valid
 * row that carries sale data (price / units / date) becomes a new sale
 * record on that product. Rows with only metadata are treated as
 * product-only entries.
 */
export default function CatalogueUploadModal({
  onClose,
  onCommitted,
}: {
  onClose: () => void;
  onCommitted: () => void;
}) {
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  const [preview, setPreview] = useState<RanksharpCsvSummary | null>(null);
  const [commit, setCommit] = useState<RanksharpCsvCommitSummary | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handlePreview() {
    if (!file) return;
    setError(null); setPhase("previewing");
    try {
      const r = await api.ranksharp.csvPreview(file);
      setPreview(r); setPhase("previewed");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Preview failed");
      setPhase("idle");
    }
  }

  async function handleCommit() {
    if (!file) return;
    setError(null); setPhase("committing");
    try {
      const r = await api.ranksharp.csvCommit(file);
      setCommit(r); setPhase("done");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Commit failed");
      setPhase("previewed");
    }
  }

  function downloadRejects() {
    const rejects = commit?.rejects || preview?.rejects || [];
    if (!rejects.length) return;
    const header = "row_number,sku,reason\n";
    const rows = rejects.map((r) => {
      const sku = (r.sku || "").replace(/"/g, '""');
      const reason = r.reason.replace(/"/g, '""');
      return `${r.row_number},"${sku}","${reason}"`;
    }).join("\n");
    const blob = new Blob([header + rows], { type: "text/csv" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `ranksharp-rejects-${Date.now()}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4" onClick={onClose}>
      <div
        className="bg-white rounded-xl shadow-xl max-w-2xl w-full max-h-[90vh] overflow-hidden flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-5 border-b border-stone-200">
          <div>
            <h2 className="text-lg font-bold text-stone-900">Upload catalogue CSV</h2>
            <p className="text-xs text-stone-500 mt-0.5">
              Append-only — existing SKUs are never overwritten, existing products never deleted
            </p>
          </div>
          <button onClick={onClose} className="text-2xl text-stone-400 hover:text-stone-900 leading-none">×</button>
        </div>

        <div className="flex-1 overflow-y-auto p-5 space-y-4">
          {phase === "idle" && (
            <>
              <div
                onClick={() => inputRef.current?.click()}
                className="border-2 border-dashed border-stone-300 rounded-xl p-8 text-center cursor-pointer hover:bg-stone-50"
              >
                <div className="text-3xl mb-2">📄</div>
                <p className="text-sm font-medium text-stone-700">
                  {file ? file.name : "Click to choose a CSV file"}
                </p>
                {file && (
                  <p className="text-xs text-stone-400 mt-1">{(file.size / 1024).toFixed(1)} KB</p>
                )}
                <input
                  ref={inputRef} type="file" accept=".csv,text/csv" className="hidden"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    if (f) setFile(f);
                    e.target.value = "";
                  }}
                />
              </div>
              <div className="bg-stone-50 rounded-lg p-3 text-xs text-stone-600 space-y-1.5">
                <p className="font-semibold text-stone-700">Required columns</p>
                <p className="font-mono">sku · name</p>
                <p className="font-semibold text-stone-700 mt-2">Optional columns</p>
                <p className="font-mono">
                  description · category · subcategory · price_wholesale · price_retail ·
                  currency · units_purchased · on_sale_date · customer · notes
                </p>
                <p className="text-stone-500 mt-2">
                  <b>customer</b> defaults to &quot;ALDI&quot;. <b>currency</b> is a 3-letter code
                  (USD, AUD, GBP, EUR). <b>on_sale_date</b> accepts YYYY-MM-DD, DD/MM/YYYY, or MM/DD/YYYY.
                </p>
                <p className="text-stone-500">
                  Same SKU can appear in multiple rows — each row becomes one sale record.
                  Rows with only sku + name (no price / units / date) create a product-only entry.
                </p>
                <p className="text-stone-500">
                  <b>Images</b> are uploaded separately — use the &quot;Upload images&quot; button
                  after committing the CSV.
                </p>
              </div>
            </>
          )}

          {phase === "previewing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Parsing and validating…</p>
          )}

          {phase === "previewed" && preview && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Stat label="Total rows" value={preview.total_rows} />
                <Stat label="New SKUs" value={preview.new_products} className="text-emerald-600" />
                <Stat label="Existing SKUs" value={preview.existing_products} className="text-stone-600" />
                <Stat label="Rejects" value={preview.rejects.length} className="text-red-500" />
              </div>
              <div className="bg-sky-50 border border-sky-200 rounded-lg p-3">
                <p className="text-xs text-sky-900">
                  <b>Commit will create</b>: {preview.new_products} new product{preview.new_products !== 1 ? "s" : ""} and{" "}
                  {preview.sale_records} sale record{preview.sale_records !== 1 ? "s" : ""}.
                  Existing products retain their name / image / category.
                </p>
              </div>
              {preview.rejects.length > 0 && (
                <div className="bg-red-50 border border-red-200 rounded-lg p-3 space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-semibold text-red-700">
                      {preview.rejects.length} rejected row{preview.rejects.length !== 1 ? "s" : ""}
                    </p>
                    <button onClick={downloadRejects} className="text-xs text-red-700 hover:text-red-900 underline">
                      Download rejects.csv
                    </button>
                  </div>
                  <div className="max-h-48 overflow-y-auto space-y-1">
                    {preview.rejects.slice(0, 20).map((r) => (
                      <div key={r.row_number} className="text-xs text-red-800">
                        <span className="font-mono text-red-500">row {r.row_number}:</span> {r.reason}
                        {r.sku && <span className="text-red-400"> — {r.sku}</span>}
                      </div>
                    ))}
                    {preview.rejects.length > 20 && (
                      <p className="text-xs text-red-500 italic">…and {preview.rejects.length - 20} more</p>
                    )}
                  </div>
                </div>
              )}
            </div>
          )}

          {phase === "committing" && (
            <p className="text-sm text-stone-500 text-center py-8 animate-pulse">Saving products and sale records…</p>
          )}

          {phase === "done" && commit && (
            <div className="bg-emerald-50 border border-emerald-200 rounded-lg p-4">
              <p className="text-sm font-semibold text-emerald-800">Upload complete ✓</p>
              <p className="text-xs text-emerald-700 mt-1">
                {commit.products_created} new product{commit.products_created !== 1 ? "s" : ""} created ·{" "}
                {commit.sales_created} sale record{commit.sales_created !== 1 ? "s" : ""} added
                {commit.rejects.length > 0 && ` · ${commit.rejects.length} row${commit.rejects.length !== 1 ? "s" : ""} rejected`}
              </p>
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
            <button
              onClick={onCommitted}
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

function Stat({ label, value, className }: { label: string; value: number; className?: string }) {
  return (
    <div className="bg-stone-50 rounded-lg p-2 text-center">
      <p className="text-[10px] text-stone-400 uppercase tracking-wide">{label}</p>
      <p className={clsx("text-lg font-semibold", className || "text-stone-800")}>
        {value.toLocaleString()}
      </p>
    </div>
  );
}
