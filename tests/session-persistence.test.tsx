/** ApexChain Network Operations Intelligence Platform */
import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";

import { useSession } from "@/hooks/useSession";
import { SessionProvider } from "@/providers/session";

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

// Isolate the provider logic from cross-tab sync / SSE / heartbeat noise.
vi.mock("@/lib/session-sync", () => ({
  createSessionSync: () => null,
}));
vi.mock("@/lib/session-sse", () => ({
  connectSessionSse: () => ({ close: () => {} }),
}));
vi.mock("@/lib/session-heartbeat", () => ({
  startHeartbeat: () => ({ stop: () => {} }),
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

function networkError() {
  return Object.assign(new Error("Network Error"), { code: "ERR_NETWORK" });
}

/**
 * Hard refresh simulation: in-memory tokens are gone (getAccessToken returns
 * null), but the browser previously authenticated (localStorage flag) and the
 * server still holds a valid httpOnly session cookie.
 */
function simulateHardRefresh() {
  mockGetAccessToken.mockReturnValue(null);
  window.localStorage.setItem("noc_session_seen", "1");
}

function renderSessionHook() {
  return renderHook(() => useSession(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <SessionProvider>{children}</SessionProvider>
    ),
  });
}

describe("session persistence across hard refresh", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockGet.mockReset();
    mockPost.mockReset();
    mockClearTokens.mockReset();
    mockGetAccessToken.mockReset();
    mockSetTokens.mockReset();
    window.localStorage.clear();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("restores an authenticated session from valid httpOnly cookies via /auth/session", async () => {
    simulateHardRefresh();

    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") {
        return Promise.resolve({ data: mockUser });
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    expect(result.current.user).toEqual(mockUser);
    expect(mockGet).toHaveBeenCalledWith("/auth/session", expect.any(Object));
  });

  it("falls back to /auth/me when the session endpoint is not deployed", async () => {
    simulateHardRefresh();

    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") {
        return Promise.reject(apiError(404));
      }
      if (url === "/auth/me") {
        return Promise.resolve({ data: mockUser });
      }
      return Promise.reject(new Error(`Unexpected request: ${url}`));
    });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("authenticated");
    });

    expect(result.current.user).toEqual(mockUser);
    expect(mockGet).toHaveBeenCalledWith("/auth/me", expect.any(Object));
  });

  it("does not clear tokens or cookies when bootstrap hits a network error", async () => {
    vi.useFakeTimers();
    simulateHardRefresh();

    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") {
        return Promise.reject(apiError(404));
      }
      return Promise.reject(networkError());
    });

    const { result } = renderSessionHook();

    // Allow the three retries (1s + 2s + 4s) to elapse.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });

    expect(result.current.state).toBe("unauthenticated");
    expect(result.current.user).toBeNull();

    // A transient failure must never destroy a potentially-valid session.
    expect(mockClearTokens).not.toHaveBeenCalled();
  });

  it("distinguishes a definitive 401 (no session) from a network error", async () => {
    simulateHardRefresh();

    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") {
        return Promise.reject(apiError(401));
      }
      return Promise.reject(networkError());
    });

    const { result } = renderSessionHook();

    await waitFor(() => {
      expect(result.current.state).toBe("unauthenticated");
    });

    // A definitive 401 from the server IS a revocation — tokens are cleared.
    expect(mockClearTokens).toHaveBeenCalled();
    // No retries should have been attempted for /auth/me.
    expect(mockGet).not.toHaveBeenCalledWith("/auth/me", expect.any(Object));
  });

  it("does not prematurely clear on first visit when the backend is unreachable", async () => {
    vi.useFakeTimers();
    // First visit: no token AND no session flag.
    mockGetAccessToken.mockReturnValue(null);

    mockGet.mockImplementation((url: string) => {
      if (url === "/auth/session") {
        return Promise.reject(apiError(404));
      }
      return Promise.reject(networkError());
    });

    const { result } = renderSessionHook();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(8_000);
    });

    expect(result.current.state).toBe("unauthenticated");
    expect(mockClearTokens).not.toHaveBeenCalled();
  });
});
