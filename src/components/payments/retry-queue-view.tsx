"use client";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { RouteEmptyState, RouteErrorState, RouteLoadingState } from "@/components/ui/route-state";
import { fetchPayments, retryPayment } from "@/services/paymentService";
import type { PaginatedPayments, Payment } from "@/types/payment";
import Link from "next/link";
import { useEffect, useState } from "react";

const statusStyles: Record<string, string> = {
  failed: "bg-red-100 text-red-700",
};

const typeStyles: Record<string, string> = {
  reward: "bg-blue-100 text-blue-700",
  penalty: "bg-red-100 text-red-700",
};

export default function RetryQueueView() {
  const [data, setData] = useState<PaginatedPayments | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [retryingIds, setRetryingIds] = useState<Set<string>>(new Set());
  const [bulkRetrying, setBulkRetrying] = useState(false);
  const [showConfirmDialog, setShowConfirmDialog] = useState(false);
  const [refreshKey, setRefreshKey] = useState(0);

  // Calculate date 7 days ago for default filter
  const getSevenDaysAgo = () => {
    const date = new Date();
    date.setDate(date.getDate() - 7);
    return date.toISOString().split('T')[0];
  };

  const dateFrom = getSevenDaysAgo();

  useEffect(() => {
    let isMounted = true;
    setLoading(true);
    fetchPayments({
      page: 1,
      page_size: 100,
      status: "failed",
      date_from: dateFrom,
    })
      .then((response) => { if (isMounted) { setData(response); setError(null); } })
      .catch(() => { if (isMounted) setError("Failed to load failed payments."); })
      .finally(() => { if (isMounted) setLoading(false); });
    return () => { isMounted = false; };
  }, [refreshKey, dateFrom]);

  const handleRetry = async (id: string) => {
    setRetryingIds(prev => new Set(prev).add(id));
    try {
      await retryPayment(id);
      // Remove from selected and refresh
      setSelectedIds(prev => {
        const newSet = new Set(prev);
        newSet.delete(id);
        return newSet;
      });
      setRefreshKey(prev => prev + 1);
    } catch (err) {
      console.error("Failed to retry payment:", err);
    } finally {
      setRetryingIds(prev => {
        const newSet = new Set(prev);
        newSet.delete(id);
        return newSet;
      });
    }
  };

  const handleBulkRetry = async () => {
    setBulkRetrying(true);
    const ids = Array.from(selectedIds);
    try {
      await Promise.all(ids.map(id => retryPayment(id)));
      setSelectedIds(new Set());
      setRefreshKey(prev => prev + 1);
      setShowConfirmDialog(false);
    } catch (err) {
      console.error("Failed to retry some payments:", err);
    } finally {
      setBulkRetrying(false);
    }
  };

  const toggleSelectAll = () => {
    if (!data) return;
    if (selectedIds.size === data.items.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(data.items.map(item => item.id)));
    }
  };

  const toggleSelect = (id: string) => {
    setSelectedIds(prev => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const cell = "px-4 py-3 text-sm";

  return (
    <div className="space-y-4 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Payment Retry Queue</h1>
          <p className="text-sm text-gray-600 mt-1">Failed payments from the last 7 days</p>
        </div>
        {selectedIds.size > 0 && (
          <Button 
            onClick={() => setShowConfirmDialog(true)}
            disabled={bulkRetrying}
          >
            {bulkRetrying ? "Retrying..." : `Bulk Retry (${selectedIds.size})`}
          </Button>
        )}
      </div>

      <div className="overflow-hidden rounded-xl bg-white shadow-sm">
        <table className="w-full text-left">
          <thead className="bg-gray-50">
            <tr>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500 w-12`}>
                <input 
                  type="checkbox" 
                  checked={data ? selectedIds.size === data.items.length && data.items.length > 0 : false}
                  onChange={toggleSelectAll}
                  className="rounded border-gray-300"
                />
              </th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Outage</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Type</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Amount</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Date</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Asset</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Status</th>
              <th className={`${cell} text-xs font-semibold uppercase tracking-wide text-gray-500`}>Actions</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={8} className="p-0">
                <RouteLoadingState title="Loading failed payments" description="Retrieving payments that need to be retried." />
              </td></tr>
            ) : error ? (
              <tr><td colSpan={8} className="p-0">
                <RouteErrorState title="Payments unavailable" description={error} primaryAction={{ label: "Reload page", onClick: () => window.location.reload() }} />
              </td></tr>
            ) : !data || data.items.length === 0 ? (
              <tr><td colSpan={8} className="p-0">
                <RouteEmptyState title="No failed payments" description="There are no failed payments from the last 7 days." />
              </td></tr>
            ) : data.items.map((payment: Payment) => (
              <tr
                key={payment.id}
                className="border-t transition-colors hover:bg-gray-50"
              >
                <td className={cell}>
                  <input 
                    type="checkbox" 
                    checked={selectedIds.has(payment.id)}
                    onChange={() => toggleSelect(payment.id)}
                    className="rounded border-gray-300"
                  />
                </td>
                <td className={`${cell} font-mono text-gray-700`}>
                  {payment.outage_id ? (
                    <Link
                      href={`/outages/${payment.outage_id}`}
                      className="text-blue-600 hover:underline underline-offset-2"
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
                <td className={`${cell} font-semibold ${payment.type === "penalty" ? "text-red-700" : "text-green-700"}`}>
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
                <td className={cell}>
                  <Button
                    size="sm"
                    onClick={() => handleRetry(payment.id)}
                    disabled={retryingIds.has(payment.id)}
                  >
                    {retryingIds.has(payment.id) ? "Retrying..." : "Retry"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <Dialog open={showConfirmDialog} onOpenChange={setShowConfirmDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Confirm Bulk Retry</DialogTitle>
            <DialogDescription>
              Are you sure you want to retry {selectedIds.size} payment{selectedIds.size > 1 ? 's' : ''}? This action cannot be undone.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="secondary" onClick={() => setShowConfirmDialog(false)} disabled={bulkRetrying}>
              Cancel
            </Button>
            <Button onClick={handleBulkRetry} disabled={bulkRetrying}>
              {bulkRetrying ? "Retrying..." : "Confirm Retry"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}