from app.providers.url_guard import object_storage_public_hosts


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
