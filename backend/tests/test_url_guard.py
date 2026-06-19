import pytest

from app.providers.url_guard import (
    ProviderUrlError,
    ensure_https_url_allowed,
    object_storage_public_hosts,
)


def test_object_storage_public_hosts_keeps_path_style_backwards_compatible() -> None:
    hosts = object_storage_public_hosts(
        "https://tos-s3-cn-beijing.volces.com",
        bucket="huading-media",
        addressing_style="path",
    )

    assert hosts == {"tos-s3-cn-beijing.volces.com"}


def test_object_storage_public_hosts_adds_virtual_hosted_bucket_domain() -> None:
    hosts = object_storage_public_hosts(
        "https://tos-s3-cn-beijing.volces.com",
        bucket="huading-media",
        addressing_style="virtual",
    )

    assert hosts == {
        "tos-s3-cn-beijing.volces.com",
        "huading-media.tos-s3-cn-beijing.volces.com",
    }


def test_https_url_guard_allows_configured_host_suffix() -> None:
    url = "https://v26-aiop.aigc-cloud.com/result.mp4"

    assert (
        ensure_https_url_allowed(
            url,
            allowed_hosts={"visual.volcengineapi.com"},
            allowed_host_suffixes={"aigc-cloud.com"},
        )
        == url
    )


def test_https_url_guard_rejects_suffix_host_without_suffix_config() -> None:
    with pytest.raises(ProviderUrlError, match="not allowed"):
        ensure_https_url_allowed(
            "https://v26-aiop.aigc-cloud.com/result.mp4",
            allowed_hosts={"visual.volcengineapi.com"},
        )


def test_https_url_guard_rejects_evil_domain_with_similar_suffix() -> None:
    with pytest.raises(ProviderUrlError, match="not allowed"):
        ensure_https_url_allowed(
            "https://aigc-cloud.com.evil.example/result.mp4",
            allowed_hosts={"visual.volcengineapi.com"},
            allowed_host_suffixes={"aigc-cloud.com"},
        )


def test_https_url_guard_rejects_http_even_when_suffix_allowed() -> None:
    with pytest.raises(ProviderUrlError, match="not allowed"):
        ensure_https_url_allowed(
            "http://v26-aiop.aigc-cloud.com/result.mp4",
            allowed_hosts={"visual.volcengineapi.com"},
            allowed_host_suffixes={"aigc-cloud.com"},
        )
