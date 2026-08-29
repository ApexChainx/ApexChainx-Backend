/** ApexChain Network Operations Intelligence Platform */
import { describe, it, expect } from "vitest";
import { createHmac } from "node:crypto";
import {
  generateWebhookSecret,
  verifyWebhookSignature,
  maskSecret,
} from "@/lib/webhook-secret";

describe("webhook-secret", () => {
  it("generates a 64-character hex secret", () => {
    const secret = generateWebhookSecret();
    expect(secret).toHaveLength(64);
    expect(/^[0-9a-f]+$/.test(secret)).toBe(true);
  });

  it("generates unique secrets", () => {
    const secret1 = generateWebhookSecret();
    const secret2 = generateWebhookSecret();
    expect(secret1).not.toBe(secret2);
  });

  it("masks long secrets correctly", () => {
    const secret = "abcdefghijklmnop";
    const masked = maskSecret(secret);
    expect(masked).toBe("abcdefgh••••mnop");
  });

  it("masks short secrets completely", () => {
    const secret = "short";
    const masked = maskSecret(secret);
    expect(masked).toBe("•••••");
  });

  describe("verifyWebhookSignature", () => {
    const payload = '{"event":"outage.created","id":"abc123"}';
    const secret = generateWebhookSecret();

    const hmacHex = (body: string, key: string) =>
      createHmac("sha256", key).update(body).digest("hex");

    it("accepts a valid HMAC-SHA256 signature", async () => {
      const signature = hmacHex(payload, secret);
      await expect(
        verifyWebhookSignature(payload, signature, secret)
      ).resolves.toBe(true);
    });

    it("rejects a tampered payload", async () => {
      const signature = hmacHex(payload, secret);
      await expect(
        verifyWebhookSignature('{"event":"outage.created","id":"hacked"}', signature, secret)
      ).resolves.toBe(false);
    });

    it("rejects a signature made with the wrong secret", async () => {
      const signature = hmacHex(payload, "a-different-secret");
      await expect(
        verifyWebhookSignature(payload, signature, secret)
      ).resolves.toBe(false);
    });

    it("rejects signatures of a different length", async () => {
      await expect(
        verifyWebhookSignature(payload, "tooshort", secret)
      ).resolves.toBe(false);
    });

    it("rejects with an explicit insecure-context error when crypto.subtle is unavailable", async () => {
      const original = globalThis.crypto;

      // Simulate a non-secure context: crypto without `subtle`.
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        value: original
          ? { getRandomValues: original.getRandomValues.bind(original) }
          : {},
      });

      try {
        await expect(
          verifyWebhookSignature(payload, "deadbeef", secret)
        ).rejects.toThrow(/secure context/i);
      } finally {
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: original,
        });
      }
    });

    it("the insecure-context failure is a plain Error, not a raw TypeError", async () => {
      const original = globalThis.crypto;
      Object.defineProperty(globalThis, "crypto", {
        configurable: true,
        value: {},
      });

      try {
        const result = await verifyWebhookSignature(payload, "deadbeef", secret).then(
          () => "resolved",
          (error: unknown) => error
        );
        expect(result).toBeInstanceOf(Error);
        expect((result as Error).name).not.toBe("TypeError");
        expect((result as Error).message).toMatch(/crypto\.subtle/i);
      } finally {
        Object.defineProperty(globalThis, "crypto", {
          configurable: true,
          value: original,
        });
      }
    });
  });
});
