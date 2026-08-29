"use client";
/** ApexChain Network Operations Intelligence Platform */

import { RouteLoadingState } from "@/components/ui/route-state";
import { useOutages } from "@/features/outages/hooks/useOutages";
import OutagesPageClient from "./outages-page-client";

/**
 * Display shape the list UI renders. The API model names these fields
 * `site_name` / `detected_at`; the existing list client was built around
 * `title` / `createdAt`, so map here rather than churning the client.
 */
type ClientOutage = {
  id: string;
  title: string;
  site_name: string;
  status: string;
  createdAt: string;
  assigned_to?: string;
};

/**
 * Connects the /outages route to `useOutages`, the offline-first data path:
 * successful fetches are persisted to IndexedDB and the hook hydrates from
 * that store on mount, so an operator who loses connectivity still sees the
 * last-known outage list.
 */
export default function OutagesConnectedList() {
  const { data, isLoading } = useOutages();

  const items: ClientOutage[] = (data?.items ?? []).map((outage) => ({
    id: outage.id,
    title: outage.site_name,
    site_name: outage.site_name,
    status: outage.status,
    createdAt: outage.detected_at,
    ...(outage.assigned_to !== undefined
      ? { assigned_to: outage.assigned_to }
      : {}),
  }));

  // First paint has nothing yet: wait for the network fetch or the IndexedDB
  // hydration before showing the (empty) table chrome.
  if (isLoading && items.length === 0) {
    return (
      <RouteLoadingState
        title="Loading outages"
        description="Gathering the latest incidents and preparing the outage table."
      />
    );
  }

  return <OutagesPageClient data={items} />;
}