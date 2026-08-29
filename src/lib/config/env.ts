const DEFAULT_API_BASE = "/api/v1";
const DEFAULT_APP_URL = "https://app.apexchain.com";
const DEFAULT_DEV_APP_URL = "http://localhost:3000";
const DEFAULT_STELLAR_HORIZON = "https://horizon-testnet.stellar.org";
const DEFAULT_STELLAR_NETWORK = "testnet";

function readEnv(name: string): string | undefined {
  const value = process.env[name];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

export function resolveApiBaseUrl(value = readEnv("NEXT_PUBLIC_API_BASE_URL"), environment = process.env.NODE_ENV): string {
  if (value) {
    return value.replace(/\/+$/, "");
  }

  return environment === "development" ? "http://localhost:8000/api/v1" : DEFAULT_API_BASE;
}

export function resolveAppUrl(value = readEnv("NEXT_PUBLIC_APP_URL"), environment = process.env.NODE_ENV): string {
  if (value) {
    return value.replace(/\/+$/, "");
  }

  return environment === "development" ? DEFAULT_DEV_APP_URL : DEFAULT_APP_URL;
}

export function resolveApiRefreshUrl(value = readEnv("NEXT_PUBLIC_API_BASE_URL"), environment = process.env.NODE_ENV): string {
  const base = resolveApiBaseUrl(value, environment);
  return `${base.replace(/\/$/, "")}/auth/refresh`;
}

export const env = {
  API_BASE_URL: resolveApiBaseUrl(),
  API_REFRESH_URL: resolveApiRefreshUrl(),

  /** Horizon RPC endpoint for Stellar network queries */
  STELLAR_HORIZON_URL:
    readEnv("NEXT_PUBLIC_STELLAR_HORIZON_URL") || DEFAULT_STELLAR_HORIZON,

  /** The Stellar network identifier (testnet | mainnet) */
  STELLAR_NETWORK:
    readEnv("NEXT_PUBLIC_STELLAR_NETWORK") || DEFAULT_STELLAR_NETWORK,

  /**
   * SLA Calculator contract ID displayed on the wallet card for audit.
   * Deliberately left `undefined` when unset, so the wallet card renders an
   * explicit "not configured" state rather than a placeholder that could be
   * mistaken for a real Stellar contract address.
   */
  SLA_CONTRACT_ID: readEnv("NEXT_PUBLIC_SLA_CONTRACT_ID"),

  /** USDC token address used for payment escrow */
  USDC_TOKEN_ADDRESS:
    readEnv("NEXT_PUBLIC_USDC_TOKEN_ADDRESS") || "",

  /** APEX token address */
  APEX_TOKEN_ADDRESS:
    readEnv("NEXT_PUBLIC_APEX_TOKEN_ADDRESS") || "",

  /** Application URL used for metadata and canonical links */
  APP_URL: resolveAppUrl(),
};