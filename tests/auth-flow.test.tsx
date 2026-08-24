/** ApexChain Frontend Test Suite */
/** ApexChain Network Operations Intelligence Platform */
import { act, renderHook, waitFor, type RenderHookResult } from "@testing-library/react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
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

/** Builds an axios-shaped rejection with a response status. */
function apiError(status: number, message = `Request failed with status code ${status}`) {
  return Object.assign(new Error(message), {
    response: { status, data: {} },
  });
}

/**
 * Routes mocked api.get by URL. By default the dedicated `/auth/session`
 * endpoint is not deployed yet (404) so bootstrap falls back to `/auth/me`.
 */
function mockHttp({
  sessionStatus = 404,
  me,
  meError,
}: {
  sessionStatus?: number;
  me?: unknown;
  meError?: unknown;
} = {}) {
  mockGet.mockImplementation((url: string) => {
    if (url === "/auth/session") {
      if (sessionStatus === 200) return Promise.resolve({ data: mockUser });
      return Promise.reject(apiError(sessionStatus));
    }
    if (url === "/auth/me") {
      if (meError) return Promise.reject(meError);
      return Promise.resolve({ data: me !== undefined ? me : mockUser });
    }
    return Promise.reject(new Error(`Unexpected request: ${url}`));
  });
}

function renderSessionHook() {
  return renderHook(() => useSession(), {
    wrapper: ({ children }: { children: ReactNode }) => (
      <SessionProvider>{children}</SessionProvider>
    ),
  });
}

describe("useSession", () => {
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
    vi.restoreAllMocks();
  });

  describe("initial authentication state", () => {
    it("authenticates when token exists and /auth/me succeeds", async () => {
      mockGetAccessToken.mockReturnValue("valid-token");
      mockHttp({ me: mockUser });

      const { result } = renderSessionHook();

      expect(result.current.state).toBe("loading");

      await waitFor(() => {
        expect(result.current.state).toBe("authenticated");
      });

      expect(result.current.user).toEqual(mockUser);

      expect(mockGet).toHaveBeenCalledWith(
        "/auth/me",
        expect.any(Object),
      );
    });

    it("authenticates via the cookie session endpoint after a hard refresh", async () => {
      // Hard refresh: in-memory token is gone, but the browser previously
      // authenticated (flag set) and the httpOnly cookie is still valid.
      mockGetAccessToken.mockReturnValue(null);
      window.localStorage.setItem("noc_session_seen", "1");
      mockHttp({ sessionStatus: 200 });

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("authenticated");
      });

      expect(result.current.user).toEqual(mockUser);
    });

    it("falls back to unauthenticated when /auth/me definitively fails", async () => {
      mockGetAccessToken.mockReturnValue("expired-token");
      mockHttp({ meError: apiError(401) });

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("unauthenticated");
      });

      expect(result.current.user).toBeNull();

      expect(mockClearTokens).toHaveBeenCalledTimes(1);
    });

    it("is unauthenticated when no token or past session exists", async () => {
      mockGetAccessToken.mockReturnValue(null);

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("unauthenticated");
      });

      expect(result.current.user).toBeNull();

      expect(mockGet).not.toHaveBeenCalled();
    });
  });

  describe("storeSession", () => {
    it("stores session and updates authenticated state", async () => {
      mockGetAccessToken.mockReturnValue(null);

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("unauthenticated");
      });

      act(() => {
        result.current.storeSession(
          "access-token",
          "refresh-token",
          mockUser,
        );
      });

      expect(mockSetTokens).toHaveBeenCalledWith(
        "access-token",
        "refresh-token",
      );

      expect(result.current.state).toBe("authenticated");
      expect(result.current.user).toEqual(mockUser);
    });
  });

  describe("logout", () => {
    it("logs out successfully and clears session state", async () => {
      mockGetAccessToken.mockReturnValue("valid-token");

      mockHttp({ me: mockUser });

      mockPost.mockResolvedValue({});

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("authenticated");
      });

      await act(async () => {
        await result.current.logout();
      });

      expect(mockPost).toHaveBeenCalledWith("/auth/logout");

      expect(mockClearTokens).toHaveBeenCalledTimes(1);

      expect(result.current.state).toBe("unauthenticated");
      expect(result.current.user).toBeNull();
    });

    it("still clears local session when logout API fails", async () => {
      mockGetAccessToken.mockReturnValue("valid-token");

      mockHttp({ me: mockUser });

      mockPost.mockRejectedValue(new Error("Network Error"));

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("authenticated");
      });

      await act(async () => {
        await result.current.logout();
      });

      expect(mockClearTokens).toHaveBeenCalled();

      expect(result.current.state).toBe("unauthenticated");
      expect(result.current.user).toBeNull();
    });
  });

  describe("edge cases", () => {
    it("handles malformed API response gracefully", async () => {
      mockGetAccessToken.mockReturnValue("token");

      mockHttp({ me: null });

      const { result } = renderSessionHook();

      await waitFor(() => {
        expect(result.current.state).toBe("authenticated");
      });

      expect(result.current.user).toBeNull();
    });

    it("does not call clearTokens unnecessarily", async () => {
      mockGetAccessToken.mockReturnValue(null);

      renderSessionHook();

      await waitFor(() => {
        expect(mockClearTokens).not.toHaveBeenCalled();
      });
    });
  });
});
