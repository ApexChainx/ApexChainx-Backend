"use client";
/** ApexChain Network Operations Intelligence Platform */

import { useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import { api } from "@/lib/api";
import { ENDPOINTS } from "@/lib/endpoints";
import { useSession } from "@/hooks/useSession";
import {
  completeTwoFactorLogin,
  type AuthSessionResponse,
} from "@/services/twoFactorService";
import type { SessionUser } from "@/types/session";

/** Sign-in step the form is currently showing. */
type LoginStep = "credentials" | "challenge";

/** Max consecutive failed TOTP challenge attempts before the submit is locked. */
const MAX_CHALLENGE_ATTEMPTS = 5;

/**
 * Determine whether a login outcome means a second factor is required.
 *
 * The backend signals this either via a success payload carrying the
 * `two_factor_required` marker (no tokens issued yet) or via a rejected
 * request whose detail/code names `two_factor_required` /
 * `TWO_FACTOR_REQUIRED`.
 */
function isTwoFactorRequired(
  data: unknown,
  error?: unknown
): boolean {
  if (data && typeof data === "object") {
    const candidate = data as { two_factor_required?: unknown };
    if (candidate.two_factor_required) {
      return true;
    }
  }

  const err = error as {
    response?: { data?: { detail?: string | { msg: string }[]; code?: string } };
    message?: string;
  };
  const detail = err?.response?.data?.detail;
  const detailText =
    typeof detail === "string"
      ? detail
      : Array.isArray(detail)
        ? detail.map((d) => d.msg).join(" ")
        : err?.response?.data?.code ?? err?.message ?? "";
  const haystack = detailText.toLowerCase();
  return (
    haystack.includes("two_factor_required") ||
    haystack.includes("two-factor required") ||
    haystack.includes("second factor required")
  );
}

/** Full authenticated session with readable tokens + user. */
function hasFullSession(data: unknown): data is AuthSessionResponse {
  const candidate = data as AuthSessionResponse | null | undefined;
  return Boolean(
    candidate &&
      typeof candidate === "object" &&
      candidate.access_token &&
      candidate.refresh_token &&
      candidate.user
  );
}

export default function LoginForm() {
  const router = useRouter();
  const { storeSession } = useSession();

  const [step, setStep] = useState<LoginStep>("credentials");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [totpCode, setTotpCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  // Client-side throttle for the TOTP challenge submission path.
  const [failedAttempts, setFailedAttempts] = useState(0);
  const [throttleMessage, setThrottleMessage] = useState<string | null>(null);

  function backToCredentials() {
    setStep("credentials");
    setTotpCode("");
    setError(null);
    setThrottleMessage(null);
    setFailedAttempts(0);
  }

  async function handleCredentialsSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setLoading(true);
    try {
      const response = await api.post<{
        access_token?: string;
        refresh_token?: string;
        user?: SessionUser;
        two_factor_required?: boolean;
      }>(ENDPOINTS.auth.login, { email, password });

      // Branch 1 — a second factor is required: advance to the challenge step.
      if (isTwoFactorRequired(response.data)) {
        setStep("challenge");
        return;
      }

      // Branch 2 — full session returned: store it and redirect. Only trust
      // the success shape when it actually carries a session; otherwise the
      // bootstrap would land the user in an unauthenticated loop.
      const { access_token, refresh_token, user } = response.data ?? {};
      if (access_token && refresh_token && user) {
        storeSession(access_token, refresh_token, user);
        router.push("/");
        router.refresh();
      } else {
        setError("Unexpected login response. Please try again.");
      }
    } catch (err) {
      // The backend may enforce 2FA as a rejection rather than a 2xx marker.
      if (isTwoFactorRequired(undefined, err)) {
        setStep("challenge");
        return;
      }
      setError(err instanceof Error ? err.message : "Login failed. Please try again.");
    } finally {
      setLoading(false);
    }
  }

  async function handleChallengeSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setThrottleMessage(null);

    if (failedAttempts >= MAX_CHALLENGE_ATTEMPTS) {
      setThrottleMessage(
        "Too many failed attempts. The verification button has been disabled."
      );
      return;
    }

    setLoading(true);
    try {
      const session = await completeTwoFactorLogin(totpCode.trim());
      if (hasFullSession(session)) {
        storeSession(
          session.access_token,
          session.refresh_token,
          session.user
        );
      }
      router.push("/");
      router.refresh();
    } catch (err) {
      const next = failedAttempts + 1;
      setFailedAttempts(next);
      setError(err instanceof Error ? err.message : "Invalid code. Please try again.");

      if (next >= MAX_CHALLENGE_ATTEMPTS) {
        setThrottleMessage(
          "Too many failed attempts. The verification button has been disabled for security."
        );
      }
    } finally {
      setLoading(false);
    }
  }

  if (step === "challenge") {
    const locked = failedAttempts >= MAX_CHALLENGE_ATTEMPTS;

    return (
      <div className="mx-auto max-w-sm space-y-6 p-8 pt-16">
        <div className="space-y-1">
          <h1 className="text-2xl font-bold text-gray-800">Two-factor authentication</h1>
          <p className="text-sm text-gray-500">
            Enter the 6-digit code from your authenticator app to continue.
          </p>
        </div>

        <form onSubmit={handleChallengeSubmit} className="space-y-4">
          <div className="space-y-1">
            <label htmlFor="totp" className="block text-sm font-medium text-gray-700">
              Authentication code
            </label>
            <input
              id="totp"
              type="text"
              required
              inputMode="numeric"
              autoComplete="one-time-code"
              autoFocus
              value={totpCode}
              disabled={locked}
              onChange={(e) => setTotpCode(e.target.value)}
              placeholder="000 000"
              className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 disabled:cursor-not-allowed disabled:opacity-40"
            />
          </div>

          {error && (
            <p className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">{error}</p>
          )}

          {throttleMessage && (
            <p className="rounded-lg bg-amber-50 px-4 py-2 text-sm text-amber-700">
              {throttleMessage}
            </p>
          )}

          <button
            type="submit"
            disabled={loading || locked}
            className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
          >
            {locked
              ? "Verification locked"
              : loading
                ? "Verifying…"
                : "Verify and sign in"}
          </button>

          {!locked && (
            <button
              type="button"
              onClick={backToCredentials}
              className="w-full text-center text-sm text-gray-500 hover:text-gray-700"
            >
              Back to sign in
            </button>
          )}
        </form>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-sm space-y-6 p-8 pt-16">
      <div className="space-y-1">
        <h1 className="text-2xl font-bold text-gray-800">Sign in</h1>
        <p className="text-sm text-gray-500">Enter your credentials to continue.</p>
      </div>

      <form onSubmit={handleCredentialsSubmit} className="space-y-4">
        <div className="space-y-1">
          <label htmlFor="email" className="block text-sm font-medium text-gray-700">
            Email
          </label>
          <input
            id="email"
            type="email"
            required
            autoComplete="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        <div className="space-y-1">
          <label htmlFor="password" className="block text-sm font-medium text-gray-700">
            Password
          </label>
          <input
            id="password"
            type="password"
            required
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="w-full rounded-lg border border-gray-300 px-3 py-2 text-sm focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
        </div>

        {error && (
          <p className="rounded-lg bg-red-50 px-4 py-2 text-sm text-red-600">{error}</p>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full rounded-lg bg-blue-600 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-40"
        >
          {loading ? "Signing in…" : "Sign in"}
        </button>
      </form>

      <p className="text-center text-sm text-gray-500">
        No account?{" "}
        <Link href="/register" className="text-blue-600 hover:underline">
          Register
        </Link>
      </p>
    </div>
  );
}