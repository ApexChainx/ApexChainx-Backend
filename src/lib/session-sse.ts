/**
 * ApexChain — Session SSE Client
 *
 * Connects to the backend SSE endpoint and listens for server-initiated
 * session events (e.g. admin-forced logout, session revocation).
 *
 * Backend contract:
 *   GET /auth/events  (SSE endpoint)
 *   Events:
 *     event: session_revoked
 *     data: { "reason": "admin_logout" | "password_changed" | "session_expired" }
 *
 *     event: heartbeat        (keep-alive every 15s)
 *     data: { "timestamp": 1234567890 }
 *
 * Authentication is handled via credentials (cookies) — the SSE connection
 * must include them so the backend can associate the stream with the user's
 * session.
 */

import { env } from "@/lib/config/env";
import { ENDPOINTS } from "@/lib/endpoints";

export type SessionRevokeReason =
  | "admin_logout"
  | "password_changed"
  | "session_expired"
  | "unknown";

export interface SessionSseEvent {
  type: "session_revoked";
  reason: SessionRevokeReason;
}

export function parseSessionSseFrame(frame: string): SessionSseEvent | null {
  const fields = new Map<string, string[]>();
  for (const line of frame.replaceAll("\\r\\n", "\\n").split("\\n")) {
    if (!line || line.startsWith(":")) continue;
    const separator = line.indexOf(":");
    const field = separator === -1 ? line : line.slice(0, separator);
    let value = separator === -1 ? "" : line.slice(separator + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    fields.set(field, [...(fields.get(field) ?? []), value]);
  }

  const eventType = fields.get("event")?.at(-1);
  const dataStr = fields.get("data")?.join("\\n");
  if (eventType !== "session_revoked" || !dataStr) return null;

  try {
    const data = JSON.parse(dataStr) as { reason?: string };
    const reason: SessionRevokeReason =
      data.reason === "admin_logout" ||
      data.reason === "password_changed" ||
      data.reason === "session_expired"
        ? data.reason
        : "unknown";
    return { type: "session_revoked", reason };
  } catch {
    return null;
  }
}export type SseEventHandler = (event: SessionSseEvent) => void;

const RECONNECT_BASE_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;

/**
 * SSE connection handle.
 * Call `close()` to tear down the connection and stop reconnection.
 */
export interface SseConnection {
  close(): void;
}

/**
 * Opens an SSE connection to the backend's session events endpoint.
 * Automatically reconnects with exponential backoff on connection loss.
 * The connection is credential-included so the auth cookie is sent.
 *
 * @param onEvent  Called when a session event is received (e.g. session_revoked).
 * @returns        A handle to close the connection.
 */
export function connectSessionSse(
  onEvent: SseEventHandler,
): SseConnection {
  let controller: AbortController | null = null;
  let retryTimeout: ReturnType<typeof setTimeout> | null = null;
  let retryCount = 0;
  let closed = false;

  function getReconnectDelay(): number {
    const delay = Math.min(
      RECONNECT_BASE_MS * 2 ** retryCount,
      RECONNECT_MAX_MS,
    );
    // Add jitter (±25%)
    return delay * (0.75 + Math.random() * 0.5);
  }

  async function connect() {
    if (closed) return;
    controller = new AbortController();

    try {
      const baseUrl = env.API_BASE_URL.replace(/\/+$/, "");
      const url = `${baseUrl}${ENDPOINTS.sessionEvents}`;

      const response = await fetch(url, {
        credentials: "include",
        signal: controller.signal,
        headers: {
          Accept: "text/event-stream",
          "Cache-Control": "no-cache",
        },
      });

      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          closed = true;
          onEvent({ type: "session_revoked", reason: "session_expired" });
          return;
        }
        console.warn(
          `[session-sse] /auth/events returned ${response.status} — retrying`,
        );
        return;
      }

      // Reset retry count on successful connection
      retryCount = 0;

      const reader = response.body?.getReader();
      if (!reader) {
        console.warn("[session-sse] Response body has no readable stream");
        return; // triggers reconnect with backoff
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (!closed) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });

        // Parse SSE frames
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? ""; // Keep incomplete frame in buffer

        for (const frame of frames) {
          const event = parseSessionSseFrame(frame);
          if (event) onEvent(event);
        }
      }
    } catch (err) {
      // If aborted/closed, don't reconnect
      if (closed) return;
    }

    // Reconnect with backoff if not closed
    if (!closed) {
      retryCount++;
      const delay = getReconnectDelay();
      retryTimeout = setTimeout(connect, delay);
    }
  }

  // Start connection (non-blocking)
  void connect();

  return {
    close() {
      closed = true;
      controller?.abort();
      if (retryTimeout) clearTimeout(retryTimeout);
      controller = null;
      retryTimeout = null;
    },
  };
}
