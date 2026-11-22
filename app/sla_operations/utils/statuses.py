from __future__ import annotations

from typing import Dict, Final, Iterable, Mapping, Optional


class StatusNormalizationError(ValueError):
    """Raised when a status value cannot be normalized to a known canonical value.

    This is a subclass of :class:`ValueError` so callers may catch either the
    specific error type or a generic ``ValueError``.

    Attributes:
        value: The original (unmodified) input value that failed normalization.
        domain: A short label describing the status domain (e.g. ``"outage"``).
        allowed: A sorted tuple of the canonical values accepted for the domain.
    """

    def __init__(
        self,
        value: object,
        domain: str,
        allowed: Iterable[str],
    ) -> None:
        self.value = value
        self.domain = domain
        self.allowed = tuple(sorted(allowed))
        super().__init__(
            f"Invalid {domain} status {value!r}; expected one of {self.allowed}"
        )


# Canonical outage lifecycle statuses.
OUTAGE_STATUSES: Final[tuple[str, ...]] = (
    "open",
    "acknowledged",
    "investigating",
    "mitigated",
    "resolved",
    "closed",
)

# Canonical payment/settlement statuses.
PAYMENT_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "processing",
    "succeeded",
    "failed",
    "refunded",
    "cancelled",
)

# Canonical webhook delivery statuses.
WEBHOOK_STATUSES: Final[tuple[str, ...]] = (
    "pending",
    "delivering",
    "delivered",
    "failed",
    "retrying",
    "dead",
)


def _build_alias_map(
    canonical: Iterable[str],
    aliases: Mapping[str, str],
) -> Dict[str, str]:
    """Build a normalized lookup map from canonical values plus aliases.

    Args:
        canonical: Canonical status strings. Each is mapped to itself.
        aliases: Mapping of alias -> canonical value. Aliases are normalized
            (trimmed and case-folded) before being stored.

    Returns:
        A dict mapping normalized keys to canonical values.
    """
    lookup: Dict[str, str] = {value: value for value in canonical}
    for alias, target in aliases.items():
        lookup[alias.strip().casefold()] = target
    return lookup


_OUTAGE_ALIASES: Final[Dict[str, str]] = {
    "new": "open",
    "created": "open",
    "reported": "open",
    "ack": "acknowledged",
    "acknowledge": "acknowledged",
    "in_progress": "investigating",
    "in-progress": "investigating",
    "investigation": "investigating",
    "fixing": "investigating",
    "mitigation": "mitigated",
    "fixed": "resolved",
    "resolved_": "resolved",
    "done": "resolved",
    "complete": "resolved",
    "completed": "resolved",
    "cancelled": "closed",
    "canceled": "closed",
}

_PAYMENT_ALIASES: Final[Dict[str, str]] = {
    "queued": "pending",
    "created": "pending",
    "in_progress": "processing",
    "in-progress": "processing",
    "processing_": "processing",
    "success": "succeeded",
    "succeed": "succeeded",
    "ok": "succeeded",
    "complete": "succeeded",
    "completed": "succeeded",
    "error": "failed",
    "failure": "failed",
    "declined": "failed",
    "refund": "refunded",
    "refunded_": "refunded",
    "canceled": "cancelled",
    "void": "cancelled",
    "voided": "cancelled",
}

_WEBHOOK_ALIASES: Final[Dict[str, str]] = {
    "queued": "pending",
    "created": "pending",
    "sending": "delivering",
    "in_progress": "delivering",
    "in-progress": "delivering",
    "success": "delivered",
    "succeeded": "delivered",
    "ok": "delivered",
    "complete": "delivered",
    "completed": "delivered",
    "error": "failed",
    "failure": "failed",
    "retry": "retrying",
    "retries": "retrying",
    "dead_letter": "dead",
    "dead-letter": "dead",
    "exhausted": "dead",
}

_OUTAGE_LOOKUP: Final[Dict[str, str]] = _build_alias_map(
    OUTAGE_STATUSES, _OUTAGE_ALIASES
)
_PAYMENT_LOOKUP: Final[Dict[str, str]] = _build_alias_map(
    PAYMENT_STATUSES, _PAYMENT_ALIASES
)
_WEBHOOK_LOOKUP: Final[Dict[str, str]] = _build_alias_map(
    WEBHOOK_STATUSES, _WEBHOOK_ALIASES
)


def _normalize(
    value: object,
    lookup: Mapping[str, str],
    domain: str,
    allowed: Iterable[str],
    default: Optional[str],
) -> str:
    """Shared normalization routine.

    Args:
        value: Raw input value.
        lookup: Normalized-key -> canonical-value mapping.
        domain: Domain label used in error messages.
        allowed: Canonical values for the domain (used in error messages).
        default: If not ``None``, returned for ``None``/empty/unknown inputs
            instead of raising.

    Returns:
        The canonical status string, or ``default`` when provided.

    Raises:
        StatusNormalizationError: If the input cannot be normalized and no
            ``default`` was supplied.
    """
    if value is None:
        if default is not None:
            return default
        raise StatusNormalizationError(value, domain, allowed)

    if not isinstance(value, str):
        if default is not None:
            return default
        raise StatusNormalizationError(value, domain, allowed)

    key = value.strip().casefold()
    if not key:
        if default is not None:
            return default
        raise StatusNormalizationError(value, domain, allowed)

    canonical = lookup.get(key)
    if canonical is not None:
        return canonical

    if default is not None:
        return default
    raise StatusNormalizationError(value, domain, allowed)


def normalize_outage_status(
    value: object,
    *,
    default: Optional[str] = None,
) -> str:
    """Normalize an outage lifecycle status to its canonical form.

    The input is trimmed and case-folded, then mapped through a table of known
    aliases (e.g. ``"ack"`` -> ``"acknowledged"``, ``"fixed"`` -> ``"resolved"``).

    Args:
        value: Raw status value. Expected to be a string but any object is
            accepted and handled explicitly.
        default: Optional safe default. When provided, it is returned for
            ``None``, empty/whitespace-only strings, non-string inputs, and
            unknown values instead of raising. The default is returned as-is
            and is *not* validated against the canonical set.

    Returns:
        One of :data:`OUTAGE_STATUSES`.

    Raises:
        StatusNormalizationError: If the value cannot be normalized and no
            ``default`` was supplied.

    Edge behavior:
        - ``None`` -> raises (or returns ``default``).
        - ``""`` / ``"   "`` -> raises (or returns ``default``).
        - Unknown values -> raises (or returns ``default``).
    """
    return _normalize(
        value, _OUTAGE_LOOKUP, "outage", OUTAGE_STATUSES, default
    )


def normalize_payment_status(
    value: object,
    *,
    default: Optional[str] = None,
) -> str:
    """Normalize a payment/settlement status to its canonical form.

    The input is trimmed and case-folded, then mapped through a table of known
    aliases (e.g. ``"success"`` -> ``"succeeded"``, ``"error"`` -> ``"failed"``).

    Args:
        value: Raw status value. Expected to be a string but any object is
            accepted and handled explicitly.
        default: Optional safe default. When provided, it is returned for
            ``None``, empty/whitespace-only strings, non-string inputs, and
            unknown values instead of raising. The default is returned as-is
            and is *not* validated against the canonical set.

    Returns:
        One of :data:`PAYMENT_STATUSES`.

    Raises:
        StatusNormalizationError: If the value cannot be normalized and no
            ``default`` was supplied.

    Edge behavior:
        - ``None`` -> raises (or returns ``default``).
        - ``""`` / ``"   "`` -> raises (or returns ``default``).
        - Unknown values -> raises (or returns ``default``).
    """
    return _normalize(
        value, _PAYMENT_LOOKUP, "payment", PAYMENT_STATUSES, default
    )


def normalize_webhook_status(
    value: object,
    *,
    default: Optional[str] = None,
) -> str:
    """Normalize a webhook delivery status to its canonical form.

    The input is trimmed and case-folded, then mapped through a table of known
    aliases (e.g. ``"sending"`` -> ``"delivering"``, ``"exhausted"`` -> ``"dead"``).

    Args:
        value: Raw status value. Expected to be a string but any object is
            accepted and handled explicitly.
        default: Optional safe default. When provided, it is returned for
            ``None``, empty/whitespace-only strings, non-string inputs, and
            unknown values instead of raising. The default is returned as-is
            and is *not* validated against the canonical set.

    Returns:
        One of :data:`WEBHOOK_STATUSES`.

    Raises:
        StatusNormalizationError: If the value cannot be normalized and no
            ``default`` was supplied.

    Edge behavior:
        - ``None`` -> raises (or returns ``default``).
        - ``""`` / ``"   "`` -> raises (or returns ``default``).
        - Unknown values -> raises (or returns ``default``).
    """
    return _normalize(
        value, _WEBHOOK_LOOKUP, "webhook", WEBHOOK_STATUSES, default
    )


_DOMAIN_DISPATCH: Final[Dict[str, tuple[Mapping[str, str], tuple[str, ...]]]] = {
    "outage": (_OUTAGE_LOOKUP, OUTAGE_STATUSES),
    "payment": (_PAYMENT_LOOKUP, PAYMENT_STATUSES),
    "webhook": (_WEBHOOK_LOOKUP, WEBHOOK_STATUSES),
}


def normalize_status(
    value: object,
    domain: str,
    *,
    default: Optional[str] = None,
) -> str:
    """Normalize a status value for a given domain.

    Dispatches to the domain-specific normalizer. This is a convenience entry
    point for callers that select the domain dynamically.

    Args:
        value: Raw status value. Expected to be a string but any object is
            accepted and handled explicitly.
        domain: One of ``"outage"``, ``"payment"``, or ``"webhook"``
            (case-insensitive, surrounding whitespace ignored).
        default: Optional safe default forwarded to the domain normalizer.
            When provided, it is returned for ``None``, empty/whitespace-only
            strings, non-string inputs, and unknown values instead of raising.

    Returns:
        The canonical status string for the domain.

    Raises:
        StatusNormalizationError: If ``value`` cannot be normalized and no
            ``default`` was supplied.
        ValueError: If ``domain`` is not a recognized domain.

    Edge behavior:
        - Unknown ``domain`` -> raises :class:`ValueError` (never silently
          falls back), regardless of ``default``.
        - All other edge behavior mirrors the domain-specific normalizers.
    """
    if not isinstance(domain, str):
        raise ValueError(
            f"Unknown status domain {domain!r}; expected one of "
            f"{tuple(sorted(_DOMAIN_DISPATCH))}"
        )

    key = domain.strip().casefold()
    entry = _DOMAIN_DISPATCH.get(key)
    if entry is None:
        raise ValueError(
            f"Unknown status domain {domain!r}; expected one of "
            f"{tuple(sorted(_DOMAIN_DISPATCH))}"
        )

    lookup, allowed = entry
    return _normalize(value, lookup, key, allowed, default)


__all__ = [
    "StatusNormalizationError",
    "OUTAGE_STATUSES",
    "PAYMENT_STATUSES",
    "WEBHOOK_STATUSES",
    "normalize_outage_status",
    "normalize_payment_status",
    "normalize_webhook_status",
    "normalize_status",
]