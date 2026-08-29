import { describe, expect, it } from "vitest";
import { normalizeApiError } from "@/lib/errors";

const error = (status?: number, data?: unknown, message?: string, headers?: Record<string, string | null>) => ({
  response: { status, data, headers },
  message,
});

describe("normalizeApiError", () => {
  it.each([
    [401, "auth"],
    [403, "auth"],
    [422, "validation"],
    [409, "conflict"],
    [429, "rate_limited"],
    [404, "not_found"],
    [500, "unknown"],
  ] as const)("maps status %s to %s", (status, kind) => {
    expect(normalizeApiError(error(status)).kind).toBe(kind);
  });

  it("joins FastAPI detail arrays", () => {
    expect(normalizeApiError(error(422, { detail: [{ msg: "first" }, { msg: "second" }] })).message)
      .toBe("first; second");
  });

  it("handles a single detail item", () => {
    expect(normalizeApiError(error(422, { detail: [{ msg: "invalid" }] })).message).toBe("invalid");
  });

  it("uses detail string before message, error message, and fallback", () => {
    expect(normalizeApiError(error(400, { detail: "detail", message: "body" }, "error")).message).toBe("detail");
    expect(normalizeApiError(error(400, { message: "body" }, "error")).message).toBe("body");
    expect(normalizeApiError(error(400, undefined, "error")).message).toBe("error");
    expect(normalizeApiError(error(400)).message).toBe("Unexpected API error");
  });

  it("prefers the header correlation id, then body ids", () => {
    expect(normalizeApiError(error(500, { correlationId: "body", requestId: "request" }, undefined, {
      "x-correlation-id": "header",
    })).correlationId).toBe("header");
    expect(normalizeApiError(error(500, { requestId: "request" })).correlationId).toBe("request");
  });
});
