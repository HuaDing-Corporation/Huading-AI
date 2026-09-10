"""Opt-in real Nginx gate: RUN_UPLOAD_NGINX_TESTS=1, Docker images preloaded.

No host ports or external network: synthetic requests and the echo backend run
on a private internal network. The production HTTPS configuration is mounted as-is.
"""

import json
import os
import subprocess
import time
from pathlib import Path
from uuid import uuid4

import pytest

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_UPLOAD_NGINX_TESTS") != "1", reason="Opt-in isolated Docker Nginx gate"
)

BACKEND = r"""
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        size = int(self.headers.get("Content-Length", "0"))
        remaining = size
        while remaining:
            chunk = self.rfile.read(min(65536, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
        result = {"path": self.path, "received": size - remaining,
                  "host": self.headers.get("Host"),
                  "proto": self.headers.get("X-Forwarded-Proto"),
                  "forwarded": self.headers.get("X-Forwarded-For")}
        with open("/tmp/received.jsonl", "a") as log:
            log.write(json.dumps(result) + "\n")
        body = json.dumps(result).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

HTTPServer(("0.0.0.0", 8000), Echo).serve_forever()
"""

CLIENT = r"""
import http.client
import json
import ssl
import sys

path, size, send_body = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
prefix = (b'--boundary\r\nContent-Disposition: form-data; name="file"; '
          b'filename="synthetic.bin"\r\nContent-Type: application/octet-stream\r\n\r\n')
suffix = b'\r\n--boundary--\r\n'
conn = http.client.HTTPSConnection("gateway", timeout=60,
                                  context=ssl._create_unverified_context())
conn.putrequest("POST", path, skip_host=True)
conn.putheader("Host", "huadingai.cn")
conn.putheader("Content-Length", str(size))
conn.putheader("Content-Type", "multipart/form-data; boundary=boundary")
conn.endheaders()
if send_body:
    try:
        conn.send(prefix)
        remaining = size - len(prefix) - len(suffix)
        while remaining:
            count = min(65536, remaining)
            conn.send(b"p" * count)
            remaining -= count
        conn.send(suffix)
    except (BrokenPipeError, ConnectionResetError, ssl.SSLError):
        pass
response = conn.getresponse()
body = response.read()
print(json.dumps({"status": response.status,
                  "body": json.loads(body) if response.status == 200 else None}))
"""


def _docker(*args, check=True, timeout=120):
    result = subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout, check=False
    )
    if check:
        assert result.returncode == 0, result.stderr
    return result


@pytest.fixture(scope="module")
def nginx_stack(tmp_path_factory):
    root = tmp_path_factory.mktemp("upload-nginx")
    certs = root / "certs"
    certs.mkdir()
    # Generated test key stays in pytest's isolated temporary directory.
    _docker(
        "run",
        "--rm",
        "--network",
        "none",
        "-v",
        f"{certs}:/certs",
        "nginx:1.27",
        "openssl",
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=huadingai.cn",
        "-keyout",
        "/certs/privkey.pem",
        "-out",
        "/certs/fullchain.pem",
    )
    (root / "backend.py").write_text(BACKEND, encoding="utf-8")
    (root / "client.py").write_text(CLIENT, encoding="utf-8")
    config = Path(__file__).resolve().parents[2] / "infra/nginx/conf.d"
    name = f"upload-limit-{uuid4().hex[:12]}"
    backend = f"{name}-backend"
    gateway = f"{name}-gateway"
    _docker("network", "create", "--internal", name)
    try:
        _docker(
            "run",
            "-d",
            "--name",
            backend,
            "--network",
            name,
            "--network-alias",
            "backend",
            "-v",
            f"{root}:/fixture:ro",
            "python:3.11-slim",
            "python",
            "/fixture/backend.py",
        )
        _docker(
            "run",
            "-d",
            "--name",
            gateway,
            "--network",
            name,
            "--network-alias",
            "gateway",
            "-v",
            f"{config}:/etc/nginx/conf.d:ro",
            "-v",
            f"{certs}:/etc/letsencrypt/live/huadingai.cn:ro",
            "nginx:1.27",
        )
        _docker("exec", gateway, "nginx", "-t")
        for _ in range(30):
            ready = _docker(
                "exec",
                backend,
                "python",
                "-c",
                "import socket; socket.create_connection(('gateway', 443), 1).close()",
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.1)
        else:
            pytest.fail("Isolated Nginx did not become ready")

        def request(path, size, *, send_body):
            before = _docker(
                "exec",
                backend,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/tmp/received.jsonl'); "
                "print(len(p.read_text().splitlines()) if p.exists() else 0)",
            ).stdout.strip()
            result = _docker(
                "exec",
                backend,
                "python",
                "/fixture/client.py",
                path,
                str(size),
                "1" if send_body else "0",
            )
            response = json.loads(result.stdout)
            after = _docker(
                "exec",
                backend,
                "python",
                "-c",
                "from pathlib import Path; p=Path('/tmp/received.jsonl'); "
                "print(len(p.read_text().splitlines()) if p.exists() else 0)",
            ).stdout.strip()
            return response, int(after) - int(before)

        yield request
    finally:
        _docker("rm", "-f", gateway, backend, check=False)
        _docker("network", "rm", name, check=False)


@pytest.mark.parametrize(
    "path,size",
    [
        ("/api/v1/uploads/images", 30 * 1024 * 1024 + 512),
        ("/api/v1/uploads/videos?purpose=reverse_prompt", 100 * 1024 * 1024 + 512),
        ("/api/v1/uploads/videos/?purpose=avatar_source", 200 * 1024 * 1024 + 512),
    ],
)
def test_nginx_multipart_reaches_echo_backend(nginx_stack, path, size):
    response, received_requests = nginx_stack(path, size, send_body=True)
    assert response["status"] == 200
    assert received_requests == 1
    assert response["body"]["path"] == path
    assert response["body"]["received"] == size
    assert response["body"]["host"] == "huadingai.cn"
    assert response["body"]["proto"] == "https"
    assert response["body"]["forwarded"]


@pytest.mark.parametrize(
    "path,limit_mib",
    [
        ("/api/v1/uploads/videos?purpose=reverse_prompt", 201),
        ("/api/v1/uploads/videos/?purpose=avatar_source", 201),
        ("/api/v1/uploads/videos-extra", 100),
        ("/api/v1/uploads/videos/child", 100),
        ("/api/v1/uploads/images", 100),
        ("/api/v1/uploads/audio", 100),
        ("/api/v1/storage/objects", 100),
        ("/api/v1/tasks/example/events", 100),
        ("/huading-videos/example", 100),
    ],
)
def test_nginx_excess_rejected_before_backend(nginx_stack, path, limit_mib):
    response, received_requests = nginx_stack(path, limit_mib * 1024 * 1024 + 1, send_body=False)
    assert response["status"] == 413
    assert received_requests == 0
