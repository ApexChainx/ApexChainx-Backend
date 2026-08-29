import { api } from "@/lib/api";
import type { PaginatedOutages } from "@/types/outages";

export interface Outage {
  id: string;
  severity: "critical" | "high" | "medium" | "low";
  status: "open" | "resolved";
  started_at: string;
  resolved_at?: string;
  mttr_minutes?: number;
  sla_result?: {
    status: "met" | "violated";
    amount: number;
    rating: string;
  };
}

export interface OutagesResponse {
  items: Outage[];
  page: number;
  page_size: number;
  total: number;
}

export interface OutagesQuery {
  page: number;
  page_size?: number | undefined;
  severity?: string | undefined;
  status?: string | undefined;
  search?: string | undefined;
  sort_field?: string | undefined;
  sort_order?: "asc" | "desc" | undefined;
}

export async function fetchOutages(query: OutagesQuery): Promise<PaginatedOutages> {
  const { data } = await api.get<PaginatedOutages>("/outages", {
    params: {
      page: query.page,
      page_size: query.page_size ?? 20,
      severity: query.severity,
      status: query.status,
      search: query.search,
      sort_field: query.sort_field,
      sort_order: query.sort_order,
    },
  });

  return data;
}
