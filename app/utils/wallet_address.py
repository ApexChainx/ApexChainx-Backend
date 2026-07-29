from __future__ import annotations

import re
from dataclasses import dataclass

_STELLAR_PUBLIC_KEY_RE = re.compile(r"^G[A-Z2-7]{55}$")

_KEY_MIN_LEN = 56
_KEY_MAX_LEN = 56


@dataclass(frozen=True)
class NormalizedAddress:
    """Immutable value object representing a validated, canonical wallet address."""

    value: str

    def __str__(self) -> str:
        return self.value


class WalletAddressError(ValueError):
    """Raised when an address fails validation or normalization."""

    def __init__(self, raw: str, reason: str) -> None:
        self.raw = raw
        self.reason = reason
        super().__init__(f"Invalid wallet address {raw!r}: {reason}")


def normalize(raw: str) -> NormalizedAddress:
    """Validate and normalize a raw wallet address string.

    Steps:
      1. Strip surrounding whitespace.
      2. Uppercase (Stellar addresses are case-insensitive in user input,
         but the canonical form is uppercase).
      3. Validate against the Stellar ed25519 public-key grammar.

    Returns a ``NormalizedAddress`` on success.
    Raises ``WalletAddressError`` with a descriptive message on failure.
    """
    if not isinstance(raw, str):
        raise WalletAddressError(repr(raw), "address must be a string")

    stripped = raw.strip()
    if not stripped:
        raise WalletAddressError(raw, "address must not be empty")

    upper = stripped.upper()

    if len(upper) != _KEY_MAX_LEN:
        raise WalletAddressError(
            raw,
            f"Stellar public keys must be exactly {_KEY_MAX_LEN} characters " f"(got {len(upper)})",
        )

    if not upper.startswith("G"):
        raise WalletAddressError(
            raw,
            "Stellar public keys must start with 'G'",
        )

    if not _STELLAR_PUBLIC_KEY_RE.match(upper):
        raise WalletAddressError(
            raw,
            "Stellar public keys may only contain uppercase letters A-Z and digits 2-7 "
            "(base-32 alphabet, no 0/1/8/9)",
        )

    return NormalizedAddress(value=upper)


def is_valid(raw: str) -> bool:
    """Return True if *raw* is a well-formed Stellar public key, False otherwise."""
    try:
        normalize(raw)
        return True
    except WalletAddressError:
        return False
