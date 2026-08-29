/** ApexChain - Network Operations Intelligence Platform */

/**
 * Encode bytes as lowercase hexadecimal.
 *
 * Shared by CSRF token generation and the webhook secret helpers so the
 * encoding lives in exactly one place (issue #311).
 */
export function bytesToHex(bytes: Uint8Array): string {
  return Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
}
