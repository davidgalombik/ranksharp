"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { api, type RanksharpProductDetail } from "@/lib/api";

const CURRENCIES: Record<string, string> = {
  USD: "$", AUD: "A$", GBP: "£", EUR: "€", NZD: "NZ$", CAD: "C$",
};

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

/** Ranksharp catalogue product detail — product info + full sales history. */
export default function ProductDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const productId = Number(params.id);
  const [detail, setDetail] = useState<RanksharpProductDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api.ranksharp.getProduct(productId)
      .then((d) => { setDetail(d); setNotFound(false); })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [productId]);

  useEffect(() => { load(); }, [load]);

  async function handleDeleteProduct() {
    if (!detail) return;
    if (!confirm(`Delete '${detail.name}' (${detail.sku}) and all ${detail.sales.length} sale record(s)? This can't be undone.`)) return;
    try {
      await api.ranksharp.deleteProduct(detail.id);
      router.push("/catalogue");
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  }

  async function handleDeleteSale(saleId: number) {
    if (!confirm("Delete this sale record? The product stays. Can't be undone.")) return;
    try {
      await api.ranksharp.deleteSale(saleId);
      load();
    } catch (e: unknown) {
      alert(e instanceof Error ? e.message : "Delete failed");
    }
  }

  if (loading) {
    return <p className="text-center text-stone-400 py-16">Loading product…</p>;
  }
  if (notFound || !detail) {
    return (
      <div className="text-center py-16">
        <p className="text-stone-500 mb-3">Product not found.</p>
        <Link href="/catalogue" className="text-stone-900 underline">Back to catalogue</Link>
      </div>
    );
  }

  const totalUnits = detail.sales.reduce((s, x) => s + (x.units_purchased || 0), 0);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      <nav className="text-sm text-stone-500">
        <Link href="/catalogue" className="hover:text-stone-900">Catalogue</Link>
        {" / "}
        <span className="text-stone-900">{detail.name}</span>
      </nav>

      <div className="grid grid-cols-1 md:grid-cols-[300px_1fr] gap-6 items-start">
        {/* Image */}
        <div className="bg-stone-100 rounded-xl overflow-hidden aspect-square flex items-center justify-center">
          {detail.has_image ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={api.ranksharp.imageUrl(detail.id)}
              alt={detail.name}
              className="w-full h-full object-cover"
            />
          ) : (
            <div className="text-stone-300 text-6xl">📦</div>
          )}
        </div>

        {/* Meta */}
        <div className="space-y-3">
          <div className="flex items-start justify-between gap-3">
            <div>
              <p className="text-xs uppercase tracking-wider text-stone-400">{detail.sku}</p>
              <h1 className="text-2xl font-bold text-stone-900 leading-tight mt-1">{detail.name}</h1>
            </div>
            <button
              onClick={handleDeleteProduct}
              className="text-xs text-red-600 hover:text-red-800 border border-red-200 rounded px-2 py-1"
              title="Delete this product and all its sales"
            >
              Delete
            </button>
          </div>
          {detail.description && (
            <p className="text-sm text-stone-700 whitespace-pre-wrap">{detail.description}</p>
          )}
          <div className="flex flex-wrap gap-2 pt-2">
            {detail.category && (
              <span className="px-2 py-0.5 rounded-full text-xs bg-stone-100 text-stone-700">
                {detail.category}
              </span>
            )}
            {detail.subcategory && (
              <span className="px-2 py-0.5 rounded-full text-xs bg-stone-100 text-stone-600">
                {detail.subcategory}
              </span>
            )}
          </div>
          <div className="grid grid-cols-3 gap-2 pt-3">
            <Stat label="Sales" value={detail.sales.length.toLocaleString()} />
            <Stat label="Total units" value={totalUnits.toLocaleString()} />
            <Stat label="Added" value={formatDate(detail.created_at)} />
          </div>
        </div>
      </div>

      {/* Sales history */}
      <div className="bg-white border border-stone-200 rounded-xl overflow-hidden">
        <div className="px-4 py-3 border-b border-stone-200 flex items-center justify-between">
          <h2 className="font-semibold text-stone-800">Sales history</h2>
          <span className="text-xs text-stone-500">{detail.sales.length} record{detail.sales.length !== 1 ? "s" : ""}</span>
        </div>
        {detail.sales.length === 0 ? (
          <p className="p-8 text-center text-sm text-stone-400">
            No sales recorded yet. Upload a CSV with price / units / date to populate history.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="bg-stone-50 text-xs uppercase text-stone-500">
                <tr>
                  <th className="text-left px-4 py-2">Customer</th>
                  <th className="text-left px-4 py-2">On sale</th>
                  <th className="text-right px-4 py-2">Units</th>
                  <th className="text-right px-4 py-2">Wholesale</th>
                  <th className="text-right px-4 py-2">Retail</th>
                  <th className="text-right px-4 py-2">Markup</th>
                  <th className="text-left px-4 py-2">Notes</th>
                  <th className="w-8"></th>
                </tr>
              </thead>
              <tbody>
                {detail.sales.map((s) => {
                  const symbol = CURRENCIES[s.currency ?? ""] ?? "$";
                  const markup = (s.price_wholesale != null && s.price_retail != null && s.price_wholesale > 0)
                    ? ((s.price_retail / s.price_wholesale - 1) * 100).toFixed(0) + "%"
                    : null;
                  return (
                    <tr key={s.id} className="border-t border-stone-100">
                      <td className="px-4 py-2 font-medium text-stone-800">{s.customer}</td>
                      <td className="px-4 py-2 text-stone-600">{formatDate(s.on_sale_date)}</td>
                      <td className="px-4 py-2 text-right tabular-nums">
                        {s.units_purchased != null ? s.units_purchased.toLocaleString() : "—"}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-stone-700">
                        {s.price_wholesale != null ? `${symbol}${s.price_wholesale.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-stone-700">
                        {s.price_retail != null ? `${symbol}${s.price_retail.toFixed(2)}` : "—"}
                      </td>
                      <td className="px-4 py-2 text-right tabular-nums text-stone-500">
                        {markup ?? "—"}
                      </td>
                      <td className="px-4 py-2 text-stone-500 max-w-xs truncate" title={s.notes ?? ""}>
                        {s.notes ?? "—"}
                      </td>
                      <td className="px-2">
                        <button
                          onClick={() => handleDeleteSale(s.id)}
                          className="text-stone-300 hover:text-red-600 text-lg leading-none"
                          title="Delete this sale record"
                          aria-label="Delete sale"
                        >
                          ×
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-stone-50 rounded-lg p-2 text-center">
      <p className="text-[10px] uppercase tracking-wide text-stone-400">{label}</p>
      <p className="text-sm font-semibold text-stone-900 mt-0.5">{value}</p>
    </div>
  );
}
