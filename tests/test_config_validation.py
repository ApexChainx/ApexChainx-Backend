import unittest

from app.core.config import Settings, validate_critical_settings


class ConfigValidationTests(unittest.TestCase):
    def make_settings(self, **overrides):
        defaults = {
            "PROJECT_NAME": "ApexChainx API",
            "VERSION": "1.0.0",
            "DEBUG": False,
            "DATABASE_URL": "postgresql://postgres:password@localhost:5432/apexchainx",
            "API_V1_PREFIX": "/api/v1",
            "ALLOWED_ORIGINS": ["http://localhost:3000"],
            "CELERY_BROKER_URL": "redis://localhost:6379/0",
            "CELERY_RESULT_BACKEND": "redis://localhost:6379/0",
            "CELERY_TASK_ALWAYS_EAGER": True,
            "SLA_CONTRACT_ADDRESS": "local-sla-calculator",
            "STELLAR_NETWORK": "testnet",
            "CONTRACT_EXECUTION_MODE": "local_adapter",
        }
        defaults.update(overrides)
        return Settings.model_construct(**defaults)

    def test_valid_settings_pass(self):
        validate_critical_settings(self.make_settings())

    def test_invalid_api_prefix_fails_fast(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(self.make_settings(API_V1_PREFIX="api/v1"))

        self.assertIn("API_V1_PREFIX must start with '/'", str(ctx.exception))

    def test_invalid_origins_fail_fast(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(
                self.make_settings(ALLOWED_ORIGINS=["localhost:3000"])
            )

        self.assertIn("ALLOWED_ORIGINS must contain valid http or https origins", str(ctx.exception))

    def test_invalid_contract_execution_mode_fails_fast(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(
                self.make_settings(CONTRACT_EXECUTION_MODE="unsupported")
            )

        self.assertIn("CONTRACT_EXECUTION_MODE must be one of", str(ctx.exception))

    def test_non_eager_celery_requires_broker_and_backend(self):
        with self.assertRaises(ValueError) as ctx:
            validate_critical_settings(
                self.make_settings(
                    CELERY_TASK_ALWAYS_EAGER=False,
                    CELERY_BROKER_URL="",
                    CELERY_RESULT_BACKEND="",
                )
            )

        message = str(ctx.exception)
        self.assertIn("CELERY_BROKER_URL must not be empty", message)
        self.assertIn("CELERY_RESULT_BACKEND must not be empty", message)


if __name__ == "__main__":
    unittest.main()
