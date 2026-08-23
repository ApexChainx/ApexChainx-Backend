/** ApexChain Frontend Test Suite */
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import OutagesPageClient from "@/app/outages/components/outages-page-client";
import OutageDetailsPage from "@/app/outages/[id]/page";

vi.mock("next/link", () => ({
  default: ({
    children,
    href,
    ...props
  }: React.AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) => (
    <a href={href} {...props}>
      {children}
    </a>
  ),
}));

vi.mock("next/navigation", () => ({
  useParams: () => ({ id: paramsId }),
  useRouter: () => ({ push: vi.fn(), back: vi.fn(), replace: vi.fn() }),
}));

const mockUseOutagesTableState = vi.fn();
const mockUseOutages = vi.fn();
const mockGetOutage = vi.fn();
const mockResolveOutage = vi.fn();

let paramsId = "outage-1";

vi.mock("@/hooks/useOutagesTableState", () => ({
  useOutagesTableState: () => mockUseOutagesTableState(),
}));

vi.mock("@/features/outages/hooks/useOutages", () => ({
  useOutages: (...args: unknown[]) => mockUseOutages(...args),
}));

vi.mock("@/services/outages", () => ({
  getOutage: (...args: unknown[]) => mockGetOutage(...args),
  resolveOutage: (...args: unknown[]) => mockResolveOutage(...args),
}));

vi.mock("@/components/ui/toast", () => ({
  useToast: () => vi.fn(),
}));

function renderWithProviders(ui: React.ReactElement) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(ui, {
    wrapper: ({ children }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    ),
  });
}

const baseOutage = {
  id: "outage-1",
  site_name: "Lagos Core POP",
  severity: "critical",
  status: "open",
  detected_at: "2026-03-27T08:00:00.000Z",
  description: "Transit outage",
  affected_services: ["Backhaul", "Voice"],
  affected_subscribers: 3200,
};

describe("outages frontend flow", () => {
  beforeEach(() => {
    paramsId = "outage-1";
    mockUseOutagesTableState.mockReset();
    mockUseOutages.mockReset();
    mockGetOutage.mockReset();
    mockResolveOutage.mockReset();
  });

  it("covers outage list browsing", () => {
    renderWithProviders(
      <OutagesPageClient
        data={[
          {
            id: "outage-1",
            title: "Lagos Core POP outage",
            site_name: "Lagos Core POP",
            status: "open",
            createdAt: "2026-03-27T08:00:00.000Z",
            assigned_to: "ops-team",
          },
        ]}
      />,
    );

    expect(screen.getByPlaceholderText("Search outages...")).toBeInTheDocument();
    expect(screen.getByRole("combobox")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Export" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Delete" })).toBeInTheDocument();
    expect(screen.getByText("Lagos Core POP outage")).toBeInTheDocument();
    expect(screen.getByText("Assigned: ops-team")).toBeInTheDocument();
  });

  it("covers outage detail loading and resolution", async () => {
    mockGetOutage.mockResolvedValue(baseOutage);
    mockResolveOutage.mockResolvedValue({
      outage: {
        ...baseOutage,
        status: "resolved",
        resolved_at: "2026-03-27T09:00:00.000Z",
      },
      sla: {
        status: "met",
        mttr_minutes: 42,
        threshold_minutes: 60,
        amount: 150,
        payment_type: "reward",
        rating: "excellent",
      },
      payment: {
        id: "pay-1",
        transaction_hash: "tx-001",
        type: "reward",
        amount: 150,
        asset_code: "USDC",
        from_address: "from-address",
        to_address: "to-address",
        status: "pending",
        outage_id: "outage-1",
        sla_result_id: 12,
        created_at: "2026-03-27T09:00:00.000Z",
        confirmed_at: null,
      },
    });

    renderWithProviders(<OutageDetailsPage />);

    expect(await screen.findByRole("heading", { name: "Outage outage-1" })).toBeInTheDocument();
    expect(mockGetOutage).toHaveBeenCalledWith("outage-1", expect.objectContaining({ signal: expect.any(AbortSignal) }));

    fireEvent.click(screen.getByRole("button", { name: "Resolve Outage" }));

    const mttrInput = screen.getByLabelText("Mean time to resolve (minutes)");
    fireEvent.change(mttrInput, { target: { value: "42" } });
    fireEvent.click(screen.getByRole("button", { name: "Confirm resolution" }));

    await waitFor(() => {
      expect(mockResolveOutage).toHaveBeenCalledWith("outage-1", { mttr_minutes: 42 });
    });

    expect(await screen.findByText("pending")).toBeInTheDocument();
    expect(screen.getByText(/150 USDC/)).toBeInTheDocument();
  });

  it("cleans up polling and ignores in-flight poll responses after unmount", async () => {
    let resolvePoll!: (outage: typeof baseOutage) => void;
    const pollPromise = new Promise<typeof baseOutage>((resolve) => {
      resolvePoll = resolve;
    });

    mockGetOutage
      .mockResolvedValueOnce(baseOutage) // initial fetch
      .mockImplementationOnce(() => pollPromise); // first poll stays in-flight

    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
    });

    try {
      const { unmount } = renderWithProviders(<OutageDetailsPage />);

      // Flush the initial fetch so the page renders with the outage
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });

      expect(screen.getByRole("heading", { name: "Outage outage-1" })).toBeInTheDocument();

      // First poll fires at 15s and remains in-flight
      act(() => {
        vi.advanceTimersByTime(15_000);
      });
      expect(mockGetOutage).toHaveBeenCalledTimes(2);

      // Navigate away while the poll request is in-flight
      unmount();

      // Resolving the in-flight poll after unmount must not throw or update state
      await act(async () => {
        resolvePoll({ ...baseOutage });
        await pollPromise;
      });

      // Interval was cleared on unmount: no further polls fire
      act(() => {
        vi.advanceTimersByTime(60_000);
      });
      expect(mockGetOutage).toHaveBeenCalledTimes(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("ignores stale poll responses after navigating to a different outage", async () => {
    let resolvePoll!: (outage: typeof baseOutage) => void;
    const stalePollPromise = new Promise<typeof baseOutage>((resolve) => {
      resolvePoll = resolve;
    });

    mockGetOutage
      .mockResolvedValueOnce(baseOutage) // initial fetch for outage-1
      .mockImplementationOnce(() => stalePollPromise) // outage-1 poll stays in-flight
      .mockResolvedValueOnce({ ...baseOutage, id: "outage-2" }); // fetch for outage-2

    vi.useFakeTimers({
      toFake: ["setTimeout", "clearTimeout", "setInterval", "clearInterval", "Date"],
    });

    try {
      const { rerender } = renderWithProviders(<OutageDetailsPage />);

      // Initial fetch for outage-1 resolves and the page renders
      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByRole("heading", { name: "Outage outage-1" })).toBeInTheDocument();

      // First poll for outage-1 fires at 15s and remains in-flight
      act(() => {
        vi.advanceTimersByTime(15_000);
      });
      expect(mockGetOutage).toHaveBeenCalledTimes(2);

      // Navigate to a different outage while the poll is in-flight
      paramsId = "outage-2";
      rerender(<OutageDetailsPage />);

      await act(async () => {
        await Promise.resolve();
        await Promise.resolve();
      });
      expect(screen.getByRole("heading", { name: "Outage outage-2" })).toBeInTheDocument();

      // The stale outage-1 poll response arrives late: it must not overwrite
      // the current outage with data from the previous one
      await act(async () => {
        resolvePoll({ ...baseOutage });
        await stalePollPromise;
      });

      expect(screen.getByRole("heading", { name: "Outage outage-2" })).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Outage outage-1" })).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});
