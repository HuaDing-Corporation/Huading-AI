from __future__ import annotations

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
