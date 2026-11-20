"""Run one optimizer inside its own process.

``run_episode.py`` starts this module as a child process with the optimizer's
working directory as its current directory. The two processes talk over the
child's standard input and output with one JSON object per line. The child
never receives a private grade. Anything the optimizer prints goes to
``optimizer.log`` in its working directory, not into the channel.

Messages from the runner carry an ``id`` and an ``op``. The child answers with
the same ``id``. While it handles an ``op`` the child may ask the runner for a
surrogate batch with a ``call`` message and waits for the ``reply``.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import os
from pathlib import Path
import sys
import traceback
import time
from typing import Any, Sequence, TextIO

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.optimizer_api import (  # noqa: E402
    DESIGN_DIM,
    Box,
    CheapResult,
    OnlineResult,
    Optimizer,
    TaskSpec,
)
from benchmark.resource_logging import cpu_snapshot, cpu_delta  # noqa: E402


class Channel:
    """The line protocol between the child and the runner."""

    def __init__(self, reader: TextIO, writer: TextIO) -> None:
        self.reader = reader
        self.writer = writer

    def send(self, message: dict[str, Any]) -> None:
        self.writer.write(json.dumps(message, allow_nan=False) + "\n")
        self.writer.flush()

    def receive(self) -> dict[str, Any] | None:
        line = self.reader.readline()
        if not line:
            return None
        return json.loads(line)

    def call(self, message: dict[str, Any]) -> Any:
        self.send(message)
        answer = self.receive()
        if answer is None:
            raise RuntimeError("the runner closed the channel")
        if "reply_error" in answer:
            raise RuntimeError(str(answer["reply_error"]))
        return answer["reply"]


class HostContext:
    """The ``EpisodeContext`` the optimizer sees inside the child process."""

    def __init__(self, task: TaskSpec, box: Box, seed: int, channel: Channel) -> None:
        self.task = task
        self.box = box
        self.seed = int(seed)
        self._channel = channel
        self._cheap_used = 0
        self._online_used = 0
        self._surrogate = False
        self._history: tuple[OnlineResult, ...] = ()

    def update(self, state: dict[str, Any]) -> None:
        self._cheap_used = int(state["cheap_queries_used"])
        self._online_used = int(state["online_queries_used"])
        self._surrogate = bool(state["surrogate_available"])
        self._history = tuple(OnlineResult.from_dict(row) for row in state["history"])

    @property
    def history(self) -> tuple[OnlineResult, ...]:
        return self._history

    @property
    def online_queries_used(self) -> int:
        return self._online_used

    @property
    def remaining_online_queries(self) -> int:
        return self.task.online_budget - self._online_used

    @property
    def cheap_queries_used(self) -> int:
        return self._cheap_used

    @property
    def remaining_cheap_queries(self) -> int:
        return self.task.cheap_budget - self._cheap_used

    @property
    def surrogate_available(self) -> bool:
        return self._surrogate

    def cheap_query(self, geometries: Sequence[Sequence[float]]) -> list[CheapResult]:
        rows: list[list[float]] = []
        for index, geometry in enumerate(geometries):
            values = [float(value) for value in geometry]
            if len(values) != DESIGN_DIM:
                raise ValueError(f"cheap_query geometry {index} has {len(values)} values, not {DESIGN_DIM}")
            rows.append(values)
        if not rows:
            return []
        reply = self._channel.call({"call": "cheap_query", "geometries": rows})
        self.update(reply["state"])
        return [CheapResult.from_dict(row) for row in reply["results"]]


def load_class(module_name: str, class_name: str) -> type[Optimizer]:
    """Import ``module:Class``. The module part may also be a path to a .py file."""
    if module_name.endswith(".py"):
        path = Path(module_name).resolve()
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = module
        spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    cls = getattr(module, class_name)
    if not (inspect.isclass(cls) and issubclass(cls, Optimizer)):
        raise TypeError(f"{module_name}:{class_name} is not a subclass of benchmark.optimizer_api.Optimizer")
    return cls


def source_identity(cls: type) -> dict[str, Any]:
    path = inspect.getsourcefile(cls)
    if path is None:
        return {"source_file": None, "source_sha256": None}
    source = Path(path).resolve()
    try:
        shown = str(source.relative_to(ROOT))
    except ValueError:
        shown = source.name
    return {"source_file": shown, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest()}


def geometry_list(values: Any) -> list[float]:
    try:
        result = [float(value) for value in values]
    except (TypeError, ValueError) as exc:
        raise TypeError("the optimizer must return a sequence of 38 numbers") from exc
    if len(result) != DESIGN_DIM:
        raise ValueError(f"the optimizer returned {len(result)} values, not {DESIGN_DIM}")
    return result


def main() -> int:
    protocol_out = os.fdopen(os.dup(sys.stdout.fileno()), "w", buffering=1)
    sys.stdout = sys.stderr
    channel = Channel(sys.stdin, protocol_out)
    optimizer: Optimizer | None = None
    ctx: HostContext | None = None
    while True:
        message = channel.receive()
        if message is None:
            return 0
        request_id = message.get("id")
        op = message.get("op")
        cpu_before = cpu_snapshot()
        operation_started = time.monotonic_ns()
        def measured_reply(payload):
            payload["resource_measurement"] = {
                "operation": op,
                "wall_seconds": (time.monotonic_ns() - operation_started) / 1e9,
                "cpu": cpu_delta(cpu_before, cpu_snapshot()),
                "cpu_scope": "optimizer_self_and_reaped_children; excludes runner_surrogate",
            }
            channel.send(payload)
        try:
            if op == "init":
                cls = load_class(str(message["module"]), str(message["class"]))
                task = TaskSpec.from_dict(message["task"])
                box = Box.from_dict(message["box"])
                seed = int(message["seed"])
                optimizer = cls(task=task, seed=seed, box=box, **dict(message.get("options") or {}))
                ctx = HostContext(task, box, seed, channel)
                identity = source_identity(cls)
                identity["name"] = str(getattr(cls, "name", None) or cls.__name__)
                identity["class"] = cls.__name__
                result: Any = identity
            elif op == "shutdown":
                measured_reply({"id": request_id, "ok": True, "result": None})
                return 0
            else:
                if optimizer is None or ctx is None:
                    raise RuntimeError("the optimizer was not initialized")
                ctx.update(message["state"])
                if op == "start":
                    optimizer.start(ctx)
                    result = None
                elif op == "propose":
                    result = geometry_list(optimizer.propose(ctx))
                elif op == "observe":
                    optimizer.observe(OnlineResult.from_dict(message["result"]))
                    result = None
                elif op == "recommend":
                    result = geometry_list(optimizer.recommend())
                else:
                    raise RuntimeError(f"unknown request {op!r}")
            measured_reply({"id": request_id, "ok": True, "result": result})
        except BaseException as exc:  # noqa: BLE001
            measured_reply({
                "id": request_id,
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            })
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                return 1


if __name__ == "__main__":
    raise SystemExit(main())
