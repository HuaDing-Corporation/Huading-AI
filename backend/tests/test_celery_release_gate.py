from __future__ import annotations

import json
import sys
import warnings
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from celery.exceptions import DuplicateNodenameWarning

from scripts.ops import celery_release_gate as gate


def _drained_snapshot() -> dict[str, object]:
    nodes = ("node-a", "node-b", "node-c")
    return {
        "ping": {node: {"ok": "pong"} for node in nodes},
        "active": {node: [] for node in nodes},
        "reserved": {node: [] for node in nodes},
        "scheduled": {node: [] for node in nodes},
        "active_queues": {
            "node-a": [{"name": "default"}, {"name": "avatar"}],
            "node-b": [{"name": "image"}],
            "node-c": [{"name": "video"}],
        },
        "broker_queues": {"default": 0, "avatar": 0, "image": 0, "video": 0},
        "unacked": 0,
    }


class _FakeInspector:
    def __init__(self, app, snapshot):
        self.app = app
        self.snapshot = snapshot

    def ping(self):
        if self.app.duplicate_warning:
            warnings.warn(self.app.duplicate_warning, DuplicateNodenameWarning, stacklevel=2)
        return self.snapshot["ping"]

    def active(self, *, safe=False):
        self.app.active_safe_values.append(safe)
        return self.snapshot["active"]

    def reserved(self):
        return self.snapshot["reserved"]

    def scheduled(self):
        return self.snapshot["scheduled"]

    def active_queues(self):
        return self.snapshot["active_queues"]


class _FakeControl:
    def __init__(self, app):
        self.app = app

    def inspect(self, **_kwargs):
        if self.app.inspect_error is not None:
            print(self.app.inspect_error)
            print(self.app.inspect_error, file=sys.stderr)
            raise RuntimeError(self.app.inspect_error)
        self.app.current_snapshot += 1
        snapshot = self.app.snapshots[self.app.current_snapshot]
        return _FakeInspector(self.app, snapshot)


class _FakeBrokerClient:
    def __init__(self, app):
        self.app = app

    def hlen(self, key: str) -> int:
        self.app.unacked_keys.append(key)
        return self.app.snapshots[self.app.current_snapshot]["unacked"]  # type: ignore[return-value]

    def scan_iter(self, *, match: str, _type: str):
        assert match == "test-prefix:*"
        assert _type == "list"
        return iter(())


class _FakeBrokerChannel:
    unacked_key = "prefixed-unacked"
    global_keyprefix = "test-prefix:"
    priority_steps = (0, 3, 6, 9)
    sep = "\x06\x16"

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def _size(self, queue: str) -> int:
        self.app.sized_queues.append(queue)
        return self.app.snapshots[self.app.current_snapshot]["broker_queues"][queue]  # type: ignore[index,return-value]

    @contextmanager
    def conn_or_acquire(self):
        yield _FakeBrokerClient(self.app)


class _FakeBrokerConnection:
    transport = SimpleNamespace(driver_type="redis")

    def __init__(self, app):
        self.app = app

    def __enter__(self):
        if self.app.connection_error is not None:
            print(self.app.connection_error)
            print(self.app.connection_error, file=sys.stderr)
            raise RuntimeError(self.app.connection_error)
        return self

    def __exit__(self, *_args):
        return False

    def channel(self):
        return _FakeBrokerChannel(self.app)


class _FakeCeleryApp:
    def __init__(
        self,
        snapshots,
        *,
        broker_only=False,
        duplicate_warning=None,
        inspect_error=None,
        connection_error=None,
    ):
        self.snapshots = snapshots
        self.broker_only = broker_only
        self.duplicate_warning = duplicate_warning
        self.inspect_error = inspect_error
        self.connection_error = connection_error
        self.current_snapshot = -1
        self.active_safe_values: list[bool] = []
        self.sized_queues: list[str] = []
        self.unacked_keys: list[str] = []
        self.connection_calls = 0
        self.connection_kwargs: list[dict[str, object]] = []
        self.conf = SimpleNamespace(broker_transport_options={"global_keyprefix": "test-prefix:"})
        self.control = _FakeControl(self)

    def connection_for_read(self, **kwargs):
        self.connection_calls += 1
        self.connection_kwargs.append(kwargs)
        if self.broker_only:
            self.current_snapshot += 1
        return _FakeBrokerConnection(self)


def test_drain_gate_accepts_three_correct_idle_workers_and_empty_broker():
    report = gate.evaluate_snapshot("drain", _drained_snapshot())

    assert report.ready is True
    assert report.as_payload() == {
        "active": 0,
        "broker_queued": 0,
        "mode": "drain",
        "ready": True,
        "reserved": 0,
        "response_sets_consistent": True,
        "scheduled": 0,
        "topology_ok": True,
        "unacked": 0,
        "workers": 3,
    }


def test_workers_gate_only_requires_three_live_workers_and_correct_topology():
    snapshot = _drained_snapshot()

    report = gate.evaluate_snapshot(
        "workers",
        {"ping": snapshot["ping"], "active_queues": snapshot["active_queues"]},
    )

    assert report.ready is True
    assert report.as_payload() == {
        "mode": "workers",
        "ready": True,
        "response_sets_consistent": True,
        "topology_ok": True,
        "workers": 3,
    }


def test_gate_rejects_a_non_pong_worker_response():
    snapshot = _drained_snapshot()
    ping = dict(snapshot["ping"])
    ping["node-c"] = {"error": "private transport detail"}
    snapshot["ping"] = ping

    report = gate.evaluate_snapshot("drain", snapshot)

    assert report.ready is False


def test_malformed_snapshot_fails_closed_without_echoing_private_data():
    report = gate.evaluate_snapshot(
        "drain",
        {"ping": {"secret-worker": {"error": "redis://user:password@private-host"}}},
    )

    payload = report.as_payload()
    assert payload == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "drain",
        "ready": False,
    }
    assert "secret-worker" not in str(payload)
    assert "password" not in str(payload)


def test_drain_gate_requires_counts_for_all_four_broker_queues():
    snapshot = _drained_snapshot()
    snapshot["broker_queues"] = {"default": 0, "image": 0, "video": 0}

    report = gate.evaluate_snapshot("drain", snapshot)

    assert report.ready is False


def test_drain_gate_blocks_unacknowledged_broker_messages():
    snapshot = _drained_snapshot()
    snapshot["unacked"] = 1

    payload = gate.evaluate_snapshot("drain", snapshot).as_payload()

    assert payload["ready"] is False
    assert payload["unacked"] == 1


def test_broker_only_gate_accepts_empty_queues_and_no_unacked_messages():
    snapshot = _drained_snapshot()

    payload = gate.evaluate_snapshot(
        "broker-only",
        {"broker_queues": snapshot["broker_queues"], "unacked": snapshot["unacked"]},
    ).as_payload()

    assert payload == {
        "broker_queued": 0,
        "mode": "broker-only",
        "ready": True,
        "unacked": 0,
    }


def test_broker_only_cli_uses_kombu_logical_queue_sizes_and_unacked_hash(capsys):
    class FakeClient:
        def __init__(self):
            self.hlen_keys: list[str] = []

        def hlen(self, key: str) -> int:
            self.hlen_keys.append(key)
            return 0

        def scan_iter(self, *, match: str, _type: str):
            assert match == "*"
            assert _type == "list"
            return iter(())

    class FakeChannel:
        unacked_key = "prefixed-unacked"
        global_keyprefix = ""
        priority_steps = (0, 3, 6, 9)
        sep = "\x06\x16"

        def __init__(self, client):
            self.client = client
            self.sized_queues: list[str] = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def _size(self, queue: str) -> int:
            self.sized_queues.append(queue)
            return 0

        @contextmanager
        def conn_or_acquire(self):
            yield self.client

    class FakeConnection:
        transport = SimpleNamespace(driver_type="redis")

        def __init__(self, channel):
            self._channel = channel

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def channel(self):
            return self._channel

    class FakeApp:
        class Control:
            def inspect(self, **_kwargs):
                raise AssertionError("broker-only must not inspect workers")

        control = Control()
        conf = SimpleNamespace(broker_transport_options={})

        def __init__(self, connection):
            self.connection = connection

        def connection_for_read(self, **_kwargs):
            return self.connection

    client = FakeClient()
    channel = FakeChannel(client)
    app = FakeApp(FakeConnection(channel))

    exit_code = gate.main(["broker-only"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "broker_queued": 0,
        "mode": "broker-only",
        "ready": True,
        "unacked": 0,
    }
    assert captured.err == ""
    assert channel.sized_queues == ["default", "avatar", "image", "video"] * 2
    assert client.hlen_keys == ["prefixed-unacked", "prefixed-unacked"]


def test_broker_only_cli_blocks_an_unapproved_prefixed_priority_queue_without_naming_it(
    capsys,
):
    """Redis LIST discovery must cover queues outside the approved topology."""
    secret_queue = "legacy-private-queue"
    separator = "\x06\x16"
    physical_lengths = {
        f"tenant:{secret_queue}{separator}3": 2,
    }

    class FakeClient:
        def __init__(self):
            self.scan_calls: list[tuple[str, str]] = []

        def scan_iter(self, *, match: str, _type: str):
            self.scan_calls.append((match, _type))
            return (key.encode() for key in physical_lengths if key.startswith(match[:-1]))

        def llen(self, key: str) -> int:
            # Kombu's prefixed Redis client adds the global prefix to LLEN.
            return physical_lengths.get(f"tenant:{key}", 0)

        def hlen(self, _key: str) -> int:
            return 0

    class FakeChannel:
        unacked_key = "unacked"
        global_keyprefix = "tenant:"
        priority_steps = (0, 3, 6, 9)
        sep = separator

        def __init__(self, client):
            self.client = client

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def _size(self, queue: str) -> int:
            return sum(
                self.client.llen(queue if priority == 0 else f"{queue}{self.sep}{priority}")
                for priority in self.priority_steps
            )

        @contextmanager
        def conn_or_acquire(self):
            yield self.client

    class FakeConnection:
        transport = SimpleNamespace(driver_type="redis")

        def __init__(self, channel):
            self._channel = channel

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def channel(self):
            return self._channel

    client = FakeClient()
    connection = FakeConnection(FakeChannel(client))
    app = SimpleNamespace(
        conf=SimpleNamespace(broker_transport_options={"global_keyprefix": "tenant:"}),
        connection_for_read=lambda **_kwargs: connection,
    )

    exit_code = gate.main(["broker-only"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "broker_queued": 2,
        "mode": "broker-only",
        "ready": False,
        "unacked": 0,
    }
    assert captured.err == ""
    assert secret_queue not in captured.out
    assert client.scan_calls == [("tenant:*", "list")]


def test_drain_gate_rejects_malformed_task_lists_even_when_they_look_empty():
    snapshot = _drained_snapshot()
    snapshot["active"] = {"node-a": "", "node-b": "", "node-c": ""}

    report = gate.evaluate_snapshot("drain", snapshot)

    assert report.as_payload() == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "drain",
        "ready": False,
    }


@pytest.mark.parametrize("field", ["active", "reserved", "scheduled", "broker_queued"])
def test_drain_gate_blocks_every_nonzero_work_count_without_exposing_task_data(field):
    snapshot = _drained_snapshot()
    if field == "broker_queued":
        snapshot["broker_queues"] = {"default": 1, "avatar": 0, "image": 0, "video": 0}
    else:
        response = dict(snapshot[field])
        response["node-b"] = [
            {"id": "sensitive-task-id", "args": "provider-id-and-private-payload"}
        ]
        snapshot[field] = response

    payload = gate.evaluate_snapshot("drain", snapshot).as_payload()

    assert payload["ready"] is False
    assert payload[field] == 1
    assert "sensitive-task-id" not in str(payload)
    assert "provider-id" not in str(payload)


def test_gate_requires_exactly_three_response_workers():
    snapshot = _drained_snapshot()
    for response_name in ("ping", "active", "reserved", "scheduled", "active_queues"):
        response = dict(snapshot[response_name])
        response.pop("node-c")
        snapshot[response_name] = response

    payload = gate.evaluate_snapshot("drain", snapshot).as_payload()

    assert payload["ready"] is False
    assert payload["workers"] == 2


def test_gate_requires_every_inspection_response_to_have_the_same_nodes():
    snapshot = _drained_snapshot()
    active = dict(snapshot["active"])
    active["replacement-node"] = active.pop("node-c")
    snapshot["active"] = active

    payload = gate.evaluate_snapshot("drain", snapshot).as_payload()

    assert payload["ready"] is False
    assert payload["response_sets_consistent"] is False


def test_gate_requires_the_exact_three_queue_groups():
    snapshot = _drained_snapshot()
    active_queues = dict(snapshot["active_queues"])
    active_queues["node-b"] = [{"name": "image"}, {"name": "video"}]
    snapshot["active_queues"] = active_queues

    payload = gate.evaluate_snapshot("workers", snapshot).as_payload()

    assert payload["ready"] is False
    assert payload["topology_ok"] is False


def test_drain_cli_reads_only_boundary_counts_and_requires_two_zero_samples(capsys):
    snapshot = _drained_snapshot()
    app = _FakeCeleryApp([snapshot, snapshot])

    exit_code = gate.main(["drain", "--expected-workers", "3"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == gate.evaluate_snapshot("drain", snapshot).as_payload()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert app.active_safe_values == [True, True]
    assert app.sized_queues == ["default", "avatar", "image", "video"] * 2
    assert app.unacked_keys == ["prefixed-unacked", "prefixed-unacked"]


def test_drain_cli_waits_for_a_later_zero_snapshot_without_intermediate_output(
    monkeypatch,
    capsys,
):
    blocked = _drained_snapshot()
    blocked["broker_queues"] = {"default": 1, "avatar": 0, "image": 0, "video": 0}
    drained = _drained_snapshot()
    app = _FakeCeleryApp([blocked, drained, drained])
    clock_values = iter((0.0, 0.0, 1.0))
    sleeps: list[float] = []
    monkeypatch.setattr(gate.time, "monotonic", lambda: next(clock_values))
    monkeypatch.setattr(gate.time, "sleep", sleeps.append)

    exit_code = gate.main(
        ["drain", "--wait-seconds", "10", "--poll-seconds", "1"],
        app=app,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out)["ready"] is True
    assert sleeps == [1.0, 1.0]


def test_workers_cli_does_not_query_tasks_or_broker(capsys):
    snapshot = _drained_snapshot()
    app = _FakeCeleryApp([snapshot], connection_error="private broker URL")

    exit_code = gate.main(
        [
            "workers",
            "--expected-workers",
            "3",
            "--wait-seconds",
            "1800",
            "--poll-seconds",
            "10",
        ],
        app=app,
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert json.loads(captured.out) == {
        "mode": "workers",
        "ready": True,
        "response_sets_consistent": True,
        "topology_ok": True,
        "workers": 3,
    }
    assert captured.err == ""
    assert app.active_safe_values == []
    assert app.connection_calls == 0


def test_boundary_exception_returns_only_generic_fail_closed_json(capsys):
    secret = "worker-name task-payload redis://private-user:private-password@provider-id.example/1"
    app = _FakeCeleryApp([_drained_snapshot()], inspect_error=secret)

    exit_code = gate.main(["drain"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "drain",
        "ready": False,
    }
    assert captured.err == ""
    assert "worker-name" not in captured.out
    assert "task-payload" not in captured.out
    assert "private-password" not in captured.out
    assert "provider-id" not in captured.out


def test_invalid_cli_value_returns_generic_json_without_echoing_it(capsys):
    secret = "redis://private-user:private-password@provider-id.example/1"

    exit_code = gate.main(["drain", "--wait-seconds", secret])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "drain",
        "ready": False,
    }
    assert captured.err == ""
    assert "private-password" not in captured.out
    assert "provider-id" not in captured.out


def test_expected_workers_cannot_weaken_the_fixed_three_worker_gate(capsys):
    exit_code = gate.main(["workers", "--expected-workers", "2"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "workers",
        "ready": False,
    }
    assert captured.err == ""


def test_drain_cli_rejects_zero_followed_by_new_work(capsys):
    drained = _drained_snapshot()
    new_work = _drained_snapshot()
    new_work["active"] = {
        "node-a": [{"id": "private-task-payload"}],
        "node-b": [],
        "node-c": [],
    }
    app = _FakeCeleryApp([drained, new_work])

    exit_code = gate.main(["drain"], app=app)

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 2
    assert payload["ready"] is False
    assert payload["active"] == 1
    assert "private-task-payload" not in captured.out


def test_broker_only_cli_rejects_zero_followed_by_unacked_work(capsys):
    drained = _drained_snapshot()
    new_work = _drained_snapshot()
    new_work["unacked"] = 1
    app = _FakeCeleryApp([drained, new_work], broker_only=True)

    exit_code = gate.main(["broker-only"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "broker_queued": 0,
        "mode": "broker-only",
        "ready": False,
        "unacked": 1,
    }
    assert app.current_snapshot == 1


def test_broker_reads_have_finite_socket_timeouts_and_preserve_transport_options(capsys):
    snapshot = _drained_snapshot()
    app = _FakeCeleryApp([snapshot, snapshot], broker_only=True)

    exit_code = gate.main(["broker-only"], app=app)

    assert exit_code == 0
    assert capsys.readouterr().err == ""
    assert len(app.connection_kwargs) == 2
    for kwargs in app.connection_kwargs:
        assert 0 < kwargs["connect_timeout"] <= 5
        transport_options = kwargs["transport_options"]
        assert transport_options["socket_connect_timeout"] <= 5
        assert transport_options["socket_timeout"] <= 5
        assert transport_options["retry_on_timeout"] is False
        assert transport_options["global_keyprefix"] == "test-prefix:"


def test_duplicate_node_warning_fails_closed_without_exposing_node_name(capsys):
    secret_node = "private-worker-name@provider-id"
    snapshot = _drained_snapshot()
    app = _FakeCeleryApp(
        [snapshot, snapshot],
        duplicate_warning=secret_node,
    )

    exit_code = gate.main(["drain"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "drain",
        "ready": False,
    }
    assert captured.err == ""
    assert secret_node not in captured.out


def test_broker_connection_exception_is_generic_and_redacted(capsys):
    secret = "redis://private-user:private-password@provider-id.example/1"
    app = _FakeCeleryApp(
        [_drained_snapshot()],
        broker_only=True,
        connection_error=secret,
    )

    exit_code = gate.main(["broker-only"], app=app)

    captured = capsys.readouterr()
    assert exit_code == 2
    assert json.loads(captured.out) == {
        "error": "CELERY_RELEASE_GATE_FAILED",
        "mode": "broker-only",
        "ready": False,
    }
    assert captured.err == ""
    assert "private-password" not in captured.out
    assert "provider-id" not in captured.out
