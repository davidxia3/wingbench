"""Canonical protocol loading and fingerprint enforcement for SuperWing CFD.

The candidate manifest is deliberately non-official until its checked
qualification campaign passes.  This module makes that distinction executable:
diagnostic runs may use a candidate, while official mode accepts only a frozen
manifest with the official switch enabled.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL_PATH = ROOT / "configs/cfd/final_protocol_candidate_v2.json"
SAFE_INHERITED_ENV = (
    "AFLR_ARCH",
    "CAPS_GLYPH",
    "CASARCH",
    "CASREV",
    "CASROOT",
    "CGNS_HOME",
    "COMPILERS",
    "COMPLEXIFY_DIR",
    "EFCOMP",
    "ESP_ARCH",
    "ESP_ROOT",
    "HOME",
    "HOMEDIR",
    "IPOPT_DIR",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "MDOLAB_JSON_DIR",
    "MDOLAB_PACKAGES_DIR",
    "MDOLAB_REPO_DIR",
    "MPI_DIR",
    "OMPI_MCA_btl_vader_single_copy_mechanism",
    "PATH",
    "PETSC_ARCH",
    "PETSC_ARCH_COMPLEX",
    "PETSC_ARCH_REAL",
    "PETSC_DIR",
    "PKG_CONFIG_PATH",
    "PYENV_ROOT",
    "PYTHONINC",
    "PYTHONLIB",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
    "TMPDIR",
    "UDUNITS2_XML_PATH",
    "USER",
)


class ProtocolError(ValueError):
    """The requested run does not satisfy the immutable protocol contract."""


_IMAGE_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_GIT_COMMIT_RE = re.compile(r"[0-9a-f]{40}\Z")


# Schema v1 is the scientific contract that was preregistered for the final
# regrade.  Most of these values are not knobs: the vendored case scripts or
# the pure result state machine implement them literally.  Keeping the exact
# contract here prevents a manifest edit from claiming settings that the
# executable would silently ignore.
_FINAL_SCIENTIFIC_SCOPE = {
    "geometry_schema": "superwing-38d-v1",
    "geometry_hash_algorithm": "sha256(superwing-geom38-f64le-v1-NUL + 38xf64le)",
    "generated_geometry_convention": "configs-generated-v1",
    "dataset_geometry_convention": (
        "dataset-index-to-configs-source-id-to-running-info-shape-v1"
    ),
    "whole_wing_unit": True,
    "mesh_once": True,
    "serial_conditions": True,
    "partial_scores_official": False,
}
_FINAL_CONDITIONS = [
    {"condition_id": "mach_q1", "mach": 0.7853, "cl_target": 0.41},
    {"condition_id": "mach_q2", "mach": 0.8195, "cl_target": 0.41},
    {"condition_id": "mach_q3", "mach": 0.853, "cl_target": 0.41},
]
_FINAL_FLOW = {
    "reynolds": 20_000_000.0,
    "reynolds_length": 1.0,
    "temperature_k": 300.0,
    "area_ref_source": "generated_wing_surface_area",
    "chord_ref": 1.0,
    "x_ref": 0.25,
}
_FINAL_EXECUTION = {
    "mpi_ranks": 16,
    "omp_num_threads": 1,
    "openblas_num_threads": 1,
    "mkl_num_threads": 1,
    "mesh_timeout_seconds": 1800,
    "solve_timeout_seconds_per_attempt": 7200,
    "infrastructure_retries": 0,
    "host_lock_path": "/workspace/.wingbench-locks/np16.lock",
    "fresh_attempt_directory": True,
    "fail_fast_conditions": False,
    "early_stall_abort": False,
    "timeout_is_scientific_failure": False,
}
_FINAL_SEED_POLICY = {
    "type": "fixed_per_condition",
    "alpha0_deg": 6.6401110468,
    "source": "superwing-successful-target-cl-band-median-v1",
    "selection_record_path": "configs/cfd/seed_aoa_selection_v1.json",
    "selection_rule": "exact_median_successful_cases_in_cl_0.39_0.43_no_rounding",
    "applies_to_every_wing_and_condition": True,
    "future_reselection_allowed": False,
}
_FINAL_MESH = {
    "recipe": "superwing-dataset-default-v1",
    "robust_mesh": False,
    "min_volume_valid_if": ">0",
    "min_quality_valid_if": ">0",
    "marginal_quality_if": "0<q<0.05",
    "missing_or_malformed_evidence": "inconclusive",
}
_FINAL_SOLVER = {
    "equation_type": "RANS",
    "mg_cycle": "3w",
    "use_ank_solver": True,
    "use_nk_solver": True,
    "nk_switch_tol": 1e-8,
    "l2_convergence": 1e-10,
    "n_cycles_coarse": 500,
    "n_cycles": 3000,
    "solvecl": {
        "clalpha_guess": None,
        "delta": 0.5,
        "tol": 0.001,
        "auto_reset": False,
        "max_iter": 20,
        "relax_clalpha": 1.0,
        "relax_alpha": 1.0,
        "l2_convergence_rel": None,
        "stop_on_stall": False,
        "write_solution": True,
        "work_unit_time": None,
        "update_cutoff": 1e-16,
    },
}
_FINAL_CONDITION_GATES = {
    "process_returncode_zero_required": True,
    "solvecl_result_required": True,
    "solvecl_converged_required": True,
    "aeroproblem_solve_failed_must_equal": False,
    "aeroproblem_fatal_fail_must_equal": False,
    "err_cl_strictly_less_than": 0.0005,
    "cl_match_abs_less_than_or_equal": 0.02,
    "aoa_deg_inclusive": [-10.0, 25.0],
    "positive_cl_required": True,
    "positive_cd_required": True,
    "positive_ld_required": True,
}
_FINAL_AGGREGATION = {
    "required_valid_conditions": 3,
    "admissibility_pass": "all_conditions_valid",
    "admissibility_fail": "any_conclusive_condition_invalid_or_unmeshable",
    "admissibility_undetermined": (
        "no_conclusive_invalidity_and_any_inconclusive_evidence"
    ),
    "score": "geometric_mean_ld",
    "score_requires_complete_pass": True,
    "diagnostic_partial_metrics_allowed": True,
}
_FINAL_OUTCOME_POLICY = {
    "timeout": "inconclusive_timeout",
    "process_error": "inconclusive_infrastructure",
    "missing_output": "inconclusive_output_failure",
    "malformed_output": "inconclusive_output_failure",
    "missing_convergence_evidence": "inconclusive_output_failure",
    "completed_solvecl_nonconvergence": "invalid_solver_nonconvergence",
    "uninterrupted_infrastructure_failure": "terminal_inconclusive_no_retry",
    "verified_spot_reclaim": "censored_external_reacquisition_not_scientific_attempt",
}
_FINAL_RESULT_CONTRACT = {
    "schema_version": "wingbench-cfd-whole-wing-result-v1",
    "atomic_write_required": True,
    "protocol_snapshot_required": True,
    "protocol_sha256_required": True,
    "geometry_sha256_required": True,
    "all_attempts_preserved": True,
    "official_requires_protocol_status": "frozen",
}
_FINAL_REQUIRED_CHILD_ENVIRONMENT = {
    "ADFLOW_USE_ANK": "1",
    "ADFLOW_USE_NK": "1",
    "ADFLOW_NK_SWITCH": "1e-8",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
}
_REQUIRED_SCIENTIFIC_FILES = {
    "scripts/cfd/grading_protocol.py",
    "scripts/cfd/grade_schema.py",
    "scripts/cfd/grade_benchmark.py",
    "scripts/cfd/runtime_probe.py",
    "scripts/cfd/validate_mesh_artifact.py",
    "scripts/cfd/raw_evidence.py",
    "scripts/cfd/geometry_hash.py",
    "scripts/cfd/dataset_geometry.py",
    "scripts/cfd/campaign_publication.py",
    "scripts/cfd/aws_campaign_publication.py",
    "scripts/cfd/build_benchmark_regrade_manifest.py",
    "scripts/cfd/build_protocol_qualification_manifest.py",
    "scripts/cfd/evaluate_protocol_qualification.py",
    "scripts/cfd/promote_benchmark_manifest.py",
    "scripts/cfd/build_release_lock.py",
    "scripts/cfd/finalize_benchmark_regrade.py",
    "scripts/cfd/derive_seed_aoa.py",
    "scripts/cfd/aerotransformer/gen-mesh.superwing.py",
    "scripts/cfd/aerotransformer/run-adflow.py",
    "scripts/cfd/aerotransformer/single-point.py",
    "scripts/cfd/aerotransformer/original_tip.xyz",
    "scripts/cfd/prepare_superwing_cases.py",
    "configs/cfd/publication_storage_binding_v1.json",
    "configs/cfd/seed_aoa_selection_v1.json",
}
_FINAL_FORBIDDEN_PARENT_PREFIXES = ["ADFLOW_"]
_FINAL_FORBIDDEN_PARENT_NAMES = [
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
]
_FINAL_MDOLAB_BASE_MANIFEST_DIGEST = (
    "sha256:68393a976907d6306f8b2d87ba95365a8720c3b2a5e55ae431f3cb05d5198223"
)
_FINAL_MDOLAB_BASE_AMD64_DIGEST = (
    "sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd"
)
_FINAL_CST_MODELING3D_COMMIT = "9500b19a732463c26b32f6860ac6c517c7d212a5"


def canonical_json_bytes(payload: Any) -> bytes:
    """Return the one byte representation used for every protocol fingerprint."""
    try:
        text = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ProtocolError(f"protocol is not canonical-JSON serializable: {exc}") from exc
    return text.encode("utf-8")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError(f"duplicate protocol JSON object key {key!r}")
        result[key] = value
    return result


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def protocol_sha256(payload: Mapping[str, Any]) -> str:
    return sha256_bytes(canonical_json_bytes(payload))


def _finite_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProtocolError(f"{label} must be numeric")
    number = float(value)
    if not math.isfinite(number):
        raise ProtocolError(f"{label} must be finite")
    return number


def _require_image_digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or _IMAGE_DIGEST_RE.fullmatch(value) is None:
        raise ProtocolError(f"{label} must be a lowercase sha256:<64-hex> digest")
    return value


def _require_exact_contract(value: Any, expected: Any, label: str) -> None:
    """Require a type-sensitive, recursively exact schema-v1 contract value."""
    if isinstance(expected, dict):
        if not isinstance(value, dict):
            raise ProtocolError(f"{label} must be an object")
        missing = sorted(set(expected) - set(value))
        extra = sorted(set(value) - set(expected))
        if missing or extra:
            details = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if extra:
                details.append("unexpected " + ", ".join(extra))
            raise ProtocolError(f"{label} has noncanonical fields: {'; '.join(details)}")
        for key, expected_child in expected.items():
            _require_exact_contract(value[key], expected_child, f"{label}.{key}")
        return
    if isinstance(expected, list):
        if not isinstance(value, list):
            raise ProtocolError(f"{label} must be an array")
        if len(value) != len(expected):
            raise ProtocolError(f"{label} must contain exactly {len(expected)} entries")
        for index, expected_child in enumerate(expected):
            _require_exact_contract(value[index], expected_child, f"{label}[{index}]")
        return
    # bool is an int subclass, and 1 compares equal to 1.0.  Neither coercion is
    # acceptable in a canonical JSON protocol, so equality is type-sensitive.
    if type(value) is not type(expected) or value != expected:
        raise ProtocolError(f"{label} must equal {expected!r}")


def validate_protocol(payload: Mapping[str, Any], *, verify_files: bool = True) -> None:
    """Validate load-bearing invariants, including pinned scientific source bytes."""
    # Reject NaN/Infinity (including inside later exact-contract fields) before
    # a more specific equality check can obscure the malformed JSON value.
    canonical_json_bytes(payload)
    if payload.get("schema_version") != "wingbench-cfd-protocol-v1":
        raise ProtocolError("unsupported protocol schema_version")
    if not isinstance(payload.get("protocol_id"), str) or not payload["protocol_id"]:
        raise ProtocolError("protocol_id is required")
    if payload.get("protocol_status") not in {"qualification_required", "frozen", "retired"}:
        raise ProtocolError("invalid protocol_status")
    if not isinstance(payload.get("official_grading_enabled"), bool):
        raise ProtocolError("official_grading_enabled must be boolean")
    if payload["protocol_status"] != "frozen" and payload["official_grading_enabled"]:
        raise ProtocolError("only a frozen protocol may enable official grading")
    if payload["protocol_status"] == "frozen":
        promotion = payload.get("promotion")
        if not isinstance(promotion, dict):
            raise ProtocolError("a frozen protocol requires qualification promotion metadata")
        for name in (
            "source_candidate_protocol_sha256",
            "qualification_record_sha256",
            "qualification_manifest_sha256",
        ):
            value = promotion.get(name)
            if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
                raise ProtocolError(f"promotion.{name} must be a full SHA-256")

    conditions = payload.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 3:
        raise ProtocolError("the protocol must contain exactly three conditions")
    ids: set[str] = set()
    for index, condition in enumerate(conditions):
        if not isinstance(condition, dict):
            raise ProtocolError(f"conditions[{index}] must be an object")
        condition_id = condition.get("condition_id")
        if not isinstance(condition_id, str) or not condition_id or condition_id in ids:
            raise ProtocolError("condition_id values must be unique non-empty strings")
        ids.add(condition_id)
        _finite_number(condition.get("mach"), f"conditions[{index}].mach")
        _finite_number(condition.get("cl_target"), f"conditions[{index}].cl_target")
    expected_order = list(range(len(conditions)))
    if payload.get("condition_output_order") != expected_order:
        raise ProtocolError("condition_output_order must preserve canonical grid order")
    execution_order = payload.get("condition_execution_order")
    if not isinstance(execution_order, list) or sorted(execution_order) != expected_order:
        raise ProtocolError("condition_execution_order must be a permutation of all conditions")

    execution = payload.get("execution") or {}
    for name in ("mpi_ranks", "mesh_timeout_seconds", "solve_timeout_seconds_per_attempt"):
        value = execution.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ProtocolError(f"execution.{name} must be a positive integer")
    if execution["mpi_ranks"] != 16:
        raise ProtocolError("the frozen worker contract requires exactly 16 MPI ranks")
    retries = execution.get("infrastructure_retries")
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ProtocolError("execution.infrastructure_retries must be a non-negative integer")
    if execution.get("early_stall_abort") is not False:
        raise ProtocolError("the final protocol must disable the unqualified stall heuristic")
    if execution.get("fail_fast_conditions") is not False:
        raise ProtocolError("the whole-wing protocol must run every condition")
    if execution.get("timeout_is_scientific_failure") is not False:
        raise ProtocolError("timeouts must remain inconclusive")
    if not isinstance(execution.get("host_lock_path"), str) or not execution["host_lock_path"].startswith("/"):
        raise ProtocolError("execution.host_lock_path must be an absolute path")

    seed = payload.get("seed_policy") or {}
    if seed.get("type") != "fixed_per_condition":
        raise ProtocolError("candidate v1 supports only its preregistered fixed seed policy")
    _finite_number(seed.get("alpha0_deg"), "seed_policy.alpha0_deg")

    gates = payload.get("condition_gates") or {}
    for name in (
        "err_cl_strictly_less_than",
        "cl_match_abs_less_than_or_equal",
    ):
        if _finite_number(gates.get(name), f"condition_gates.{name}") <= 0:
            raise ProtocolError(f"condition_gates.{name} must be positive")
    aoa = gates.get("aoa_deg_inclusive")
    if not isinstance(aoa, list) or len(aoa) != 2:
        raise ProtocolError("condition_gates.aoa_deg_inclusive must have two bounds")
    if _finite_number(aoa[0], "minimum AoA") >= _finite_number(aoa[1], "maximum AoA"):
        raise ProtocolError("AoA bounds are reversed")

    environment_contract = payload.get("environment")
    if not isinstance(environment_contract, dict):
        raise ProtocolError("environment must be an object")
    required_environment = environment_contract.get("required_child_environment")
    if not isinstance(required_environment, dict) or not required_environment:
        raise ProtocolError("required_child_environment must be a non-empty object")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in required_environment.items()):
        raise ProtocolError("child environment keys and values must be strings")
    unsupported_probe_keys = sorted(
        key for key in required_environment
        if not key.startswith("ADFLOW_")
        and key not in {"OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"}
    )
    if unsupported_probe_keys:
        raise ProtocolError(
            "required child environment contains keys outside the MPI probe contract: "
            + ", ".join(unsupported_probe_keys)
        )
    if environment_contract.get("require_non_root") is not True:
        raise ProtocolError("the np16 protocol requires a non-root runtime")
    base_manifest = _require_image_digest(
        environment_contract.get("mdolab_base_image_manifest_digest"),
        "environment.mdolab_base_image_manifest_digest",
    )
    base_amd64 = _require_image_digest(
        environment_contract.get("mdolab_base_image_linux_amd64_digest"),
        "environment.mdolab_base_image_linux_amd64_digest",
    )
    child_manifest = environment_contract.get("final_child_image_manifest_digest")
    child_amd64 = environment_contract.get("final_child_image_linux_amd64_digest")
    if (child_manifest is None) != (child_amd64 is None):
        raise ProtocolError("final child image manifest and linux/amd64 digests must be pinned together")
    if child_manifest is not None:
        _require_image_digest(child_manifest, "environment.final_child_image_manifest_digest")
        _require_image_digest(child_amd64, "environment.final_child_image_linux_amd64_digest")
        if child_manifest == base_manifest or child_amd64 == base_amd64:
            raise ProtocolError("the final CST-equipped child image digests must differ from the MDOLab base")
    if payload["protocol_status"] == "frozen" or payload["official_grading_enabled"]:
        if child_manifest is None or child_amd64 is None:
            raise ProtocolError("a frozen or official protocol must pin both final child image digests")
    cst_commit = environment_contract.get("cst_modeling3d_commit")
    if not isinstance(cst_commit, str) or _GIT_COMMIT_RE.fullmatch(cst_commit) is None:
        raise ProtocolError("environment.cst_modeling3d_commit must be a lowercase 40-hex commit")
    runtime_sha = environment_contract.get("expected_runtime_fingerprint_sha256")
    if runtime_sha is not None and (
        not isinstance(runtime_sha, str) or _SHA256_RE.fullmatch(runtime_sha) is None
    ):
        raise ProtocolError("expected runtime fingerprint must be null or a full SHA-256")
    if payload["protocol_status"] == "frozen" and runtime_sha is None:
        raise ProtocolError("a frozen protocol must pin the qualified runtime fingerprint")

    # Bind declarative protocol claims to what the current case generator,
    # ADflow wrapper, and result state machine actually execute.  Dynamic
    # promotion evidence (child-image digests and runtime fingerprint) is
    # intentionally validated above instead of being fixed here.
    for label, expected in (
        ("scientific_scope", _FINAL_SCIENTIFIC_SCOPE),
        ("conditions", _FINAL_CONDITIONS),
        ("flow", _FINAL_FLOW),
        ("execution", _FINAL_EXECUTION),
        ("seed_policy", _FINAL_SEED_POLICY),
        ("mesh", _FINAL_MESH),
        ("solver", _FINAL_SOLVER),
        ("condition_gates", _FINAL_CONDITION_GATES),
        ("aggregation", _FINAL_AGGREGATION),
        ("outcome_policy", _FINAL_OUTCOME_POLICY),
        ("result_contract", _FINAL_RESULT_CONTRACT),
    ):
        _require_exact_contract(payload.get(label), expected, label)
    _require_exact_contract(
        payload.get("condition_output_order"), [0, 1, 2], "condition_output_order"
    )
    _require_exact_contract(
        payload.get("condition_execution_order"), [0, 1, 2], "condition_execution_order"
    )
    _require_exact_contract(
        required_environment,
        _FINAL_REQUIRED_CHILD_ENVIRONMENT,
        "environment.required_child_environment",
    )
    _require_exact_contract(
        environment_contract.get("forbidden_parent_prefixes"),
        _FINAL_FORBIDDEN_PARENT_PREFIXES,
        "environment.forbidden_parent_prefixes",
    )
    _require_exact_contract(
        environment_contract.get("forbidden_parent_names"),
        _FINAL_FORBIDDEN_PARENT_NAMES,
        "environment.forbidden_parent_names",
    )
    _require_exact_contract(
        base_manifest,
        _FINAL_MDOLAB_BASE_MANIFEST_DIGEST,
        "environment.mdolab_base_image_manifest_digest",
    )
    _require_exact_contract(
        base_amd64,
        _FINAL_MDOLAB_BASE_AMD64_DIGEST,
        "environment.mdolab_base_image_linux_amd64_digest",
    )
    _require_exact_contract(
        cst_commit,
        _FINAL_CST_MODELING3D_COMMIT,
        "environment.cst_modeling3d_commit",
    )

    # The duplicated thread/solver values are deliberately cross-checked too:
    # the execution section describes resource use, while these environment
    # strings are what the subprocess actually receives.
    for execution_name, environment_name in (
        ("omp_num_threads", "OMP_NUM_THREADS"),
        ("openblas_num_threads", "OPENBLAS_NUM_THREADS"),
        ("mkl_num_threads", "MKL_NUM_THREADS"),
    ):
        if required_environment[environment_name] != str(execution[execution_name]):
            raise ProtocolError(
                f"execution.{execution_name} must match "
                f"environment.required_child_environment.{environment_name}"
            )
    for solver_name, environment_name in (
        ("use_ank_solver", "ADFLOW_USE_ANK"),
        ("use_nk_solver", "ADFLOW_USE_NK"),
    ):
        encoded = "1" if payload["solver"][solver_name] else "0"
        if required_environment[environment_name] != encoded:
            raise ProtocolError(
                f"solver.{solver_name} must match "
                f"environment.required_child_environment.{environment_name}"
            )
    if not math.isclose(
        float(required_environment["ADFLOW_NK_SWITCH"]),
        float(payload["solver"]["nk_switch_tol"]),
        rel_tol=0.0,
        abs_tol=0.0,
    ):
        raise ProtocolError(
            "solver.nk_switch_tol must match "
            "environment.required_child_environment.ADFLOW_NK_SWITCH"
        )
    if payload["aggregation"]["required_valid_conditions"] != len(conditions):
        raise ProtocolError(
            "aggregation.required_valid_conditions must equal the condition count"
        )

    files = payload.get("scientific_files")
    if not isinstance(files, list) or not files:
        raise ProtocolError("scientific_files must pin at least one source asset")
    seen_paths: set[str] = set()
    for row in files:
        if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
            raise ProtocolError("each scientific_files row must contain only path and sha256")
        rel = row["path"]
        expected = row["sha256"]
        if not isinstance(rel, str) or rel in seen_paths or Path(rel).is_absolute() or ".." in Path(rel).parts:
            raise ProtocolError(f"invalid or duplicate scientific file path: {rel!r}")
        seen_paths.add(rel)
        if not isinstance(expected, str) or len(expected) != 64:
            raise ProtocolError(f"invalid SHA-256 for {rel}")
        if verify_files:
            path = ROOT / rel
            if not path.is_file():
                raise ProtocolError(f"pinned scientific file is missing: {rel}")
            observed = sha256_file(path)
            if observed != expected:
                raise ProtocolError(
                    f"pinned scientific file changed: {rel}; expected {expected}, observed {observed}"
                )
    if seen_paths != _REQUIRED_SCIENTIFIC_FILES:
        missing = sorted(_REQUIRED_SCIENTIFIC_FILES - seen_paths)
        extra = sorted(seen_paths - _REQUIRED_SCIENTIFIC_FILES)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unexpected " + ", ".join(extra))
        raise ProtocolError("scientific_files has noncanonical scope: " + "; ".join(details))

    if verify_files:
        selection_path = ROOT / seed["selection_record_path"]
        try:
            selection = json.loads(
                selection_path.read_text(),
                object_pairs_hook=_reject_duplicate_keys,
            )
        except (OSError, json.JSONDecodeError, ProtocolError) as exc:
            raise ProtocolError(f"cannot read seed selection record: {exc}") from exc
        selection_rule = selection.get("selection_rule") or {}
        expected_target_cl = conditions[0]["cl_target"]
        if (
            selection.get("schema_version")
            != "wingbench-cfd-seed-aoa-selection-v1"
            or selection.get("selection_status") != "canonical_candidate_input"
            or selection.get("canonical_seed_alpha0_deg") != seed["alpha0_deg"]
            or selection_rule.get("population") != "successful_superwing_cases"
            or selection_rule.get("target_cl") != expected_target_cl
            or selection_rule.get("cl_band_inclusive") != [0.39, 0.43]
            or selection_rule.get("cl_band_basis")
            != "target_cl_plus_or_minus_protocol_cl_match_tolerance"
            or selection_rule.get("mach_population")
            != "all_recorded_superwing_machs"
            or selection_rule.get("statistic") != "exact_median_recorded_aoa"
            or selection_rule.get("rounding") != "none"
            or selection_rule.get("applies_to_every_wing_and_condition") is not True
            or selection_rule.get("future_reselection_allowed") is not False
            or selection_rule.get("failed_cases_in_selection") is not False
        ):
            raise ProtocolError("seed selection record does not match the canonical protocol rule")

    generated = payload.get("generated_case_file_sha256")
    required_generated = {"gen-mesh.py", "run-adflow.py", "single-point.py", "original_tip.xyz"}
    if not isinstance(generated, dict) or set(generated) != required_generated:
        raise ProtocolError("generated_case_file_sha256 must pin all generated scientific assets")
    if any(not isinstance(value, str) or len(value) != 64 for value in generated.values()):
        raise ProtocolError("generated case file hashes must be full SHA-256 strings")

    # This final serialization check catches NaN/Infinity anywhere, including in
    # fields that are not individually enumerated above.
    canonical_json_bytes(payload)


@dataclass(frozen=True)
class GradingProtocol:
    path: Path
    payload: dict[str, Any]
    sha256: str

    @property
    def protocol_id(self) -> str:
        return str(self.payload["protocol_id"])

    @property
    def conditions(self) -> list[dict[str, Any]]:
        return list(self.payload["conditions"])

    @property
    def seed_alpha0(self) -> float:
        return float(self.payload["seed_policy"]["alpha0_deg"])

    def require_mode(self, mode: str) -> None:
        if mode not in {"diagnostic", "official"}:
            raise ProtocolError("mode must be 'diagnostic' or 'official'")
        if mode == "official":
            if self.payload["protocol_status"] != "frozen":
                raise ProtocolError("official grading is locked until protocol qualification and freeze")
            if self.payload["official_grading_enabled"] is not True:
                raise ProtocolError("official grading is disabled by the protocol manifest")
            if not self.payload.get("benchmark_release_id"):
                raise ProtocolError("a frozen official protocol requires benchmark_release_id")

    def reject_parent_overrides(self, environ: Mapping[str, str] | None = None) -> None:
        env = os.environ if environ is None else environ
        contract = self.payload["environment"]
        prefixes = tuple(contract.get("forbidden_parent_prefixes") or [])
        names = set(contract.get("forbidden_parent_names") or [])
        offenders = sorted(key for key in env if key in names or any(key.startswith(p) for p in prefixes))
        if offenders:
            raise ProtocolError(
                "load-bearing environment overrides are forbidden; unset: " + ", ".join(offenders)
            )

    def child_environment(self, environ: Mapping[str, str] | None = None) -> dict[str, str]:
        source = os.environ if environ is None else environ
        result = {key: source[key] for key in SAFE_INHERITED_ENV if key in source}
        result.update(self.payload["environment"]["required_child_environment"])
        return result

    def snapshot(self) -> dict[str, Any]:
        # The round trip returns an independent, JSON-only object.
        return json.loads(canonical_json_bytes(self.payload).decode("utf-8"))


def load_protocol(
    path: str | os.PathLike[str] = DEFAULT_PROTOCOL_PATH,
    *,
    verify_files: bool = True,
) -> GradingProtocol:
    resolved = Path(path).resolve()
    try:
        payload = json.loads(
            resolved.read_text(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ProtocolError(f"non-finite protocol JSON constant {value}")
            ),
        )
    except (OSError, json.JSONDecodeError, ProtocolError) as exc:
        raise ProtocolError(f"cannot read protocol {resolved}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ProtocolError("protocol root must be an object")
    validate_protocol(payload, verify_files=verify_files)
    return GradingProtocol(path=resolved, payload=payload, sha256=protocol_sha256(payload))


if __name__ == "__main__":
    protocol = load_protocol()
    print(json.dumps({
        "protocol_id": protocol.protocol_id,
        "protocol_status": protocol.payload["protocol_status"],
        "official_grading_enabled": protocol.payload["official_grading_enabled"],
        "canonical_sha256": protocol.sha256,
    }, indent=2, sort_keys=True))
