from typing import Protocol


class StorageKeyError(ValueError):
    """Raised when an object key is invalid or escapes the storage root."""


class ObjectStorage(Protocol):
    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        ...

    def put_text(self, key: str, content: str, *, content_type: str) -> str:
        return self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def get_bytes(self, key: str) -> bytes:
        ...
