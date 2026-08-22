/** ApexChain Frontend Test Suite */
import { renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api } from "@/lib/api";
import { useSlaConfig, useUpdateSlaConfig } from "@/hooks/useSlaConfig";

vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>();
  return {
    ...actual,
    api: {
      get: vi.fn(),
      put: vi.fn(),
    },
    dedupeByKey: async (_key: string, fn: () => Promise<unknown>) => fn(),
  };
});

const mockedApi = vi.mocked(api);

function makeWrapper(client: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  };
}

describe("useSlaConfig cache invalidation", () => {
  let client: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  it("populates cache after successful fetch", async () => {
    mockedApi.get.mockResolvedValueOnce({
      data: { critical: { threshold_minutes: 30, penalty_per_minute: 5, reward_base: 100 } },
    });

    const { result } = renderHook(() => useSlaConfig(), { wrapper: makeWrapper(client) });
    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    expect(result.current.data?.length).toBe(1);
    expect(result.current.data?.[0]?.severity).toBe("critical");
  });

  it("updates cache entry after mutation without refetch", async () => {
    const initial = [{ severity: "high", threshold_minutes: 60, penalty_per_minute: 2, reward_base: 50 }];
    client.setQueryData(["sla", "config"], initial);

    mockedApi.put.mockResolvedValueOnce({
      data: { threshold_minutes: 45, penalty_per_minute: 3, reward_base: 50 },
    });

    const { result } = renderHook(() => useUpdateSlaConfig(), { wrapper: makeWrapper(client) });
    result.current.mutate({ severity: "high", threshold_minutes: 45, penalty_per_minute: 3, reward_base: 50 });

    await waitFor(() => expect(result.current.isSuccess).toBe(true));

    const cached = client.getQueryData<typeof initial>(["sla", "config"]);
    expect(cached?.[0]?.threshold_minutes).toBe(45);
    expect(mockedApi.get).not.toHaveBeenCalled();
  });
});
