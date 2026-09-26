#!/usr/bin/env python3
"""Run one WingBench episode with your optimizer.

    python3.11 benchmark/run_episode.py --optimizer module:Class \
        --task low --seed 1 --evaluator recorded --out submissions/mine

The runner drives the released episode machine through 32 online CFD queries
with checkpoints at 0, 1, 2, 4, 8, 16 and 32. It writes the hash chained
trace and the record tables in the schemas of ``data/``. The optimizer runs in
a child process with its own working directory and receives only what
``observe`` and ``ctx`` expose. Every private grade is kept in memory and
written sealed until the episode ends. Then the key and the grades are
revealed next to the records.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import traceback
from typing import Any, Callable, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wingbench.benchmark.episode import (  # noqa: E402
    EpisodeMachine,
    V2ProtocolError,
    validate_geometry,
)

from benchmark import sealed  # noqa: E402
from benchmark.common import (  # noqa: E402
    CHEAP_BATCH_SIZE,
    CHEAP_BUDGET,
    DATA,
    PROFILE,
    STUDY,
    TASK_NAMES,
    as_geometry,
    canonical_json_bytes,
    canonical_sha256,
    geometry_sha256,
    json_ready,
    load_box,
    load_episode_protocol,
    load_tasks,
    out_of_box_report,
    utc_now,
)
from benchmark.evaluators import GradeResult, UngradedWingError, make_evaluator  # noqa: E402
from benchmark.optimizer_api import Box, OnlineResult, TaskSpec  # noqa: E402
from benchmark.records import (  # noqa: E402
    EPISODE_COLUMNS,
    EVALUATION_COLUMNS,
    RECOMMENDATION_COLUMNS,
    WING_COLUMNS,
    wing_row,
    write_table,
)
from benchmark.surrogate_local import CheapCounter, SurrogateUnavailable  # noqa: E402

EPISODE_ID_PATTERN = re.compile(r"U(\d{3,})\Z")
SUMMARY_SCHEMA = "wingbench-submission-episode-summary-v1"
REVEAL_SCHEMA = "wingbench-submission-private-grades-v1"


class OptimizerError(RuntimeError):
    """The optimizer process failed or returned something the rules refuse."""


class OptimizerClient:
    """Talk to ``benchmark.optimizer_host`` running in a child process."""

    def __init__(self, workspace: Path, log_path: Path) -> None:
        env = dict(os.environ)
        env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        env["PYTHONUNBUFFERED"] = "1"
        self.log = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
        self.process = subprocess.Popen(
            [sys.executable, "-m", "benchmark.optimizer_host"],
            cwd=str(workspace),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self.log,
            text=True,
            bufsize=1,
            env=env,
        )
        self.next_id = 0
        self.call_handler: Callable[[dict[str, Any]], Any] | None = None

    def _send(self, message: dict[str, Any]) -> None:
        assert self.process.stdin is not None
        self.process.stdin.write(json.dumps(message, allow_nan=False) + "\n")
        self.process.stdin.flush()

    def request(self, op: str, **fields: Any) -> Any:
        request_id = self.next_id
        self.next_id += 1
        self._send({"id": request_id, "op": op, **fields})
        assert self.process.stdout is not None
        while True:
            line = self.process.stdout.readline()
            if not line:
                raise OptimizerError(
                    "the optimizer process ended before it answered. See optimizer.log in its working directory."
                )
            message = json.loads(line)
            if "call" in message:
                try:
                    if self.call_handler is None:
                        raise RuntimeError("the runner accepts no calls right now")
                    reply = self.call_handler(message)
                except Exception as exc:  # noqa: BLE001
                    self._send({"reply_error": f"{type(exc).__name__}: {exc}"})
                else:
                    self._send({"reply": reply})
                continue
            if message.get("id") != request_id:
                raise OptimizerError("the optimizer process answered out of order")
            if not message.get("ok"):
                raise OptimizerError(
                    f"the optimizer failed during {op}: {message.get('error')}\n{message.get('traceback', '')}"
                )
            return message.get("result")

    def close(self) -> None:
        try:
            if self.process.poll() is None:
                try:
                    self._send({"id": self.next_id, "op": "shutdown"})
                except (BrokenPipeError, OSError):
                    pass
                try:
                    self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.process.kill()
        finally:
            self.log.close()


class EpisodeRunner:
    def __init__(
        self,
        *,
        task: TaskSpec,
        box: Box,
        seed: int,
        episode_id: str,
        episode_dir: Path,
        evaluator: Any,
        evaluator_kind: str,
        surrogate: Any,
        optimizer_spec: str,
        optimizer_options: dict[str, Any],
        method_name: str | None,
    ) -> None:
        self.task = task
        self.box = box
        self.seed = int(seed)
        self.episode_id = episode_id
        self.episode_dir = episode_dir
        self.evaluator = evaluator
        self.evaluator_kind = evaluator_kind
        self.surrogate = surrogate
        self.optimizer_spec = optimizer_spec
        self.optimizer_options = optimizer_options
        self.method_name_override = method_name
        self.protocol = load_episode_protocol()
        self.counter = CheapCounter(CHEAP_BUDGET)
        self.history: list[OnlineResult] = []
        self.online_grades: dict[str, GradeResult] = {}
        self.geometries: dict[str, tuple[float, ...]] = {}
        self.evaluation_rows: list[dict[str, Any]] = []
        self.recommendation_rows: list[dict[str, Any]] = []
        self.private_grades: list[dict[str, Any]] = []
        self.checkpoints: list[dict[str, Any]] = []
        self.key = sealed.new_key()
        self.sealed_path = episode_dir / "private_grades.enc"
        self.started_at = utc_now()
        self.machine: EpisodeMachine | None = None
        self.client: OptimizerClient | None = None
        self.identity: dict[str, Any] = {}
        self.surrogate_seconds = 0.0

    # ----------------------------------------------------------------- helpers
    def state(self) -> dict[str, Any]:
        assert self.machine is not None
        return {
            "cheap_queries_used": self.counter.used,
            "online_queries_used": int(self.machine.budget["online_cfd_queries_used"]),
            "surrogate_available": self.surrogate is not None,
            "history": [row.to_dict() for row in self.history],
        }

    def check_geometry(self, values: Sequence[float], label: str) -> tuple[float, ...]:
        geometry = as_geometry(values, label)
        try:
            validate_geometry(geometry, self.protocol)
        except V2ProtocolError as exc:
            problems = out_of_box_report(geometry, self.box)
            raise OptimizerError(
                f"{label} lies outside the design box. {exc}. " + " ".join(problems)
            ) from exc
        return geometry

    def handle_call(self, message: dict[str, Any]) -> Any:
        if message.get("call") != "cheap_query":
            raise RuntimeError(f"unknown call {message.get('call')!r}")
        if self.surrogate is None:
            raise SurrogateUnavailable(
                "cheap_query needs the surrogate weights. Start the runner with --surrogate-model-dir."
            )
        rows = [self.check_geometry(row, f"cheap_query geometry {index}") for index, row in enumerate(message["geometries"])]
        self.counter.charge(len(rows))
        started = time.perf_counter()
        results = self.surrogate.predict(rows)
        elapsed = time.perf_counter() - started
        self.surrogate_seconds += elapsed
        assert self.machine is not None
        per_wing = elapsed / len(rows)
        for start in range(0, len(rows), CHEAP_BATCH_SIZE):
            chunk_rows = rows[start : start + CHEAP_BATCH_SIZE]
            chunk_results = results[start : start + CHEAP_BATCH_SIZE]
            self.machine.cheap_query(
                chunk_rows,
                [json_ready(row.to_dict()) for row in chunk_results],
                cache_hit=False,
                resources={"evaluator_cpu_seconds": per_wing * len(chunk_rows)},
            )
        return {"results": [json_ready(row.to_dict()) for row in results], "state": self.state()}

    def grade(self, geometry: tuple[float, ...], label: str) -> GradeResult:
        try:
            return self.evaluator.grade(geometry, label)
        except UngradedWingError as exc:
            raise OptimizerError(str(exc)) from exc

    def evaluation_row(self, kind: str, index: int, grade: GradeResult, *, query: int | None, checkpoint: int | None) -> dict[str, Any]:
        tag = f"q{query:02d}" if kind == "online" else f"ck{checkpoint:02d}"
        return {
            "evaluation": f"{self.episode_id}-E{index:03d}",
            "episode": self.episode_id,
            "study": STUDY,
            "method": self.method_name,
            "task": self.task.name,
            "seed": self.seed,
            "kind": kind,
            "query": query,
            "checkpoint": checkpoint,
            "wing": grade.wing,
            "delivered": grade.delivered,
            "ld": grade.ld,
            "outcome": grade.outcome,
            "reasons": ";".join(grade.reason_codes),
            "recorded_at": utc_now(),
            "original_id": f"{self.episode_id}-{kind}-{tag}-{grade.wing[:12]}",
        }

    def seal_private_grades(self) -> None:
        blob = sealed.seal(self.key, canonical_json_bytes(json_ready(self.private_grades)))
        temporary = self.sealed_path.with_suffix(".enc.tmp")
        temporary.write_bytes(blob)
        os.replace(temporary, self.sealed_path)

    # ------------------------------------------------------------------- steps
    def checkpoint(self, checkpoint: int) -> None:
        assert self.machine is not None and self.client is not None
        values = self.client.request("recommend", state=self.state())
        geometry = self.check_geometry(values, f"recommendation at checkpoint {checkpoint}")
        wing = geometry_sha256(geometry)
        self.geometries[wing] = geometry
        if wing in self.online_grades:
            grade = self.online_grades[wing]
            trace_grade = self.machine.checkpoint(geometry)
            graded_by = "online"
            print(f"[{self.episode_id}] checkpoint {checkpoint}: wing {wing[:12]} reuses its online result "
                  f"({'delivered ' + format(grade.ld, '.3f') if grade.delivered else 'not delivered'})", flush=True)
        else:
            label = f"ck{checkpoint:02d}-{wing[:12]}"
            print(f"[{self.episode_id}] checkpoint {checkpoint}: wing {wing[:12]} needs a private grade", flush=True)
            grade = self.grade(geometry, label)
            trace_grade = self.machine.checkpoint(
                geometry,
                fresh_hidden_grade=grade.to_trace_grade(self.protocol),
                resources={"evaluator_cpu_seconds": grade.evaluator_cpu_seconds},
            )
            graded_by = "private"
            row = self.evaluation_row(
                "private", len(self.evaluation_rows) + 1, grade, query=None, checkpoint=checkpoint
            )
            self.evaluation_rows.append(row)
            self.private_grades.append({
                "checkpoint": checkpoint,
                "wing": wing,
                "evaluation": row["evaluation"],
                "recorded_at": row["recorded_at"],
                "grade": trace_grade,
                "result": {
                    "delivered": grade.delivered,
                    "ld": grade.ld,
                    "outcome": grade.outcome,
                    "reason_codes": list(grade.reason_codes),
                    "source": grade.source,
                    "core_seconds": grade.core_seconds,
                },
            })
            self.seal_private_grades()
        self.recommendation_rows.append({
            "episode": self.episode_id,
            "study": STUDY,
            "method": self.method_name,
            "task": self.task.name,
            "seed": self.seed,
            "checkpoint": checkpoint,
            "wing": wing,
            "delivered": grade.delivered,
            "ld": grade.ld,
            "graded_by": graded_by,
        })
        self.checkpoints.append({
            "checkpoint": checkpoint,
            "wing": wing,
            "graded_by": graded_by,
            "grade_result_sha256": trace_grade["result_sha256"],
        })

    def online_query(self, query: int) -> None:
        assert self.machine is not None and self.client is not None
        values = self.client.request("propose", state=self.state())
        geometry = self.check_geometry(values, f"proposal for online query {query}")
        wing = self.machine.request_online(geometry)
        self.geometries[wing] = geometry
        if wing in self.online_grades:
            grade = self.online_grades[wing]
            print(f"[{self.episode_id}] query {query}: wing {wing[:12]} was already queried in this episode. "
                  "The same result is returned and the query is spent.", flush=True)
        else:
            grade = self.grade(geometry, f"q{query:02d}-{wing[:12]}")
        self.machine.observe_online(
            grade.to_trace_grade(self.protocol),
            resources={"evaluator_cpu_seconds": grade.evaluator_cpu_seconds},
        )
        self.online_grades[wing] = grade
        result = OnlineResult(
            query=query,
            geometry=geometry,
            wing=wing,
            delivered=grade.delivered,
            ld=grade.ld,
            outcome=grade.outcome,
            reason_codes=grade.reason_codes,
        )
        self.history.append(result)
        self.evaluation_rows.append(
            self.evaluation_row("online", len(self.evaluation_rows) + 1, grade, query=query, checkpoint=None)
        )
        status = f"delivered L/D {grade.ld:.3f}" if grade.delivered else f"{grade.outcome} ({', '.join(grade.reason_codes)})"
        print(f"[{self.episode_id}] query {query}: wing {wing[:12]} {status}", flush=True)
        self.client.request("observe", state=self.state(), result=result.to_dict())

    def run(self) -> dict[str, Any]:
        self.episode_dir.mkdir(parents=True, exist_ok=True)
        workspace = self.episode_dir / "optimizer"
        workspace.mkdir(exist_ok=True)
        self.client = OptimizerClient(workspace, workspace / "optimizer.log")
        self.client.call_handler = self.handle_call
        module_name, _, class_name = self.optimizer_spec.partition(":")
        self.identity = self.client.request(
            "init",
            module=module_name,
            **{"class": class_name},
            task=self.task.to_dict(),
            seed=self.seed,
            box=self.box.to_dict(),
            options=self.optimizer_options,
        )
        self.method_name = self.method_name_override or str(self.identity["name"])
        implementation = self.identity.get("source_sha256") or hashlib.sha256(
            self.optimizer_spec.encode("utf-8")
        ).hexdigest()
        self.machine = EpisodeMachine(
            protocol=self.protocol,
            profile=PROFILE,
            episode_id=self.episode_id,
            optimizer_identity={"method_id": self.method_name, "implementation_sha256": implementation},
            instance_id=self.task.task_id,
            matched_seed=self.seed,
        )
        self.machine.reset()
        self.client.request("start", state=self.state())
        self.checkpoint(0)
        for query in range(1, self.task.online_budget + 1):
            self.online_query(query)
            if query in self.task.checkpoints:
                self.checkpoint(query)
        final = self.geometries[self.checkpoints[-1]["wing"]]
        self.machine.terminal(final)
        self.machine.stop()
        return self.finish(status="complete", error=None)

    def finish(self, *, status: str, error: str | None) -> dict[str, Any]:
        assert self.machine is not None
        trace_path = self.episode_dir / ("trace.json" if status == "complete" else "trace.partial.json")
        if self.machine.writer.events:
            self.machine.writer.write(trace_path)
        self.write_records()
        self.seal_private_grades()
        (self.episode_dir / "private_key.hex").write_text(self.key.hex() + "\n", encoding="utf-8")
        reveal = {
            "schema_version": REVEAL_SCHEMA,
            "episode": self.episode_id,
            "sealed_file": self.sealed_path.name,
            "sealed_plaintext_sha256": hashlib.sha256(
                canonical_json_bytes(json_ready(self.private_grades))
            ).hexdigest(),
            "private_grades": json_ready(self.private_grades),
        }
        (self.episode_dir / "private_grades.json").write_text(
            json.dumps(reveal, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        summary = {
            "schema_version": SUMMARY_SCHEMA,
            "episode": self.episode_id,
            "study": STUDY,
            "status": status,
            "error": error,
            "task": self.task.name,
            "task_id": self.task.task_id,
            "seed": self.seed,
            "method": getattr(self, "method_name", None),
            "optimizer": {"spec": self.optimizer_spec, **self.identity, "options": self.optimizer_options},
            "evaluator": self.evaluator_kind,
            "surrogate": {
                "configured": self.surrogate is not None,
                "evaluations": self.counter.used,
                "seconds": self.surrogate_seconds,
            },
            "budget": dict(self.machine.budget),
            "trace_file": trace_path.name if self.machine.writer.events else None,
            "trace_head_sha256": self.machine.trace_head_sha256,
            "online_queries": len(self.history),
            "online_delivered": sum(1 for row in self.history if row.delivered),
            "checkpoints": self.checkpoints,
            "private_grade_count": len(self.private_grades),
            "cfd_runtime": getattr(self.evaluator, "runtime_status", None),
            "started_at": self.started_at,
            "finished_at": utc_now(),
            "python": sys.version.split()[0],
        }
        (self.episode_dir / "summary.json").write_text(
            json.dumps(json_ready(summary), indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary

    def write_records(self) -> None:
        method = getattr(self, "method_name", None) or self.optimizer_spec
        core = [row.core_seconds for row in self.online_grades.values() if row.core_seconds is not None]
        private_core = [
            entry["result"]["core_seconds"]
            for entry in self.private_grades
            if entry["result"]["core_seconds"] is not None
        ]
        timed = core + private_core
        episode_row = {
            "episode": self.episode_id,
            "study": STUDY,
            "method": method,
            "task": self.task.name,
            "target_cl": self.task.target_cl,
            "seed": self.seed,
            "online_queries": len(self.history),
            "cfd_core_seconds": sum(timed) if timed else None,
            "timed_evaluations": len(timed),
            "original_id": f"{self.episode_id}-{self.task.task_id}-s{self.seed}",
        }
        write_table(self.episode_dir / "episodes.csv", EPISODE_COLUMNS, [episode_row])
        write_table(self.episode_dir / "evaluations.csv", EVALUATION_COLUMNS, self.evaluation_rows)
        write_table(self.episode_dir / "recommendations.csv", RECOMMENDATION_COLUMNS, self.recommendation_rows)
        wings = sorted(self.geometries)
        write_table(self.episode_dir / "wings.csv", WING_COLUMNS, [wing_row(self.geometries[wing]) for wing in wings])


def next_episode_id(out: Path) -> str:
    used = []
    if out.is_dir():
        for child in out.iterdir():
            match = EPISODE_ID_PATTERN.match(child.name)
            if match and child.is_dir():
                used.append(int(match.group(1)))
    return f"U{(max(used) + 1 if used else 0):03d}"


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--optimizer", required=True, help="module:Class or path/to/file.py:Class of your Optimizer subclass")
    parser.add_argument("--task", required=True, choices=TASK_NAMES)
    parser.add_argument("--seed", required=True, type=int)
    parser.add_argument("--evaluator", required=True, choices=("recorded", "cfd"))
    parser.add_argument("--out", required=True, type=Path, help="folder that receives one subfolder per episode")
    parser.add_argument("--episode-id", default=None, help="for example U000. Default is the next free id in --out")
    parser.add_argument("--method-name", default=None, help="name shown in the tables. Default is the optimizer's name attribute or class name")
    parser.add_argument("--optimizer-options", default="{}", help="JSON object passed to the optimizer as keyword arguments")
    parser.add_argument("--surrogate-model-dir", type=Path, default=None, help="folder with model_config and best_model_weights")
    parser.add_argument("--flogen-root", type=Path, default=None, help="folder that contains the flowvae package, if it is not installed")
    parser.add_argument("--surrogate-device", default="cpu")
    parser.add_argument("--cfd-work", type=Path, default=None, help="work folder for CFD cases. Default is <out>/<episode>/cfd")
    parser.add_argument("--no-runtime-probe", action="store_true", help="skip the released runtime probe before CFD grading")
    parser.add_argument("--data", type=Path, default=DATA, help="folder with the released tables, for the recorded evaluator")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_arguments(argv)
    if ":" not in args.optimizer:
        print("--optimizer must look like module:Class", file=sys.stderr)
        return 2
    try:
        options = json.loads(args.optimizer_options)
        if not isinstance(options, dict):
            raise ValueError("not a JSON object")
    except ValueError as exc:
        print(f"--optimizer-options must be a JSON object: {exc}", file=sys.stderr)
        return 2
    tasks = load_tasks()
    task = tasks[args.task]
    box = load_box()
    args.out.mkdir(parents=True, exist_ok=True)
    episode_id = args.episode_id or next_episode_id(args.out)
    if not EPISODE_ID_PATTERN.match(episode_id):
        print("--episode-id must look like U000", file=sys.stderr)
        return 2
    episode_dir = args.out / episode_id
    if episode_dir.exists() and any(episode_dir.iterdir()):
        print(f"{episode_dir} already exists and is not empty. Choose another --episode-id.", file=sys.stderr)
        return 2
    episode_dir.mkdir(parents=True, exist_ok=True)

    surrogate = None
    if args.surrogate_model_dir is not None:
        from benchmark.surrogate_local import LocalSurrogate

        surrogate = LocalSurrogate(
            task,
            args.surrogate_model_dir,
            flogen_root=args.flogen_root,
            device=args.surrogate_device,
        )
    if args.evaluator == "recorded":
        evaluator = make_evaluator("recorded", task, data_dir=args.data)
    else:
        from scripts.cfd.grading_protocol import ProtocolError

        work_root = args.cfd_work if args.cfd_work is not None else episode_dir / "cfd"
        try:
            evaluator = make_evaluator(
                "cfd", task, work_root=work_root, run_id=episode_id, runtime_probe=not args.no_runtime_probe
            )
        except ProtocolError as exc:
            from benchmark.grade_local import environment_report

            print(f"[cfd] the CFD grader cannot run here. {exc}", file=sys.stderr)
            print("[cfd] what this machine has:", file=sys.stderr)
            print(json.dumps(environment_report(task), indent=2), file=sys.stderr)
            return 2
        status = evaluator.runtime_status
        if not status.get("matches_campaign"):
            print(
                "[cfd] this machine is not known to match the campaign's runtime. "
                f"Grading proceeds and the probe result is recorded. {status.get('detail')}",
                flush=True,
            )

    runner = EpisodeRunner(
        task=task,
        box=box,
        seed=args.seed,
        episode_id=episode_id,
        episode_dir=episode_dir,
        evaluator=evaluator,
        evaluator_kind=args.evaluator,
        surrogate=surrogate,
        optimizer_spec=args.optimizer,
        optimizer_options=options,
        method_name=args.method_name,
    )
    print(f"[{episode_id}] task {task.name} (target C_L {task.target_cl}), seed {args.seed}, evaluator {args.evaluator}", flush=True)
    code = 0
    try:
        summary = runner.run()
        delivered = sum(1 for row in runner.recommendation_rows if row["delivered"])
        print(f"[{episode_id}] complete. {summary['online_delivered']}/{summary['online_queries']} online queries delivered, "
              f"{delivered}/{len(runner.recommendation_rows)} recommendations delivered, "
              f"{summary['private_grade_count']} private grades. Files in {episode_dir}", flush=True)
    except Exception as exc:  # noqa: BLE001
        code = 1
        message = f"{type(exc).__name__}: {exc}"
        print(f"[{episode_id}] FAILED. {message}", file=sys.stderr, flush=True)
        traceback.print_exc()
        if runner.machine is not None:
            try:
                runner.finish(status="failed", error=message)
            except Exception as inner:  # noqa: BLE001
                print(f"[{episode_id}] could not write the partial records: {inner}", file=sys.stderr)
    finally:
        if runner.client is not None:
            runner.client.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
