"""Tests for #510: Celery eager mode is a development-only opt-in.

``CELERY_TASK_ALWAYS_EAGER`` shipped as ``True``.  Any deployment that did not
override it ran webhook dispatch, SLA snapshots, audit rotation and secret
housekeeping synchronously inside the request worker while still advertising a
Celery topology, which makes retry/backoff, the breaker and the dead-letter
queue inert.
"""

import pytest

from app.core.config import DEV_ENVIRONMENTS, Settings, validate_critical_settings

NON_DEV_ENVIRONMENTS = ["staging", "production", "prod", "ci"]
STRONG_SECRET = "k" * 48


def make_settings(**overrides) -> Settings:
    defaults = {
        "PROJECT_NAME": "ApexChainx API",
        "VERSION": "1.0.0",
        "DEBUG": False,
        "DATABASE_URL": "postgresql://postgres:password@localhost:5432/apexchainx",
        "API_V1_PREFIX": "/api/v1",
        "ALLOWED_ORIGINS": ["http://localhost:3000"],
        "CELERY_BROKER_URL": "redis://localhost:6379/0",
        "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
        "CELERY_TASK_ALWAYS_EAGER": False,
        "SLA_CONTRACT_ADDRESS": "local-sla-calculator",
        "STELLAR_NETWORK": "testnet",
        "CONTRACT_EXECUTION_MODE": "local_adapter",
        "ENVIRONMENT": "local",
        "SECRET_KEY": "apexchainx-dev-secret",
        "PAYMENT_WEBHOOK_SECRET": "test-webhook-secret-1234",
        "WEBHOOK_SECRET_ENCRYPTION_KEY": "V5OOA_Ao70n9OxGEbj1WmRsZX6vI4IdtuJ_jYcIhNDg=",
    }
    defaults.update(overrides)
    # Outside local/test the unrelated SECRET_KEY guard also fires, which would
    # drown out the assertion these tests are making.
    if defaults["ENVIRONMENT"] not in DEV_ENVIRONMENTS and "SECRET_KEY" not in overrides:
        defaults["SECRET_KEY"] = STRONG_SECRET
    return Settings.model_construct(**defaults)


class TestDefault:
    def test_eager_mode_is_off_by_default(self):
        # The default is what every deployment inherits when it forgets to set
        # the variable, so it must be the safe value.
        assert Settings.model_construct().CELERY_TASK_ALWAYS_EAGER is False

    def test_dev_environments_are_local_and_test(self):
        assert DEV_ENVIRONMENTS == {"local", "test"}


class TestNonDevStartupFails:
    @pytest.mark.parametrize("environment", NON_DEV_ENVIRONMENTS)
    def test_eager_mode_is_rejected_outside_development(self, environment):
        config = make_settings(ENVIRONMENT=environment, CELERY_TASK_ALWAYS_EAGER=True)
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        assert "CELERY_TASK_ALWAYS_EAGER must be false outside development" in str(exc.value)
        assert environment in str(exc.value)

    @pytest.mark.parametrize("environment", NON_DEV_ENVIRONMENTS)
    def test_worker_mode_is_accepted_outside_development(self, environment):
        config = make_settings(ENVIRONMENT=environment, CELERY_TASK_ALWAYS_EAGER=False)
        validate_critical_settings(config)


class TestDevStartupPasses:
    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_eager_mode_is_allowed_in_development(self, environment):
        config = make_settings(ENVIRONMENT=environment, CELERY_TASK_ALWAYS_EAGER=True)
        validate_critical_settings(config)

    @pytest.mark.parametrize("environment", sorted(DEV_ENVIRONMENTS))
    def test_worker_mode_is_allowed_in_development(self, environment):
        config = make_settings(ENVIRONMENT=environment, CELERY_TASK_ALWAYS_EAGER=False)
        validate_critical_settings(config)


class TestBrokerRequirementUnchanged:
    def test_worker_mode_requires_broker_urls(self):
        config = make_settings(
            CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="", CELERY_RESULT_BACKEND=""
        )
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        message = str(exc.value)
        assert "CELERY_BROKER_URL" in message
        assert "CELERY_RESULT_BACKEND" in message

    def test_eager_mode_does_not_require_broker_urls(self):
        config = make_settings(
            CELERY_TASK_ALWAYS_EAGER=True, CELERY_BROKER_URL="", CELERY_RESULT_BACKEND=""
        )
        validate_critical_settings(config)


class TestErrorIsReportedAlongsideOthers:
    def test_eager_violation_does_not_mask_other_errors(self):
        config = make_settings(
            ENVIRONMENT="production",
            CELERY_TASK_ALWAYS_EAGER=True,
            API_V1_PREFIX="api/v1",
        )
        with pytest.raises(ValueError) as exc:
            validate_critical_settings(config)
        message = str(exc.value)
        assert "CELERY_TASK_ALWAYS_EAGER" in message
        assert "API_V1_PREFIX must start with '/'" in message
