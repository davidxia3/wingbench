"""Sidecar measurements, independent of scientific grades and budget accounting.

Do not add nested/overlapping spans to obtain episode elapsed time. Rusage
children are *reaped* children only. Use an isolated cgroup for full Linux
process-tree accounting, especially for agents that leave background work.
Never write request bodies, credentials, or model response text here.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import time
import uuid

SCHEMA = "wingbench-resource-event-v1"
try:
    CLOCK_ID = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
except OSError:
    CLOCK_ID = "process-" + str(os.getpid())


def stamp():
    return {"utc": datetime.now(timezone.utc).isoformat(),
            "monotonic_ns": time.monotonic_ns(), "pid": os.getpid(), "clock_id": CLOCK_ID}


def cpu_snapshot():
    own = resource.getrusage(resource.RUSAGE_SELF)
    children = resource.getrusage(resource.RUSAGE_CHILDREN)
    return {"self_user_seconds": own.ru_utime, "self_system_seconds": own.ru_stime,
            "reaped_children_user_seconds": children.ru_utime,
            "reaped_children_system_seconds": children.ru_stime}


def cpu_delta(before, after):
    return {key: after[key] - before[key] for key in before}


def read_cgroup_cpu(path):
    """One explicitly selected scope; callers must not sum ancestors/children."""
    try:
        values = dict(line.split() for line in (Path(path) / "cpu.stat").read_text().splitlines())
        return {key: int(values[key]) for key in ("usage_usec", "user_usec", "system_usec")}
    except (OSError, KeyError, ValueError):
        return None


class ResourceLog:
    def __init__(self, path, episode=None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.episode = episode
        self.writer_id = str(uuid.uuid4())

    def event(self, kind, **fields):
        row = {"schema": SCHEMA, "writer_id": self.writer_id, "episode": self.episode,
               **stamp(), "kind": kind, **fields}
        # Each process writes its own file. Flush each event to retain failures.
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(row, allow_nan=False, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        return row

    @contextmanager
    def span(self, stage, **fields):
        span_id = str(uuid.uuid4())
        self.event("span_start", span_id=span_id, stage=stage, **fields)
        before, started = cpu_snapshot(), time.monotonic_ns()
        result = {}
        status, error_type = "ok", None
        try:
            yield result
        except BaseException as exc:
            status, error_type = "error", type(exc).__name__
            raise
        finally:
            elapsed = (time.monotonic_ns() - started) / 1e9
            self.event("span_end", span_id=span_id, stage=stage, status=status,
                       error_type=error_type, wall_seconds=elapsed,
                       cpu=cpu_delta(before, cpu_snapshot()),
                       cpu_scope="self_and_reaped_children_only", **fields, **result)


def _count(mapping, *keys):
    for key in keys:
        if key in mapping:
            value = mapping[key]
            # Missing, malformed, negative and boolean values are not zero.
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                return value
    return None


def normalized_usage(usage):
    usage = usage if isinstance(usage, dict) else {}
    prompt = usage.get("prompt_tokens_details") or {}
    completion = usage.get("completion_tokens_details") or {}
    prompt = prompt if isinstance(prompt, dict) else {}
    completion = completion if isinstance(completion, dict) else {}
    cached = _count(prompt, "cached_tokens")
    if cached is None:
        cached = _count(usage, "cached_input_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens")
    return {"input_tokens": _count(usage, "prompt_tokens", "input_tokens"),
            "output_tokens": _count(usage, "completion_tokens", "output_tokens"),
            "cached_input_tokens": cached,
            "reasoning_tokens": _count(completion, "reasoning_tokens"),
            "total_tokens": _count(usage, "total_tokens"),
            "input_definition": "provider_reported_input; cache inclusion depends on provider"}


def usage_fields(usage):
    """Allowlist provider usage metadata, excluding arbitrary server strings."""
    if not isinstance(usage, dict):
        return None
    keys = ("prompt_tokens", "input_tokens", "completion_tokens", "output_tokens", "total_tokens",
            "cached_input_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens", "cache_read_input_tokens",
            "prompt_tokens_details", "completion_tokens_details")
    def numeric(value):
        if isinstance(value, dict):
            return {k: numeric(v) for k, v in value.items() if isinstance(v, (int, float, dict)) or v is None}
        return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None
    return {k: numeric(usage[k]) for k in keys if k in usage}


def measured_model_call(call, emit, *args, **kwargs):
    """Wrap one actual HTTP attempt; each retry invokes this separately.

    Emit through the controller's existing session stream. This also works in
    nested planning processes without granting access to manager-private files.
    """
    request_id = str(uuid.uuid4())
    emit({"type": "resource", "schema": SCHEMA, "kind": "model_request_start",
          "request_id": request_id, **stamp()})
    started = time.monotonic_ns()
    try:
        response = call(*args, **kwargs)
    except BaseException as exc:
        emit({"type": "resource", "schema": SCHEMA, "kind": "model_request_end",
              "request_id": request_id, **stamp(), "status": "error", "error_type": type(exc).__name__,
              "wall_seconds": (time.monotonic_ns() - started) / 1e9,
              "usage_raw": None, "usage": normalized_usage(None), "usage_available": False})
        raise
    usage = response.get("usage") if isinstance(response, dict) else None
    normalized = normalized_usage(usage)
    emit({"type": "resource", "schema": SCHEMA, "kind": "model_request_end",
          "request_id": request_id, **stamp(), "status": "response",
          "wall_seconds": (time.monotonic_ns() - started) / 1e9,
          "usage_raw": usage_fields(usage), "usage": normalized,
          "usage_available": normalized["input_tokens"] is not None and normalized["output_tokens"] is not None})
    return response
