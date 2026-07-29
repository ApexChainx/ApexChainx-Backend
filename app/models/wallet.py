import re
from datetime import datetime

from pydantic import BaseModel, Field, field_validator

_STELLAR_PUBLIC_KEY_RE = re.compile(r"^G[A-Z2-7]{55}$")


class Wallet(BaseModel):
    user_id: str
    public_key: str
    created_at: datetime
    last_updated: datetime
    funded: bool = False
    active: bool = True
    trustline_ready: bool = False
    cached_at: datetime | None = None  # when data was last cached; None means never refreshed
    # BE-033: Cache freshness indicator
    cache_status: str = "fresh"  # "fresh", "stale", or "live"


class WalletCreateRequest(BaseModel):
    user_id: str = Field(..., min_length=1)


class WalletLinkRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    public_key: str = Field(..., min_length=2)
    funded: bool = False
    trustline_ready: bool = False

    @field_validator("public_key")
    @classmethod
    def validate_stellar_public_key(cls, v: str) -> str:
        if not _STELLAR_PUBLIC_KEY_RE.match(v):
            raise ValueError(
                "public_key must be a valid Stellar public key (starts with G, 56 characters, base32 alphabet)"
            )
        return v


class WalletCreateResponse(Wallet):
    message: str


class AssetBalance(BaseModel):
    balance: str
    asset_type: str
    asset_code: str | None = None
    asset_issuer: str | None = None


class WalletBalanceResponse(BaseModel):
    address: str
    balances: dict[str, AssetBalance]
    last_updated: datetime
    # BE-033: Cache metadata
    cache_status: str = "fresh"
    cache_ttl_seconds: int | None = None
    cached_at: datetime | None = None


class WalletStatusResponse(BaseModel):
    user_id: str
    public_key: str
    funded: bool
    trustline_ready: bool
    usable: bool
    active: bool
    last_updated: datetime
    # BE-033: Cache metadata for frontend polling decisions
    cache_status: str = "fresh"  # "fresh", "stale", or "live"
    cache_ttl_seconds: int | None = None  # seconds until cache expires
    cached_at: datetime | None = None  # when this data was cached


class WalletTrustlineResponse(BaseModel):
    user_id: str
    public_key: str
    trustline_ready: bool
    trustline_error: str | None = None
    # BE-033: Cache metadata
    cache_status: str = "fresh"
    cached_at: datetime | None = None


class WalletFundingStateResponse(BaseModel):
    user_id: str
    public_key: str
    funded: bool
    funding_error: str | None = None
    # BE-033: Cache metadata
    cache_status: str = "fresh"
    cached_at: datetime | None = None
