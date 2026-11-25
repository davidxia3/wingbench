"""The two evaluators behind ``run_episode.py``.

``RecordedEvaluator`` answers a wing from the released records. It is for
testing the plumbing. ``CfdEvaluator`` grades a wing with the released CFD
grader through ``benchmark.cfd_local``. Both return a ``GradeResult`` and
both are turned into a trace grade with ``to_trace_grade``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from wingbench.benchmark.episode import V2Protocol, make_grade

from benchmark.common import DATA, OUTCOMES, TASK_NAMES, geometry_sha256
from benchmark.optimizer_api import TaskSpec
from benchmark.records import read_table


class UngradedWingError(LookupError):
    """The recorded evaluator has no grade for this wing under this task."""


@dataclass(frozen=True)
class GradeResult:
    """One CFD grade as the runner uses it."""

    geometry: tuple[float, ...]
    wing: str
    delivered: bool
    ld: float | None
    outcome: str
    reason_codes: tuple[str, ...]
    source: str
    evaluator_cpu_seconds: float = 0.0
    core_seconds: float | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.outcome not in OUTCOMES:
            raise ValueError(f"unknown outcome {self.outcome!r}")
        if self.delivered and self.ld is None:
            raise ValueError("a delivered grade needs an L/D")
        if not self.delivered and self.ld is not None:
            raise ValueError("a nondelivery cannot carry an L/D")

    def to_trace_grade(self, protocol: V2Protocol) -> dict[str, Any]:
        return make_grade(
            geometry=self.geometry,
            protocol=protocol,
            delivery=self.delivered,
            conditional_value=self.ld,
            reason_codes=list(self.reason_codes),
            artifact_id=self.source,
        )


class RecordedEvaluator:
    """Answer a wing by its hash from ``data/evaluations.csv``.

    A wing may appear in several records, because two episodes of the same
    task sometimes queried the same wing. The answer is the earliest record by
    ``recorded_at``, then by evaluation id. Nine wings have records that
    disagree with each other. A wing graded under another task is refused,
    because its grade was made at a different lift target.
    """

    def __init__(self, task: TaskSpec, data_dir: Path = DATA) -> None:
        self.task = task
        self.data_dir = Path(data_dir)
        self.rows_by_wing: dict[str, list[dict[str, str]]] = {}
        for row in read_table(self.data_dir / "evaluations.csv"):
            if row["wing"]:
                self.rows_by_wing.setdefault(row["wing"], []).append(row)
        for rows in self.rows_by_wing.values():
            rows.sort(key=lambda row: (row["recorded_at"], row["evaluation"]))
        self.known_wings = {row["wing"] for row in read_table(self.data_dir / "wings.csv")}

    def grade(self, geometry: Sequence[float], label: str = "") -> GradeResult:
        values = tuple(float(value) for value in geometry)
        wing = geometry_sha256(values)
        rows = self.rows_by_wing.get(wing)
        if not rows:
            raise UngradedWingError(
                f"wing {wing[:12]} was never graded in the released records. "
                "The recorded evaluator can only answer the wings listed in "
                "data/wings.csv. Use --evaluator cfd for new wings."
            )
        same_task = [row for row in rows if row["task"] == self.task.name]
        if not same_task:
            tasks = sorted({row["task"] for row in rows})
            raise UngradedWingError(
                f"wing {wing[:12]} was graded under task {', '.join(tasks)} only, "
                f"not under task {self.task.name}. Its grade was made at a different lift target."
            )
        row = same_task[0]
        delivered = row["delivered"] == "True"
        ld = float(row["ld"]) if row["ld"] else None
        if delivered and ld is None:
            raise UngradedWingError(
                f"record {row['evaluation']} delivered but its score was not exported, "
                "so it cannot be replayed"
            )
        outcome = row["outcome"] or ("pass" if delivered else "undetermined")
        reasons = tuple(code for code in row["reasons"].split(";") if code)
        if not reasons:
            reasons = ("complete_score_eligible",) if delivered else ("undetermined",)
        conflicting = len({(r["delivered"], r["ld"]) for r in same_task}) > 1
        return GradeResult(
            geometry=values,
            wing=wing,
            delivered=delivered,
            ld=ld if delivered else None,
            outcome=outcome,
            reason_codes=reasons,
            source=f"recorded:{row['evaluation']}",
            evaluator_cpu_seconds=0.0,
            core_seconds=None,
            detail={
                "evaluation": row["evaluation"],
                "episode": row["episode"],
                "method": row["method"],
                "records_for_wing_and_task": len(same_task),
                "records_disagree": conflicting,
            },
        )


class CfdEvaluator:
    """Grade a wing with the released CFD grader on this machine, through ``grade_local``."""

    def __init__(self, task: TaskSpec, work_root: Path, *, run_id: str = "local", runtime_probe: bool = True) -> None:
        from benchmark.grade_local import LocalGrader

        self.task = task
        self.grader = LocalGrader(task, Path(work_root), run_id=run_id, runtime_probe=runtime_probe)
        from benchmark.resource_logging import ResourceLog
        self.resource_log = ResourceLog(Path(work_root) / "cfd_resources.jsonl", run_id)
        original_runner = self.grader.process_runner

        def measured_process(command, cwd, log_path, timeout, env):
            stage = "mesh" if any(str(arg).endswith("gen-mesh.py") for arg in command) else "flow"
            with self.resource_log.span(stage, case=str(cwd), log_path=str(log_path),
                                        timeout_seconds=timeout) as measured:
                result = original_runner(command, cwd, log_path, timeout, env)
                measured.update(process_record={key: result.get(key) for key in
                                ("termination_reason", "returncode", "wall_seconds", "started_at", "finished_at")})
                return result

        self.grader.process_runner = measured_process

    @property
    def runtime_status(self) -> dict[str, Any]:
        return dict(self.grader.runtime_status)

    def grade(self, geometry: Sequence[float], label: str) -> GradeResult:
        values = tuple(float(value) for value in geometry)
        result = self.grader.grade(values, label)
        grade = result["benchmark_grade"]
        return GradeResult(
            geometry=values,
            wing=result["geometry"]["sha256"],
            delivered=bool(grade["delivered"]),
            ld=grade["ld"],
            outcome=str(grade["outcome"]),
            reason_codes=tuple(grade["reason_codes"]),
            source=f"cfd:{result['result_sha256']}",
            evaluator_cpu_seconds=float(result["timing"]["core_seconds"]),
            core_seconds=float(result["timing"]["core_seconds"]),
            detail={
                "result_path": result["result_path"],
                "grading_mode": result["grading_mode"],
                "protocol_id": result["protocol_id"],
                "admissibility": result["aggregate"]["admissibility"],
                "mesh_outcome": result["mesh"]["outcome"],
                "wall_seconds": result["timing"]["wall_seconds"],
                "runtime_probe_report_sha256": self.grader.runtime_status.get("report_sha256"),
                "runtime_fingerprint_matches_campaign": self.grader.runtime_status.get("matches_campaign"),
            },
        )


def make_evaluator(kind: str, task: TaskSpec, **options: Any):
    if kind == "recorded":
        return RecordedEvaluator(task, data_dir=Path(options.get("data_dir") or DATA))
    if kind == "cfd":
        return CfdEvaluator(
            task,
            Path(options["work_root"]),
            run_id=str(options.get("run_id") or "local"),
            runtime_probe=bool(options.get("runtime_probe", True)),
        )
    raise ValueError(f"unknown evaluator {kind!r}")


def task_names() -> tuple[str, ...]:
    return TASK_NAMES
