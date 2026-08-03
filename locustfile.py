"""Locust load-testing harness for the ApexChainx Backend API (#58).

Defines realistic user flows against the FastAPI service so hot paths
(health, auth round-trip, outage listing, SLA config/analytics) can be
exercised under load and measured against the baselines recorded in
``docs/PERFORMANCE.md``.

Requirements
------------
- ``locust`` installed (``pip install -e ".[dev]"`` includes it).
- The API is running and reachable at ``LOAD_TEST_BASE_URL``.
- ``LOAD_TEST_USERNAME`` / ``LOAD_TEST_PASSWORD`` identify an existing
  account with the ``engineer`` role (the outage/SLA endpoints are gated
  by ``require_engineer``). Create one via the CLI/admin tooling first.

Usage
-----
Interactive (``http://localhost:8089``):

    locust -f locustfile.py --host http://localhost:8000

Headless for baseline recording / nightly regression (see Makefile):

    make load-test:ci
    python scripts/check_performance_regression.py --record
"""

from __future__ import annotations

import os

from locust import HttpUser, between, task

# ── Configuration (all overridable via environment) ─────────────────────

# The API prefix is injected by the deployment (see app/core/config.py).
API_PREFIX = os.environ.get("LOAD_TEST_API_PREFIX", "/api/v1")

# Credentials for the authenticated flows. The account must exist and
# carry the engineer role so every flow below is authorized.
USERNAME = os.environ.get("LOAD_TEST_USERNAME", "loadtest@example.com")
PASSWORD = os.environ.get("LOAD_TEST_PASSWORD", "LoadTestPassword123!")


class HealthCheckUser(HttpUser):
    """Unauthenticated smoke flows: verify the service is up and routing."""

    wait_time = between(1.0, 3.0)

    @task(2)
    def liveness(self) -> None:
        self.client.get("/health/liveness", name="health/liveness")

    @task(1)
    def readiness(self) -> None:
        self.client.get("/health/readiness", name="health/readiness")


class SlaApiUser(HttpUser):
    """Authenticated read-heavy flows: log in once, then query hot paths.

    Login is deliberately performed once per user (in ``on_start``)
    instead of on every request: the login endpoint is IP rate-limited
    (10 requests / 5 min) and account-lockout protected, so hammering it
    would skew the very numbers this harness is meant to measure.
    """

    wait_time = between(0.5, 2.0)

    def on_start(self) -> None:
        login = self.client.post(
            f"{API_PREFIX}/auth/login",
            json={"email": USERNAME, "password": PASSWORD},
            name="auth/login",
        )
        if login.status_code != 200:
            # Bad credentials / account lockout — this user cannot run
            # authenticated flows. Stop it so we don't trip the login
            # rate limiter further; the failure is visible in the report.
            self.stop()
            return

        token = login.json()["access_token"]
        self.client.headers.update({"Authorization": f"Bearer {token}"})

    @task(3)
    def me(self) -> None:
        """Auth round-trip: token validation + profile lookup."""
        self.client.get(f"{API_PREFIX}/auth/me", name="auth/me")

    @task(3)
    def list_outages(self) -> None:
        """Paginated outage listing (read-heavy hot path)."""
        self.client.get(f"{API_PREFIX}/outages/?page=1&page_size=20", name="outages/list")

    @task(2)
    def sla_config(self) -> None:
        """SLA policy configuration read."""
        self.client.get(f"{API_PREFIX}/sla/config", name="sla/config")

    @task(2)
    def sla_dashboard(self) -> None:
        """SLA analytics dashboard KPIs (cached, 30s TTL)."""
        self.client.get(f"{API_PREFIX}/sla/analytics/dashboard", name="sla/analytics/dashboard")
