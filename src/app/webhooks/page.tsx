"use client";
/** ApexChain Network Operations Intelligence Platform */
/** ApexChain Network Operations Intelligence Platform */

import { useState } from "react";
import { validateUrl } from "@/lib/validate-url";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  fetchWebhooks,
  createWebhook,
  updateWebhook,
  deleteWebhook,
  fetchWebhookDeliveries,
  retryDelivery,
} from "@/services/webhookService";
import type { Webhook, WebhookDelivery } from "@/types/webhook";

const AVAILABLE_EVENTS = ["outage.created", "outage.resolved", "payment.processed", "sla.breached"];

export default function WebhooksPage() {
  const qc = useQueryClient();
  const [selectedWebhook, setSelectedWebhook] = useState<Webhook | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [formUrl, setFormUrl] = useState("");
  const [formEvents, setFormEvents] = useState<string[]>([]);
  const [formError, setFormError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const { data: webhooks = [], isLoading } = useQuery({
    queryKey: ["webhooks"],
    queryFn: fetchWebhooks,
  });

  const { data: deliveries = [], isLoading: deliveriesLoading } = useQuery({
    queryKey: ["webhook-deliveries", selectedWebhook?.id],
    queryFn: () => fetchWebhookDeliveries(selectedWebhook!.id),
    enabled: !!selectedWebhook,
  });

  const createMutation = useMutation({
    mutationFn: createWebhook,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webhooks"] });
      resetForm();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const updateMutation = useMutation({
    mutationFn: ({ id, payload }: { id: string; payload: Parameters<typeof updateWebhook>[1] }) =>
      updateWebhook(id, payload),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webhooks"] });
      resetForm();
    },
    onError: (err: Error) => setFormError(err.message),
  });

  const deleteMutation = useMutation({
    mutationFn: deleteWebhook,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["webhooks"] });
      if (selectedWebhook) setSelectedWebhook(null);
    },
  });

  const retryMutation = useMutation({
    mutationFn: ({ webhookId, deliveryId }: { webhookId: string; deliveryId: string }) =>
      retryDelivery(webhookId, deliveryId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["webhook-deliveries", selectedWebhook?.id] }),
  });

  function resetForm() {
    setShowForm(false);
    setFormUrl("");
    setFormEvents([]);
    setFormError(null);
    setEditingId(null);
  }

  function openCreate() {
    setEditingId(null);
    setFormUrl("");
    setFormEvents([]);
    setFormError(null);
    setShowForm(true);
  }

  function openEdit(wh: Webhook) {
    setEditingId(wh.id);
    setFormUrl(wh.url);
    setFormEvents(wh.events);
    setFormError(null);
    setShowForm(true);
  }

  function toggleEvent(event: string) {
    setFormEvents((prev) =>
      prev.includes(event) ? prev.filter((e) => e !== event) : [...prev, event]
    );
  }

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setFormError(null);
    if (!formUrl) { setFormError("URL is required."); return; }
    const urlValidation = validateUrl(formUrl);
    if (!urlValidation.valid) { setFormError(urlValidation.reason ?? "Invalid URL"); return; }
    if (formEvents.length === 0) { setFormError("Select at least one event."); return; }

    if (editingId) {
      updateMutation.mutate({ id: editingId, payload: { url: formUrl, events: formEvents } });
    } else {
      createMutation.mutate({ url: formUrl, events: formEvents });
    }
  }

  const isSaving = createMutation.isPending || updateMutation.isPending;

  return (
    <div className="mx-auto max-w-4xl space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-800">Webhooks</h1>
          <p className="text-sm text-gray-500">Manage webhook endpoints and delivery history.</p>
        </div>
        <button
          onClick={openCreate}
          className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700"
        >
          + New webhook
        </button>
      </div>

      {showForm && (
        <form
          onSubmit={handleSubmit}
          className="space-y-4 rounded-xl border bg-white p-5 shadow-sm"
        >
          <h2 className="text-base font-semibold text-gray-700">
            {editingId ? "Edit webhook" : "New webhook"}
          </h2>

          <div className="space-y-1">
            <label className="block text-sm font-medium text-gray-700">Payload URL</label>
            <input
              type="url"
              required
              value={formUrl}
              onChange={(e) => setFormUrl(e.target.value)}
              placeholder="https://example.com/webhook"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
            />
          </div>

          <div className="space-y-2">
            <p className="text-sm font-medium text-gray-700">Events</p>
            <div className="flex flex-wrap gap-2">
              {AVAILABLE_EVENTS.map((ev) => (
                <label key={ev} className="flex cursor-pointer items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={formEvents.includes(ev)}
                    onChange={() => toggleEvent(ev)}
                    className="rounded"
                  />
                  {ev}
                </label>
              ))}
            </div>
          </div>

          {formError && (
            <p className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">{formError}</p>
          )}

          <div className="flex gap-2">
            <button
              type="submit"
              disabled={isSaving}
              className="rounded-lg bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-40"
            >
              {isSaving ? "Saving…" : "Save"}
            </button>
            <button
              type="button"
              onClick={resetForm}
              className="rounded-lg border px-4 py-2 text-sm text-gray-600 hover:bg-gray-50"
            >
              Cancel
            </button>
          </div>
        </form>
      )}

      {isLoading ? (
        <p className="text-sm text-gray-400">Loading webhooks…</p>
      ) : webhooks.length === 0 ? (
        <p className="text-sm text-gray-400">No webhooks configured yet.</p>
      ) : (
        <div className="space-y-3">
          {webhooks.map((wh) => (
            <div
              key={wh.id}
              className="rounded-xl border bg-white p-4 shadow-sm"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-gray-800">{wh.url}</p>
                  <p className="mt-0.5 text-xs text-gray-400">
                    {wh.events.join(", ")} &middot;{" "}
                    <span className={wh.active ? "text-green-600" : "text-gray-400"}>
                      {wh.active ? "Active" : "Inactive"}
                    </span>
                  </p>
                </div>
                <div className="flex shrink-0 gap-2">
                  <button
                    onClick={() => setSelectedWebhook(selectedWebhook?.id === wh.id ? null : wh)}
                    className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
                  >
                    {selectedWebhook?.id === wh.id ? "Hide deliveries" : "Deliveries"}
                  </button>
                  <button
                    onClick={() => openEdit(wh)}
                    className="rounded border px-2 py-1 text-xs text-gray-600 hover:bg-gray-50"
                  >
                    Edit
                  </button>
                  <button
                    onClick={() => deleteMutation.mutate(wh.id)}
                    disabled={deleteMutation.isPending}
                    className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50 disabled:opacity-40"
                  >
                    Delete
                  </button>
                </div>
              </div>

              {selectedWebhook?.id === wh.id && (
                <div className="mt-4 border-t pt-4">
                  <h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                    Delivery history
                  </h3>
                  {deliveriesLoading ? (
                    <p className="text-xs text-gray-400">Loading…</p>
                  ) : deliveries.length === 0 ? (
                    <p className="text-xs text-gray-400">No deliveries yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {deliveries.map((d: WebhookDelivery) => (
                        <div
                          key={d.id}
                          className="flex items-center justify-between rounded-lg bg-gray-50 px-3 py-2 text-xs"
                        >
                          <div className="flex items-center gap-3">
                            <span
                              className={
                                d.status === "success"
                                  ? "text-green-600"
                                  : d.status === "failed"
                                  ? "text-red-600"
                                  : "text-yellow-600"
                              }
                            >
                              {d.status}
                            </span>
                            <span className="text-gray-500">{d.event}</span>
                            {d.response_code && (
                              <span className="text-gray-400">HTTP {d.response_code}</span>
                            )}
                          </div>
                          <div className="flex items-center gap-2">
                            <span className="text-gray-400">
                              {new Date(d.created_at).toLocaleString()}
                            </span>
                            {d.status === "failed" && (
                              <button
                                onClick={() =>
                                  retryMutation.mutate({ webhookId: wh.id, deliveryId: d.id })
                                }
                                disabled={retryMutation.isPending}
                                className="rounded border border-blue-200 px-2 py-0.5 text-blue-600 hover:bg-blue-50 disabled:opacity-40"
                              >
                                Retry
                              </button>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
