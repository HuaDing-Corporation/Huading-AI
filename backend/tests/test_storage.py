from pathlib import Path

from app.services.storage.local import LocalObjectStorage


def test_local_storage_writes_inside_root(tmp_path: Path) -> None:
    storage = LocalObjectStorage(str(tmp_path))
    uri = storage.put_text("demo/task.txt", "hello", content_type="text/plain")
    assert uri.startswith("file:///")
    assert (tmp_path / "demo" / "task.txt").read_text(encoding="utf-8") == "hello"
