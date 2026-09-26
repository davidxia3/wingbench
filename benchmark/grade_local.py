"""Grade one wing with the released CFD grader on this machine.

The released grader ``scripts/cfd/grade_benchmark.py`` cannot run in a
rebuilt environment. Its production path needs an execution authorization
file and a runtime attestation file whose generators are not released, a
publication journal, a worker lock at a fixed path, and a runtime fingerprint
that only the original machines match. Its test path needs pytest and a fake
process runner. This module is the local adapter. It reuses the grader's own
functions for every scientific step and skips only the campaign machinery.

Reused from the released code, in the order the grader calls them.

* ``scripts/cfd/grading_protocol.py``. ``load_protocol`` checks the pinned
  files by content, ``require_mode``, ``reject_parent_overrides`` and
  ``child_environment`` give the exact solver environment that the campaign
  injected into every ``mpirun`` child.
* ``scripts/cfd/task_protocols.py``. ``load_instance_protocol`` derives the
  low and high load protocols from the frozen base.
* ``scripts/cfd/prepare_superwing_cases.py``. ``prepare_geometry_case``
  builds the case folder from the 38 values and the fetched SuperWing
  scripts.
* ``scripts/cfd/grade_benchmark.py``. ``mpi_python_command`` builds the
  ``mpirun -np 16`` command with the exported variables, ``run_process`` runs
  it with the frozen time limit and records the process evidence,
  ``bind_process_environment``, ``artifact``, ``read_json``,
  ``atomic_write_json``, ``probe_runtime``, ``validate_runtime_fingerprint``
  and the helpers ``_expected_case_input``, ``_validate_case_input``,
  ``_validate_post_mesh_derived_geometry``, ``_read_optional_json``,
  ``_mesh_log_crosscheck``, ``_adflow_log_crosscheck``,
  ``_select_final_mesh``, ``_not_run_condition``, ``_fresh_directory`` and
  ``_safe_path_component``.
* ``scripts/cfd/validate_mesh_artifact.py``. ``validate_mesh_artifacts`` and
  ``atomic_write_report``.
* ``scripts/cfd/grade_schema.py``. ``classify_mesh_attempt``,
  ``classify_condition_attempt``, ``finalize_condition`` and
  ``aggregate_wing`` apply the lift matching gate, the convergence gates and
  the whole wing roll up.
* ``wingbench/benchmark/multifidelity_protocol.py``. ``evaluate_ld_envelope``
  applies the (0, 40] envelope of the benchmark objective.

Written here, not reused.

* The loop that strings these calls together. It follows the grader's
  internal ``_grade_wing_impl`` step by step.
* The mapping of the whole wing verdict to the benchmark's delivered flag,
  L/D, outcome type and reason codes. In the campaign an unreleased adapter
  did this. The mapping follows ``data/README.md``.
* The runtime probe handling. The grader requires the probe and the pinned
  fingerprint. Here the probe is attempted, its report hash is recorded, and
  grading proceeds either way.
* The load instance file for the low and high tasks, which is written from
  the current pinned files and then read by the released loader.

Skipped. The benchmark manifest, the execution authorization, the runtime
attestation, the publication preflight and journal, the release lock, the
worker lock and ``validate_result``. The result document keeps the shape of
the released one, sets ``grading_mode`` to ``local``, and names the protocol
it followed, so a trace made this way is clearly not a sealed campaign grade.

On 2026-09-25 this adapter graded one recorded wing on a fresh machine of the
reference type, inside the rebuilt container, and reproduced the recorded
lift, drag, angle of attack, and score exactly at all three conditions.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence

from scripts.cfd import grade_benchmark as grader
from scripts.cfd.grade_schema import (
    RESULT_SCHEMA_VERSION,
    aggregate_wing,
    classify_condition_attempt,
    classify_mesh_attempt,
    finalize_condition,
)
from scripts.cfd.grading_protocol import GradingProtocol, ProtocolError, load_protocol
from scripts.cfd.prepare_superwing_cases import VENDOR, prepare_geometry_case
from scripts.cfd.task_protocols import load_instance_protocol
from scripts.cfd.validate_mesh_artifact import (
    REPORT_SCHEMA_VERSION as MESH_VALIDATION_SCHEMA_VERSION,
    atomic_write_report as atomic_write_mesh_validation,
    validate_mesh_artifacts,
)
from wingbench.benchmark.multifidelity_protocol import evaluate_ld_envelope, resolve_protocol

from benchmark.common import ROOT, canonical_json_bytes, geometry_sha256, json_ready, utc_now
from benchmark.optimizer_api import TaskSpec

GRADING_MODE = "local"
VENDOR_DIR = Path(VENDOR)
VENDOR_FILES = ("gen-mesh.superwing.py", "run-adflow.py", "single-point.py", "original_tip.xyz")
BASE_PROTOCOL_PATH = "configs/cfd/final_protocol_candidate_v2.json"
SEED_SELECTION_PATHS = {
    "low": "configs/cfd/seed_aoa_selection_load_low_v1.json",
    "high": "configs/cfd/seed_aoa_selection_load_high_v1.json",
}
SKIPPED_STEPS = (
    "benchmark_manifest",
    "execution_authorization",
    "runtime_attestation",
    "campaign_publication_preflight",
    "publication_journal",
    "release_lock",
    "worker_lock",
    "runtime_fingerprint_match",
    "validate_result",
)
REQUIRED_MODULES = ("adflow", "baseclasses", "cgnsutilities", "cst_modeling", "mpi4py", "petsc4py", "pyhyp")


def missing_vendor_files() -> list[str]:
    return [name for name in VENDOR_FILES if not (VENDOR_DIR / name).is_file()]


def write_task_instance(task: TaskSpec, destination: Path) -> Path:
    """Write the load instance file that ``task_protocols.load_instance_protocol`` reads.

    Every field is dictated by ``scripts/cfd/task_protocols.py``. The file
    binds the base protocol and the seed selection record by content, so it
    is written from the current files rather than shipped, and the released
    loader then checks all of it.
    """
    from scripts.cfd.task_protocols import ALLOWED, MACH_GRID, SCHEMA

    if task.task_id not in ALLOWED:
        raise ProtocolError(f"{task.task_id} is not a load instance of the base protocol")
    base_path = ROOT / BASE_PROTOCOL_PATH
    base = load_protocol(base_path, verify_files=False)
    seed_path = ROOT / SEED_SELECTION_PATHS[task.name]
    spec = {
        "schema_version": SCHEMA,
        "instance_id": task.task_id,
        "purpose": "Load instance of the CFD grading protocol, written by benchmark/grade_local.py. "
                   "Only the lift target and its angle of attack seed differ from the base protocol.",
        "development_only": True,
        "official": False,
        "sealed": False,
        "mach_grid": list(MACH_GRID),
        "cl_target": ALLOWED[task.task_id]["cl_target"],
        "aoa_seed_deg": ALLOWED[task.task_id]["alpha0_deg"],
        "only_scientific_differences_from_base": ["common_cl_target", "fixed_aoa_seed"],
        "base_protocol_binding": {
            "path": BASE_PROTOCOL_PATH,
            "file_sha256": grader.sha256_file(base_path),
            "canonical_sha256": base.sha256,
        },
        "seed_selection_binding": {
            "path": SEED_SELECTION_PATHS[task.name],
            "file_sha256": grader.sha256_file(seed_path),
        },
    }
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(spec, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return destination


def load_task_protocol(task: TaskSpec, *, verify_files: bool = True, spec_dir: Path | None = None) -> GradingProtocol:
    """The grading protocol of one task, with the pinned files checked."""
    if task.name == "nominal":
        protocol = load_protocol(verify_files=verify_files)
    else:
        folder = Path(spec_dir) if spec_dir is not None else Path(tempfile.mkdtemp(prefix="wingbench-task-"))
        spec = write_task_instance(task, folder / f"{task.task_id}.json")
        protocol = load_instance_protocol(spec, verify_files=verify_files)
    targets = {float(row["cl_target"]) for row in protocol.conditions}
    if targets != {float(task.target_cl)} or float(protocol.seed_alpha0) != float(task.aoa_seed_deg):
        raise ProtocolError(f"the grading protocol of task {task.name} does not match the task settings")
    machs = [float(row["mach"]) for row in protocol.conditions]
    if machs != [float(value) for value in task.mach]:
        raise ProtocolError(f"the grading protocol of task {task.name} has a different Mach grid")
    return protocol


class LocalGrader:
    """Grade wings one at a time under the task's frozen protocol."""

    def __init__(
        self,
        task: TaskSpec,
        work_root: Path,
        *,
        run_id: str = "local",
        runtime_probe: bool = True,
        process_runner: Callable[..., dict[str, Any]] = grader.run_process,
        case_preparer: Callable[..., str] = prepare_geometry_case,
        mesh_validator: Callable[[Path, Path], dict[str, Any]] = validate_mesh_artifacts,
    ) -> None:
        missing = missing_vendor_files()
        if missing:
            raise ProtocolError(
                "the SuperWing case scripts are missing from scripts/cfd/aerotransformer/: "
                + ", ".join(missing)
                + ". Obtain them from the SuperWing authors. The protocol pins them by SHA-256."
            )
        self.task = task
        self.run_id = grader._safe_path_component(run_id, "run_id")
        if process_runner is grader.run_process and shutil.which("mpirun") is None:
            raise ProtocolError(
                "mpirun is not on the path. Every mesh and solve would fail before it starts. "
                "Run the adapter inside the container the protocol pins."
            )
        self.work_root = Path(work_root)
        self.work_root.mkdir(parents=True, exist_ok=True)
        self.protocol = load_task_protocol(task, verify_files=True, spec_dir=self.work_root)
        self.protocol.require_mode("diagnostic")
        self.protocol.reject_parent_overrides()
        if (
            self.protocol.payload["environment"]["require_non_root"]
            and hasattr(os, "geteuid")
            and os.geteuid() == 0
        ):
            raise ProtocolError("the grading protocol refuses to run as root")
        self.process_runner = process_runner
        self.case_preparer = case_preparer
        self.mesh_validator = mesh_validator
        self.child_env = self.protocol.child_environment()
        self.mf_protocol = resolve_protocol(ROOT)
        self.runtime: dict[str, Any] | None = None
        self.runtime_status: dict[str, Any] = {
            "probed": False,
            "probe_error": None,
            "report_sha256": None,
            "expected_fingerprint_sha256": self.protocol.payload["environment"].get(
                "expected_runtime_fingerprint_sha256"
            ),
            "matches_campaign": None,
            "detail": "the runtime probe was not run",
        }
        if runtime_probe:
            self._probe_runtime()

    def _probe_runtime(self) -> None:
        """Run the released probe when possible. Record the report hash. Never abort."""
        output = self.work_root / f"runtime_fingerprint_{self.run_id}_{int(time.time())}.json"
        try:
            payload = grader.probe_runtime(self.child_env, output)
        except ProtocolError as exc:
            self.runtime_status.update({
                "probed": False,
                "probe_error": str(exc),
                "detail": "the runtime probe could not complete on this machine",
            })
            return
        self.runtime_status.update({
            "probed": True,
            "report_sha256": grader.canonical_payload_sha256(payload),
            "report_file": str(output),
        })
        try:
            grader.validate_runtime_fingerprint(payload, self.protocol, official=False, active_runtime=True)
        except ProtocolError as exc:
            self.runtime_status.update({
                "matches_campaign": False,
                "detail": f"the runtime fingerprint differs from the campaign's: {exc}",
            })
            self.runtime = None
        else:
            self.runtime_status.update({
                "matches_campaign": True,
                "detail": "the runtime fingerprint equals the one pinned in the protocol",
            })
            self.runtime = payload

    def _command(self, script: str) -> list[str]:
        return grader.mpi_python_command(script, self.protocol, self.runtime)

    def grade(self, geometry: Sequence[float], label: str) -> dict[str, Any]:
        """Mesh once, solve the three conditions in series, and write one result document."""
        protocol = self.protocol
        wing_id = grader._safe_path_component(label, "grade label")
        values = grader.validated_geometry(geometry)
        digest = geometry_sha256(values)
        expected_case_input = grader._expected_case_input(values, protocol)
        wing_work = self.work_root / wing_id
        grader._fresh_directory(wing_work)
        started_at = utc_now()
        started = time.monotonic()
        execution = protocol.payload["execution"]
        seed = protocol.seed_alpha0
        seed_conditions = [(float(row["mach"]), seed) for row in protocol.conditions]
        max_attempts = 1 + int(execution["infrastructure_retries"])
        process_wall = 0.0

        mesh_attempts: list[dict[str, Any]] = []
        usable_case: Path | None = None
        for attempt_index in range(max_attempts):
            attempt_root = wing_work / f"mesh_attempt_{attempt_index:02d}"
            grader._fresh_directory(attempt_root)
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                case = Path(
                    self.case_preparer(
                        wing_id,
                        values,
                        str(attempt_root),
                        seed_conditions,
                        int(execution["mpi_ranks"]),
                        robust_mesh=False,
                        screen_ncycles=None,
                    )
                )
            (attempt_root / "prepare.log").write_text(captured.getvalue(), encoding="utf-8")
            if case.resolve() != (attempt_root / wing_id).resolve():
                raise ProtocolError("case preparer returned a directory outside the fresh attempt")
            shutil.copy2(case / "input.json", case / "input.before_mesh.json")
            raw_input_before_mesh = grader.read_json(case / "input.before_mesh.json")
            grader._validate_case_input(
                raw_input_before_mesh, expected_case_input, exact=True, label="pre-mesh input"
            )
            generated = {
                name: grader.artifact(case / name)
                for name in (
                    "input.before_mesh.json", "gen-mesh.py", "run-adflow.py",
                    "single-point.py", "original_tip.xyz",
                )
            }
            for name, expected_sha256 in protocol.payload["generated_case_file_sha256"].items():
                observed = generated[name]["sha256"]
                if observed != expected_sha256:
                    raise ProtocolError(
                        f"generated scientific script drift for {name}: "
                        f"expected {expected_sha256}, observed {observed}"
                    )
            process = self.process_runner(
                self._command("gen-mesh.py"),
                case,
                case / "grade_mesh.log",
                int(execution["mesh_timeout_seconds"]),
                self.child_env,
            )
            process = grader.bind_process_environment(process, self.child_env)
            process_wall += float(process.get("wall_seconds") or 0.0)
            mesh_info_path = case / "mesh_info.json"
            mesh_info, output_error = grader._read_optional_json(mesh_info_path)
            volume_log_artifact = grader.artifact(case / "volume_meshing.log")
            raw_volume_log_evidence, volume_log_error = grader._mesh_log_crosscheck(
                mesh_info, volume_log_artifact
            )
            raw_input_after_mesh, input_after_mesh_error = grader._read_optional_json(case / "input.json")
            if raw_input_after_mesh is not None:
                if input_after_mesh_error is not None:
                    raise ProtocolError("parseable post-mesh input carries a parse error")
                grader._validate_case_input(
                    raw_input_after_mesh, expected_case_input, exact=False, label="post-mesh input"
                )
            volume_grid = grader.artifact(case / "wing_vol.cgns")
            mesh_validation = self.mesh_validator(case / "wing_vol.cgns", case / "input.json")
            if (
                not isinstance(mesh_validation, dict)
                or mesh_validation.get("schema_version") != MESH_VALIDATION_SCHEMA_VERSION
                or type(mesh_validation.get("valid")) is not bool
                or not isinstance(mesh_validation.get("reason_codes"), list)
            ):
                raise ProtocolError("mesh validator returned a malformed evidence packet")
            mesh_validation_path = case / "mesh_validation.json"
            atomic_write_mesh_validation(mesh_validation_path, mesh_validation)
            classified = classify_mesh_attempt(
                process,
                mesh_info if volume_log_error is None else None,
                protocol,
                volume_grid_valid=mesh_validation["valid"] is True,
            )
            if classified["outcome"] in {"valid", "marginal"}:
                if raw_input_after_mesh is None:
                    raise ProtocolError("usable mesh lacks a parseable geometry-bound post-mesh input")
                grader._validate_post_mesh_derived_geometry(raw_input_after_mesh, expected_case_input)
            classified.update({
                "attempt_index": attempt_index,
                "case_directory": str(case),
                "generated_artifacts": generated,
                "raw_input_before_mesh": raw_input_before_mesh,
                "raw_input_after_mesh": raw_input_after_mesh,
                "input_after_mesh_error": input_after_mesh_error,
                "raw_mesh_info": mesh_info,
                "raw_volume_meshing_log_evidence": raw_volume_log_evidence,
                "volume_meshing_log_evidence_error": volume_log_error,
                "raw_mesh_validation": mesh_validation,
                "mesh_info_artifact": grader.artifact(mesh_info_path),
                "volume_meshing_log_artifact": volume_log_artifact,
                "mesh_validation_artifact": grader.artifact(mesh_validation_path),
                "volume_grid_artifact": volume_grid,
                "input_after_mesh_artifact": grader.artifact(case / "input.json"),
                "output_error": output_error,
            })
            mesh_attempts.append(classified)
            if classified["outcome"] in {"valid", "marginal"}:
                usable_case = case
                break
            if classified["outcome"] == "unmeshable":
                break
        mesh = grader._select_final_mesh(mesh_attempts)

        by_index: dict[int, dict[str, Any]] = {}
        if usable_case is None:
            reason = (
                "not_run_unmeshable" if mesh["outcome"] == "unmeshable" else "not_run_mesh_inconclusive"
            )
            for index, spec in enumerate(protocol.conditions):
                by_index[index] = grader._not_run_condition(spec, reason)
        else:
            for condition_index in protocol.payload["condition_execution_order"]:
                spec = protocol.conditions[condition_index]
                attempts: list[dict[str, Any]] = []
                for attempt_index in range(max_attempts):
                    attempt_dir = usable_case / f"{spec['condition_id']}_attempt_{attempt_index:02d}"
                    grader._fresh_directory(attempt_dir)
                    conds = {
                        "M3": float(spec["mach"]),
                        "AA": seed,
                        "CL_targ": float(spec["cl_target"]),
                        "re": float(protocol.payload["flow"]["reynolds"]),
                        "temp": float(protocol.payload["flow"]["temperature_k"]),
                    }
                    grader.atomic_write_json(attempt_dir / "conds.json", conds)
                    shutil.copy2(usable_case / "run-adflow.py", attempt_dir / "run-adflow.py")
                    process = self.process_runner(
                        self._command("run-adflow.py"),
                        attempt_dir,
                        attempt_dir / "grade_adflow.log",
                        int(execution["solve_timeout_seconds_per_attempt"]),
                        self.child_env,
                    )
                    process = grader.bind_process_environment(process, self.child_env)
                    process_wall += float(process.get("wall_seconds") or 0.0)
                    output_path = attempt_dir / "output.json"
                    output, output_error = grader._read_optional_json(output_path)
                    adflow_log_artifact = grader.artifact(attempt_dir / "adflow.log")
                    raw_adflow_evidence, adflow_log_error = grader._adflow_log_crosscheck(
                        output, adflow_log_artifact
                    )
                    classified = classify_condition_attempt(
                        process,
                        output if adflow_log_error is None else None,
                        spec,
                        seed,
                        protocol,
                    )
                    classified.update({
                        "attempt_index": attempt_index,
                        "attempt_directory": str(attempt_dir),
                        "output_error": output_error,
                        "raw_output": output,
                        "raw_adflow_log_evidence": raw_adflow_evidence,
                        "adflow_log_evidence_error": adflow_log_error,
                        "artifacts": {
                            "conditions": grader.artifact(attempt_dir / "conds.json"),
                            "solver_script": grader.artifact(attempt_dir / "run-adflow.py"),
                            "output": grader.artifact(output_path),
                            "adflow_log": adflow_log_artifact,
                        },
                    })
                    attempts.append(classified)
                    if classified["outcome_class"] != "inconclusive":
                        break
                by_index[condition_index] = finalize_condition(spec, attempts)
        conditions = [by_index[index] for index in protocol.payload["condition_output_order"]]
        aggregate = aggregate_wing(mesh, conditions, protocol)
        benchmark_grade = self._benchmark_grade(mesh, conditions, aggregate)
        wall_seconds = time.monotonic() - started
        solver_environment: dict[str, Any] | None
        if self.runtime_status.get("probed"):
            solver_environment = {"status": "complete", "report_sha256": self.runtime_status["report_sha256"],
                                  "report_file": self.runtime_status.get("report_file")}
        else:
            solver_environment = {"status": "not_probed_local_grading",
                                  "probe_error": self.runtime_status.get("probe_error")}
        result = {
            "schema_version": RESULT_SCHEMA_VERSION,
            "grading_mode": GRADING_MODE,
            "official": False,
            "run_id": self.run_id,
            "wing_id": wing_id,
            "protocol_id": protocol.protocol_id,
            "protocol": {
                "protocol_id": protocol.protocol_id,
                "protocol_status": protocol.payload["protocol_status"],
                "canonical_sha256": protocol.sha256,
                "snapshot": protocol.snapshot(),
            },
            "benchmark_manifest": None,
            "release_lock": None,
            "execution_authorization": None,
            "campaign_publication_preflight": None,
            "local_grading": {
                "adapter": "benchmark/grade_local.py",
                "task_id": self.task.task_id,
                "skipped_steps": list(SKIPPED_STEPS),
                "runtime_probe": dict(self.runtime_status),
            },
            "geometry": {
                "schema": "superwing-38d-v1",
                "hash_algorithm": grader.GEOMETRY_HASH_ALGORITHM,
                "sha256": digest,
                "geom_38d": list(values),
                "manifest_provenance": None,
                "canonical_source": None,
            },
            "runtime": {
                "started_at": started_at,
                "finished_at": utc_now(),
                "git_commit": grader._git_commit(),
                "hostname": None,
                "platform": platform.platform(),
                "python": platform.python_version(),
                "final_child_image_linux_amd64_digest_observed": None,
                "runtime_attestation": {"status": "not_provided_local_grading"},
                "runtime_attestation_sha256": None,
                "aws_instance_type_observed": None,
                "aws_lifecycle_observed": None,
                "solver_environment": solver_environment,
                "solver_environment_sha256": self.runtime_status.get("report_sha256"),
            },
            "timing": {
                "wall_seconds": wall_seconds,
                "process_wall_seconds": process_wall,
                "core_seconds": process_wall * int(execution["mpi_ranks"]),
                "mpi_ranks": int(execution["mpi_ranks"]),
            },
            "mesh": mesh,
            "conditions": conditions,
            "aggregate": aggregate,
            "benchmark_grade": benchmark_grade,
            "legacy_evidence_used_for_verdict": False,
        }
        result = json_ready(result)
        result["result_sha256"] = hashlib.sha256(canonical_json_bytes(result)).hexdigest()
        result_path = wing_work / "result.json"
        grader.atomic_write_json(result_path, result)
        result["result_path"] = str(result_path)
        return result

    def _benchmark_grade(
        self,
        mesh: Mapping[str, Any],
        conditions: Sequence[Mapping[str, Any]],
        aggregate: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Whole wing verdict to the benchmark's delivered flag, L/D, outcome and reason codes.

        ``pass`` means all three conditions are valid and every L/D lies in
        (0, 40]. ``conclusive_failure`` means the geometry was rejected or a
        condition was conclusively invalid. ``partial`` means some but not
        all conditions gave a result. ``undetermined`` means no condition gave
        a result.
        """
        if aggregate["score_eligible"]:
            lds = [row["measurements"]["ld"] for row in conditions]
            envelope = evaluate_ld_envelope(lds, self.mf_protocol)
            if envelope.eligible:
                return {
                    "delivered": True,
                    "ld": float(aggregate["cfd_ld_geomean"]),
                    "outcome": "pass",
                    "reason_codes": ["complete_score_eligible"],
                }
            return {
                "delivered": False,
                "ld": None,
                "outcome": "conclusive_failure",
                "reason_codes": ["ld_envelope_violation", *envelope.reason_codes],
            }
        if mesh["outcome"] == "unmeshable":
            return {
                "delivered": False,
                "ld": None,
                "outcome": "conclusive_failure",
                "reason_codes": ["not_run_unmeshable"],
            }
        if mesh["outcome"] == "inconclusive":
            return {
                "delivered": False,
                "ld": None,
                "outcome": "undetermined",
                "reason_codes": ["not_run_mesh_inconclusive"],
            }
        reasons = sorted({code for row in conditions for code in row.get("reason_codes", [])})
        if aggregate["admissibility"] == "fail":
            outcome = "conclusive_failure"
        elif int(aggregate["n_valid"]) > 0:
            outcome = "partial"
        else:
            outcome = "undetermined"
        return {
            "delivered": False,
            "ld": None,
            "outcome": outcome,
            "reason_codes": reasons or ["undetermined"],
        }


def environment_report(task: TaskSpec) -> dict[str, Any]:
    """Say what is present on this machine, without running anything heavy."""
    report: dict[str, Any] = {
        "vendor_files_missing": missing_vendor_files(),
        "mpirun_on_path": shutil.which("mpirun") is not None,
        "python": sys.executable,
        "imports": {},
    }
    for module in REQUIRED_MODULES:
        try:
            __import__(module)
            report["imports"][module] = True
        except Exception:  # noqa: BLE001
            report["imports"][module] = False
    try:
        load_task_protocol(task, verify_files=True)
        report["protocol_pinned_files_verified"] = True
    except ProtocolError as exc:
        report["protocol_pinned_files_verified"] = False
        report["protocol_error"] = str(exc)
    return report
