#!/usr/bin/env python3
"""
OpenAPI drift checker — compares the committed snapshot against the live schema.

Usage:
    python scripts/openapi_diff.py

Exits 0 when there is no drift.
Exits 1 and prints a diff when the live schema has drifted from the snapshot.

To update the snapshot after an intentional schema change:
    make openapi-update
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

SNAPSHOT_PATH = Path(__file__).parent.parent / "docs" / "openapi.snapshot.json"


def load_live_schema() -> dict:
    """Load the OpenAPI schema from the running FastAPI application."""
    os.environ.setdefault("DATABASE_URL", "postgresql://user:pass@localhost:5432/apexchainx")
    os.environ.setdefault("JWT_SECRET_KEY", "openapi-drift-check-key")
    os.environ.setdefault("STELLAR_NETWORK", "testnet")
    os.environ.setdefault("CONTRACT_EXECUTION_MODE", "local_adapter")
    os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")
    os.environ.setdefault("API_V1_PREFIX", "/api/v1")
    os.environ.setdefault("ALLOWED_ORIGINS", '["http://localhost:3000"]')

    # Import must happen after env vars are set
    from app.main import app  # noqa: PLC0415

    return app.openapi()


def normalise(schema: dict) -> str:
    """Serialise schema to a canonical JSON string for comparison."""
    return json.dumps(schema, indent=2, sort_keys=True)


def diff_lines(snapshot_text: str, live_text: str) -> list[str]:
    """Return a unified diff between snapshot and live schema."""
    import difflib

    return list(
        difflib.unified_diff(
            snapshot_text.splitlines(keepends=True),
            live_text.splitlines(keepends=True),
            fromfile="docs/openapi.snapshot.json (committed)",
            tofile="live schema (app.openapi())",
            n=5,
        )
    )


def main() -> int:
    if not SNAPSHOT_PATH.exists():
        print(
            f"ERROR: Snapshot file not found at {SNAPSHOT_PATH}\n"
            "Run `make openapi-update` to generate it.",
            file=sys.stderr,
        )
        return 1

    print("Loading committed OpenAPI snapshot…")
    snapshot_text = SNAPSHOT_PATH.read_text()
    snapshot = json.loads(snapshot_text)

    print("Loading live schema from app…")
    try:
        live = load_live_schema()
    except Exception as exc:
        print(f"ERROR: Could not load live schema: {exc}", file=sys.stderr)
        return 1

    live_text = normalise(live)
    snapshot_text_normalised = normalise(snapshot)

    if snapshot_text_normalised == live_text:
        print("✅  No OpenAPI drift detected.")
        return 0

    diff = diff_lines(snapshot_text_normalised, live_text)
    print("❌  OpenAPI schema drift detected!\n")
    print("".join(diff))
    print(
        "\nThe live schema differs from the committed snapshot.\n"
        "If this change is intentional, run:\n\n"
        "    make openapi-update\n\n"
        "then commit the updated docs/openapi.snapshot.json.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
