from __future__ import annotations

import ipaddress
import socket
from collections.abc import Iterable
from urllib.parse import urlparse


class ProviderUrlError(RuntimeError):
    pass


def host_from_url(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    return parsed.hostname.lower() if parsed.hostname else None


def object_storage_public_hosts(
    *urls: str | None,
    bucket: str | None = None,
    addressing_style: str = "path",
) -> set[str]:
    hosts = {host for host in (host_from_url(url) for url in urls) if host}
    if addressing_style == "virtual" and bucket:
        hosts.update(f"{bucket}.{host}" for host in tuple(hosts))
    return hosts


def parse_host_suffixes(value: object) -> set[str]:
    if isinstance(value, str):
        return {item.strip().lower().lstrip(".") for item in value.split(",") if item.strip()}
    if isinstance(value, list | tuple | set):
        return {str(item).strip().lower().lstrip(".") for item in value if str(item).strip()}
    return set()


def ensure_https_url_allowed(
    url: str,
    *,
    allowed_hosts: Iterable[str],
    allowed_host_suffixes: Iterable[str] = (),
) -> str:
    parsed = urlparse(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    normalized_hosts = {item.lower() for item in allowed_hosts if item}
    normalized_suffixes = {
        item.strip().lower().lstrip(".") for item in allowed_host_suffixes if item.strip()
    }
    suffix_allowed = any(
        host == suffix or host.endswith(f".{suffix}") for suffix in normalized_suffixes
    )
    if parsed.scheme != "https" or (host not in normalized_hosts and not suffix_allowed):
        raise ProviderUrlError(f"Provider URL host is not allowed by whitelist: {host or url}")
    return url


def ensure_public_https_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if parsed.scheme != "https" or not host:
        raise ProviderUrlError("URL must be an HTTPS URL.")
    _ensure_public_host(host, parsed.port or 443)
    return url


def _ensure_public_host(host: str, port: int) -> None:
    try:
        _ensure_public_ip(ipaddress.ip_address(host))
        return
    except ValueError:
        pass

    try:
        results = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ProviderUrlError("URL host could not be resolved.") from exc
    addresses = {item[4][0] for item in results if item[4]}
    if not addresses:
        raise ProviderUrlError("URL host could not be resolved.")
    for address in addresses:
        _ensure_public_ip(ipaddress.ip_address(address))


def _ensure_public_ip(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> None:
    if (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        raise ProviderUrlError(f"URL host is not public: {address}")
