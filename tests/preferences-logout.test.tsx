/** ApexChain Network Operations Intelligence Platform */
/**
 * Issue #413 — Preferences survive logout: resetPreferences has no callers.
 *
 * Verifies that `resetPreferences` (src/lib/preferences.ts) is actually wired
 * into `clearSession` (src/providers/session.tsx), so that both a
 * user-initiated logout and a forced logout (a definitive 401/403, or the
 * `auth:logout` window event) clear the preferences store identically. This
 * prevents a cross-user information leak on shared/kiosk browsers, where the
 * next operator to sign in would otherwise inherit the previous user's table
 * density, column visibility, and filter presets.
 */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { useSession } from "@/hooks/useSession";
import { SessionProvider } from "@/providers/session";
import {
  getPreferences,
  hydratePreferences,
  updatePreferences,
} from "@/lib/preferences";

const PREFERENCES_STORAGE_KEY = "apexchain_user_preferences";

const mockGet = vi.fn();
const mockPost = vi.fn();
const mockClearTokens = vi.fn();
const mockGetAccessToken = vi.fn();
const mockSetTokens = vi.fn();

vi.mock("@/lib/api", () => ({
  api: {
    get: (...args: unknown[]) => mockGet(...args),
    post: (...args: unknown[]) => mockPost(...args),
  },
  clearTokens: () => mockClearTokens(),
  getAccessToken: () => mockGetAccessToken(),
  setTokens: (...args: unknown[]) => mockSetTokens(...args),
}));

// Isolate the provider logic from cross-tab sync / SSE / heartbeat noise —
// this test cares only about the resetPreferences wiring in clearSession.
vi.mock("@/lib/session-sync", () => ({
  createSessionSync: () => null,
}));
vi.mock("@/lib/session-sse", () => ({
  connectSessionSse: () => ({ close: () => {} }),
}));
vi.mock("@/lib/session-heartbeat", () => ({
  startHeartbeat: () => ({ stop: () => {} }),
}));

// preferences.ts syncs to the server through a separate lightweight
// apiClient (src/lib/client.ts, fetch-based) rather than the axios `api`
// used by the session provider. Mock it so tests never hit the network.
const mockApiClient = vi.fn();
vi.mock("@/lib/client", () => ({
  apiClient: (...args: unknown[]) => mockApiClient(...args),
}));

const mockUser = {
  id: "u1",
  email: "op@example.com",
  role: "engineer",
};

function apiError(status: number) {
  return Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: {} },
  });
}

function renderSessionHook() {
  return renderHook(() => useSession(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <SessionProvider>{children}</SessionProvider>
    ),
  });
}

describe("logout clears preferences (Issue #413)", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockReset();
    mockPost.mockReset();
    mockClearTokens.mockReset();
    mockGetAccessToken.mockReset();
    mockSetTokens.mockReset();
    mockApiClient.mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("clears the preferences store after a user-initiated logout", async () => {
    // Seed preferences as if the operator customized their table view.
    mockApiClient.mockResolvedValue({});
    await updatePreferences({ tableDensity: "compact" });

    expect(getPreferences()).toEqual({ tableDensity: "compact" });
    expect(window.localStorage.getItem(PREFERENCES_STORAGE_KEY)).not.toBeNull();

    // Authenticate, then log out via the user-initiated path.
    mockGetAccessToken.mockReturnValue(null);
    window.localStorage.setItem("noc_session_seen", "1");
    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") return Promise.resolve({ data: mockUser });
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    mockPost.mockResolvedValue({ data: {} });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.state).toBe("unauthenticated");
    // The store — in-memory and localStorage — must be empty afterward.
    expect(getPreferences()).toEqual({});
    expect(window.localStorage.getItem(PREFERENCES_STORAGE_KEY)).toBeNull();
  });

  it("clears the preferences store identically on a forced logout (401)", async () => {
    mockApiClient.mockResolvedValue({});
    await updatePreferences({ tableDensity: "compact" });
    expect(getPreferences()).toEqual({ tableDensity: "compact" });

    // Simulate a hard refresh where the server now returns a definitive 401
    // (revoked/forced logout), the same way an expired session is detected.
    mockGetAccessToken.mockReturnValue(null);
    window.localStorage.setItem("noc_session_seen", "1");
    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") return Promise.reject(apiError(401));
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("unauthenticated");
    });

    expect(mockClearTokens).toHaveBeenCalled();
    expect(getPreferences()).toEqual({});
    expect(window.localStorage.getItem(PREFERENCES_STORAGE_KEY)).toBeNull();
  });

  it("clears the preferences store when the auth:logout window event fires", async () => {
    mockApiClient.mockResolvedValue({});
    await updatePreferences({ tableDensity: "compact" });
    expect(getPreferences()).toEqual({ tableDensity: "compact" });

    mockGetAccessToken.mockReturnValue(null);
    window.localStorage.setItem("noc_session_seen", "1");
    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") return Promise.resolve({ data: mockUser });
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    // The 401 interceptor in api.ts dispatches this event on a revoked
    // session; session.tsx listens for it and calls clearSession().
    await act(async () => {
      window.dispatchEvent(new Event("auth:logout"));
    });

    await waitFor(() => {
      expect(result.current.state).toBe("unauthenticated");
    });

    expect(getPreferences()).toEqual({});
    expect(window.localStorage.getItem(PREFERENCES_STORAGE_KEY)).toBeNull();
  });

  it("a fresh sign-in does not inherit the previous user's presets", async () => {
    // Previous operator customized preferences.
    mockApiClient.mockResolvedValue({});
    await updatePreferences({ tableDensity: "compact" });
    expect(getPreferences()).toEqual({ tableDensity: "compact" });

    // Log out — clears the store (in-memory + localStorage + hydration flag).
    mockGetAccessToken.mockReturnValue(null);
    window.localStorage.setItem("noc_session_seen", "1");
    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") return Promise.resolve({ data: mockUser });
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });
    mockPost.mockResolvedValue({ data: {} });

    const { result } = renderSessionHook();
    await waitFor(() => expect(result.current.state).toBe("authenticated"));
    await act(async () => {
      await result.current.logout();
    });
    expect(getPreferences()).toEqual({});

    // A new operator signs in on the same browser. Their hydration call
    // returns their own (different) server-side preferences.
    mockApiClient.mockResolvedValue({ tableDensity: "default" });
    const hydrated = await hydratePreferences();

    // The new user's preferences must not contain any trace of the
    // previous operator's "compact" density preset.
    expect(hydrated).toEqual({ tableDensity: "default" });
    expect(getPreferences()).toEqual({ tableDensity: "default" });
  });
});
