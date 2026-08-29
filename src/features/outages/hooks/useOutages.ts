import { keepPreviousData, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { fetchOutages } from "@/lib/outages";
import { persistedCache } from "@/lib/persisted-cache";
import { slaEventKeys } from "@/lib/query-keys";
import type { PaginatedOutages } from "@/types/outages";
import type { OutagesQuery } from "@/lib/outages";

/**
 * See docs/offline-cache.md for the full picture of what's cached where
 * (IndexedDB via `persistedCache`, this React Query cache, and the service
 * worker), TTL/staleTime/gcTime values, the hydration rules applied below,
 * and — importantly — the current purge/eviction gaps (e.g. neither this
 * cache nor `persistedCache` is cleared on logout today).
 */

export interface UseOutagesParams {
  page?: number | undefined;
  page_size?: number | undefined;
  severity?: string | undefined;
  status?: string | undefined;
  search?: string | undefined;
  sort_field?: string | undefined;
  sort_order?: "asc" | "desc" | undefined;
}

const DEFAULT_PAGE = 1;
const DEFAULT_PAGE_SIZE = 10;
const CACHE_TTL_MS = 1000 * 60 * 30; // 30 minutes

function cacheKey(params: UseOutagesParams): string {
  return `outages:${JSON.stringify(params)}`;
}

export function useOutages(params: UseOutagesParams = {}) {
  const queryClient = useQueryClient();
  const hydratedRef = useRef(false);

  const normalizedParams = useMemo<UseOutagesParams>(
    () => ({
      page: params.page ?? DEFAULT_PAGE,
      page_size: params.page_size ?? DEFAULT_PAGE_SIZE,
      severity: params.severity?.trim() || undefined,
      status: params.status?.trim() || undefined,
      search: params.search?.trim() || undefined,
      sort_field: params.sort_field?.trim() || undefined,
      sort_order: params.sort_order,
    }),
    [params.page, params.page_size, params.severity, params.status, params.search, params.sort_field, params.sort_order],
  );

  const queryKey = useMemo(
    () => slaEventKeys.outages.list(normalizedParams as Record<string, unknown>),
    [normalizedParams],
  );
  const cacheKeyStr = useMemo(() => cacheKey(normalizedParams), [normalizedParams]);

  // Hydrate from IndexedDB on first mount (offline-first)
  useEffect(() => {
    if (hydratedRef.current) return;
    hydratedRef.current = true;

    let mounted = true;

    persistedCache.get<PaginatedOutages>(cacheKeyStr).then((cached) => {
      if (!mounted) return;
      if (cached && cached.items.length > 0) {
        // Only set query data if there's no existing data yet
        const existing = queryClient.getQueryData<PaginatedOutages>(queryKey);
        if (!existing || existing.items.length === 0) {
          queryClient.setQueryData(queryKey, cached);
        }
      }
    }).catch(() => {
      // Silently ignore — fall back to network fetch
    });

    return () => {
      mounted = false;
    };
  }, [queryClient, queryKey, cacheKeyStr]);

  const query = useQuery<PaginatedOutages, Error>({
    queryKey,

    queryFn: async () => {
      const data = await fetchOutages(normalizedParams as unknown as OutagesQuery);

      // Persist successful fetches to IndexedDB
      if (data && data.items.length > 0) {
        void persistedCache.set(cacheKeyStr, data, CACHE_TTL_MS);
      }

      return data;
    },

    placeholderData: keepPreviousData,

    staleTime: 1000 * 60 * 5, // 5 minutes

    gcTime: 1000 * 60 * 10, // 10 minutes

    retry: 2,

    refetchOnWindowFocus: false,

    enabled: (normalizedParams.page ?? 1) > 0,

    select: (data) => ({
      ...data,
      items: data.items ?? [],
    }),
  });

  return query;
}