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


def object_storage_public_hosts(*urls: str | None) -> set[str]:
    return {host for host in (host_from_url(url) for url in urls) if host}


def ensure_https_url_allowed(url: str, *, allowed_hosts: Iterable[str]) -> str:
    parsed = urlparse(url)
    host = parsed.hostname.lower() if parsed.hostname else ""
    normalized_hosts = {item.lower() for item in allowed_hosts if item}
    if parsed.scheme != "https" or host not in normalized_hosts:
        raise ProviderUrlError(f"Provider URL host is not allowed by whitelist: {host or url}")
    return url
