"use client";
/** ApexChain Network Operations Intelligence Platform */

import { useHealth } from "@/hooks/useHealth";
import { useSession } from "@/hooks/useSession";
import { STELLAR_NETWORK } from "@/lib/explorer";
import { useI18n } from "@/i18n/i18n";
import Link from "next/link";

// Routes only visible to admin users
const ADMIN_ROUTES = ["/webhooks", "/config"];

const Navigation = () => {
  const { state, user, logout } = useSession();
  const { t } = useI18n();
  const isAdmin = user?.role === "admin";
  const { status, isOffline } = useHealth();

  return (
    <>
      {isOffline && (
        <div className="bg-red-500 text-white text-center py-2 text-sm font-semibold sticky top-0 z-50">
          {t('errors.offline')}
        </div>
      )}
      <nav style={{ padding: "1rem", borderBottom: "1px solid #ccc" }} className="flex items-center justify-between">
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <div
            className={`w-3 h-3 rounded-full mr-2 ${
              status === "green"
                ? "bg-green-500"
                : "bg-red-500"
            }`}
            title={`${t('navigation.systemHealth')}: ${status}`}
          />
          {STELLAR_NETWORK === "mainnet" && (
            <span className="rounded bg-red-600 px-2 py-0.5 text-xs font-bold uppercase tracking-wider text-white animate-pulse">
              MAINNET
            </span>
          )}
          <Link href="/" data-tour="nav-dashboard">{t('common.dashboard')}</Link>
          <span>|</span>
          <Link href="/outages" data-tour="nav-outages">{t('common.outages')}</Link>
          <span>|</span>
          <Link href="/bulk-import">{t('common.bulkImport')}</Link>
          <span>|</span>
          <Link href="/payments" data-tour="nav-payments">{t('common.payments')}</Link>
          <span>|</span>
          <Link href="/setting">{t('common.settings')}</Link>
          <span>|</span>
          <Link href="/payments/retry-queue">Retry Queue</Link>
          {isAdmin && (
            <>
              <span>|</span>
              <Link href="/config">{t('common.config')}</Link>
              <span>|</span>
              <Link href="/webhooks">{t('common.webhooks')}</Link>
            </>
          )}
        </div>

        <div className="text-sm text-slate-600">
          {state === "loading" && (
            <span className="text-slate-400">{t('settings.loadingSession')}</span>
          )}
          {state === "authenticated" && user && (
            <span className="flex items-center gap-3">
              <span>{user.email}</span>
              {isAdmin && (
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-xs font-medium text-slate-600">
                  admin
                </span>
              )}
              <button
                onClick={() => void logout()}
                className="rounded border border-slate-200 px-2 py-0.5 text-xs hover:bg-slate-100"
              >
                {t('common.signOut')}
              </button>
            </span>
          )}
          {state === "unauthenticated" && (
            <Link
              href="/login"
              className="rounded border border-slate-200 px-2 py-0.5 text-xs hover:bg-slate-100"
            >
              {t('auth.signIn')}
            </Link>
          )}
        </div>
      </nav>
    </>
  );
};

export default Navigation;

// Export admin route list so RouteGuard can use it
export { ADMIN_ROUTES };
