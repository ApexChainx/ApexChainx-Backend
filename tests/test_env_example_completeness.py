""".env.example must document the whole config matrix — Issue #561.

`app/core/config.py` grew ~60 settings with dev-only defaults and no template
existed, so a new operator had no way to see which values were load-bearing and
which ones silently degrade in production. The template is now grouped by
concern and documents the `validate_critical_settings` guards, and these tests
keep it honest:

* every `Settings` field appears in the template (nothing undocumented);
* every key in the template is a real setting, minus an explicit list of keys
  documented elsewhere on purpose (no typos, no renames left behind);
* the dev profile parses and boots;
* flipping `ENVIRONMENT` to a non-local value makes the startup guard demand the
  production secrets.
"""

import json
import re
from pathlib import Path

import pytest

from app.core.config import Settings, validate_critical_settings

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLE_PATH = REPO_ROOT / ".env.example"

# Documented in the template but deliberately not settings: the Stellar network
# endpoints/keys (documented in docs/STELLAR_INTEGRATION.md, injected at
# runtime) and the placeholder used for third-party provider keys.
DOCUMENTED_ONLY = {
    "STELLAR_HORIZON_URL",
    "STELLAR_SOROBAN_RPC_URL",
    "STELLAR_POOL_PUBLIC_KEY",
    "STELLAR_POOL_SECRET_KEY",
    "EXAMPLE_API_KEY",
}

ASSIGNMENT = re.compile(r"^\s*#?\s*([A-Z][A-Z0-9_]*)\s*=")


def _example_text() -> str:
    return EXAMPLE_PATH.read_text(encoding="utf-8")


def _example_keys() -> set[str]:
    keys = set()
    for line in _example_text().splitlines():
        match = ASSIGNMENT.match(line)
        if match:
            keys.add(match.group(1))
    return keys


def _coerce(raw: str):
    """Env files carry strings; pydantic-settings parses lists as JSON."""
    if raw.startswith("["):
        return json.loads(raw)
    return raw


def _dev_values() -> dict:
    """Uncommented assignments from the template, coerced like env values."""
    values = {}
    for line in _example_text().splitlines():
        if ASSIGNMENT.match(line) is None or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.partition("=")
        values[key.strip()] = _coerce(raw.strip())
    return values


def _dev_settings(**overrides) -> Settings:
    # _env_file=None keeps a developer's real .env out of these assertions.
    return Settings(_env_file=None, **{**_dev_values(), **overrides})


class TestTemplateCoverage:
    def test_template_exists(self):
        assert EXAMPLE_PATH.is_file(), ".env.example is the documented reference for operators"

    def test_every_setting_is_documented(self):
        undocumented = sorted(set(Settings.model_fields) - _example_keys())

        assert not undocumented, f"undocumented settings in .env.example: {undocumented}"

    def test_every_documented_key_is_a_real_setting(self):
        unknown = sorted(_example_keys() - set(Settings.model_fields) - DOCUMENTED_ONLY)

        assert not unknown, f".env.example documents keys that are not settings: {unknown}"

    def test_no_placeholders_are_used_for_production_secrets(self):
        """The required-secret section must not ship a literal value."""
        text = _example_text()
        for key in ("SECRET_KEY", "PAYMENT_WEBHOOK_SECRET"):
            assigned = [line for line in text.splitlines() if ASSIGNMENT.match(line) and line.partition("=")[0].strip() == key]
            assert assigned, f"{key} should be present in the template"
            for line in assigned:
                assert "GENERATE" in line, f"{key} must stay a documented placeholder, got: {line}"


class TestDevProfile:
    def test_dev_values_pass_startup_validation(self):
        validate_critical_settings(_dev_settings())

    def test_dev_environment_is_local(self):
        assert _dev_settings().ENVIRONMENT == "local"

    def test_dev_profile_leaves_production_secrets_unset(self):
        settings = _dev_settings()

        # Unset means "derive from SECRET_KEY locally"; required once deployed.
        assert settings.WEBHOOK_SECRET_ENCRYPTION_KEY == ""

    def test_eager_celery_default_needs_no_broker(self):
        """The dev profile must work with CELERY_TASK_ALWAYS_EAGER=true."""
        settings = _dev_settings()

        assert settings.CELERY_TASK_ALWAYS_EAGER is True
        validate_critical_settings(settings)

    def test_documented_retry_delays_parse_into_three_attempts(self):
        delays = [int(value) for value in _dev_settings().WEBHOOK_RETRY_BASE_DELAYS.split(",")]

        assert delays == [30, 120, 600]

    def test_malformed_retry_delays_fail_at_startup(self):
        with pytest.raises(ValueError, match="WEBHOOK_RETRY_BASE_DELAYS must be a comma-separated list of integers"):
            validate_critical_settings(_dev_settings(WEBHOOK_RETRY_BASE_DELAYS="30,soon,600"))


class TestProductionGuards:
    """ENVIRONMENT is the switch that turns dev defaults into startup errors."""

    def test_production_environment_rejects_the_dev_placeholders(self):
        """The example alone, with ENVIRONMENT flipped, must not boot."""
        config = _dev_settings(ENVIRONMENT="production")

        with pytest.raises(ValueError) as exc_info:
            validate_critical_settings(config)

        message = str(exc_info.value)
        assert "SECRET_KEY" in message
        assert "WEBHOOK_SECRET_ENCRYPTION_KEY" in message

    def test_production_requires_the_webhook_hmac_secret(self):
        config = _dev_settings(ENVIRONMENT="production", PAYMENT_WEBHOOK_SECRET="")

        with pytest.raises(ValueError, match="PAYMENT_WEBHOOK_SECRET must not be empty"):
            validate_critical_settings(config)

    def test_production_environment_accepts_real_secrets(self):
        from cryptography.fernet import Fernet

        config = _dev_settings(
            ENVIRONMENT="production",
            SECRET_KEY="k" * 48,
            IMPERSONATION_SIGNING_KEY="i" * 48,
            PAYMENT_WEBHOOK_SECRET="w" * 48,
            WEBHOOK_SECRET_ENCRYPTION_KEY=Fernet.generate_key().decode(),
        )

        validate_critical_settings(config)

    def test_production_rejects_the_default_secret_key(self):
        from app.core.config import DEFAULT_SECRET_KEY

        config = _dev_settings(ENVIRONMENT="production", SECRET_KEY=DEFAULT_SECRET_KEY)

        with pytest.raises(ValueError, match="SECRET_KEY"):
            validate_critical_settings(config)

    def test_eager_celery_false_requires_broker_urls(self):
        config = _dev_settings(CELERY_TASK_ALWAYS_EAGER=False, CELERY_BROKER_URL="   ")

        with pytest.raises(ValueError, match="CELERY_BROKER_URL must not be empty"):
            validate_critical_settings(config)
