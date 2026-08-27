/** ApexChain Network Operations Intelligence Platform */
/**
 * Hook: useInvalidateOutageChange
 *
 * Issue #131 — Coordinate query invalidation across mutating flows.
 *
 * Resolving an outage from /outages/[id] should invalidate:
 *  - outages (list & detail)
 *  - dashboard-metrics (SLA compliance, penalties, rewards)
 *  - payments (list & detail)
 *  - disputes (list & detail)
 *
 * This hook returns a stable invalidate function that busts every cache
 * in the slaEventKeys family so no stale data lingers after a mutation.
 */

import { useQueryClient } from "@tanstack/react-query";
import { useCallback } from "react";
import { slaEventKeys } from "@/lib/query-keys";

export function useInvalidateOutageChange() {
  const queryClient = useQueryClient();

  const invalidate = useCallback(async () => {
    await Promise.all([
      // Bust all outage queries (list + detail)
      queryClient.invalidateQueries({ queryKey: slaEventKeys.outages.all }),

      // Bust dashboard analytics (SLA compliance, trends)
      queryClient.invalidateQueries({
        queryKey: slaEventKeys.dashboard(),
      }),

      // Bust payment lists & details
      queryClient.invalidateQueries({ queryKey: slaEventKeys.payments.all }),

      // Bust dispute lists
      queryClient.invalidateQueries({ queryKey: slaEventKeys.disputes.all }),

      // Bust SLA calculations
      queryClient.invalidateQueries({ queryKey: slaEventKeys.sla.all }),
    ]);
  }, [queryClient]);

  return invalidate;
}
