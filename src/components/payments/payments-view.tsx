"use client";
/** ApexChain Network Operations Intelligence Platform */

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";

import { PaymentDetailDrawer } from "@/components/payments/payment-detail-drawer";
import type { TableDensity } from "@/components/data-table";
import { RouteEmptyState, RouteErrorState, RouteLoadingState } from "@/components/ui/route-state";
import { exportPayments, fetchPayments } from "@/services/paymentService";
import { getPreferences, hydratePreferences, subscribeToPreferences, updatePreferences } from "@/lib/preferences";
import type { PaginatedPayments, Payment } from "@/types/payment";

type SortKey = "created_at" | "amount" | "status";
type SortDir = "asc" | "desc";

const statusStyles: Record<string, string> = {
  completed: "bg-green-100 text-green-700",
  pending: "bg-yellow-100 text-yellow-700",
  failed: "bg-red-100 text-red-700",
  confirmed: "bg-emerald-100 text-emerald-700",
};

const typeStyles: Record<string, string> = {
  reward: "bg-blue-100 text-blue-700",
  penalty: "bg-red-100 text-red-700",
};

export default function PaymentsView() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [data, setData] = useState<PaginatedPayments | null>(null);
  const [page, setPage] = useState(1);
  const [perPage] = useState(10);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedPaymentId, setSelectedPaymentId] = useState<string | null>(
    () => searchParams?.get("paymentId") ?? null
  );
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  // Sync drawer open/close with URL
  function openDrawer(id: string) {
    setSelectedPaymentId(id);
    router.replace(`/payments?paymentId=${id}`, { scroll: false });
  }
  function closeDrawer() {
    setSelectedPaymentId(null);
    router.replace("/payments", { scroll: false });
  }

  // FE-069: filter state
  const [statusFilter, setStatusFilter] = useState("");
  const [typeFilter, setTypeFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");

  // FE-072: sort + density
  const [sortKey, setSortKey] = useState<SortKey>("created_at");
  const [sortDir, setSortDir] = useState<SortDir>("desc");
  const [density, setDensity] = useState<TableDensity>(() => {
    const prefs = getPreferences();
    return prefs.tableDensity || "default";
  });

  // Hydrate preferences from server and subscribe to changes
  useEffect(() => {
    hydratePreferences().then((prefs) => {
      setDensity(prefs.tableDensity || "default");
    });

    return subscribeToPreferences((prefs) => {
      setDensity(prefs.tableDensity || "default");
    });
  }, []);

  // Update density in preferences when changed
  const handleDensityChange = (newDensity: TableDensity) => {
    setDensity(newDensity);
    updatePreferences({ tableDensity: newDensity });
  };

  const requestKey = useMemo(
    () => `${page}:${perPage}:${statusFilter}:${typeFilter}:${dateFrom}:${dateTo}:${sortKey}:${sortDir}`,
    [page, perPage, statusFilter, typeFilter, dateFrom, dateTo, sortKey, sortDir]
  );

  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    fetchPayments({
      page,
      page_size: perPage,
      status: statusFilter || undefined,
      type: typeFilter || undefined,
      date_from: dateFrom || undefined,
      date_to: dateTo || undefined,
      sort_by: sortKey,
      sort_dir: sortDir,
    })
      .then((response) => { if (isMounted) { setData(response); setError(null); } })
      .catch(() => { if (isMounted) setError("Failed to load payments."); })
      .finally(() => { if (isMounted) setLoading(false); });
    return () => { isMounted = false; };
  }, [requestKey]); // eslint-disable-line react-hooks/exhaustive-deps

  function toggleSort(key: SortKey) {
    if (sortKey === key) {
      setSortDir((d) => (d === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(key);
      setSortDir("asc");
    }
    setPage(1);
  }

  function SortIndicator({ col }: { col: SortKey }) {
    if (sortKey !== col) return <span className="ml-1 text-gray-300">↕</span>;
    return <span className="ml-1">{sortDir === "asc" ? "↑" : "↓"}</span>;
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / perPage)) : 1;
  const cell = density === "compact" ? "px-3 py-1.5 text-xs" : "px-4 py-3 text-sm";

  async function handleExport() {
    setExporting(true);
    setExportError(null);
    try {
      await exportPayments({
        status: statusFilter || undefined,
        type: typeFilter || undefined,
        date_from: dateFrom || undefined,
        date_to: dateTo || undefined,
      });
    } catch {
      setExportError("Export failed. Please try again.");
    } finally {
      setExporting(false);
    }
  }

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-4">
          <h1 className="text-2xl font-bold text-gray-800">Payments</h1>
          <Link
            href="/payments/retry-queue"
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50"
          >
            Retry Queue
          </Link>
        </div>
        <div className="flex items-center gap-3">
          {exportError && <span className="text-xs text-red-600">{exportError}</span>}
          <button
            onClick={() => void handleExport()}
            disabled={exporting}
            className="rounded-lg border border-slate-200 px-3 py-1.5 text-sm font-medium text-slate-600 transition-colors hover:bg-slate-50 disabled:opacity-50"
          >
            {exporting ? "Exporting…" : "Export CSV"}
          </button>
          {/* FE-072: density toggle */}
          <div className="flex items-center gap-1 text-xs text-slate-600">
            <span className="font-medium">Density:</span>
            {(["default", "compact"] as TableDensity[]).map((d) => (
              <button
                key={d}
                  onClick={() => handleDensityChange(d as TableDensity)}
                  className={`rounded px-2 py-0.5 capitalize border ${density === d ? "bg-slate-800 text-white border-slate-800" : "border-slate-200 hover:bg-slate-100"}`}
              >
                {d}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* FE-069: filter bar */}
      <div className="grid grid-cols-2 gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm md:grid-cols-4" data-tour="payments-filters">
        <label className="space-y-1 text-xs">
          <span className="font-medium text-slate-600">Status</span>
          <select
            className="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm"
            value={statusFilter}
            onChange={(e) => { setStatusFilter(e.target.value); setPage(1); }}
          >
            <option value="">All</option>
            <option value="pending">Pending</option>
            <option value="completed">Completed</option>
            <option value="confirmed">Confirmed</option>
            <option value="failed">Failed</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="font-medium text-slate-600">Type</span>
          <select
            className="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm"
            value={typeFilter}
            onChange={(e) => { setTypeFilter(e.target.value); setPage(1); }}
          >
            <option value="">All</option>
            <option value="reward">Reward</option>
            <option value="penalty">Penalty</option>
          </select>
        </label>
        <label className="space-y-1 text-xs">
          <span className="font-medium text-slate-600">From</span>
          <input
            type="date"
            className="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm"
            value={dateFrom}
            onChange={(e) => { setDateFrom(e.target.value); setPage(1); }}
          />
        </label>
        <label className="space-y-1 text-xs">
          <span className="font-medium text-slate-600">To</span>
          <input
            type="date"
            className="w-full rounded border border-slate-200 bg-white px-2 py-1.5 text-sm"
            value={dateTo}
            onChange={(e) => { setDateTo(e.target.value); setPage(1); }}
          />
        </label>
      </div>

      <div className="overflow-hidden rounded-xl bg-white shadow-sm" data-tour="payments-table">
        <table className="w-full text-left">
          <thead className="bg-gray-50">
            <tr>
              {/* FE-070: Outage ID column is now a link */}
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Outage</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Type</th>
              {/* FE-072: sortable Amount */}
              <th
                className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500 cursor-pointer select-none`}
                onClick={() => toggleSort("amount")}
              >
                Amount <SortIndicator col="amount" />
              </th>
              {/* FE-072: sortable Date */}
              <th
                className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500 cursor-pointer select-none`}
                onClick={() => toggleSort("created_at")}
              >
                Date <SortIndicator col="created_at" />
              </th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Asset</th>
              {/* FE-072: sortable Status */}
              <th
                className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500 cursor-pointer select-none`}
                onClick={() => toggleSort("status")}
              >
                Status <SortIndicator col="status" />
              </th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={6} className="p-0">
                <RouteLoadingState title="Loading payments" description="Retrieving the latest reward and penalty records." />
              </td></tr>
            ) : error ? (
              <tr><td colSpan={6} className="p-0">
                <RouteErrorState title="Payments unavailable" description={error} primaryAction={{ label: "Reload page", onClick: () => window.location.reload() }} />
              </td></tr>
            ) : (data && data.items.length === 0) ? (
              <tr><td colSpan={6} className="p-0">
                <RouteEmptyState title="No payments found" description="Try adjusting your filters." />
              </td></tr>
            ) : (data && data.items.map((payment: Payment) => (
              <tr
                key={payment.id}
                className="border-t transition-colors hover:bg-gray-50 cursor-pointer"
                onClick={() => openDrawer(payment.id)}
              >
                {/* FE-070: outage link in table */}
                <td className={`${cell} font-mono text-gray-700`}>
                  {payment.outage_id ? (
                    <Link
                      href={`/outages/${payment.outage_id}`}
                      className="text-blue-600 hover:underline underline-offset-2"
                      onClick={(e) => e.stopPropagation()}
                    >
                      {payment.outage_id}
                    </Link>
                  ) : (
                    <span className="italic text-gray-400">—</span>
                  )}
                </td>
                <td className={cell}>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold capitalize ${typeStyles[payment.type]}`}>
                    {payment.type}
                  </span>
                </td>
                <td className={`${cell} font-semibold ${payment.type === "penalty" ? "text-red-600" : "text-green-600"}`}>
                  {payment.type === "penalty" ? "-" : "+"}${payment.amount.toLocaleString()}
                </td>
                <td className={`${cell} text-gray-600`}>
                  {new Date(payment.created_at).toLocaleDateString()}
                </td>
                <td className={`${cell} font-mono text-gray-500`}>{payment.asset_code}</td>
                <td className={cell}>
                  <span className={`rounded-full px-2 py-0.5 text-xs font-semibold capitalize ${statusStyles[payment.status] ?? "bg-gray-100 text-gray-500"}`}>
                    {payment.status}
                  </span>
                </td>
              </tr>
            )))}
          </tbody>
        </table>
      </div>

      {data && totalPages > 1 && (
        <div className="flex items-center justify-between text-sm text-gray-500">
          <span>Page {page} of {totalPages} — {data.total} total</span>
          <div className="flex gap-2">
            <button onClick={() => setPage((p) => Math.max(1, p - 1))} disabled={page === 1} className="rounded-lg border px-3 py-1.5 transition-colors hover:bg-gray-100 disabled:opacity-40">Previous</button>
            <button onClick={() => setPage((p) => Math.min(totalPages, p + 1))} disabled={page === totalPages} className="rounded-lg border px-3 py-1.5 transition-colors hover:bg-gray-100 disabled:opacity-40">Next</button>
          </div>
        </div>
      )}

      <PaymentDetailDrawer
        paymentId={selectedPaymentId}
        onClose={closeDrawer}
      />
    </div>
  );
}