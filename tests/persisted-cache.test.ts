/** ApexChain Network Operations Intelligence Platform */
/**
 * Issue #417 — Unit tests for persisted-cache TTL and eviction semantics.
 *
 * `persistedCache` (src/lib/persisted-cache.ts) had no dedicated unit tests.
 * These tests use `fake-indexeddb` to exercise the real IndexedDB code paths
 * (rather than mocking the module's internals), and simulate an
 * IndexedDB-unavailable environment by leaving `globalThis.indexedDB`
 * undefined, which is what jsdom does by default without fake-indexeddb.
 */
import { IDBFactory } from "fake-indexeddb";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { persistedCache } from "@/lib/persisted-cache";

/** Give the background (fire-and-forget) IndexedDB transaction time to settle. */
async function flushMicrotasks() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("persistedCache", () => {
  beforeEach(() => {
    // Fresh, empty IndexedDB instance per test so state never leaks across
    // tests (and so the DB_VERSION upgrade path runs every time).
    (globalThis as unknown as { indexedDB: IDBFactory }).indexedDB =
      new IDBFactory();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("round-trips a value through set and get", async () => {
    await persistedCache.set("outages", { items: [1, 2, 3] });
    const result = await persistedCache.get<{ items: number[] }>("outages");
    expect(result).toEqual({ items: [1, 2, 3] });
  });

  it("returns null for a key that was never set", async () => {
    const result = await persistedCache.get("does-not-exist");
    expect(result).toBeNull();
  });

  it("does not expire an entry exactly at the expiresAt boundary", async () => {
    const t0 = 1_000_000;
    vi.spyOn(Date, "now").mockReturnValue(t0);
    await persistedCache.set("boundary", "value", 1000);

    // Date.now() === expiresAt: the implementation only expires when
    // Date.now() is strictly greater than expiresAt.
    vi.spyOn(Date, "now").mockReturnValue(t0 + 1000);
    const result = await persistedCache.get("boundary");
    expect(result).toBe("value");
  });

  it("expires and deletes an entry once Date.now() passes expiresAt", async () => {
    const t0 = 1_000_000;
    vi.spyOn(Date, "now").mockReturnValue(t0);
    await persistedCache.set("expiring", "value", 1000);

    const delSpy = vi.spyOn(persistedCache, "del");

    // One millisecond past expiry.
    vi.spyOn(Date, "now").mockReturnValue(t0 + 1001);
    const expiredResult = await persistedCache.get("expiring");
    expect(expiredResult).toBeNull();

    // The expired-entry deletion path fires in the background — confirm it
    // was actually invoked for this key.
    expect(delSpy).toHaveBeenCalledWith("expiring");
    await flushMicrotasks();

    // Prove the row was really deleted (not just skipped due to the TTL
    // check) by rewinding the clock to before expiry and reading again: if
    // deletion had not actually happened, this would return "value".
    vi.spyOn(Date, "now").mockReturnValue(t0);
    const afterDeletion = await persistedCache.get("expiring");
    expect(afterDeletion).toBeNull();
  });

  it("treats ttlMs = 0 as no expiration", async () => {
    const t0 = 1_000_000;
    vi.spyOn(Date, "now").mockReturnValue(t0);
    await persistedCache.set("forever", "value", 0);

    // Even far in the future, a ttl of 0 must never expire.
    vi.spyOn(Date, "now").mockReturnValue(t0 + 1000 * 60 * 60 * 24 * 365);
    const result = await persistedCache.get("forever");
    expect(result).toBe("value");
  });

  it("overwrites an existing entry with the same key", async () => {
    await persistedCache.set("outages", "first");
    await persistedCache.set("outages", "second");

    const result = await persistedCache.get("outages");
    expect(result).toBe("second");
  });

  it("del removes a single key without affecting others", async () => {
    await persistedCache.set("a", "value-a");
    await persistedCache.set("b", "value-b");

    await persistedCache.del("a");

    await expect(persistedCache.get("a")).resolves.toBeNull();
    await expect(persistedCache.get("b")).resolves.toBe("value-b");
  });

  it("clear wipes every entry from the store", async () => {
    await persistedCache.set("a", "value-a");
    await persistedCache.set("b", "value-b");

    await persistedCache.clear();

    await expect(persistedCache.get("a")).resolves.toBeNull();
    await expect(persistedCache.get("b")).resolves.toBeNull();
  });

  describe("when IndexedDB is unavailable", () => {
    beforeEach(() => {
      // jsdom does not implement IndexedDB by default; simulate that
      // environment explicitly regardless of the fake-indexeddb instance
      // installed in the outer beforeEach.
      (globalThis as unknown as { indexedDB: undefined }).indexedDB =
        undefined;
    });

    it("get warns and resolves to null instead of throwing", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

      await expect(persistedCache.get("anything")).resolves.toBeNull();

      expect(warnSpy).toHaveBeenCalledWith(
        "[persisted-cache] get failed:",
        expect.any(Error),
      );
    });

    it("set warns and resolves without throwing", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

      await expect(
        persistedCache.set("anything", "value"),
      ).resolves.toBeUndefined();

      expect(warnSpy).toHaveBeenCalledWith(
        "[persisted-cache] set failed:",
        expect.any(Error),
      );
    });

    it("del warns and resolves without throwing", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

      await expect(persistedCache.del("anything")).resolves.toBeUndefined();

      expect(warnSpy).toHaveBeenCalledWith(
        "[persisted-cache] del failed:",
        expect.any(Error),
      );
    });

    it("clear warns and resolves without throwing", async () => {
      const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

      await expect(persistedCache.clear()).resolves.toBeUndefined();

      expect(warnSpy).toHaveBeenCalledWith(
        "[persisted-cache] clear failed:",
        expect.any(Error),
      );
    });
  });
});
