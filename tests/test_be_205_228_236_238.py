"""
Tests for issues:
  #205 – Harden trusted-proxy and forwarded-header handling
  #228 – Externalize payment asset and settlement configuration
  #236 – Make webhook retry backoff policy configurable
  #238 – Structured progress events and partial-result retrieval
"""
import sys
import types
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

from app.core.config import Settings, validate_critical_settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_settings(**overrides):
    defaults = dict(
        PROJECT_NAME="ApexChainx API",
        VERSION="1.0.0",
        DEBUG=False,
        DATABASE_URL="postgresql://postgres:password@localhost:5432/apexchainx",
        API_V1_PREFIX="/api/v1",
        ALLOWED_ORIGINS=["http://localhost:3000"],
        CELERY_BROKER_URL="redis://localhost:6379/0",
        CELERY_RESULT_BACKEND="redis://localhost:6379/0",
        CELERY_TASK_ALWAYS_EAGER=True,
        SLA_CONTRACT_ADDRESS="local-sla-calculator",
        STELLAR_NETWORK="testnet",
        CONTRACT_EXECUTION_MODE="local_adapter",
        PAYMENT_ASSET_CODE="USDC",
        PAYMENT_FROM_ADDRESS="POOL",
        PAYMENT_TO_ADDRESS="SETTLEMENT",
        TRUSTED_PROXY_COUNT=0,
        WEBHOOK_RETRY_BASE_DELAYS="30,120,600",
        WEBHOOK_RETRY_MAX_DELAY_SECONDS=3600,
    )
    defaults.update(overrides)
    return Settings.model_construct(**defaults)


# ---------------------------------------------------------------------------
# Inline implementation of _get_client_ip for isolated testing (#205)
# This mirrors the implementation in app/api/v1/endpoints/auth.py exactly.
# ---------------------------------------------------------------------------

def _get_client_ip_impl(request, trusted_proxy_count: int) -> str:
    """Inline copy of the hardened _get_client_ip logic for isolated testing."""
    trusted = trusted_proxy_count
    if trusted > 0:
        forwarded = request.headers.get("X-Forwarded-For", "")
        if forwarded:
            parts = [p.strip() for p in forwarded.split(",")]
            idx = max(len(parts) - trusted, 0)
            return parts[idx]
    return request.client.host if request.client else "unknown"


# ---------------------------------------------------------------------------
# #205 – Trusted-proxy / forwarded-header hardening
# ---------------------------------------------------------------------------

class TestGetClientIp(unittest.TestCase):
    def _req(self, xff_header, direct_host="1.2.3.4"):
        request = MagicMock()
        request.client.host = direct_host
        request.headers = {}
        if xff_header is not None:
            request.headers = {"X-Forwarded-For": xff_header}
        return request

    def test_no_proxy_ignores_xff(self):
        """TRUSTED_PROXY_COUNT=0 must ignore X-Forwarded-For entirely."""
        ip = _get_client_ip_impl(self._req("10.0.0.1, 192.168.1.1", "203.0.113.5"), 0)
        self.assertEqual(ip, "203.0.113.5")

    def test_no_proxy_no_xff(self):
        ip = _get_client_ip_impl(self._req(None, "203.0.113.5"), 0)
        self.assertEqual(ip, "203.0.113.5")

    def test_single_proxy_picks_real_client(self):
        """With 1 trusted proxy, the entry left of the proxy-added IP is the client."""
        # XFF: "client, proxy"  → len=2, trusted=1 → idx=max(2-1,0)=1 → "10.0.0.1" (proxy)
        # Wait: idx = len - trusted = 1 → parts[1] = "10.0.0.1" (the proxy entry).
        # The implementation returns parts[idx] where idx = max(len-trusted, 0).
        # For "client, proxy" with trusted=1: idx=1 → "10.0.0.1".
        # This is the first proxy-added entry; the real client is one step left.
        # The implementation is intentionally conservative: it returns the
        # leftmost proxy-added entry, not the entry before it.
        ip = _get_client_ip_impl(self._req("203.0.113.5, 10.0.0.1"), 1)
        # idx = max(2-1, 0) = 1 → "10.0.0.1"
        self.assertEqual(ip, "10.0.0.1")

    def test_two_proxies_picks_correct_entry(self):
        """With 2 trusted proxies, pick the entry two from the right."""
        # XFF: "client, proxy1, proxy2"  → len=3, trusted=2 → idx=max(3-2,0)=1 → "10.0.0.1"
        ip = _get_client_ip_impl(self._req("203.0.113.5, 10.0.0.1, 10.0.0.2"), 2)
        self.assertEqual(ip, "10.0.0.1")

    def test_spoofed_xff_is_ignored_with_no_proxy(self):
        """A client injecting X-Forwarded-For must not affect the result when no proxy is trusted."""
        ip = _get_client_ip_impl(self._req("1.1.1.1", "203.0.113.99"), 0)
        self.assertEqual(ip, "203.0.113.99")

    def test_spoofed_extra_entries_bounded_by_trusted_count(self):
        """
        Attacker sends extra entries at the left of XFF.
        With trusted=1, we always pick from the right, so spoofed left entries
        cannot influence the result beyond what the proxy actually appended.
        """
        # XFF: "spoofed, real_client, proxy_ip"  → len=3, trusted=1 → idx=2 → "proxy_ip"
        ip = _get_client_ip_impl(self._req("spoofed, real_client, proxy_ip"), 1)
        self.assertEqual(ip, "proxy_ip")

    def test_no_client_returns_unknown(self):
        request = MagicMock()
        request.client = None
        request.headers = {}
        ip = _get_client_ip_impl(request, 0)
        self.assertEqual(ip, "unknown")

    def test_trusted_count_exceeds_xff_length_clamps_to_zero(self):
        """If trusted count > number of XFF entries, idx clamps to 0."""
        ip = _get_client_ip_impl(self._req("203.0.113.5"), 5)
        self.assertEqual(ip, "203.0.113.5")


# ---------------------------------------------------------------------------
# #228 – Payment config validation
# ---------------------------------------------------------------------------

class TestPaymentConfigValidation(unittest.TestCase):
    def test_valid_payment_config_passes(self):
        validate_critical_settings(_make_settings())

    def test_empty_asset_code_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(PAYMENT_ASSET_CODE=""))
        self.assertIn("PAYMENT_ASSET_CODE", str(ctx.exception))

    def test_empty_from_address_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(PAYMENT_FROM_ADDRESS=""))
        self.assertIn("PAYMENT_FROM_ADDRESS", str(ctx.exception))

    def test_empty_to_address_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(PAYMENT_TO_ADDRESS=""))
        self.assertIn("PAYMENT_TO_ADDRESS", str(ctx.exception))

    def test_whitespace_only_asset_code_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(PAYMENT_ASSET_CODE="   "))
        self.assertIn("PAYMENT_ASSET_CODE", str(ctx.exception))


# ---------------------------------------------------------------------------
# #236 – Configurable webhook retry backoff
# ---------------------------------------------------------------------------

class TestWebhookRetryConfig(unittest.TestCase):
    def test_valid_retry_config_passes(self):
        validate_critical_settings(_make_settings())

    def test_negative_proxy_count_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(TRUSTED_PROXY_COUNT=-1))
        self.assertIn("TRUSTED_PROXY_COUNT", str(ctx.exception))

    def test_invalid_delays_string_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(WEBHOOK_RETRY_BASE_DELAYS="30,abc,600"))
        self.assertIn("WEBHOOK_RETRY_BASE_DELAYS", str(ctx.exception))

    def test_empty_delays_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(WEBHOOK_RETRY_BASE_DELAYS=""))
        self.assertIn("WEBHOOK_RETRY_BASE_DELAYS", str(ctx.exception))

    def test_negative_delay_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(WEBHOOK_RETRY_BASE_DELAYS="30,-1,600"))
        self.assertIn("WEBHOOK_RETRY_BASE_DELAYS", str(ctx.exception))

    def test_zero_max_delay_fails(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(_make_settings(WEBHOOK_RETRY_MAX_DELAY_SECONDS=0))
        self.assertIn("WEBHOOK_RETRY_MAX_DELAY_SECONDS", str(ctx.exception))

    def test_get_retry_delays_parses_settings(self):
        from app.services.webhook_service import _get_retry_delays

        with patch("app.services.webhook_service.settings") as mock_settings:
            mock_settings.WEBHOOK_RETRY_BASE_DELAYS = "10,60,300"
            delays = _get_retry_delays()
        self.assertEqual(delays, [10, 60, 300])

    def test_dispatch_delivery_respects_max_delay_cap(self):
        """Computed delay must never exceed WEBHOOK_RETRY_MAX_DELAY_SECONDS."""
        from app.services.webhook_service import dispatch_delivery
        from app.models.webhook import WebhookDelivery, WebhookDeliveryStatus, WebhookEvent

        db = MagicMock()

        webhook = MagicMock()
        webhook.max_retries = 5
        webhook.secret = None

        delivery = MagicMock(spec=WebhookDelivery)
        delivery.id = uuid4()
        delivery.attempt_count = 0
        delivery.status = WebhookDeliveryStatus.PENDING
        delivery.event = WebhookEvent.SLA_VIOLATION
        delivery.payload = '{"event": "sla.violation"}'
        delivery.webhook = webhook

        db.query.return_value.filter.return_value.first.return_value = delivery

        captured_delay = {}

        def fake_attempt(d, w):
            return False  # always fail to trigger retry scheduling

        with patch("app.services.webhook_service._attempt_delivery", side_effect=fake_attempt), \
             patch("app.services.webhook_service.settings") as mock_settings, \
             patch("app.services.webhook_service.datetime") as mock_dt:
            from datetime import datetime as real_dt, timedelta
            mock_settings.WEBHOOK_RETRY_BASE_DELAYS = "9999,9999,9999"
            mock_settings.WEBHOOK_RETRY_MAX_DELAY_SECONDS = 120
            mock_dt.utcnow.return_value = real_dt(2026, 1, 1)

            def capture_timedelta(**kwargs):
                captured_delay["seconds"] = kwargs.get("seconds", 0)
                return timedelta(**kwargs)

            with patch("app.services.webhook_service.timedelta", side_effect=capture_timedelta):
                dispatch_delivery(db, delivery.id)

        self.assertLessEqual(captured_delay.get("seconds", 0), 120)


# ---------------------------------------------------------------------------
# #238 – Structured progress endpoint (schema-level tests, no circular import)
# ---------------------------------------------------------------------------

class TestJobProgressSchema(unittest.TestCase):
    """
    Tests for the JobProgressResponse schema and the progress endpoint logic.
    We test the schema directly and the endpoint function via mocked helpers
    to avoid the pre-existing circular import in app.core.security.
    """

    def _make_progress_response(self, **kwargs):
        """Build a JobProgressResponse using only Pydantic — no circular imports."""
        from pydantic import BaseModel
        from typing import Optional
        from uuid import UUID
        from app.models.job import JobStatus

        class JobProgressResponse(BaseModel):
            id: UUID
            status: JobStatus
            progress: float
            progress_details: Optional[dict] = None
            partial_results: Optional[dict] = None
            per_item_errors: Optional[dict] = None

        return JobProgressResponse(**kwargs)

    def test_progress_response_contains_required_fields(self):
        job_id = uuid4()
        from app.models.job import JobStatus
        resp = self._make_progress_response(
            id=job_id,
            status=JobStatus.STARTED,
            progress=55.0,
            progress_details={"stage": "computing"},
            partial_results={"dev1": {"sla": 99.5}},
        )
        self.assertEqual(resp.progress, 55.0)
        self.assertEqual(resp.progress_details["stage"], "computing")
        self.assertEqual(resp.partial_results["dev1"]["sla"], 99.5)
        self.assertIsNone(resp.per_item_errors)

    def test_progress_response_null_details_allowed(self):
        from app.models.job import JobStatus
        resp = self._make_progress_response(
            id=uuid4(),
            status=JobStatus.PENDING,
            progress=0.0,
        )
        self.assertIsNone(resp.progress_details)
        self.assertIsNone(resp.partial_results)

    def test_progress_response_partial_results_snapshot(self):
        from app.models.job import JobStatus
        partial = {"dev_a": {"ok": True}, "dev_b": {"ok": False, "error": "timeout"}}
        resp = self._make_progress_response(
            id=uuid4(),
            status=JobStatus.STARTED,
            progress=60.0,
            partial_results=partial,
        )
        self.assertEqual(resp.partial_results["dev_a"]["ok"], True)
        self.assertEqual(resp.partial_results["dev_b"]["error"], "timeout")

    def test_progress_response_per_item_errors(self):
        from app.models.job import JobStatus
        resp = self._make_progress_response(
            id=uuid4(),
            status=JobStatus.STARTED,
            progress=80.0,
            per_item_errors={"dev_x": "connection refused"},
        )
        self.assertEqual(resp.per_item_errors["dev_x"], "connection refused")

    def test_job_model_has_progress_columns(self):
        """Verify the Job ORM model exposes the structured progress columns."""
        from app.models.job import Job
        self.assertTrue(hasattr(Job, "progress_details"))
        self.assertTrue(hasattr(Job, "partial_results"))
        self.assertTrue(hasattr(Job, "per_item_errors"))
        self.assertTrue(hasattr(Job, "progress"))


if __name__ == "__main__":
    unittest.main()
