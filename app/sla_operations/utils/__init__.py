"""Pure, dependency-light helper utilities for the SLA operations workspace.

This subpackage groups small, side-effect-free helper modules that support
analytics, reporting, validation, and operational insight use cases within
``app.sla_operations``. The helpers are intentionally independent of core
application state (no database, ORM, or settings imports) so they can be
reused freely by workspace services and schemas.

Modules
-------
- ``thresholds``: compare observed metrics against SLA thresholds.
- ``aggregation``: generic counts, sums, averages, and group-by helpers.
- ``identifiers``: validation and formatting of operational identifiers.
- ``statistics``: basic statistical helpers (min/max/mean/median/percentiles).

Public helpers from each module are re-exported here for convenience, e.g.::

    from app.sla_operations.utils import mean, is_within_threshold
"""

from app.sla_operations.utils.aggregation import (
    average,
    count,
    group_by,
    sum_values,
)
from app.sla_operations.utils.identifiers import (
    format_identifier,
    is_valid_identifier,
    normalize_identifier,
)
from app.sla_operations.utils.statistics import (
    maximum,
    mean,
    median,
    minimum,
    percentile,
)
from app.sla_operations.utils.thresholds import (
    compare_to_threshold,
    is_within_threshold,
)

__all__ = [
    # thresholds
    "compare_to_threshold",
    "is_within_threshold",
    # aggregation
    "average",
    "count",
    "group_by",
    "sum_values",
    # identifiers
    "format_identifier",
    "is_valid_identifier",
    "normalize_identifier",
    # statistics
    "maximum",
    "mean",
    "median",
    "minimum",
    "percentile",
]