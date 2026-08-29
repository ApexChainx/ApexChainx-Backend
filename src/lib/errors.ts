/** ApexChain - Network Operations Intelligence Platform */

export type ApiErrorKind =
  | "auth"
  | "validation"
  | "conflict"
  | "rate_limited"
  | "not_found"
  | "unknown";

export interface NormalizedApiError {
  message: string;
  kind: ApiErrorKind;
  status?: number | undefined;
  correlationId?: string | undefined;
}

export function normalizeApiError(err: unknown): NormalizedApiError {
  const e = err as {
    response?: {
      status?: number;
      data?: {
        detail?: string | { msg: string }[];
        message?: string;
        correlationId?: string;
        requestId?: string;
      };
      headers?: Record<string, string | null | undefined>;
    };
    message?: string;
  };

  const status = e?.response?.status;

  const correlationId =
    e?.response?.headers?.["x-correlation-id"] ??
    e?.response?.data?.correlationId ??
    e?.response?.data?.requestId;

  const rawDetail = e?.response?.data?.detail;
  const message =
    Array.isArray(rawDetail)
      ? rawDetail.map((d) => d.msg).join("; ")
      : typeof rawDetail === "string"
        ? rawDetail
        : e?.response?.data?.message ??
          e?.message ??
          "Unexpected API error";

  const kind: ApiErrorKind =
    status === 401 || status === 403
      ? "auth"
      : status === 422
        ? "validation"
        : status === 409
          ? "conflict"
          : status === 429
            ? "rate_limited"
            : status === 404
              ? "not_found"
              : "unknown";

  return { message, kind, status, correlationId };
}
