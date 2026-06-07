"""HTTP driver for the distributed e2e: POST -> SSE/poll progress -> assert URL.

Pure HTTP client against a running API (no app import). Used by
scripts/e2e_distributed.sh, which brings up Redis + uvicorn + a Celery worker.

Exit 0 only if the task reaches SUCCESS with a non-empty video_url (and, for
local file:// storage, the file exists on disk).
"""

from __future__ import annotations

import os
import re
import sys
import time
from urllib.parse import unquote, urlparse

import requests

BASE_URL = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8000")
POLL_TIMEOUT_S = int(os.environ.get("E2E_TIMEOUT", "420"))
TERMINAL = {"SUCCESS", "FAILURE"}


def _file_url_to_path(url: str) -> str:
    """Convert a file:// URL to a local filesystem path.

    Percent-decodes the path (so non-ASCII dirs like '华鼎' -> %E5%8D%8E... are
    restored) and handles Windows file URLs ('/C:/x' -> 'C:/x', plus UNC hosts).
    """
    parsed = urlparse(url)
    path = unquote(parsed.path)
    if os.name == "nt":
        # file://server/share -> \\server\share
        if parsed.netloc:
            return f"\\\\{parsed.netloc}{path}".replace("/", "\\")
        # '/C:/Users/...' -> 'C:/Users/...'
        if re.match(r"^/[A-Za-z]:", path):
            path = path[1:]
    return path


def _submit() -> str:
    resp = requests.post(
        f"{BASE_URL}/api/v1/videos",
        json={
            "topic": "如何提高学习效率",
            "n_scenes": 2,
            "frame_template": "1080x1920/static_default.html",
        },
        timeout=30,
    )
    resp.raise_for_status()
    if resp.status_code != 202:
        raise SystemExit(f"expected 202, got {resp.status_code}: {resp.text}")
    task_id = resp.json()["data"]["task_id"]
    print(f"[submit] task_id={task_id} status={resp.json()['data']['status']}")
    return task_id


def _watch_sse(task_id: str, max_seconds: int = 20) -> None:
    """Best-effort: print a few SSE progress events. Never fails the run."""
    try:
        with requests.get(
            f"{BASE_URL}/api/v1/videos/{task_id}/events",
            stream=True,
            timeout=(5, max_seconds + 5),
        ) as resp:
            deadline = time.time() + max_seconds
            for raw in resp.iter_lines(decode_unicode=True):
                if raw and raw.startswith("data:"):
                    print(f"[sse] {raw[5:].strip()}")
                    if '"status": "SUCCESS"' in raw or '"status": "FAILURE"' in raw:
                        return
                if time.time() > deadline:
                    return
    except Exception as exc:  # noqa: BLE001
        print(f"[sse] stream ended/unavailable ({exc}); falling back to polling")


def _poll(task_id: str) -> dict:
    deadline = time.time() + POLL_TIMEOUT_S
    last = None
    while time.time() < deadline:
        resp = requests.get(f"{BASE_URL}/api/v1/videos/{task_id}", timeout=30)
        resp.raise_for_status()
        body = resp.json()["data"]
        snap = (body.get("status"), body.get("progress"), body.get("stage"))
        if snap != last:
            print(f"[poll] status={snap[0]} progress={snap[1]} stage={snap[2]}")
            last = snap
        if body.get("status") in TERMINAL:
            return body
        time.sleep(2)
    raise SystemExit(f"timed out after {POLL_TIMEOUT_S}s")


def main() -> int:
    print(f"[e2e] base_url={BASE_URL}")
    task_id = _submit()
    _watch_sse(task_id)
    body = _poll(task_id)

    if body.get("status") != "SUCCESS":
        print(f"[fail] terminal status={body.get('status')} error={body.get('error')}")
        return 1

    url = body.get("video_url")
    if not url:
        print("[fail] SUCCESS but no video_url")
        return 1
    print(f"[ok] status=SUCCESS video_url={url}")

    # For local file:// storage, verify the artifact exists on disk.
    if url.startswith("file://"):
        path = _file_url_to_path(url)
        if not (os.path.exists(path) and os.path.getsize(path) > 0):
            print(f"[fail] artifact missing/empty: {path}")
            return 1
        print(f"[ok] artifact on disk: {path} ({os.path.getsize(path)} bytes)")

    print("[e2e] PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
