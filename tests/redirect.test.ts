/** ApexChain Network Operations Intelligence Platform */
import { describe, it, expect } from "vitest";
import {
  getSafeDefault,
  isSafeRedirect,
  sanitizeRedirect,
} from "@/lib/auth/redirect";

/**
 * Known-good routes, mirroring the Next.js app router tree in `src/app/`
 * plus the nested link used by the Navigation component.
 * If a route is renamed or removed, update it here — this table is what
 * keeps the redirect fallback honest (issue #312).
 */
const KNOWN_ROUTES = [
  "/", // dashboard (src/app/page.tsx)
  "/login",
  "/register",
  "/outages",
  "/payments",
  "/payments/retry-queue", // linked from Navigation
  "/webhooks",
  "/config",
  "/setting",
  "/bulk-import",
];

describe("auth/redirect — safe default", () => {
  it("falls back to `/` for absent redirects", () => {
    expect(sanitizeRedirect(undefined)).toBe("/");
    expect(sanitizeRedirect(null)).toBe("/");
    expect(sanitizeRedirect()).toBe(getSafeDefault());
  });

  it("falls back to `/` for unsafe redirects", () => {
    expect(sanitizeRedirect("https://evil.example.com")).toBe("/");
    expect(sanitizeRedirect("//evil.example.com")).toBe("/");
    expect(sanitizeRedirect("javascript:alert(1)")).toBe("/");
  });

  it("keeps safe redirects untouched", () => {
    expect(sanitizeRedirect("/outages")).toBe("/outages");
    expect(sanitizeRedirect("/payments/retry-queue")).toBe(
      "/payments/retry-queue"
    );
  });

  it("never sends users to /login or /register (auth loops)", () => {
    expect(isSafeRedirect("/login")).toBe(false);
    expect(isSafeRedirect("/register")).toBe(false);
    expect(sanitizeRedirect("/login?next=/outages")).toBe("/");
  });
});

describe("auth/redirect — fallback is a real route (issue #312)", () => {
  it("default matches a known-good route in the app", () => {
    expect(KNOWN_ROUTES).toContain(getSafeDefault());
  });

  it("default agrees with Navigation: the dashboard is rendered at `/`", () => {
    // src/components/Navigation.tsx links the dashboard via href="/"
    expect(getSafeDefault()).toBe("/");
    expect(getSafeDefault()).not.toBe("/dashboard");
  });
});
