import { beforeEach, describe, expect, it, vi } from "vitest";

const { apiClient } = vi.hoisted(() => ({ apiClient: vi.fn() }));
vi.mock("@/lib/client", () => ({ apiClient }));

import {
  getPreferences,
  hydratePreferences,
  resetPreferences,
  subscribeToPreferences,
  updatePreferences,
} from "@/lib/preferences";

const key = "apexchain_user_preferences";

describe("preferences", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    resetPreferences();
    vi.clearAllMocks();
  });

  it("hydrates with server values winning", async () => {
    localStorage.setItem(key, JSON.stringify({ tableDensity: "compact", onboardingTourDone: false }));
    apiClient.mockResolvedValue({ tableDensity: "comfortable" });
    await expect(hydratePreferences()).resolves.toEqual({ tableDensity: "comfortable", onboardingTourDone: false });
  });

  it("falls back to local preferences when hydration fails", async () => {
    localStorage.setItem(key, JSON.stringify({ tableDensity: "compact" }));
    apiClient.mockRejectedValue(new Error("offline"));
    await expect(hydratePreferences()).resolves.toEqual({ tableDensity: "compact" });
  });

  it("hydrates only once until reset", async () => {
    apiClient.mockResolvedValue({ tableDensity: "compact" });
    await hydratePreferences();
    await hydratePreferences();
    expect(apiClient).toHaveBeenCalledTimes(1);
  });

  it("updates local state and syncs the server", async () => {
    apiClient.mockResolvedValue({});
    await expect(updatePreferences({ tableDensity: "compact" })).resolves.toEqual({ tableDensity: "compact" });
    expect(getPreferences()).toEqual({ tableDensity: "compact" });
    expect(apiClient).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ method: "PUT" }));
  });

  it("keeps local changes when server update fails", async () => {
    apiClient.mockRejectedValue(new Error("offline"));
    await updatePreferences({ tableDensity: "compact" });
    expect(getPreferences()).toEqual({ tableDensity: "compact" });
  });

  it("notifies subscribers and supports unsubscribe", async () => {
    const listener = vi.fn();
    const unsubscribe = subscribeToPreferences(listener);
    expect(listener).toHaveBeenCalledWith({});
    await updatePreferences({ tableDensity: "compact" });
    expect(listener).toHaveBeenLastCalledWith({ tableDensity: "compact" });
    unsubscribe();
    await updatePreferences({ tableDensity: "comfortable" });
    expect(listener).toHaveBeenCalledTimes(2);
  });

  it("resets memory, storage, hydration, and subscribers", async () => {
    const listener = vi.fn();
    subscribeToPreferences(listener);
    await updatePreferences({ tableDensity: "compact" });
    resetPreferences();
    expect(getPreferences()).toEqual({});
    expect(localStorage.getItem(key)).toBeNull();
    expect(listener).toHaveBeenLastCalledWith({});
  });
});
