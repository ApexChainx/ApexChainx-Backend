from __future__ import annotations

from typing import Any, Final

__all__ = [
    "CANONICAL_SEVERITIES",
    "SEVERITY_RANKS",
    "UNKNOWN_SEVERITY",
    "normalize_severity",
    "severity_rank",
    "compare_severity",
    "is_valid_severity",
]

# Canonical severity vocabulary, ordered from most to least severe.
# Aligned with app/sla_operations/services/severity_distribution.py.
CANONICAL_SEVERITIES: Final[tuple[str, ...]] = (
    "critical",
    "high",
    "medium",
    "low",
)

# Fallback value used when a severity cannot be determined.
# Aligned with severity_distribution_report.py.
UNKNOWN_SEVERITY: Final[str] = "unknown"

# Numeric ranking where a higher number means a more severe outage.
# "critical" is the highest rank; "unknown" is the lowest.
SEVERITY_RANKS: Final[dict[str, int]] = {
    "critical": 4,
    "high": 3,
    "medium": 2,
    "low": 1,
    UNKNOWN_SEVERITY: 0,
}

# Aliases mapped to canonical severities. Keys are expected to be
# already trimmed and lower-cased by the caller.
_ALIASES: Final[dict[str, str]] = {
    # Canonical values map to themselves.
    "critical": "critical",
    "high": "high",
    "medium": "medium",
    "low": "low",
    # Sev-style aliases.
    "sev1": "critical",
    "sev2": "high",
    "sev3": "medium",
    "sev4": "low",
    # Common synonyms.
    "crit": "critical",
    "urgent": "critical",
    "blocker": "critical",
    "p1": "critical",
    "p2": "high",
    "p3": "medium",
    "p4": "low",
    "moderate": "medium",
    "minor": "low",
    "trivial": "low",
    "info": "low",
    "informational": "low",
    # Unknown fallback maps to itself.
    UNKNOWN_SEVERITY: UNKNOWN_SEVERITY,
}


def _coerce_to_str(value: Any) -> str | None:
    """Return a trimmed, lower-cased string for ``value`` or ``None``.

    ``None`` and non-string inputs yield ``None``. Strings are stripped of
    surrounding whitespace and lower-cased. Empty strings after trimming
    also yield ``None``.
    """
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized:
        return None
    return normalized


def normalize_severity(value: Any, default: str | None = None) -> str:
    """Normalize an outage severity value to a canonical severity string.

    Parameters
    ----------
    value:
        The raw severity input. May be any type; strings are trimmed and
        lower-cased before lookup. Common aliases (e.g. ``"sev1"``,
        ``"P2"``, ``"info"``) are mapped to canonical values.
    default:
        Optional fallback returned when ``value`` is missing, empty, or
        unrecognized. When provided, it is itself normalized and must be a
        canonical severity or ``"unknown"``; otherwise a ``ValueError`` is
        raised.

    Returns
    -------
    str
        One of ``"critical"``, ``"high"``, ``"medium"``, ``"low"``, or
        ``"unknown"``.

    Raises
    ------
    ValueError
        If ``value`` cannot be normalized and no valid ``default`` is
        provided, or if ``default`` is not a recognized severity.

    Notes
    -----
    ``None``, empty/whitespace-only strings, and non-string inputs are
    treated as missing values and fall back to ``default`` (or raise).
    """
    normalized_default: str | None = None
    if default is not None:
        candidate_default = _coerce_to_str(default)
        if candidate_default is None or candidate_default not in _ALIASES:
            raise ValueError(f"Invalid default severity: {default!r}")
        normalized_default = _ALIASES[candidate_default]

    normalized = _coerce_to_str(value)
    if normalized is None:
        if normalized_default is not None:
            return normalized_default
        raise ValueError(f"Invalid severity value: {value!r}")

    canonical = _ALIASES.get(normalized)
    if canonical is not None:
        return canonical

    if normalized_default is not None:
        return normalized_default
    raise ValueError(f"Unknown severity value: {value!r}")


def severity_rank(value: Any, default: str | None = None) -> int:
    """Return the numeric rank of a severity, where higher means more severe.

    Parameters
    ----------
    value:
        The raw severity input, normalized via :func:`normalize_severity`.
    default:
        Optional fallback severity used when ``value`` is missing or
        unrecognized. See :func:`normalize_severity`.

    Returns
    -------
    int
        ``4`` for ``"critical"``, ``3`` for ``"high"``, ``2`` for
        ``"medium"``, ``1`` for ``"low"``, and ``0`` for ``"unknown"``.

    Raises
    ------
    ValueError
        If ``value`` cannot be normalized and no valid ``default`` is
        provided.
    """
    canonical = normalize_severity(value, default=default)
    return SEVERITY_RANKS[canonical]


def compare_severity(
    a: Any,
    b: Any,
    default: str | None = None,
) -> int:
    """Compare two severity values by rank.

    Parameters
    ----------
    a:
        First severity input, normalized via :func:`normalize_severity`.
    b:
        Second severity input, normalized via :func:`normalize_severity`.
    default:
        Optional fallback severity applied to either input that is missing
        or unrecognized. See :func:`normalize_severity`.

    Returns
    -------
    int
        ``-1`` if ``a`` is less severe than ``b``, ``0`` if they are equal
        in rank, and ``1`` if ``a`` is more severe than ``b``.

    Raises
    ------
    ValueError
        If either input cannot be normalized and no valid ``default`` is
        provided.
    """
    rank_a = severity_rank(a, default=default)
    rank_b = severity_rank(b, default=default)
    if rank_a < rank_b:
        return -1
    if rank_a > rank_b:
        return 1
    return 0


def is_valid_severity(value: Any) -> bool:
    """Return whether ``value`` normalizes to a known severity.

    Parameters
    ----------
    value:
        The raw severity input to validate.

    Returns
    -------
    bool
        ``True`` if ``value`` is a recognized canonical severity or alias
        (including ``"unknown"``); ``False`` for ``None``, empty strings,
        non-string inputs, and unrecognized values.
    """
    normalized = _coerce_to_str(value)
    if normalized is None:
        return False
    return normalized in _ALIASES
</parameter>