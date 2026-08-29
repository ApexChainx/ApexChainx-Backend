/** ApexChain - Centralized API Endpoints registry */

export const ENDPOINTS = {
  auth: {
    login: "/auth/login",
    register: "/auth/register",
    logout: "/auth/logout",
    logoutAll: "/auth/logout-all",
    /**
     * Completes a login challenge for accounts that require a second factor.
     * The TOTP code is submitted here after a successful credentials check.
     */
    loginTwoFactor: "/auth/login/2fa",
    /**
     * Cookie-only session check. Reads the httpOnly session cookies directly
     * (no Authorization header) so a hard refresh — where in-memory tokens are
     * gone — can still restore the session. Falls back to `/auth/me` when the
     * endpoint is not deployed yet.
     */
    session: "/auth/session",
    me: "/auth/me",
    twoFactorSetup: "/auth/2fa/setup",
    twoFactorVerify: "/auth/2fa/verify",
    twoFactorDisable: "/auth/2fa/disable",
    twoFactorBackupCodes: "/auth/2fa/backup-codes",
  },
  outages: {
    base: "/outages",
    byId: (id: string) => `/outages/${id}`,
    resolve: (id: string) => `/outages/${id}/resolve`,
    bulk: "/outages/bulk",
    bulkHistory: "/outages/bulk/history",
    export: "/outages/export",
  },
  payments: {
    base: "/payments",
    byId: (id: string) => `/payments/${id}`,
    retry: (id: string) => `/payments/${id}/retry`,
    reconcile: (id: string) => `/payments/${id}/reconcile`,
    export: "/payments/export",
  },
  webhooks: {
    base: "/webhooks",
    byId: (id: string) => `/webhooks/${id}`,
    deliveries: (webhookId: string) => `/webhooks/${webhookId}/deliveries`,
    retryDelivery: (webhookId: string, deliveryId: string) =>
      `/webhooks/${webhookId}/deliveries/${deliveryId}/retry`,
  },
  sla: {
    calculate: "/sla/calculate",
    preview: "/sla/preview",
    disputes: "/sla/disputes",
    dashboard: "/sla/analytics/dashboard",
    trends: "/sla/analytics/trends",
  },
  wallets: {
    create: "/wallets/create",
    link: "/wallets/link",
    byId: (userId: string) => `/wallets/${userId}`,
    status: (userId: string) => `/wallets/${userId}/status`,
    balance: (address: string) => `/wallets/${address}/balance`,
    friendbot: (address: string) => `/wallets/friendbot?address=${encodeURIComponent(address)}`,
  },
  /** Server-Sent Events stream for real-time session invalidation */
  sessionEvents: "/auth/events",

  preferences: {
    base: "/user/preferences",
  },
} as const;