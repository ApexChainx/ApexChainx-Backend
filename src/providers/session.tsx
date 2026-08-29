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
  getAccessToken,
  setTokens,
} from "@/lib/api";
import { ENDPOINTS } from "@/lib/endpoints";
import { checkRateLimit } from "@/lib/rate-limit";

const LOGOUT_RATE_LIMIT = { maxAttempts: 5, windowMs: 60_000 };
import { logger } from "@/lib/logger";

/**
 * localStorage flag recording that this browser has successfully
 * authenticated at least once. Unlike `wasAuthenticatedRef` it survives hard
 * refreshes, so a first-visit bootstrap failure is never mistaken for a real
 * logout (and never triggers a premature logout broadcast to other tabs).
 */
const SESSION_FLAG_KEY = "noc_session_seen";

/** Bootstrap retry policy for transient (non-auth) failures. */
const MAX_BOOTSTRAP_ATTEMPTS = 3;
const BOOTSTRAP_RETRY_DELAYS_MS = [1_000, 2_000, 4_000];

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

function hasSessionFlag(): boolean {
  if (!isBrowser()) return false;
  try {
    return window.localStorage.getItem(SESSION_FLAG_KEY) === "1";
  } catch {
    return false;
  }
}

function setSessionFlag(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.setItem(SESSION_FLAG_KEY, "1");
  } catch {
    // Storage may be unavailable (e.g. private browsing) — non-fatal.
  }
}

function clearSessionFlag(): void {
  if (!isBrowser()) return;
  try {
    window.localStorage.removeItem(SESSION_FLAG_KEY);
  } catch {
    // Storage may be unavailable (e.g. private browsing) — non-fatal.
  }
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
      setSessionFlag();
      setUser(sessionUser);
      setState("authenticated");
    },
    []
  );

  /**
   * Broadcast helpers — send messages to other tabs via SharedWorker/BroadcastChannel
   */

  function broadcastLogout() {
    // Only broadcast if this tab — or this browser, across hard refreshes —
    // was previously authenticated. Otherwise the initial bootstrap failure
    // on a first visit would incorrectly tell other tabs to clear their
    // still-valid sessions.
    if (!wasAuthenticatedRef.current && !hasSessionFlag()) return;
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
    clearSessionFlag();
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
      const status = (error as { response?: { status?: number } })
        ?.response?.status;

      // Only a definitive 401/403 means the session was revoked. Network
      // errors, timeouts and 5xx responses must not destroy a session that
      // may still be valid on the server.
      if (status !== 401 && status !== 403) {
        logger.warn("session-refresh-unreachable", {
          message: error instanceof Error ? error.message : String(error),
        });
        return;
      }

      logger.error("session-refresh-failed", {
        status,
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

    function isCanceled(error: unknown): boolean {
      return (error as { name?: string })?.name === "CanceledError";
    }

    function getStatus(error: unknown): number | undefined {
      return (error as { response?: { status?: number } })?.response?.status;
    }

    /**
     * Fallback validation via /auth/me. Distinguishes a definitive
     * "no session" (401/403 response) from a transient failure (network
     * error, timeout, 5xx) — only the former clears the session. Transient
     * failures are retried with backoff and never destroy cookies.
     */
    async function tryMe(attempt: number): Promise<void> {
      try {
        // With httpOnly cookies the cookie is sent automatically
        // (withCredentials). If a token is still readable, the interceptor
        // attaches it as a Bearer header.
        const response = await api.get<SessionUser>(
          ENDPOINTS.auth.me,
          {
            signal: controller.signal,
          } as Parameters<typeof api.get>[1]
        );

        if (!mountedRef.current) return;

        setAuthenticated(response.data);
      } catch (error: unknown) {
        if (isCanceled(error)) return;

        const status = getStatus(error);

        // Definitive: the server says there is no valid session.
        if (status === 401 || status === 403) {
          logger.info("session-bootstrap-no-session", {
            source: "me",
            status,
          });

          if (!mountedRef.current) return;

          clearSession();
          return;
        }

        // Transient: the server could not be reached or errored. The
        // session may still be valid, so retry and never clear tokens.
        logger.warn("session-bootstrap-unreachable", {
          source: "me",
          attempt: attempt + 1,
          message: error instanceof Error ? error.message : String(error),
        });

        if (attempt < MAX_BOOTSTRAP_ATTEMPTS - 1) {
          await new Promise((resolve) =>
            setTimeout(resolve, BOOTSTRAP_RETRY_DELAYS_MS[attempt])
          );
          if (!mountedRef.current) return;
          return tryMe(attempt + 1);
        }

        if (!mountedRef.current) return;

        // Give up on the UI state, but preserve cookies so a later refresh
        // or page load can recover the session without a forced logout.
        setUser(null);
        setState("unauthenticated");
      }
    }

    async function bootstrapSession() {
      const hasReadableToken = !!getAccessToken();
      const sessionSeen = hasSessionFlag();

      // A missing local flag is not proof that no cookie session exists:
      // privacy tools and clear-site-data can remove it while httpOnly
      // cookies remain valid. Probe the cookie-only endpoint on this path.
      void hasReadableToken;
      void sessionSeen;

      // 1) Prefer the dedicated cookie-session endpoint. It validates the
      //    httpOnly session cookies directly — without an Authorization
      //    header — so a hard refresh (where in-memory tokens are gone and a
      //    stale cookie token could mask a valid session) still restores the
      //    session. When the endpoint is not deployed yet (404/405) or is
      //    unreachable, fall back to /auth/me below.
      try {
        const sessionResponse = await api.get<SessionUser>(
          ENDPOINTS.auth.session,
          {
            signal: controller.signal,
            skipAuth: true,
          } as Parameters<typeof api.get>[1]
        );

        if (!mountedRef.current) return;

        setAuthenticated(sessionResponse.data);
        return;
      } catch (error: unknown) {
        if (isCanceled(error)) return;

        const status = getStatus(error);

        if (status === 401 || status === 403) {
          logger.info("session-bootstrap-no-session", {
            source: "session",
            status,
          });

          if (!mountedRef.current) return;

          clearSession();
          return;
        }

        logger.debug("session-bootstrap-fallback", {
          source: "session",
          status,
        });
      }

      // 2) Fallback: validate via /auth/me with transient-error retries.
      await tryMe(0);
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