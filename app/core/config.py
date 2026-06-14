from typing import List
from urllib.parse import urlparse

from pydantic_settings import BaseSettings


VALID_STELLAR_NETWORKS = {"testnet", "mainnet", "futurenet", "standalone"}
VALID_CONTRACT_EXECUTION_MODES = {"local_adapter", "soroban_rpc"}


class Settings(BaseSettings):
    PROJECT_NAME: str = "ApexChainx API"
    VERSION: str = "1.0.0"
    DEBUG: bool = False
    DATABASE_URL: str = "postgresql://postgres:password@localhost:5432/apexchainx"
    API_V1_PREFIX: str = "/api/v1"
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://localhost:3001"]
    CELERY_BROKER_URL: str = "redis://localhost:6379/0"
    CELERY_RESULT_BACKEND: str = "redis://localhost:6379/0"
    CELERY_TASK_ALWAYS_EAGER: bool = True
    SLA_CONTRACT_ADDRESS: str = "local-sla-calculator"
    STELLAR_NETWORK: str = "testnet"
    CONTRACT_EXECUTION_MODE: str = "local_adapter"
    PAYMENT_WEBHOOK_SECRET: str = ""
    WALLET_CACHE_TTL_SECONDS: int = 60  # how long wallet data is considered fresh
    PAYMENT_ASSET_CODE: str = "USDC"
    PAYMENT_FROM_ADDRESS: str = "SYSTEM_POOL"
    PAYMENT_TO_ADDRESS: str = "OUTAGE_SETTLEMENT"
    # Trusted-proxy settings (#205)
    # Number of trusted reverse-proxy hops in front of this app.
    # Set to 0 when running without a proxy (uses direct connection IP).
    # Set to N when N proxy hops are trusted (e.g. 1 for a single load balancer).
    # Only the Nth entry from the right of X-Forwarded-For is used, preventing
    # spoofed headers injected by untrusted clients from being trusted.
    TRUSTED_PROXY_COUNT: int = 0

    # Auth rate limiting settings
    AUTH_MAX_FAILED_ATTEMPTS: int = 5  # Max failed login attempts before lockout
    AUTH_LOCKOUT_DURATION_MINUTES: int = 15  # Lockout duration in minutes
    AUTH_RATE_LIMIT_REQUESTS: int = 10  # Max requests per window
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 300  # Rate limit window in seconds

    # Input size and payload guardrails
    MAX_REQUEST_BODY_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB max request body size
    MAX_FILE_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MB max file upload size (matches existing import limit)
    MAX_BULK_OUTAGES_COUNT: int = 1000  # Max number of outages in bulk create/import
    MAX_WEBHOOK_PAYLOAD_SIZE_BYTES: int = 1024 * 1024  # 1 MB max webhook payload size
    MAX_AFFECTED_SERVICES_COUNT: int = 100  # Max number of affected services per outage
    MAX_SITE_NAME_LENGTH: int = 255  # Max site name length
    MAX_DESCRIPTION_LENGTH: int = 5000  # Max description length
    MAX_WEBHOOK_EVENTS_COUNT: int = 50  # Max webhook events per webhook
    MAX_WEBHOOK_NAME_LENGTH: int = 255  # Max webhook name length
    MAX_WEBHOOK_URL_LENGTH: int = 2048  # Max webhook URL length

    # Webhook retry backoff policy (#236)
    # Comma-separated base delay seconds for each retry attempt.
    # e.g. "30,120,600" means 30 s on first retry, 2 min on second, 10 min on third.
    WEBHOOK_RETRY_BASE_DELAYS: str = "30,120,600"
    # Hard cap on any single computed delay (seconds) to prevent retry storms.
    WEBHOOK_RETRY_MAX_DELAY_SECONDS: int = 3600

    class Config:
        env_file = ".env"


settings = Settings()


def validate_critical_settings(config: Settings) -> None:
    errors: list[str] = []

    if not config.PROJECT_NAME.strip():
        errors.append("PROJECT_NAME must not be empty.")

    if not config.VERSION.strip():
        errors.append("VERSION must not be empty.")

    if not config.API_V1_PREFIX.startswith("/"):
        errors.append("API_V1_PREFIX must start with '/'.")

    if len(config.API_V1_PREFIX) > 1 and config.API_V1_PREFIX.endswith("/"):
        errors.append("API_V1_PREFIX must not end with '/' unless it is the root path.")

    if not config.DATABASE_URL.strip():
        errors.append("DATABASE_URL must not be empty.")
    else:
        parsed_database_url = urlparse(config.DATABASE_URL)
        if not parsed_database_url.scheme:
            errors.append("DATABASE_URL must include a valid URL scheme.")

    if not config.ALLOWED_ORIGINS:
        errors.append("ALLOWED_ORIGINS must include at least one origin.")
    else:
        invalid_origins = [
            origin
            for origin in config.ALLOWED_ORIGINS
            if not origin.startswith(("http://", "https://"))
        ]
        if invalid_origins:
            errors.append(
                "ALLOWED_ORIGINS must contain valid http or https origins."
            )

    if config.STELLAR_NETWORK not in VALID_STELLAR_NETWORKS:
        errors.append(
            "STELLAR_NETWORK must be one of: "
            + ", ".join(sorted(VALID_STELLAR_NETWORKS))
            + "."
        )

    if config.CONTRACT_EXECUTION_MODE not in VALID_CONTRACT_EXECUTION_MODES:
        errors.append(
            "CONTRACT_EXECUTION_MODE must be one of: "
            + ", ".join(sorted(VALID_CONTRACT_EXECUTION_MODES))
            + "."
        )

    if not config.CELERY_TASK_ALWAYS_EAGER:
        if not config.CELERY_BROKER_URL.strip():
            errors.append(
                "CELERY_BROKER_URL must not be empty when CELERY_TASK_ALWAYS_EAGER is false."
            )
        if not config.CELERY_RESULT_BACKEND.strip():
            errors.append(
                "CELERY_RESULT_BACKEND must not be empty when CELERY_TASK_ALWAYS_EAGER is false."
            )

    if not config.PAYMENT_ASSET_CODE.strip():
        errors.append("PAYMENT_ASSET_CODE must not be empty.")
    if not config.PAYMENT_FROM_ADDRESS.strip():
        errors.append("PAYMENT_FROM_ADDRESS must not be empty.")
    if not config.PAYMENT_TO_ADDRESS.strip():
        errors.append("PAYMENT_TO_ADDRESS must not be empty.")

    if config.TRUSTED_PROXY_COUNT < 0:
        errors.append("TRUSTED_PROXY_COUNT must be >= 0.")

    try:
        delays = [int(d.strip()) for d in config.WEBHOOK_RETRY_BASE_DELAYS.split(",") if d.strip()]
        if not delays:
            errors.append("WEBHOOK_RETRY_BASE_DELAYS must contain at least one value.")
        elif any(d < 0 for d in delays):
            errors.append("WEBHOOK_RETRY_BASE_DELAYS values must be >= 0.")
    except ValueError:
        errors.append("WEBHOOK_RETRY_BASE_DELAYS must be a comma-separated list of integers.")

    if config.WEBHOOK_RETRY_MAX_DELAY_SECONDS <= 0:
        errors.append("WEBHOOK_RETRY_MAX_DELAY_SECONDS must be > 0.")

    if errors:
        raise ValueError("Invalid startup configuration:\n- " + "\n- ".join(errors))
