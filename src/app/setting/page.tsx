"use client";
/** ApexChain Network Operations Intelligence Platform */

import { useEffect, useMemo, useState } from "react";

import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { useToast } from "@/components/ui/toast";
import { useSession } from "@/hooks/useSession";
import { useStellarHealth } from "@/hooks/useStellarHealth";
import { useUsdRates } from "@/hooks/useUsdRates";
import { useI18n } from "@/i18n/i18n";
import { api } from "@/lib/api";
import { env } from "@/lib/config/env";
import { ENDPOINTS } from "@/lib/endpoints";
import { explorerLink } from "@/lib/explorer";
import { useRouter } from "next/navigation";

type AuthUser = {
  id: string;
  email: string;
  full_name?: string | null;
  role: string;
  stellar_wallet?: string | null;
  created_at: string;
};

type AuthSessionResponse = {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
};

type Wallet = {
  user_id: string;
  public_key: string;
  created_at: string;
  last_updated: string;
  funded: boolean;
  active: boolean;
  trustline_ready: boolean;
  message?: string;
};

type WalletStatus = {
  user_id: string;
  public_key: string;
  funded: boolean;
  trustline_ready: boolean;
  usable: boolean;
  active: boolean;
  last_updated: string;
};

type WalletBalance = {
  address: string;
  balances: Record<
    string,
    {
      balance: string;
      asset_type: string;
      asset_code?: string;
      asset_issuer?: string;
    }
  >;
  last_updated: string;
};

function getErrorMessage(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong";
}

export default function SettingsPage() {
  const { state: sessionState, user: sessionUser, logout } = useSession();
  const router = useRouter();
  const toast = useToast();
  const { t, locale, setLocale, locales, localeNames } = useI18n();
  const [sessionActionLoading, setSessionActionLoading] = useState<string | null>(null);
  const [sessionActionFeedback, setSessionActionFeedback] = useState<string | null>(null);
  const [sessionActionError, setSessionActionError] = useState<string | null>(null);
  const [theme, setTheme] = useState<string>("system");

  // Issue #128 — Stellar Horizon reachability + latency
  const stellarHealth = useStellarHealth();
  const isHorizonUnreachable = stellarHealth.status === "unreachable";

  // Initialize theme from localStorage
  useEffect(() => {
    const storedTheme = localStorage.getItem('theme');
    if (storedTheme) {
      setTheme(storedTheme);
    }
  }, []);

  // Update theme when it changes
  useEffect(() => {
    const root = document.documentElement;
    
    function applyTheme(currentTheme: string) {
      if (currentTheme === 'dark') {
        root.classList.add('dark');
      } else if (currentTheme === 'light') {
        root.classList.remove('dark');
      } else {
        // System preference
        const systemPrefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
        if (systemPrefersDark) {
          root.classList.add('dark');
        } else {
          root.classList.remove('dark');
        }
      }
    }

    applyTheme(theme);
    localStorage.setItem('theme', theme);

    // Listen for system preference changes
    const mediaQuery = window.matchMedia('(prefers-color-scheme: dark)');
    mediaQuery.addEventListener('change', () => {
      if (theme === 'system') {
        applyTheme('system');
      }
    });

    return () => mediaQuery.removeEventListener('change', () => {});
  }, [theme]);

  async function handleSignOut() {
    setSessionActionLoading("signout");
    setSessionActionFeedback(null);
    setSessionActionError(null);
    try {
      await logout();
      router.replace("/login");
    } catch {
      setSessionActionError("Sign out failed. Please try again.");
    } finally {
      setSessionActionLoading(null);
    }
  }

  async function handleLogoutAll() {
    setSessionActionLoading("logout-all");
    setSessionActionFeedback(null);
    setSessionActionError(null);
    try {
      await api.post(ENDPOINTS.auth.logoutAll);
      await logout();
      router.replace("/login");
    } catch (err) {
      // logout-all endpoint may not exist yet; fall back to single logout
      if ((err as { response?: { status?: number } }).response?.status === 404) {
        await logout();
        router.replace("/login");
      } else {
        setSessionActionError("Could not revoke all sessions. Please try again.");
        setSessionActionLoading(null);
      }
    }
  }
  const [session, setSession] = useState<AuthSessionResponse | null>(null);
  const [currentUser, setCurrentUser] = useState<AuthUser | null>(null);
  const [wallet, setWallet] = useState<Wallet | null>(null);
  const [walletStatus, setWalletStatus] = useState<WalletStatus | null>(null);
  const [walletBalance, setWalletBalance] = useState<WalletBalance | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loadingAction, setLoadingAction] = useState<string | null>(null);

  const [registerForm, setRegisterForm] = useState({
    email: "operator@example.com",
    password: "secure123",
    full_name: "NOC Operator",
    role: "engineer",
  });
  const [loginForm, setLoginForm] = useState({
    email: "operator@example.com",
    password: "secure123",
  });
  const [walletForm, setWalletForm] = useState({
    user_id: "",
    public_key: "",
    funded: false,
    trustline_ready: false,
  });

  const activeUserId = useMemo(
    () => currentUser?.id ?? walletForm.user_id.trim(),
    [currentUser?.id, walletForm.user_id],
  );
  const walletAssetCount = useMemo(
    () => Object.keys(walletBalance?.balances ?? {}).length,
    [walletBalance],
  );
  const walletReadinessLabel = useMemo(() => {
    if (!walletStatus) {
      return t('settings.notLoaded');
    }
    if (!walletStatus.active) {
      return t('settings.inactive');
    }
    if (!walletStatus.funded) {
      return t('settings.fundingRequired');
    }
    if (!walletStatus.trustline_ready) {
      return t('settings.trustlineMissing');
    }
    return walletStatus.usable ? t('settings.ready') : t('settings.reviewRequired');
  }, [walletStatus, t]);
  const walletReadinessTone = useMemo(() => {
    if (!walletStatus) {
      return "text-slate-900";
    }
    return walletStatus.usable ? "text-emerald-600" : "text-amber-600";
  }, [walletStatus]);
  const walletAddress = wallet?.public_key ?? walletStatus?.public_key ?? walletForm.public_key;

  // USD rates for balance conversion (mainnet only)
  const { rates: usdRates, loading: usdRatesLoading } = useUsdRates();

  function getUsdValue(assetCode: string, balance: string): string | null {
    if (!usdRates || !usdRates[assetCode]) return null;
    const numBalance = parseFloat(balance);
    if (isNaN(numBalance)) return null;
    return (numBalance * usdRates[assetCode]).toFixed(2);
  }

  async function handleRegister() {
    setLoadingAction("register");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.post<AuthUser>(ENDPOINTS.auth.register, registerForm);
      setCurrentUser(response.data);
      setWalletForm((current) => ({
        ...current,
        user_id: response.data.id,
      }));
      setFeedback("Account registered successfully.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLogin() {
    setLoadingAction("login");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.post<AuthSessionResponse>(ENDPOINTS.auth.login, loginForm);
      setSession(response.data);
      setCurrentUser(response.data.user);
      setWalletForm((current) => ({
        ...current,
        user_id: response.data.user.id,
      }));
      setFeedback("Signed in successfully.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLoadSession() {
    if (!session?.access_token) {
      setError("Login first to load the current session.");
      return;
    }

    setLoadingAction("session");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.get<AuthUser>(ENDPOINTS.auth.me, {
        headers: {
          Authorization: `Bearer ${session.access_token}`,
        },
      });
      setCurrentUser(response.data);
      setFeedback("Session refreshed from the backend.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLogout() {
    if (!session?.access_token) {
      setSession(null);
      setCurrentUser(null);
      setFeedback("Local session cleared.");
      return;
    }

    setLoadingAction("logout");
    setError(null);
    setFeedback(null);

    try {
      await api.post(
        ENDPOINTS.auth.logout,
        {},
        {
          headers: {
            Authorization: `Bearer ${session.access_token}`,
          },
        },
      );
      setSession(null);
      setCurrentUser(null);
      setFeedback("Logged out successfully.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleCreateWallet() {
    if (!activeUserId) {
      setError("Provide a user ID or log in before creating a wallet.");
      return;
    }

    setLoadingAction("create-wallet");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.post<Wallet>(ENDPOINTS.wallets.create, {
        user_id: activeUserId,
      });
      setWallet(response.data);
      setWalletForm((current) => ({
        ...current,
        user_id: response.data.user_id,
        public_key: response.data.public_key,
        funded: response.data.funded,
        trustline_ready: response.data.trustline_ready,
      }));
      setFeedback(response.data.message ?? "Wallet created.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLinkWallet() {
    if (!walletForm.user_id.trim() || !walletForm.public_key.trim()) {
      setError("Provide both a user ID and public key before linking a wallet.");
      return;
    }

    setLoadingAction("link-wallet");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.post<Wallet>(ENDPOINTS.wallets.link, {
        user_id: walletForm.user_id.trim(),
        public_key: walletForm.public_key.trim(),
        funded: walletForm.funded,
        trustline_ready: walletForm.trustline_ready,
      });
      setWallet(response.data);
      setFeedback("Wallet linked successfully.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLoadWalletDetails() {
    if (!activeUserId) {
      setError("Provide a user ID or log in before loading wallet details.");
      return;
    }

    setLoadingAction("wallet-details");
    setError(null);
    setFeedback(null);

    try {
      const [walletResponse, statusResponse] = await Promise.all([
        api.get<Wallet>(ENDPOINTS.wallets.byId(activeUserId)),
        api.get<WalletStatus>(ENDPOINTS.wallets.status(activeUserId)),
      ]);
      setWallet(walletResponse.data);
      setWalletStatus(statusResponse.data);
      setWalletForm((current) => ({
        ...current,
        user_id: walletResponse.data.user_id,
        public_key: walletResponse.data.public_key,
        funded: walletResponse.data.funded,
        trustline_ready: walletResponse.data.trustline_ready,
      }));
      setFeedback("Wallet details loaded.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleLoadBalance() {
    const address = wallet?.public_key ?? walletForm.public_key.trim();
    if (!address) {
      setError("Load or link a wallet before requesting balances.");
      return;
    }

    setLoadingAction("wallet-balance");
    setError(null);
    setFeedback(null);

    try {
      const response = await api.get<WalletBalance>(ENDPOINTS.wallets.balance(address));
      setWalletBalance(response.data);
      setFeedback("Wallet balance loaded.");
    } catch (issue) {
      setError(getErrorMessage(issue));
    } finally {
      setLoadingAction(null);
    }
  }

  async function handleFundTestnetWallet() {
    const address = wallet?.public_key ?? walletForm.public_key.trim();
    if (!address) {
      setError("Load or link a wallet before funding the wallet.");
      return;
    }

    setLoadingAction("fund-testnet-wallet");
    setError(null);
    setFeedback(null);

    try {
      await api.get(ENDPOINTS.wallets.friendbot(address));

      const statusUserId = wallet?.user_id ?? (activeUserId || address);
      const [statusResponse, balanceResponse] = await Promise.all([
        api.get<WalletStatus>(ENDPOINTS.wallets.status(statusUserId)),
        api.get<WalletBalance>(ENDPOINTS.wallets.balance(address)),
      ]);

      setWalletStatus(statusResponse.data);
      setWalletBalance(balanceResponse.data);
      setWalletForm((current) => ({
        ...current,
        funded: true,
      }));

      const successMessage = "Wallet funded successfully.";
      setFeedback(successMessage);
      toast(successMessage, "success");
    } catch (issue) {
      const errorMessage = getErrorMessage(issue);
      setError(errorMessage);
      toast(errorMessage, "error");
    } finally {
      setLoadingAction(null);
    }
  }

  return (
    <div className="mx-auto max-w-6xl space-y-6 px-4 py-8">
      <div className="space-y-1">
        <h1 className="text-3xl font-semibold tracking-tight text-slate-900">
          {t('settings.walletControl')}
        </h1>
        <p className="text-sm text-slate-500">
          {t('settings.manageSessionWallet')}
        </p>
      </div>

      {feedback ? (
        <div className="rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {feedback}
        </div>
      ) : null}

      {/* Theme Settings */}
      <section className="rounded-2xl border border-slate-200 bg-white dark:bg-slate-900 p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900 dark:text-white">Appearance Settings</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Customize your visual theme preference.</p>
        <div className="mt-6 grid gap-4 md:grid-cols-3">
          <button
            onClick={() => setTheme("light")}
            className={`flex flex-col items-center gap-3 rounded-lg border-2 p-4 transition-all ${
              theme === "light"
                ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                : "border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
            }`}
          >
            <svg className="h-8 w-8 text-slate-700 dark:text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 3v1m0 16v1m9-9h-1M4 12H3m15.364 6.364l-.707-.707M6.343 6.343l-.707-.707m12.728 0l-.707.707M6.343 17.657l-.707.707M16 12a4 4 0 11-8 0 4 4 0 018 0z" />
            </svg>
            <span className="text-sm font-medium text-slate-900 dark:text-white">Light</span>
          </button>
          <button
            onClick={() => setTheme("dark")}
            className={`flex flex-col items-center gap-3 rounded-lg border-2 p-4 transition-all ${
              theme === "dark"
                ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                : "border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
            }`}
          >
            <svg className="h-8 w-8 text-slate-700 dark:text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M20.354 15.354A9 9 0 018.646 3.646 9.003 9.003 0 0012 21a9.003 9.003 0 008.354-5.646z" />
            </svg>
            <span className="text-sm font-medium text-slate-900 dark:text-white">Dark</span>
          </button>
          <button
            onClick={() => setTheme("system")}
            className={`flex flex-col items-center gap-3 rounded-lg border-2 p-4 transition-all ${
              theme === "system"
                ? "border-blue-500 bg-blue-50 dark:bg-blue-900/20"
                : "border-slate-200 dark:border-slate-700 hover:border-slate-300 dark:hover:border-slate-600"
            }`}
          >
            <svg className="h-8 w-8 text-slate-700 dark:text-slate-300" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.75 17L9 20l-1 1h8l-1-1-.75-3M3 13h18M5 17h14a2 2 0 002-2V5a2 2 0 00-2-2H5a2 2 0 00-2 2v10a2 2 0 002 2z" />
            </svg>
            <span className="text-sm font-medium text-slate-900 dark:text-white">System</span>
          </button>
        </div>
      </section>

      {/* Onboarding tour replay (Issue #159) — mirrors OnboardingTour's START_TOUR_EVENT */}
      <section className="rounded-2xl border border-slate-200 bg-white dark:bg-slate-900 p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900 dark:text-white">Onboarding</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">
          Replay the guided tour of the dashboard, outages, and payments.
        </p>
        <button
          onClick={() => window.dispatchEvent(new CustomEvent("apexchain:start-tour"))}
          className="mt-4 rounded-md border border-slate-200 dark:border-slate-700 px-4 py-2 text-sm font-medium text-slate-700 dark:text-slate-200 hover:bg-slate-100 dark:hover:bg-slate-800"
        >
          Replay onboarding tour
        </button>
      </section>

      {/* FE-056: Account profile section */}
      <section className="rounded-2xl border border-slate-200 bg-white dark:bg-slate-900 p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900 dark:text-white">Account Profile</h2>
        <p className="mt-1 text-sm text-slate-500 dark:text-slate-400">Current session identity and metadata.</p>
        {sessionState === "loading" && (
          <p className="mt-4 text-sm text-slate-400">{t('settings.loadingSession')}</p>
        )}
        {sessionState === "unauthenticated" && (
          <p className="mt-4 text-sm text-slate-500 dark:text-slate-400">Not signed in.</p>
        )}
        {sessionState === "authenticated" && sessionUser && (
          <dl className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 text-sm">
            {[
              { label: "Email", value: sessionUser.email },
              { label: "Role", value: sessionUser.role },
              { label: "Full name", value: sessionUser.full_name ?? "—" },
              { label: "User ID", value: sessionUser.id },
              { label: "Wallet", value: sessionUser.stellar_wallet ?? "Not linked" },
              {
                label: "Member since",
                value: sessionUser.created_at
                  ? new Date(sessionUser.created_at).toLocaleDateString()
                  : "—",
              },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-xl border border-slate-100 bg-slate-50 px-4 py-3">
                <dt className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</dt>
                <dd className="mt-1 truncate font-medium text-slate-900">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </section>

      {error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      {/* FE-008: Session management */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900">{t('settings.sessionManagement')}</h2>
        <p className="mt-1 text-sm text-slate-500">
          {t('settings.controlActiveSession')}
        </p>

        {sessionActionFeedback && (
          <div className="mt-4 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
            {sessionActionFeedback}
          </div>
        )}
        {sessionActionError && (
          <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {sessionActionError}
          </div>
        )}

        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm space-y-3">
            <h3 className="font-medium text-slate-900">{t('settings.signOutOfThisSession')}</h3>
            <p className="text-slate-500">
              {t('settings.endsCurrentSession')}
            </p>
            <button
              onClick={() => void handleSignOut()}
              disabled={sessionState !== "authenticated" || sessionActionLoading !== null}
              className="rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {sessionActionLoading === "signout" ? `${t('common.loading')}` : t('common.signOut')}
            </button>
          </div>

          <div className="rounded-xl border border-slate-100 bg-slate-50 p-4 text-sm space-y-3">
            <h3 className="font-medium text-slate-900">{t('settings.revokeAllSessions')}</h3>
            <p className="text-slate-500">
              {t('settings.invalidateAllTokens')}
            </p>
            <button
              onClick={() => void handleLogoutAll()}
              disabled={sessionState !== "authenticated" || sessionActionLoading !== null}
              className="rounded-md border border-red-200 px-4 py-2 text-sm font-medium text-red-600 hover:bg-red-50 disabled:opacity-50"
            >
              {sessionActionLoading === "logout-all" ? `${t('common.loading')}` : t('settings.revokeAllSessions')}
            </button>
          </div>
        </div>

        <div className="mt-4 rounded-xl border border-blue-100 bg-blue-50 p-4 text-sm text-blue-800 space-y-1">
          <p className="font-medium">{t('settings.howSessionRefreshWorks')}</p>
          <p>
            {t('settings.sessionRefreshExplanation')}
          </p>
          <p>
            {t('settings.sessionExpiredMessage')}
          </p>
        </div>
      </section>

      {/* Language Settings */}
      <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
        <h2 className="text-xl font-semibold text-slate-900">{t('settings.languageSettings')}</h2>
        <p className="mt-1 text-sm text-slate-500">{t('settings.selectLanguage')}</p>
        
        <div className="mt-6 max-w-md">
          <DropdownMenu>
            <DropdownMenuTrigger className="flex w-full items-center justify-between rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50">
              {localeNames[locale]}
              <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="6 9 12 15 18 9"></polyline>
              </svg>
            </DropdownMenuTrigger>
            <DropdownMenuContent className="w-full min-w-[200px]">
              {locales.map((loc) => (
                <DropdownMenuItem
                  key={loc}
                  onClick={() => setLocale(loc)}
                  className={`flex cursor-pointer items-center justify-between px-4 py-2 text-sm ${
                    locale === loc ? "bg-slate-100 font-medium" : ""
                  }`}
                >
                  {localeNames[loc]}
                  {locale === loc && (
                    <svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                      <polyline points="20 6 9 17 4 12"></polyline>
                    </svg>
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-4">
        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{t('settings.session')}</p>
          <p className="mt-2 text-xl font-semibold text-slate-900">
            {currentUser ? t('settings.authenticated') : t('settings.notSignedIn')}
          </p>
          <p className="mt-1 text-sm text-slate-500">
            {currentUser?.email ?? t('settings.loadCreateAccount')}
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{t('settings.wallet')}</p>
          <p className="mt-2 text-xl font-semibold text-slate-900">
            {walletAddress ? t('settings.connected') : t('settings.notLinked')}
          </p>
          <p className="mt-1 truncate text-sm text-slate-500">
            {walletAddress || t('settings.createLinkWallet')}
          </p>
          {env.STELLAR_NETWORK === "testnet" && walletAddress ? (
            <button
              type="button"
              onClick={() => void handleFundTestnetWallet()}
              disabled={loadingAction === "fund-testnet-wallet" || isHorizonUnreachable}
              className="mt-3 w-full rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm font-medium text-emerald-700 hover:bg-emerald-100 disabled:opacity-60"
            >
              {loadingAction === "fund-testnet-wallet" ? "Funding..." : "Fund testnet wallet"}
            </button>
          ) : null}
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{t('settings.readiness')}</p>
          <p className={`mt-2 text-xl font-semibold ${walletReadinessTone}`}>
            {walletReadinessLabel}
          </p>
          <p className="mt-1 text-sm text-slate-500">
            {walletStatus
              ? `${walletStatus.funded ? t('settings.funded') : t('settings.unfunded')} • ${
                  walletStatus.trustline_ready ? t('settings.trustlineReady') : t('settings.trustlineMissing')
                }`
              : t('settings.loadWalletDetails')}
          </p>
        </div>

        <div className="rounded-2xl border border-slate-200 bg-white p-4 shadow-sm">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{t('settings.balances')}</p>
          <p className="mt-2 text-xl font-semibold text-slate-900">{walletAssetCount}</p>
          <p className="mt-1 text-sm text-slate-500">
            {walletAssetCount > 0 ? t('settings.trackedAssetsLoaded') : t('settings.noBalanceData')}
          </p>
        </div>
      </div>

      {/* ------------------------------------------------------------------ */}
      {/* Issue #128 / #129 — Stellar Network Status + SLA Contract ID      */}
      {/* ------------------------------------------------------------------ */}
      <StellarHealthCard
        horizonStatus={stellarHealth.status}
        latencyMs={stellarHealth.latencyMs}
        network={env.STELLAR_NETWORK}
      />

      <SLAContractIdCard
        contractId={env.SLA_CONTRACT_ID}
        network={env.STELLAR_NETWORK}
      />

      <div className="grid gap-6 lg:grid-cols-2">
        <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">{t('settings.accountSession')}</h2>
            <p className="text-sm text-slate-500">
              {t('settings.registerSignInValidate')}
            </p>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-3 rounded-xl bg-slate-50 p-4">
              <h3 className="font-medium text-slate-900">{t('settings.register')}</h3>
              <input
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
                value={registerForm.full_name}
                onChange={(event) =>
                  setRegisterForm((current) => ({
                    ...current,
                    full_name: event.target.value,
                  }))
                }
                placeholder={t('settings.fullName')}
              />
              <input
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
                value={registerForm.email}
                onChange={(event) =>
                  setRegisterForm((current) => ({
                    ...current,
                    email: event.target.value,
                  }))
                }
                placeholder={t('settings.email')}
              />
              <input
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
                type="password"
                value={registerForm.password}
                onChange={(event) =>
                  setRegisterForm((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
                placeholder={t('settings.password')}
              />
              <button
                onClick={handleRegister}
                disabled={loadingAction === "register"}
                className="w-full rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
              >
                {loadingAction === "register" ? `${t('common.loading')}` : t('settings.registerAccount')}
              </button>
            </div>

            <div className="space-y-3 rounded-xl bg-slate-50 p-4">
              <h3 className="font-medium text-slate-900">{t('settings.login')}</h3>
              <input
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
                value={loginForm.email}
                onChange={(event) =>
                  setLoginForm((current) => ({
                    ...current,
                    email: event.target.value,
                  }))
                }
                placeholder={t('settings.email')}
              />
              <input
                className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
                type="password"
                value={loginForm.password}
                onChange={(event) =>
                  setLoginForm((current) => ({
                    ...current,
                    password: event.target.value,
                  }))
                }
                placeholder={t('settings.password')}
              />
              <button
                onClick={handleLogin}
                disabled={loadingAction === "login"}
                className="w-full rounded-md bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-60"
              >
                {loadingAction === "login" ? `${t('common.loading')}` : t('settings.signIn')}
              </button>
              <div className="flex gap-2">
                <button
                  onClick={handleLoadSession}
                  disabled={loadingAction === "session"}
                  className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {t('settings.refreshSession')}
                </button>
                <button
                  onClick={handleLogout}
                  disabled={loadingAction === "logout"}
                  className="flex-1 rounded-md border border-slate-200 px-3 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
                >
                  {t('settings.logout')}
                </button>
              </div>
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
            <h3 className="font-medium text-slate-900">{t('settings.currentUser')}</h3>
            {currentUser ? (
              <dl className="mt-3 grid gap-2 text-slate-600">
                <div className="flex justify-between gap-4">
                  <dt>{t('settings.userId')}</dt>
                  <dd className="font-medium text-slate-900">{currentUser.id}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt>{t('settings.email')}</dt>
                  <dd className="font-medium text-slate-900">{currentUser.email}</dd>
                </div>
                <div className="flex justify-between gap-4">
                  <dt>{t('settings.role')}</dt>
                  <dd className="font-medium text-slate-900">{currentUser.role}</dd>
                </div>
              </dl>
            ) : (
              <p className="mt-3 text-slate-500">{t('settings.noActiveUser')}</p>
            )}
          </div>
        </section>

        <section className="space-y-4 rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
          <div>
            <h2 className="text-xl font-semibold text-slate-900">{t('settings.walletStatus')}</h2>
            <p className="text-sm text-slate-500">
              {t('settings.walletBackendBridge')}
            </p>
          </div>

          <div className="grid gap-3">
            <input
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
              value={walletForm.user_id}
              onChange={(event) =>
                setWalletForm((current) => ({
                  ...current,
                  user_id: event.target.value,
                }))
              }
              placeholder="User ID"
            />
            <input
              className="w-full rounded-md border border-slate-200 px-3 py-2 text-sm"
              value={walletForm.public_key}
              onChange={(event) =>
                setWalletForm((current) => ({
                  ...current,
                  public_key: event.target.value,
                }))
              }
              placeholder="Public key"
            />
            <div className="flex flex-wrap gap-4 text-sm text-slate-600">
              <label className="inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={walletForm.funded}
                  onChange={(event) =>
                    setWalletForm((current) => ({
                      ...current,
                      funded: event.target.checked,
                    }))
                  }
                />
                Funded
              </label>
              <label className="inline-flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={walletForm.trustline_ready}
                  onChange={(event) =>
                    setWalletForm((current) => ({
                      ...current,
                      trustline_ready: event.target.checked,
                    }))
                  }
                />
                Trustline ready
              </label>
            </div>
          </div>

          <div className="grid gap-2 sm:grid-cols-2">
            <button
              onClick={handleCreateWallet}
              disabled={loadingAction === "create-wallet" || isHorizonUnreachable}
              className="rounded-md bg-slate-900 px-4 py-2 text-sm font-medium text-white hover:bg-slate-800 disabled:opacity-60"
              title={isHorizonUnreachable ? "Horizon is unreachable — wallet actions disabled" : "Create wallet"}
            >
              {loadingAction === "create-wallet" ? "Creating..." : "Create wallet"}
            </button>
            <button
              onClick={handleLinkWallet}
              disabled={loadingAction === "link-wallet" || isHorizonUnreachable}
              className="rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              title={isHorizonUnreachable ? "Horizon is unreachable — wallet actions disabled" : "Link wallet"}
            >
              {loadingAction === "link-wallet" ? "Linking..." : "Link wallet"}
            </button>
            <button
              onClick={handleLoadWalletDetails}
              disabled={loadingAction === "wallet-details" || isHorizonUnreachable}
              className="rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              title={isHorizonUnreachable ? "Horizon is unreachable — wallet actions disabled" : "Load wallet details"}
            >
              {loadingAction === "wallet-details" ? "Loading..." : "Load wallet details"}
            </button>
            <button
              onClick={handleLoadBalance}
              disabled={loadingAction === "wallet-balance" || isHorizonUnreachable}
              className="rounded-md border border-slate-200 px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-60"
              title={isHorizonUnreachable ? "Horizon is unreachable — wallet actions disabled" : "Load balance"}
            >
              {loadingAction === "wallet-balance" ? "Loading..." : "Load balance"}
            </button>
          </div>

          <div className="grid gap-4 md:grid-cols-2">
            <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
              <h3 className="font-medium text-slate-900">Wallet details</h3>
              {wallet ? (
                <dl className="mt-3 grid gap-2 text-slate-600">
                  <div className="flex justify-between gap-4">
                    <dt>Address</dt>
                    <dd className="break-all text-right font-medium text-slate-900">
                      {explorerLink("account", wallet.public_key) ? (
                        <a href={explorerLink("account", wallet.public_key)!} target="_blank" rel="noopener noreferrer" className="text-blue-600 hover:underline">
                          {wallet.public_key}
                        </a>
                      ) : wallet.public_key}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Funded</dt>
                    <dd className="font-medium text-slate-900">
                      {wallet.funded ? "Yes" : "No"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Trustline</dt>
                    <dd className="font-medium text-slate-900">
                      {wallet.trustline_ready ? "Ready" : "Missing"}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="mt-3 text-slate-500">No wallet loaded yet.</p>
              )}
            </div>

            <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
              <h3 className="font-medium text-slate-900">Wallet readiness</h3>
              {walletStatus ? (
                <dl className="mt-3 grid gap-2 text-slate-600">
                  <div className="flex justify-between gap-4">
                    <dt>Active</dt>
                    <dd className="font-medium text-slate-900">
                      {walletStatus.active ? "Yes" : "No"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Usable</dt>
                    <dd className="font-medium text-slate-900">
                      {walletStatus.usable ? "Ready" : "Not ready"}
                    </dd>
                  </div>
                  <div className="flex justify-between gap-4">
                    <dt>Last updated</dt>
                    <dd className="font-medium text-slate-900">
                      {new Date(walletStatus.last_updated).toLocaleString()}
                    </dd>
                  </div>
                </dl>
              ) : (
                <p className="mt-3 text-slate-500">Load wallet details to inspect readiness.</p>
              )}
            </div>
          </div>

          <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-sm">
            <h3 className="font-medium text-slate-900">Balances</h3>
            {walletBalance ? (
              <div className="mt-3 grid gap-2">
                {Object.entries(walletBalance.balances).map(([asset, balance]) => {
                  const usdValue = getUsdValue(asset, balance.balance);
                  return (
                  <div
                    key={asset}
                    className="flex items-center justify-between rounded-lg border border-slate-200 bg-white px-3 py-2"
                  >
                    <div className="flex flex-col">
                      <span className="font-medium text-slate-900">{asset}</span>
                      {usdValue && (
                        <span className="text-xs text-emerald-600">≈ ${usdValue} USD</span>
                      )}
                    </div>
                    <span className="text-slate-600">{balance.balance}</span>
                  </div>
                  );
                })}
              </div>
            ) : (
              <p className="mt-3 text-slate-500">No balance data loaded yet.</p>
            )}
          </div>
        </section>
      </div>

      {/* FE-022: Wallet readiness guidance */}
      {walletStatus && !walletStatus.usable && (
        <section className="rounded-2xl border border-amber-200 bg-amber-50 p-6 shadow-sm">
          <h2 className="text-lg font-semibold text-amber-900">Wallet Not Ready — Next Steps</h2>
          <p className="mt-1 text-sm text-amber-700">
            Your wallet must be funded and have a trustline set up before payments can be processed.
          </p>
          <ul className="mt-4 space-y-3">
            {!walletStatus.active && (
              <li className="flex items-start gap-3 text-sm text-amber-800">
                <span className="mt-0.5 h-5 w-5 shrink-0 rounded-full bg-amber-200 text-center text-xs font-bold leading-5 text-amber-900">1</span>
                <span><strong>Activate your wallet.</strong> The wallet is currently inactive. Contact your administrator or re-link the wallet via the Wallet Status panel above.</span>
              </li>
            )}
            {walletStatus.active && !walletStatus.funded && (
              <li className="flex items-start gap-3 text-sm text-amber-800">
                <span className="mt-0.5 h-5 w-5 shrink-0 rounded-full bg-amber-200 text-center text-xs font-bold leading-5 text-amber-900">2</span>
                <span><strong>Fund your wallet.</strong> Send at least 1 XLM to <code className="rounded bg-amber-100 px-1 font-mono text-xs">{walletStatus.public_key}</code> on the Stellar network to activate the account.</span>
              </li>
            )}
            {walletStatus.active && walletStatus.funded && !walletStatus.trustline_ready && (
              <li className="flex items-start gap-3 text-sm text-amber-800">
                <span className="mt-0.5 h-5 w-5 shrink-0 rounded-full bg-amber-200 text-center text-xs font-bold leading-5 text-amber-900">3</span>
                <span><strong>Set up a trustline.</strong> Your wallet is funded but missing a trustline for the payment asset. Use the Stellar Laboratory or your wallet app to add a trustline for the required asset.</span>
              </li>
            )}
          </ul>
        </section>
      )}

      {walletStatus?.usable && (
        <section className="rounded-2xl border border-emerald-200 bg-emerald-50 p-4 shadow-sm">
          <p className="text-sm font-medium text-emerald-800">
            ✓ Wallet is fully ready — funded, trustline active, and usable for payments.
          </p>
        </section>
      )}
    </div>
  );
}

/* -------------------------------------------------------------------------- */
/* Issue #128 — Stellar Network Health Card                                   */
/* -------------------------------------------------------------------------- */

function StellarHealthCard({
  horizonStatus,
  latencyMs,
  network,
}: {
  horizonStatus: string;
  latencyMs: number | null;
  network: string;
}) {
  const isReachable = horizonStatus === "reachable";
  const isChecking = horizonStatus === "checking";

  const statusIcon = isChecking ? (
    <div className="h-3 w-3 animate-pulse rounded-full bg-amber-400" />
  ) : isReachable ? (
    <div className="h-3 w-3 rounded-full bg-emerald-500" />
  ) : (
    <div className="h-3 w-3 rounded-full bg-red-500" />
  );

  const statusLabel = isChecking
    ? "Checking..."
    : isReachable
    ? "Reachable"
    : "Unreachable";

  return (
    <section className="rounded-2xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            Stellar Network Status
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Horizon endpoint health check for the {network} network
          </p>
        </div>
        <span className="inline-flex items-center gap-1.5 rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
          {statusIcon}
          {statusLabel}
        </span>
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Latency
          </p>
          <p className="mt-1 text-lg font-semibold text-slate-900">
            {isChecking
              ? "—"
              : latencyMs !== null
              ? `${latencyMs} ms`
              : "N/A"}
          </p>
        </div>
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Network
          </p>
          <p className="mt-1 text-lg font-semibold text-slate-900 capitalize">
            {network}
          </p>
        </div>
      </div>

      {!isReachable && !isChecking && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <p className="font-medium">Horizon is unreachable</p>
          <p className="mt-1 text-red-700">
            Stellar network actions (wallet creation, payments) are currently
            unavailable. Please check your network connection or the Horizon
            endpoint configuration.
          </p>
        </div>
      )}
    </section>
  );
}

/* -------------------------------------------------------------------------- */
/* Issue #129 — SLA Contract ID Card                                         */
/* -------------------------------------------------------------------------- */

/** Canonical SLA contract IDs published for each network */
const CANONICAL_SLA_CONTRACT_IDS: Record<string, string> = {
  testnet: "CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC",
  // TODO: replace the placeholder below with the actual published mainnet
  //       contract ID from the DOCS.md once it is confirmed.
  mainnet: "PLACEHOLDER_MAINNET_CONTRACT_ID_CHANGE_ME",
};

function SLAContractIdCard({
  contractId,
  network,
}: {
  contractId: string;
  network: string;
}) {
  const canonicalId = CANONICAL_SLA_CONTRACT_IDS[network];
  const isMismatch = canonicalId && contractId !== canonicalId;

  const truncatedId =
    contractId && contractId.length > 10
      ? `${contractId.slice(0, 6)}...${contractId.slice(-4)}`
      : contractId;

  const canonicalTruncated =
    canonicalId && canonicalId.length > 10
      ? `${canonicalId.slice(0, 6)}...${canonicalId.slice(-4)}`
      : canonicalId;

  return (
    <section
      className={`rounded-2xl border p-6 shadow-sm ${
        isMismatch
          ? "border-red-200 bg-red-50"
          : "border-slate-200 bg-white"
      }`}
    >
      <div className="flex items-start justify-between">
        <div>
          <h2 className="text-xl font-semibold text-slate-900">
            SLA Contract ID
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            Resolved smart contract identifier used for SLA calculation calls
          </p>
        </div>
        {canonicalId && !isMismatch && (
          <span className="inline-flex items-center gap-1 rounded-full bg-emerald-100 px-3 py-1 text-xs font-medium text-emerald-700">
            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
            </svg>
            Verified
          </span>
        )}
        {isMismatch && (
          <span className="inline-flex items-center gap-1 rounded-full bg-red-100 px-3 py-1 text-xs font-medium text-red-700">
            <svg className="h-3 w-3" fill="none" viewBox="0 0 24 24" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
            Mismatch
          </span>
        )}
      </div>

      <div className="mt-4 grid grid-cols-2 gap-4">
        <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
          <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Resolved Contract ID
          </p>
          <p className="mt-1 font-mono text-sm font-semibold text-slate-900">
            {truncatedId || "Not configured"}
          </p>
        </div>
        {canonicalId && (
          <div className="rounded-lg border border-slate-100 bg-slate-50 p-3">
            <p className="text-xs font-medium uppercase tracking-wide text-slate-500">
              Expected ({network})
            </p>
            <p className="mt-1 font-mono text-sm font-semibold text-slate-900">
              {canonicalTruncated}
            </p>
          </div>
        )}
      </div>

      {isMismatch && (
        <div className="mt-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <p className="font-medium">Contract ID Mismatch</p>
          <p className="mt-1 text-red-600">
            The configured SLA contract ID does not match the canonical published
            contract ID for the {network} network. Double-check your
            NEXT_PUBLIC_SLA_CONTRACT_ID environment variable to ensure it points
            to the correct contract.
          </p>
        </div>
      )}
    </section>
  );
}