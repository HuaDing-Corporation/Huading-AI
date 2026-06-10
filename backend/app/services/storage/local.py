from pathlib import Path

from app.services.storage.base import StorageKeyError


class LocalObjectStorage:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    def _resolve(self, key: str) -> Path:
        path = (self.root / key).resolve()
        root = self.root.resolve()
        if root not in path.parents and path != root:
            raise StorageKeyError("Object key escapes local storage root.")
        return path

    def put_text(self, key: str, content: str, *, content_type: str) -> str:
        return self.put_bytes(key, content.encode("utf-8"), content_type=content_type)

    def put_bytes(self, key: str, content: bytes, *, content_type: str) -> str:
        path = self._resolve(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path.as_uri()

    def get_bytes(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.is_file():
            raise FileNotFoundError(f"object not found: {key}")
        return path.read_bytes()
