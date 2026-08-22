"use client";
/** ApexChain Network Operations Intelligence Platform */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import {
  api,
  clearTokens,
  setTokens,
} from "@/lib/api";
import { ENDPOINTS } from "@/lib/endpoints";
import { checkRateLimit } from "@/lib/rate-limit";

const LOGOUT_RATE_LIMIT = { maxAttempts: 5, windowMs: 60_000 };
import { logger } from "@/lib/logger";

import {
  createSessionSync,
  type SessionSync,
  type SessionSyncMessage,
} from "@/lib/session-sync";

import {
  connectSessionSse,
  type SseConnection,
} from "@/lib/session-sse";

import {
  startHeartbeat,
  type HeartbeatHandle,
} from "@/lib/session-heartbeat";

export type SessionState =
  | "loading"
  | "authenticated"
  | "unauthenticated";

import type { SessionUser } from "@/types/session";

export type { SessionUser };

interface SessionContextValue {
  state: SessionState;
  user: SessionUser | null;
  isAuthenticated: boolean;

  logout: () => Promise<void>;

  storeSession: (
    accessToken: string,
    refreshToken: string,
    user: SessionUser
  ) => void;

  refreshSession: () => Promise<void>;
}

const SessionContext =
  createContext<SessionContextValue | null>(null);

function isBrowser() {
  return typeof window !== "undefined";
}

export function SessionProvider({
  children,
}: {
  children: ReactNode;
}) {
  const [state, setState] =
    useState<SessionState>("loading");

  const [user, setUser] =
    useState<SessionUser | null>(null);

  const syncRef =
    useRef<SessionSync | null>(null);

  const sseRef =
    useRef<SseConnection | null>(null);

  const heartbeatRef =
    useRef<HeartbeatHandle | null>(null);

  const mountedRef = useRef(true);

  /**
   * wasAuthenticatedRef tracks whether the user was previously authenticated
   * on this tab, so we only broadcast "logout" when we had an active session
   * (e.g. not during the initial bootstrap when no session exists).
   */
  const wasAuthenticatedRef = useRef(false);

  /**
   * -------------------------
   * Helpers
   * -------------------------
   */

  const setAuthenticated = useCallback(
    (sessionUser: SessionUser) => {
      wasAuthenticatedRef.current = true;
      setUser(sessionUser);
      setState("authenticated");
    },
    []
  );

  /**
   * Broadcast helpers — send messages to other tabs via SharedWorker/BroadcastChannel
   */

  function broadcastLogout() {
    // Only broadcast if this tab was previously authenticated — otherwise
    // the initial bootstrap failure (no session on first load) would
    // incorrectly tell other tabs to clear their valid sessions.
    if (!wasAuthenticatedRef.current) return;
    syncRef.current?.postMessage({ type: "logout" });
  }

  function broadcastAuth(sessionUser: SessionUser) {
    syncRef.current?.postMessage({
      type: "authenticated",
      user: sessionUser,
    });
  }

  const clearSession = useCallback(() => {
    clearTokens();
    setUser(null);
    setState("unauthenticated");
    broadcastLogout();
  }, []);

  /**
   * -------------------------
   * Cross-tab sync (SharedWorker / BroadcastChannel)
   * -------------------------
   */

  useEffect(() => {
    const sync = createSessionSync();
    if (!sync) return;

    syncRef.current = sync;

    sync.setHandler((msg: SessionSyncMessage) => {
      switch (msg.type) {
        case "logout":
          clearSession();
          break;

        case "authenticated":
          setAuthenticated(msg.user);
          break;

        default:
          break;
      }
    });

    return () => {
      sync.close();
      syncRef.current = null;
    };
  }, [clearSession, setAuthenticated]);

  /**
   * -------------------------
   * SSE connection for cross-device session invalidation
   * -------------------------
   */

  useEffect(() => {
    // Only connect SSE when the user is authenticated
    if (state !== "authenticated") return;

    const sse = connectSessionSse((event) => {
      logger.info("session-revoked-via-sse", {
        reason: event.reason,
      });
      clearSession();
    });

    sseRef.current = sse;

    return () => {
      sse.close();
      sseRef.current = null;
    };
  }, [state, clearSession]);

  /**
   * -------------------------
   * Heartbeat polling for session freshness
   * -------------------------
   */

  useEffect(() => {
    // Only poll when the user is authenticated
    if (state !== "authenticated") return;

    const heartbeat = startHeartbeat((_status) => {
      // The 401 interceptor in api.ts already calls clearTokens(),
      // which dispatches auth:logout — so this is a safety net.
      // If the heartbeat detects a revoked session the interceptor
      // handles cleanup automatically.
    });

    heartbeatRef.current = heartbeat;

    return () => {
      heartbeat.stop();
      heartbeatRef.current = null;
    };
  }, [state]);

  /**
   * -------------------------
   * Refresh Session
   * -------------------------
   */

  const refreshSession = useCallback(async () => {
    try {
      const response = await api.get<SessionUser>(
        ENDPOINTS.auth.me
      );

      if (!mountedRef.current) return;

      setAuthenticated(response.data);
      broadcastAuth(response.data);
    } catch (error) {
      logger.error("session-refresh-failed", {
        message: error instanceof Error ? error.message : String(error),
      });

      if (!mountedRef.current) return;

      clearSession();
    }
  }, [clearSession, setAuthenticated]);

  /**
   * -------------------------
   * Bootstrap Session
   * -------------------------
   */

  useEffect(() => {
    mountedRef.current = true;

    if (!isBrowser()) return;

    const controller = new AbortController();

    async function bootstrapSession() {
      try {
        // With httpOnly cookies, we cannot read tokens directly.
        // Always try /auth/me — the cookie will be sent automatically.
        // If no valid session exists, the request will 401 and we'll
        // set state to unauthenticated.

        const response = await api.get<SessionUser>(
          ENDPOINTS.auth.me,
          {
            signal: controller.signal,
          } as Parameters<typeof api.get>[1]
        );

        if (!mountedRef.current) return;

        setAuthenticated(response.data);
      } catch (error: unknown) {
        if (
          (error as { name?: string }).name ===
          "CanceledError"
        ) {
          return;
        }

        logger.error("session-bootstrap-failed", {
          message: error instanceof Error ? error.message : String(error),
        });

        if (!mountedRef.current) return;

        clearSession();
      }
    }

    bootstrapSession();

    function handleLogoutEvent() {
      clearSession();
    }

    window.addEventListener(
      "auth:logout",
      handleLogoutEvent
    );

    return () => {
      mountedRef.current = false;

      controller.abort();

      window.removeEventListener(
        "auth:logout",
        handleLogoutEvent
      );
    };
  }, [clearSession, setAuthenticated]);

  /**
   * -------------------------
   * Store Session
   * -------------------------
   */

  const storeSession = useCallback(
    (
      accessToken: string,
      refreshToken: string,
      sessionUser: SessionUser
    ) => {
      setTokens(accessToken, refreshToken);

      setAuthenticated(sessionUser);
      broadcastAuth(sessionUser);
    },
    [setAuthenticated]
  );

  /**
   * -------------------------
   * Logout
   * -------------------------
   */

  const logout = useCallback(async () => {
    if (!checkRateLimit("auth:logout", LOGOUT_RATE_LIMIT.maxAttempts, LOGOUT_RATE_LIMIT.windowMs)) {
      console.warn("Logout rate limit exceeded. Please wait before trying again.");
      return;
    }

    try {
      await api.post(ENDPOINTS.auth.logout);
    } catch (error) {
      logger.error("logout-request-failed", {
        message: error instanceof Error ? error.message : String(error),
      });
    } finally {
      clearSession();
      broadcastLogout();
    }
  }, [clearSession]);

  /**
   * -------------------------
   * Memoized Context
   * -------------------------
   */

  const value = useMemo<SessionContextValue>(
    () => ({
      state,
      user,

      isAuthenticated:
        state === "authenticated",

      logout,

      storeSession,

      refreshSession,
    }),
    [
      state,
      user,
      logout,
      storeSession,
      refreshSession,
    ]
  );

  return (
    <SessionContext.Provider value={value}>
      {children}
    </SessionContext.Provider>
  );
}

/**
 * -------------------------
 * Hook
 * -------------------------
 */

export function useSession(): SessionContextValue {
  const context = useContext(SessionContext);

  if (!context) {
    throw new Error(
      "useSession must be used within SessionProvider"
    );
  }

  return context;
}