/** ApexChain - Network Operations Intelligence Platform */

import { bytesToHex } from "@/lib/encoding";

/**
 * Generate a cryptographically secure webhook secret.
 */
export function generateWebhookSecret(): string {
  const array = new Uint8Array(32);
  crypto.getRandomValues(array);
  return bytesToHex(array);
}

/**
 * Verify a webhook signature using HMAC-SHA256.
 * Compares using constant-time comparison to prevent timing attacks.
 *
 * Requires WebCrypto (`crypto.subtle`), which is only available in secure
 * contexts (HTTPS or localhost). On insecure origins this rejects with an
 * explicit error instead of throwing a raw TypeError — callers should treat
 * rejection as "signature could not be verified" (fail closed).
 */
export async function verifyWebhookSignature(
  payload: string,
  signature: string,
  secret: string
): Promise<boolean> {
  const subtle: SubtleCrypto | undefined = globalThis.crypto?.subtle;
  if (!subtle) {
    // Fail loud with a documented error instead of the raw TypeError from
    // dereferencing crypto.subtle on a non-secure origin (issue #311).
    throw new Error(
      "verifyWebhookSignature requires WebCrypto (crypto.subtle), which is only available in secure contexts (HTTPS or localhost). The signature cannot be verified on an insecure origin."
    );
  }

  const encoder = new TextEncoder();
  const key = await subtle.importKey(
    "raw",
    encoder.encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"]
  );

  const signatureBuffer = await subtle.sign(
    "HMAC",
    key,
    encoder.encode(payload)
  );

  const expectedSignature = bytesToHex(new Uint8Array(signatureBuffer));

  // Constant-time comparison
  if (expectedSignature.length !== signature.length) {
    return false;
  }

  let result = 0;
  for (let i = 0; i < expectedSignature.length; i++) {
    result |= expectedSignature.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return result === 0;
}

/**
 * Mask a webhook secret for display (show first 8 and last 4 chars).
 */
export function maskSecret(secret: string): string {
  if (secret.length <= 12) return "•".repeat(secret.length);
  return `${secret.slice(0, 8)}${"•".repeat(secret.length - 12)}${secret.slice(-4)}`;
}
