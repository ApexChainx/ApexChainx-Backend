import ipaddress
import socket
from typing import List
from urllib.parse import urlparse

from app.core.config import settings


class NetworkValidationError(ValueError):
    pass


CLOUD_METADATA_ADDRESSES = {
    ipaddress.ip_address("169.254.169.254"),
}


def _resolve_host(hostname: str, max_results: int = 5) -> List[str]:
    if not hostname:
        raise NetworkValidationError("Webhook URL must include a hostname.")

    try:
        addr_info = socket.getaddrinfo(hostname, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise NetworkValidationError(f"Could not resolve hostname: {hostname}") from exc

    ips: List[str] = []
    for result in addr_info:
        sockaddr = result[4]
        ip = sockaddr[0]
        if ip not in ips:
            ips.append(ip)
        if len(ips) >= max_results:
            break
    if not ips:
        raise NetworkValidationError(f"Could not resolve hostname: {hostname}")
    return ips


def _validate_ip_address(ip_str: str) -> None:
    try:
        ip_value = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise NetworkValidationError(f"Invalid IP address: {ip_str}") from exc

    if ip_value.is_loopback:
        raise NetworkValidationError("Loopback addresses are not allowed.")
    if ip_value.is_link_local:
        raise NetworkValidationError("Link-local addresses are not allowed.")
    if ip_value.is_multicast:
        raise NetworkValidationError("Multicast addresses are not allowed.")
    if ip_value in CLOUD_METADATA_ADDRESSES:
        raise NetworkValidationError("Cloud metadata service addresses are not allowed.")
    if ip_value.is_reserved:
        raise NetworkValidationError("Reserved IP addresses are not allowed.")
    if ip_value.is_private and not settings.WEBHOOK_ALLOW_PRIVATE_NETWORKS:
        raise NetworkValidationError("Private network addresses are not allowed.")


def validate_webhook_url(url: str) -> List[str]:
    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise NetworkValidationError("Webhook URL must use http or https.")

    if settings.ENVIRONMENT != "local" and parsed.scheme != "https":
        raise NetworkValidationError("In non-local environments, webhook URLs must use https.")

    if settings.WEBHOOK_URL_ALLOWLIST:
        if not any(url.startswith(allowed) for allowed in settings.WEBHOOK_URL_ALLOWLIST):
            raise NetworkValidationError("Webhook URL is not in the configured allowlist.")

    if settings.WEBHOOK_URL_VALIDATOR_BYPASS and settings.ENVIRONMENT == "local":
        return _resolve_host(parsed.hostname or "")

    hostname = parsed.hostname or ""
    if hostname.lower() == "localhost":
        raise NetworkValidationError("Localhost is not allowed for webhook URLs.")

    resolved_ips = _resolve_host(hostname)
    for ip in resolved_ips:
        _validate_ip_address(ip)
    return resolved_ips


def validate_webhook_url_and_rewrite(url: str, webhook_id: str | None = None) -> List[str]:
    if settings.WEBHOOK_URL_VALIDATOR_BYPASS and settings.ENVIRONMENT == "local":
        return _resolve_host(urlparse(url).hostname or "")
    return validate_webhook_url(url)
