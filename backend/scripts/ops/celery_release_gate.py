from __future__ import annotations

import argparse
import json
import sys
import time
import warnings
from collections import Counter
from collections.abc import Mapping
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass, replace
from io import StringIO
from pathlib import Path
from typing import Any, Literal

from celery.exceptions import DuplicateNodenameWarning

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

GateMode = Literal["drain", "broker-only", "workers"]

_QUEUE_ORDER = ("default", "avatar", "image", "video")
_MAX_WAIT_SECONDS = 3600.0
_MAX_POLL_SECONDS = 300.0
_BROKER_IO_TIMEOUT_SECONDS = 5.0
_BROKER_QUEUES = frozenset({"default", "avatar", "image", "video"})
_UNAPPROVED_BROKER_QUEUE = "__unapproved__"
_EXPECTED_TOPOLOGY = Counter(
    (
        frozenset({"default", "avatar"}),
        frozenset({"image"}),
        frozenset({"video"}),
    )
)


@dataclass(frozen=True)
class GateReport:
    mode: GateMode
    ready: bool
    workers: int
    response_sets_consistent: bool
    topology_ok: bool
    active: int | None = None
    reserved: int | None = None
    scheduled: int | None = None
    broker_queued: int | None = None
    unacked: int | None = None
    error: str | None = None

    def as_payload(self) -> dict[str, bool | int | str]:
        missing_broker_count = self.mode in {"drain", "broker-only"} and any(
            count is None for count in (self.broker_queued, self.unacked)
        )
        missing_task_count = self.mode == "drain" and any(
            count is None for count in (self.active, self.reserved, self.scheduled)
        )
        if self.error is not None or missing_broker_count or missing_task_count:
            return {
                "error": self.error or "CELERY_RELEASE_GATE_FAILED",
                "mode": self.mode,
                "ready": False,
            }
        if self.mode == "broker-only":
            return {
                "broker_queued": int(self.broker_queued),  # type: ignore[arg-type]
                "mode": self.mode,
                "ready": self.ready,
                "unacked": int(self.unacked),  # type: ignore[arg-type]
            }
        payload: dict[str, bool | int | str] = {
            "mode": self.mode,
            "ready": self.ready,
            "response_sets_consistent": self.response_sets_consistent,
            "topology_ok": self.topology_ok,
            "workers": self.workers,
        }
        if self.mode == "drain":
            payload.update(
                {
                    "active": int(self.active),  # type: ignore[arg-type]
                    "broker_queued": int(self.broker_queued),  # type: ignore[arg-type]
                    "reserved": int(self.reserved),  # type: ignore[arg-type]
                    "scheduled": int(self.scheduled),  # type: ignore[arg-type]
                    "unacked": int(self.unacked),  # type: ignore[arg-type]
                }
            )
        return payload


def _mapping(value: object) -> Mapping[object, object]:
    if not isinstance(value, Mapping):
        raise ValueError("invalid inspection response")
    return value


def _task_count(response: Mapping[object, object]) -> int:
    if any(not isinstance(items, list) for items in response.values()):
        raise ValueError("invalid task response")
    return sum(len(items) for items in response.values())


def _topology(response: Mapping[object, object]) -> Counter[frozenset[str]]:
    topology: Counter[frozenset[str]] = Counter()
    for queues in response.values():
        if not isinstance(queues, list):
            raise ValueError("invalid queue response")
        queue_names: list[str] = []
        for queue in queues:
            queue_mapping = _mapping(queue)
            queue_name = queue_mapping.get("name")
            if not isinstance(queue_name, str):
                raise ValueError("invalid queue response")
            queue_names.append(queue_name)
        if len(queue_names) != len(set(queue_names)):
            raise ValueError("invalid queue response")
        topology[frozenset(queue_names)] += 1
    return topology


def _broker_counts(snapshot: Mapping[str, object]) -> tuple[int, int, bool]:
    broker_queues = _mapping(snapshot["broker_queues"])
    if any(
        not isinstance(count, int) or isinstance(count, bool) or count < 0
        for count in broker_queues.values()
    ):
        raise ValueError("invalid broker response")
    unacked = snapshot["unacked"]
    if not isinstance(unacked, int) or isinstance(unacked, bool) or unacked < 0:
        raise ValueError("invalid broker response")
    return sum(broker_queues.values()), unacked, set(broker_queues) == _BROKER_QUEUES


def _evaluate_snapshot(mode: GateMode, snapshot: Mapping[str, object]) -> GateReport:
    broker_queued: int | None = None
    unacked: int | None = None
    broker_counts_ok = False
    if mode in {"drain", "broker-only"}:
        broker_queued, unacked, broker_counts_ok = _broker_counts(snapshot)
    if mode == "broker-only":
        return GateReport(
            mode=mode,
            ready=broker_counts_ok and broker_queued == 0 and unacked == 0,
            workers=0,
            response_sets_consistent=False,
            topology_ok=False,
            broker_queued=broker_queued,
            unacked=unacked,
        )

    ping = _mapping(snapshot["ping"])
    active_queues = _mapping(snapshot["active_queues"])

    ping_ok = all(
        isinstance(response, Mapping) and response.get("ok") == "pong" for response in ping.values()
    )
    response_sets = [set(response) for response in (ping, active_queues)]
    response_sets_consistent = all(nodes == response_sets[0] for nodes in response_sets[1:])
    topology_ok = _topology(active_queues) == _EXPECTED_TOPOLOGY
    workers = len(ping)
    if mode == "workers":
        return GateReport(
            mode=mode,
            ready=workers == 3 and ping_ok and response_sets_consistent and topology_ok,
            workers=workers,
            response_sets_consistent=response_sets_consistent,
            topology_ok=topology_ok,
        )

    active = _mapping(snapshot["active"])
    reserved = _mapping(snapshot["reserved"])
    scheduled = _mapping(snapshot["scheduled"])
    response_sets = [
        set(response) for response in (ping, active, reserved, scheduled, active_queues)
    ]
    response_sets_consistent = all(nodes == response_sets[0] for nodes in response_sets[1:])
    active_count = _task_count(active)
    reserved_count = _task_count(reserved)
    scheduled_count = _task_count(scheduled)
    ready = (
        workers == 3
        and ping_ok
        and response_sets_consistent
        and topology_ok
        and active_count == 0
        and reserved_count == 0
        and scheduled_count == 0
        and broker_counts_ok
        and broker_queued == 0
        and unacked == 0
    )
    return GateReport(
        mode=mode,
        ready=ready,
        workers=workers,
        response_sets_consistent=response_sets_consistent,
        topology_ok=topology_ok,
        active=active_count,
        reserved=reserved_count,
        scheduled=scheduled_count,
        broker_queued=broker_queued,
        unacked=unacked,
    )


def evaluate_snapshot(mode: GateMode, snapshot: Mapping[str, object]) -> GateReport:
    try:
        return _evaluate_snapshot(mode, snapshot)
    except Exception:
        return GateReport(
            mode=mode,
            ready=False,
            workers=0,
            response_sets_consistent=False,
            topology_ok=False,
            error="CELERY_RELEASE_GATE_FAILED",
        )


def _failed_report(mode: GateMode) -> GateReport:
    return GateReport(
        mode=mode,
        ready=False,
        workers=0,
        response_sets_consistent=False,
        topology_ok=False,
        error="CELERY_RELEASE_GATE_FAILED",
    )


def _load_celery_app() -> Any:
    from app.workers.celery_app import celery_app

    return celery_app


def _broker_transport_options(app: Any) -> dict[str, object]:
    configured = getattr(getattr(app, "conf", None), "broker_transport_options", None)
    if configured is None:
        options: dict[str, object] = {}
    elif isinstance(configured, Mapping):
        options = dict(configured)
    else:
        raise ValueError("invalid broker transport options")
    for option_name in ("socket_connect_timeout", "socket_timeout"):
        configured_timeout = options.get(option_name)
        if not (
            isinstance(configured_timeout, (int, float))
            and not isinstance(configured_timeout, bool)
            and 0 < configured_timeout <= _BROKER_IO_TIMEOUT_SECONDS
        ):
            options[option_name] = _BROKER_IO_TIMEOUT_SECONDS
    options["retry_on_timeout"] = False
    return options


def _redis_text(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8")
    if isinstance(value, str):
        return value
    raise ValueError("invalid broker response")


def _unapproved_broker_count(channel: Any, client: Any) -> int:
    prefix = _redis_text(getattr(channel, "global_keyprefix", ""))
    separator = _redis_text(getattr(channel, "sep", ""))
    priority_steps = getattr(channel, "priority_steps", None)
    if not separator or not isinstance(priority_steps, (list, tuple)):
        raise ValueError("invalid broker response")
    priority_suffixes: list[str] = []
    for priority in priority_steps:
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            raise ValueError("invalid broker response")
        if priority:
            priority_suffixes.append(f"{separator}{priority}")

    seen_storage_keys: set[str] = set()
    total = 0
    for physical_key in client.scan_iter(match=f"{prefix}*", _type="list"):
        key = _redis_text(physical_key)
        if prefix and not key.startswith(prefix):
            raise ValueError("invalid broker response")
        storage_key = key[len(prefix) :] if prefix else key
        if not storage_key or storage_key in seen_storage_keys:
            continue
        seen_storage_keys.add(storage_key)
        logical_queue = storage_key
        for suffix in sorted(priority_suffixes, key=len, reverse=True):
            if logical_queue.endswith(suffix):
                logical_queue = logical_queue[: -len(suffix)]
                break
        if not logical_queue:
            raise ValueError("invalid broker response")
        if logical_queue in _BROKER_QUEUES:
            continue
        count = client.llen(storage_key)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("invalid broker response")
        total += count
    return total


def _broker_snapshot(app: Any) -> dict[str, object]:
    with app.connection_for_read(
        connect_timeout=_BROKER_IO_TIMEOUT_SECONDS,
        transport_options=_broker_transport_options(app),
    ) as connection:
        if getattr(connection.transport, "driver_type", None) != "redis":
            raise RuntimeError("unsupported broker transport")
        with connection.channel() as channel:
            broker_queues = {queue: channel._size(queue) for queue in _QUEUE_ORDER}
            with channel.conn_or_acquire() as client:
                unapproved_queued = _unapproved_broker_count(channel, client)
                unacked = client.hlen(channel.unacked_key)
            if unapproved_queued:
                broker_queues[_UNAPPROVED_BROKER_QUEUE] = unapproved_queued
    return {"broker_queues": broker_queues, "unacked": unacked}


def _collect_snapshot(mode: GateMode, app: Any | None = None) -> dict[str, object]:
    with (
        warnings.catch_warnings(),
        redirect_stdout(StringIO()),
        redirect_stderr(StringIO()),
    ):
        warnings.simplefilter("error", DuplicateNodenameWarning)
        runtime_app = app if app is not None else _load_celery_app()
        if mode == "broker-only":
            return _broker_snapshot(runtime_app)

        inspector = runtime_app.control.inspect(timeout=5.0)
        snapshot: dict[str, object] = {"ping": inspector.ping()}
        if mode == "workers":
            snapshot["active_queues"] = inspector.active_queues()
            return snapshot

        snapshot.update(
            {
                "active": inspector.active(safe=True),
                "reserved": inspector.reserved(),
                "scheduled": inspector.scheduled(),
                "active_queues": inspector.active_queues(),
            }
        )
        snapshot.update(_broker_snapshot(runtime_app))
        return snapshot


class _CliArgumentError(Exception):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise _CliArgumentError


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(description="Count-only Celery release gate")
    parser.add_argument("mode", choices=("drain", "broker-only", "workers"))
    parser.add_argument("--expected-workers", type=_expected_workers, default=3)
    parser.add_argument("--wait-seconds", type=_wait_seconds, default=0.0)
    parser.add_argument("--poll-seconds", type=_poll_seconds, default=10.0)
    return parser


def _expected_workers(raw_value: str) -> int:
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected workers must be 3") from exc
    if value != 3:
        raise argparse.ArgumentTypeError("expected workers must be 3")
    return value


def _wait_seconds(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid wait seconds") from exc
    if not 0.0 <= value <= _MAX_WAIT_SECONDS:
        raise argparse.ArgumentTypeError("invalid wait seconds")
    return value


def _poll_seconds(raw_value: str) -> float:
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("invalid poll seconds") from exc
    if not 0.1 <= value <= _MAX_POLL_SECONDS:
        raise argparse.ArgumentTypeError("invalid poll seconds")
    return value


def main(argv: list[str] | None = None, *, app: Any | None = None) -> int:
    raw_argv = list(argv) if argv is not None else sys.argv[1:]
    try:
        args = _parser().parse_args(raw_argv)
    except _CliArgumentError:
        payload: dict[str, bool | str] = {
            "error": "CELERY_RELEASE_GATE_FAILED",
            "ready": False,
        }
        if raw_argv and raw_argv[0] in {"drain", "broker-only", "workers"}:
            payload["mode"] = raw_argv[0]
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 2
    mode: GateMode = args.mode
    required_ready_samples = 2 if mode in {"drain", "broker-only"} else 1
    consecutive_ready_samples = 0
    deadline = time.monotonic() + args.wait_seconds if args.wait_seconds > 0 else None
    while True:
        try:
            report = evaluate_snapshot(mode, _collect_snapshot(mode, app))
        except Exception:
            report = _failed_report(mode)
        if report.ready:
            consecutive_ready_samples += 1
        else:
            consecutive_ready_samples = 0
        if consecutive_ready_samples >= required_ready_samples:
            break
        if report.ready:
            report = replace(report, ready=False)
        if deadline is None:
            if consecutive_ready_samples > 0:
                continue
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(args.poll_seconds, remaining))
    print(json.dumps(report.as_payload(), sort_keys=True, separators=(",", ":")))
    return 0 if report.ready else 2


if __name__ == "__main__":
    raise SystemExit(main())
