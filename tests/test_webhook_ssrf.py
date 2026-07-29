import pytest

from app.core.config import settings
from app.utils.network_validation import NetworkValidationError, validate_webhook_url


def test_webhook_url_rejects_loopback():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://127.0.0.1/webhook")


def test_webhook_url_rejects_private_ipv4():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://10.0.0.1/webhook")


def test_webhook_url_rejects_cidr_private():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://192.168.1.1/webhook")


def test_webhook_url_rejects_link_local():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://169.254.169.254/webhook")


def test_webhook_url_rejects_ipv6_ula():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://[fc00::1]/webhook")


def test_webhook_url_rejects_ipv6_link_local():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://[fe80::1]/webhook")


def test_webhook_url_rejects_localhost():
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("https://localhost/webhook")


def test_webhook_url_allows_private_networks_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "WEBHOOK_ALLOW_PRIVATE_NETWORKS", True)
    resolved_ips = validate_webhook_url("https://10.0.0.1/webhook")
    assert resolved_ips == ["10.0.0.1"]


def test_webhook_url_rejects_http_outside_local(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    with pytest.raises(NetworkValidationError):
        validate_webhook_url("http://example.com/webhook")
