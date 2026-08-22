import { afterEach, describe, expect, it, vi } from "vitest";
import { computeComparisonFilters } from "@/components/dashboard/sla-dashboard-view";

describe("computeComparisonFilters", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns empty filters when no date range is selected", () => {
    expect(computeComparisonFilters({})).toEqual({});
    expect(computeComparisonFilters({ severity: "high" })).toEqual({});
  });

  it("shifts the comparison window back by the primary window length when both dates are set", () => {
    const result = computeComparisonFilters({
      date_from: "2026-08-10",
      date_to: "2026-08-20",
    });
    expect(result).toEqual({
      date_from: "2026-07-31",
      date_to: "2026-08-10",
    });
  });

  it("mirrors an open-ended range when only date_from is set", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-08-20T00:00:00.000Z"));

    const result = computeComparisonFilters({ date_from: "2026-08-10" });
    expect(result).toEqual({
      date_from: "2026-07-31",
      date_to: "2026-08-10",
    });
  });

  it("defaults to a 30-day window when only date_to is set", () => {
    const result = computeComparisonFilters({ date_to: "2026-08-20" });
    expect(result).toEqual({
      date_from: "2026-07-21",
      date_to: "2026-08-20",
    });
  });

  it("carries severity and site filters into the comparison window", () => {
    const result = computeComparisonFilters({
      date_from: "2026-08-01",
      date_to: "2026-08-08",
      severity: "critical",
      site: "site-a",
    });
    expect(result).toEqual({
      date_from: "2026-07-25",
      date_to: "2026-08-01",
      severity: "critical",
      site: "site-a",
    });
  });

  it("keeps date_to after date_from when the primary range is inverted", () => {
    const result = computeComparisonFilters({
      date_from: "2026-08-20",
      date_to: "2026-08-10",
    });
    expect(result).toEqual({
      date_from: "2026-08-20",
      date_to: "2026-08-20",
    });
  });
});
