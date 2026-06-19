from app.services.storage.s3 import S3ObjectStorage


def test_s3_presign_uses_public_endpoint() -> None:
    storage = S3ObjectStorage(
        bucket="huading-videos",
        endpoint_url="http://minio:9000",
        public_endpoint_url="http://localhost:9000",
        region_name="us-east-1",
        access_key_id="huading",
        secret_access_key="secret-secret-secret-secret-secret-32",
    )

    url = storage.presign_get_url(
        "tenants/t1/videos/v1/output.mp4",
        expires_in=3600,
        download_filename="v1.mp4",
    )

    assert url.startswith("http://localhost:9000/huading-videos/tenants/t1/videos/v1/output.mp4")
    assert "X-Amz-Signature=" in url
    assert "response-content-disposition=" in url.lower()


def test_s3_storage_accepts_virtual_hosted_addressing_style() -> None:
    storage = S3ObjectStorage(
        bucket="huading-media",
        endpoint_url="https://tos-s3-cn-beijing.volces.com",
        public_endpoint_url="https://tos-s3-cn-beijing.volces.com",
        region_name="cn-beijing",
        access_key_id="ak",
        secret_access_key="sk",
        addressing_style="virtual",
    )

    assert storage.client.meta.config.s3["addressing_style"] == "virtual"
    assert storage.public_client.meta.config.s3["addressing_style"] == "virtual"
