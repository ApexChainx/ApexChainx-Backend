const REDIRECT_KEY = "redirectTo";

/**
 * Fallback for unsafe/absent redirect targets.
 * Must be a real route: the dashboard lives at `/` (see src/app/page.tsx
 * and the dashboard link in src/components/Navigation.tsx). Tests pin
 * this value to the app's route table so route renames cannot silently
 * break redirect defaults again (see tests/redirect.test.ts).
 */
const SAFE_DEFAULT = "/";

export function getSafeDefault() {
  return SAFE_DEFAULT;
}

export function isSafeRedirect(path: string) {
  try {
    // Prevent external redirects
    if (!path.startsWith("/")) return false;

    // Prevent protocol-relative external redirects (e.g. "//evil.example.com"):
    // browsers resolve these against another origin.
    if (path.startsWith("//")) return false;

    // Prevent auth loops
    if (path.startsWith("/login") || path.startsWith("/register")) {
      return false;
    }

    return true;
  } catch {
    return false;
  }
}

export function sanitizeRedirect(path?: string | null) {
  if (!path) return getSafeDefault();
  return isSafeRedirect(path) ? path : getSafeDefault();
}

export { REDIRECT_KEY };