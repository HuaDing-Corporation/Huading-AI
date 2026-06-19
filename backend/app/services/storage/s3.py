class S3ObjectStorage:
    def __init__(
        self,
        *,
        bucket: str,
        endpoint_url: str | None,
        public_endpoint_url: str | None,
        region_name: str,
        access_key_id: str | None,
        secret_access_key: str | None,
        addressing_style: str = "path",
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        config = Config(signature_version="s3v4", s3={"addressing_style": addressing_style})
        self.client = boto3.client(
            "s3",
            endpoint_url=endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )
        self.public_client = boto3.client(
            "s3",
            endpoint_url=public_endpoint_url or endpoint_url or None,
            region_name=region_name,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=config,
        )

    def put_text(self, key: str, content: str, *, content_type: str) -> str:
        return self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        from io import BytesIO

        from boto3.s3.transfer import TransferConfig

        self.client.upload_fileobj(
            BytesIO(content),
            self.bucket,
            key,
            ExtraArgs={"ContentType": content_type},
            Config=TransferConfig(multipart_threshold=8 * 1024 * 1024),
        )
        return f"s3://{self.bucket}/{key}"

    def get_bytes(self, key: str) -> bytes:
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        return response["Body"].read()

    def presign_get_url(
        self,
        key: str,
        *,
        expires_in: int,
        download_filename: str | None = None,
    ) -> str:
        params = {"Bucket": self.bucket, "Key": key}
        if download_filename:
            params["ResponseContentDisposition"] = f'attachment; filename="{download_filename}"'
        return self.public_client.generate_presigned_url(
            "get_object",
            Params=params,
            ExpiresIn=expires_in,
        )
