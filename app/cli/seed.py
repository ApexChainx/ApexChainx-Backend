"""Seed the development database with synthetic data.

Usage:
    python -m app.cli.seed --outages 100 --devices 20 --payments 50 --seed 42
    python -m app.cli.seed --force  # clear existing data before seeding

Issue #99: Idempotent seeding with --force flag.
"""

from __future__ import annotations

import argparse
import logging
import random
import sys
from datetime import UTC, datetime, timedelta

logger = logging.getLogger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed the development database with synthetic data.",
    )
    parser.add_argument(
        "--outages",
        type=int,
        default=100,
        help="Number of synthetic outages to create (default: 100).",
    )
    parser.add_argument(
        "--devices",
        type=int,
        default=20,
        help="Number of synthetic devices to create (default: 20).",
    )
    parser.add_argument(
        "--payments",
        type=int,
        default=50,
        help="Number of synthetic payments to create (default: 50).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Random seed for reproducible data (default: 42).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Clear existing data before seeding (idempotent re-run).",
    )
    return parser


def _seed_devices(db, count: int, rng: random.Random) -> int:
    """Create synthetic outages (devices). Returns the number created."""
    from app.models.outage import OutageCreate
    from app.repositories.outage_repository import OutageRepository

    repo = OutageRepository(db)
    severities = ["low", "medium", "high", "critical"]
    created = 0
    for i in range(count):
        site_name = f"dev-site-{i:04d}"
        site_id = f"site-{i:04d}"
        try:
            payload = OutageCreate(
                site_name=site_name,
                site_id=site_id,
                description="Synthetic outage for dev seeding",
                severity=rng.choice(severities),
                status="resolved",
                detected_at=datetime.now(UTC) - timedelta(days=rng.randint(0, 90)),
            )
            repo.create_or_get_existing(payload)
            created += 1
        except Exception:
            db.rollback()
            continue
    return created


def _seed_outages(db, count: int, rng: random.Random, device_ids: list[str]) -> int:
    """Create additional synthetic outages. Returns the number created."""
    from app.models.outage import OutageCreate
    from app.repositories.outage_repository import OutageRepository

    repo = OutageRepository(db)
    severities = ["low", "medium", "high", "critical"]
    created = 0
    for _ in range(count):
        device_id = rng.choice(device_ids) if device_ids else f"site-{rng.randint(0, 9999):04d}"
        site_name = device_id.replace("site-", "dev-site-")
        detected_at = datetime.now(UTC) - timedelta(days=rng.randint(0, 365))
        resolved_at = detected_at + timedelta(minutes=rng.randint(5, 480))
        try:
            payload = OutageCreate(
                site_name=site_name,
                site_id=device_id,
                description=f"Synthetic outage #{_} for dev seeding",
                severity=rng.choice(severities),
                status="resolved",
                detected_at=detected_at,
                resolved_at=resolved_at,
            )
            repo.create_or_get_existing(payload)
            created += 1
        except Exception:
            db.rollback()
            continue
    return created


def _seed_payments(db, count: int, rng: random.Random, device_ids: list[str]) -> int:
    """Create synthetic payments. Returns the number created."""
    from app.repositories.payment_repository import PaymentRepository

    payment_repo = PaymentRepository(db)
    created = 0
    for _ in range(count):
        device_id = rng.choice(device_ids) if device_ids else f"site-{rng.randint(0, 9999):04d}"
        try:
            # Create a synthetic SLA result first (required for payment creation)
            from app.models.sla import SLAResult
            from app.repositories.sla_repository import SLARepository

            sla_result = SLAResult(
                outage_id=device_id,
                status="met",
                mttr_minutes=rng.randint(10, 120),
                threshold_minutes=60,
                amount=rng.randint(50, 500),
                payment_type="reward",
                rating=rng.choice(["excellent", "good"]),
                policy_version="1.0",
                threshold_source="config",
                reason_code="seed",
            )
            sla_repo = SLARepository(db)
            stored_sla = sla_repo.create_if_changed(sla_result)
            payment_repo.create_for_sla_result(device_id, stored_sla)
            created += 1
        except Exception:
            db.rollback()
            continue
    return created


def _clear_existing(db) -> dict[str, int]:
    """Remove existing data for idempotent --force runs. Returns counts cleared."""
    from sqlalchemy import func

    from app.models.orm.outage import OutageORM

    counts: dict[str, int] = {}
    try:
        result = db.query(func.count()).select_from(OutageORM).scalar()
        counts["outages"] = result or 0
        db.query(OutageORM).delete()
        db.commit()
    except Exception:
        db.rollback()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s")

    rng = random.Random(args.seed)
    logger.info(
        "Seeding dev DB with seed=%d  outages=%d  devices=%d  payments=%d  force=%s",
        args.seed,
        args.outages,
        args.devices,
        args.payments,
        args.force,
    )

    # Lazy imports so the seed tool works without a fully bootstrapped app
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        if args.force:
            logger.info("--force supplied: clearing existing data …")
            cleared = _clear_existing(db)
            logger.info("Cleared: %s", cleared)

        # Phase 1: devices
        device_ids = [f"site-{i:04d}" for i in range(args.devices)]
        dev_count = _seed_devices(db, args.devices, rng)
        logger.info("Devices created: %d", dev_count)

        # Phase 2: outages
        outage_count = _seed_outages(db, args.outages, rng, device_ids)
        logger.info("Outages created: %d", outage_count)

        # Phase 3: payments
        payment_count = _seed_payments(db, args.payments, rng, device_ids)
        logger.info("Payments created: %d", payment_count)

        logger.info("Seed complete ✓")
        return 0
    except Exception as exc:
        logger.exception("Seed failed: %s", exc)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
