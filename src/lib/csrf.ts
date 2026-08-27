/** ApexChain - Network Operations Intelligence Platform */

import { bytesToHex } from "@/lib/encoding";

/**
 * Read a cookie value by name.
 * Only works for non-HttpOnly cookies (like the CSRF token).
 */
export function getCookie(name: string): string | null {
  if (typeof document === "undefined") return null;

  const match = document.cookie
    .split("; ")
    .find((c) => c.startsWith(`${name}=`));

  return match ? decodeURIComponent(match.split("=").slice(1).join("=")) : null;
}

/**
 * Generate a cryptographically random CSRF token.
 * Used when no server-provided token exists.
 */
export function generateCsrfToken(): string {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return bytesToHex(array);
}
