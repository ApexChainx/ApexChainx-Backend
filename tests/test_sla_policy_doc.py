"""Drift guard for the SLA threshold catalog (#90).

The acceptance criterion for docs/SLA_POLICY.md is "document updated when
`sla_config` changes". This test parses the catalog table out of the
document and asserts it matches `SLA_CONFIG` exactly, so CI fails
whenever the doc and the runtime configuration drift apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.sla.config import SLA_CONFIG

DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "SLA_POLICY.md"
SEVERITIES = ["critical", "high", "medium", "low"]


def _parse_catalog(path: Path) -> dict[str, dict[str, int]]:
    """Extract severity -> {threshold, penalty, reward} from the catalog table."""
    rows: dict[str, dict[str, int]] = {}
    in_catalog = False

    for line in path.read_text().splitlines():
        stripped = line.strip()

        if stripped.startswith("#") and "Threshold catalog" in stripped:
            in_catalog = True
            continue

        if not in_catalog:
            continue

        # The catalog table lives at the top of the document; a new
        # heading ends the section.
        if stripped.startswith("#"):
            break

        if not stripped.startswith("|"):
            continue

        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        severity = cells[0] if cells else ""
        if severity in SEVERITIES:
            rows[severity] = {
                "threshold_minutes": int(cells[1]),
                "penalty_per_minute": int(cells[2]),
                "reward_base": int(cells[3]),
            }

    return rows


def test_catalog_exists_and_covers_all_severities() -> None:
    """The document exists and lists every configured severity."""
    assert DOC_PATH.exists(), "docs/SLA_POLICY.md not found"
    catalog = _parse_catalog(DOC_PATH)
    assert set(catalog) == set(
        SLA_CONFIG
    ), f"catalog severities {sorted(catalog)} != config severities {sorted(SLA_CONFIG)}"


@pytest.mark.parametrize("severity", SEVERITIES)
def test_catalog_matches_config(severity: str) -> None:
    """Every row in the catalog matches the runtime SLA_CONFIG values."""
    catalog = _parse_catalog(DOC_PATH)
    expected = SLA_CONFIG[severity]
    actual = catalog.get(severity)
    assert actual is not None, f"severity {severity!r} missing from the catalog table"

    for key in ("threshold_minutes", "penalty_per_minute", "reward_base"):
        assert actual[key] == expected[key], (
            f"docs/SLA_POLICY.md {severity}.{key} = {actual[key]} but "
            f"SLA_CONFIG = {expected[key]} — update the catalog when sla_config changes (#90)"
        )
