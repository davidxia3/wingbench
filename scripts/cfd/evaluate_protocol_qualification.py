#!/usr/bin/env python3
"""Evaluate the exact v5 CFD qualification campaign without promoting it.

The evaluator consumes the complete exported WORM publication ledger for the
seven controls in ``protocol_qualification_manifest_v1.json``.  It validates
the exact embedded result bytes and every recorded artifact through the
production grader, then applies only the preregistered control rules.  Its
output is a deterministic, content-addressed qualification record.  Passing
this evaluator does not freeze a protocol, authorize official grading, or
mutate any release file.  The CLI materializes the record, publication ledger,
seven extracted result JSONs, and every present artifact as one immutable
relocatable evidence bundle; release review replays the production validator
against that bundle rather than worker-local paths.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import os
import shutil
import sys
import uuid
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.build_protocol_qualification_manifest import (  # noqa: E402
    CONTROL_IDS,
    DEFAULT_OUTPUT as DEFAULT_MANIFEST_PATH,
    validate_manifest,
)
from scripts.cfd.campaign_publication import (  # noqa: E402
    PUBLICATION_INVENTORY_FILENAME,
    PUBLICATION_LEDGER_DIRECTORY,
    CampaignPublicationError,
    load_campaign_publication_export,
    validate_manifest_publication_contract,
    validate_publication_ledger_record,
)
from scripts.cfd.geometry_hash import geometry_sha256  # noqa: E402
from scripts.cfd.grade_benchmark import (  # noqa: E402
    _verified_artifact_bytes,
    qualification_control_as_wing,
    validate_result,
)
from scripts.cfd.grading_protocol import (  # noqa: E402
    GradingProtocol,
    ProtocolError,
    canonical_json_bytes,
    load_protocol,
    protocol_sha256,
    sha256_file,
)


ENVELOPE_SCHEMA_VERSION = "wingbench-cfd-qualification-record-envelope-v1"
RECORD_SCHEMA_VERSION = "wingbench-cfd-protocol-qualification-record-v1"
HASH_SEMANTICS = "sha256(canonical compact sorted-key JSON at /qualification_record)"
EVIDENCE_BUNDLE_SCHEMA_VERSION = "wingbench-cfd-evidence-bundle-v1"
RESULTS_DIRECTORY = "results"
ARTIFACT_STORE = "artifacts/sha256"
QUALIFICATION_RECORD_FILENAME = "qualification_record.json"
ResultValidator = Callable[..., None]
EXPLORATORY_CONTROL_IDS = ("pool_ordinal_0057",)
GATE_CONTROL_IDS = tuple(
    control_id for control_id in CONTROL_IDS if control_id not in EXPLORATORY_CONTROL_IDS
)
RULE_BY_CONTROL = {
    "l5_originals__run11": "all_three_conditions_valid",
    "fullcap__fullcap_run1": "all_three_conditions_valid",
    "active_learning__r0_iter5": "all_three_conditions_valid",
    "dataset_1216": "all_three_conditions_valid",
    "pool_ordinal_0057": "exploratory_record_only",
    "active_learning__r1_cand_06": "out_of_range_aoa_must_not_be_accepted",
    "l5_originals__run1": "reproduce_nonpositive_mesh_invalidity",
}
EXPECTED_ACCEPTANCE_TOLERANCES = {
    "seed_identity_abs_tolerance_deg": 1e-12,
    "condition_identity_abs_tolerance": 1e-12,
    "aoa_boundary_padding_deg": 0.0,
    "mesh_nonpositive_threshold": 0.0,
    "count_comparison": "exact_integer_equality",
    "timeout_count_unit": "process_attempt",
}
EXPECTED_EVIDENCE_REQUIREMENTS = {
    "exactly_one_shared_run_id": True,
    "exactly_one_shared_execution_authorization_sha256": True,
    "exactly_one_shared_runtime_attestation_sha256": True,
    "exactly_one_shared_solver_environment_sha256": True,
    "test_only_fake_runner_results_allowed": False,
    "runtime_fingerprint_schema_version": "superwing-runtime-fingerprint-v2",
    "runtime_status_required": "complete",
    "mpi_smoke_test_passed_required": True,
    "runtime_attestation_schema_version": "wingbench-cfd-runtime-attestation-v2",
    "execution_authorization_trust_model": "procedural_scope_record_not_cryptographic_signature",
    "runtime_attestation_trust_model": "operator_generated_observation_record_not_hardware_attestation",
    "final_child_image_digests_required": True,
    "all_recorded_artifact_hashes_must_verify": True,
    "valid_solve_watchdog_max_fraction": 0.8,
    "max_valid_solve_wall_seconds": 5760.0,
    "mesh_watchdog_minimum_margin_factor": 2.0,
    "max_valid_mesh_wall_seconds": 900.0,
}


class QualificationError(ProtocolError):
    """The qualification inputs cannot form trustworthy campaign evidence."""


def _finite(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _full_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _image_digest(value: Any) -> bool:
    return (
        isinstance(value, str)
        and value.startswith("sha256:")
        and _full_sha256(value.removeprefix("sha256:"))
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _parse_json_object_bytes(raw: bytes, label: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise QualificationError(f"cannot read JSON {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise QualificationError(f"expected a JSON object in {label}")
    return payload


def _strict_json_object(path: Path) -> dict[str, Any]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise QualificationError(f"cannot read JSON {path}: {exc}") from exc
    return _parse_json_object_bytes(raw, path)


def _manifest_relative_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.resolve().relative_to(repo_root.resolve()))
    except ValueError as exc:
        raise QualificationError("qualification manifest must be inside the repository") from exc


def _resolve_bundle_path(bundle_root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise QualificationError(f"{label} must be a non-empty bundle-relative path")
    candidate = (Path(bundle_root).resolve() / relative).resolve()
    try:
        candidate.relative_to(Path(bundle_root).resolve())
    except ValueError as exc:
        raise QualificationError(f"{label} escapes the evidence bundle") from exc
    return candidate


def _iter_artifact_records(value: Any):
    """Yield every artifact-shaped record embedded in one immutable result."""
    if isinstance(value, Mapping):
        if {"path", "present", "size_bytes", "sha256"}.issubset(value):
            yield value
        for child in value.values():
            yield from _iter_artifact_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_artifact_records(child)


def _referenced_artifact_sha256s(results: Sequence[Mapping[str, Any]]) -> list[str]:
    digests: set[str] = set()
    for result in results:
        for record in _iter_artifact_records(result):
            if record.get("present") is True:
                digest = record.get("sha256")
                if not _full_sha256(digest):
                    raise QualificationError("present artifact record lacks a full SHA-256")
                digests.add(digest)
    return sorted(digests)


def load_qualification_context(
    manifest_path: Path = DEFAULT_MANIFEST_PATH,
    *,
    repo_root: Path = REPO_ROOT,
    verify_files: bool = True,
) -> tuple[dict[str, Any], str, str, GradingProtocol]:
    """Load and cross-bind the manifest, current protocol bytes, and snapshot."""
    manifest_path = Path(manifest_path).resolve()
    try:
        manifest_raw = manifest_path.read_bytes()
    except OSError as exc:
        raise QualificationError(f"cannot read JSON {manifest_path}: {exc}") from exc
    manifest = _parse_json_object_bytes(manifest_raw, manifest_path)
    manifest_file_sha256 = hashlib.sha256(manifest_raw).hexdigest()
    errors = validate_manifest(manifest, repo_root, verify_files=verify_files)
    if errors:
        raise QualificationError("qualification manifest failed validation: " + "; ".join(errors))

    reference = manifest.get("protocol_reference") or {}
    protocol_relative = reference.get("path")
    if not isinstance(protocol_relative, str):
        raise QualificationError("qualification manifest lacks a protocol path")
    protocol_path = (repo_root / protocol_relative).resolve()
    try:
        protocol_path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise QualificationError("qualification protocol path escapes the repository") from exc
    protocol = load_protocol(protocol_path, verify_files=verify_files)
    if protocol.payload != manifest.get("protocol_snapshot"):
        raise QualificationError("current candidate protocol does not equal the manifest snapshot")
    if protocol.sha256 != reference.get("sha256"):
        raise QualificationError("current candidate protocol canonical hash is stale")
    if sha256_file(protocol_path) != reference.get("file_sha256"):
        raise QualificationError("current candidate protocol file hash is stale")
    if (
        protocol.payload.get("protocol_status") != "qualification_required"
        or protocol.payload.get("official_grading_enabled") is not False
    ):
        raise QualificationError("qualification may evaluate only a non-official candidate")
    _publication_contract(manifest)

    manifest_canonical_sha256 = protocol_sha256(manifest)
    if sha256_file(manifest_path) != manifest_file_sha256:
        raise QualificationError("qualification manifest changed during evaluation")
    return manifest, manifest_canonical_sha256, manifest_file_sha256, protocol


def _publication_contract(manifest: Mapping[str, Any]) -> dict[str, Any]:
    if manifest.get("execution_readiness") != {
        "ready": True,
        "status": "ready",
    }:
        raise QualificationError(
            "qualification execution is blocked until publication storage is pinned"
        )
    try:
        return validate_manifest_publication_contract(
            manifest,
            ordered_manifest_wing_ids=CONTROL_IDS,
            expected_recovery_allowed=False,
        )
    except CampaignPublicationError as exc:
        raise QualificationError(
            f"qualification publication contract failed: {exc}"
        ) from exc


def _load_publication_results(
    campaign_root: Path,
    manifest: Mapping[str, Any],
    *,
    require_exact_root: bool,
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any], bytes]]]:
    contract = _publication_contract(manifest)
    try:
        ledger, documents = load_campaign_publication_export(
            campaign_root,
            ordered_manifest_wing_ids=CONTROL_IDS,
            expected_campaign_publication_id=contract["campaign_publication_id"],
            expected_publication_bucket=contract["publication_bucket"],
            allow_recovery=False,
            require_exact_root=require_exact_root,
        )
    except CampaignPublicationError as exc:
        raise QualificationError(
            f"qualification publication ledger failed: {exc}"
        ) from exc
    if tuple(documents) != CONTROL_IDS:
        raise QualificationError(
            "qualification publication ledger does not contain the exact ordered seven"
        )
    return ledger, documents


def _result_evidence(control_id: str, result: Mapping[str, Any], raw: bytes) -> dict[str, Any]:
    return {
        "path": f"{RESULTS_DIRECTORY}/{control_id}.json",
        "file_sha256": hashlib.sha256(raw).hexdigest(),
        "canonical_sha256": protocol_sha256(result),
        "size_bytes": len(raw),
    }


def _campaign_artifact_store(campaign_root: Path, *, exact_root: bool) -> Path:
    root = Path(campaign_root)
    if root.is_symlink():
        raise QualificationError("qualification campaign export cannot be a symlink")
    root = root.resolve()
    required = {
        PUBLICATION_LEDGER_DIRECTORY,
        PUBLICATION_INVENTORY_FILENAME,
        ARTIFACT_STORE.split("/", 1)[0],
    }
    names = {path.name for path in root.iterdir()} if root.is_dir() else set()
    if (names != required if exact_root else not required.issubset(names)):
        raise QualificationError(
            "qualification campaign export has a non-canonical root inventory"
        )
    artifacts_parent = _resolve_bundle_path(
        root, ARTIFACT_STORE.split("/", 1)[0], "campaign artifacts directory"
    )
    store_name = ARTIFACT_STORE.split("/", 1)[1]
    if (
        not artifacts_parent.is_dir()
        or artifacts_parent.is_symlink()
        or {path.name for path in artifacts_parent.iterdir()} != {store_name}
    ):
        raise QualificationError("qualification campaign artifact layout changed")
    store = _resolve_bundle_path(root, ARTIFACT_STORE, "campaign artifact store")
    if not store.is_dir() or store.is_symlink():
        raise QualificationError("qualification campaign artifact store is not a directory")
    return store


def _validate_artifact_store_inventory(
    artifact_store: Path,
    results: Sequence[Mapping[str, Any]],
) -> list[str]:
    expected = _referenced_artifact_sha256s(results)
    actual: list[str] = []
    for path in artifact_store.iterdir():
        if (
            not path.is_file()
            or path.is_symlink()
            or not _full_sha256(path.name)
            or sha256_file(path) != path.name
        ):
            raise QualificationError(
                f"qualification artifact store contains an invalid entry: {path.name}"
            )
        actual.append(path.name)
    if sorted(actual) != expected:
        raise QualificationError(
            "qualification artifact store is not the exact published artifact inventory"
        )
    return expected


def _runtime_evidence_identity(
    result: Mapping[str, Any], protocol: GradingProtocol, control_id: str
) -> dict[str, str]:
    """Require attested v2 MPI runtime evidence for every promotion input."""
    run_id = result.get("run_id")
    if not isinstance(run_id, str) or not run_id:
        raise QualificationError(f"{control_id} lacks a campaign run_id")
    authorization_value = result.get("execution_authorization")
    authorization = authorization_value if isinstance(authorization_value, Mapping) else {}
    authorization_snapshot_value = authorization.get("snapshot")
    authorization_snapshot = (
        authorization_snapshot_value
        if isinstance(authorization_snapshot_value, Mapping)
        else {}
    )
    authorization_sha = authorization.get("canonical_sha256")
    authorization_attestation_sha = authorization.get("runtime_attestation_sha256")
    if (
        authorization_snapshot.get("trust_model")
        != "procedural_scope_record_not_cryptographic_signature"
        or authorization_snapshot.get("run_id") != run_id
        or authorization_snapshot.get("authorized_mode") != "diagnostic"
        or authorization_snapshot.get("wing_ids") != list(CONTROL_IDS)
        or protocol_sha256(authorization_snapshot) != authorization_sha
        or authorization.get("test_only") is True
        or authorization.get("test_only_fake_runner") is True
        or not isinstance(authorization.get("authorization_id"), str)
        or not authorization["authorization_id"]
        or not _full_sha256(authorization_sha)
        or not _full_sha256(authorization_attestation_sha)
    ):
        raise QualificationError(
            f"{control_id} lacks a real diagnostic execution authorization binding"
        )

    runtime = result.get("runtime") or {}
    solver_environment = runtime.get("solver_environment") or {}
    solver_sha = runtime.get("solver_environment_sha256")
    mpi_smoke = solver_environment.get("mpi_smoke_test") or {}
    required_child_environment = dict(sorted(
        protocol.payload["environment"]["required_child_environment"].items()
    ))
    if (
        solver_environment.get("schema_version") != "superwing-runtime-fingerprint-v2"
        or solver_environment.get("status") != "complete"
        or mpi_smoke.get("schema_version") != "superwing-mpi-smoke-v1"
        or mpi_smoke.get("requested_ranks") != protocol.payload["execution"]["mpi_ranks"]
        or mpi_smoke.get("passed") is not True
        or mpi_smoke.get("required_child_environment") != required_child_environment
        or mpi_smoke.get("probe_process_load_bearing_environment")
        != required_child_environment
        or not _full_sha256(solver_sha)
        or protocol_sha256(solver_environment) != solver_sha
    ):
        raise QualificationError(f"{control_id} lacks a complete passing v2 MPI runtime fingerprint")

    attestation = runtime.get("runtime_attestation") or {}
    attestation_sha = runtime.get("runtime_attestation_sha256")
    environment = protocol.payload.get("environment") or {}
    expected_manifest = environment.get("final_child_image_manifest_digest")
    expected_amd64 = environment.get("final_child_image_linux_amd64_digest")
    if not _image_digest(expected_manifest) or not _image_digest(expected_amd64):
        raise QualificationError(
            "candidate protocol must pin both final child-image digests before qualification evaluation"
        )
    attestation_checks = (
        attestation.get("schema_version") == "wingbench-cfd-runtime-attestation-v2",
        attestation.get("trust_model")
        == "operator_generated_observation_record_not_hardware_attestation",
        attestation.get("attestation_status") == "verified_by_host",
        attestation.get("protocol_sha256") == protocol.sha256,
        attestation.get("final_child_image_manifest_digest") == expected_manifest,
        attestation.get("final_child_image_linux_amd64_digest") == expected_amd64,
        attestation.get("runtime_fingerprint_schema_version")
        == "superwing-runtime-fingerprint-v2",
        attestation.get("runtime_fingerprint_sha256") == solver_sha,
        attestation.get("runtime_probe_executed_inside_final_child") is True,
        attestation.get("mpi_smoke_test_passed") is True,
        attestation.get("mpi_ranks_verified") == protocol.payload["execution"]["mpi_ranks"],
        attestation.get("host_image_inspection_passed") is True,
        attestation.get("mounted_read_only") is True,
        attestation.get("exclusive_grader_scheduling_verified") is True,
        attestation.get("host_backed_lock_mount_verified") is True,
        attestation.get("host_lock_path")
        == protocol.payload["execution"]["host_lock_path"],
        attestation.get("architecture") == "x86_64",
        isinstance(attestation.get("container_id"), str) and bool(attestation["container_id"]),
        isinstance(attestation.get("host_image_id"), str) and bool(attestation["host_image_id"]),
        runtime.get("final_child_image_linux_amd64_digest_observed") == expected_amd64,
        _full_sha256(attestation_sha),
        protocol_sha256(attestation) == attestation_sha,
        authorization_attestation_sha == attestation_sha,
    )
    if not all(attestation_checks):
        raise QualificationError(f"{control_id} runtime attestation v2 binding failed")
    return {
        "run_id": run_id,
        "execution_authorization_sha256": str(authorization_sha),
        "runtime_attestation_sha256": str(attestation_sha),
        "solver_environment_sha256": str(solver_sha),
        "final_child_image_manifest_digest": str(expected_manifest),
        "final_child_image_linux_amd64_digest": str(expected_amd64),
    }


def _timeout_count(result: Mapping[str, Any]) -> int:
    count = 0
    for attempt in (result.get("mesh") or {}).get("attempts") or []:
        execution = attempt.get("execution") or {}
        if execution.get("termination_reason") == "timeout":
            count += 1
    for condition in result.get("conditions") or []:
        for attempt in condition.get("attempts") or []:
            execution = attempt.get("execution") or {}
            if execution.get("termination_reason") == "timeout":
                count += 1
    return count


def _executed_seed_values(result: Mapping[str, Any]) -> list[float]:
    seeds: list[float] = []
    for condition in result.get("conditions") or []:
        for attempt in condition.get("attempts") or []:
            seed = (attempt.get("measurements") or {}).get("seed_alpha0_deg")
            if _finite(seed):
                seeds.append(float(seed))
    return seeds


def _observed_summary(result: Mapping[str, Any]) -> dict[str, Any]:
    aggregate = result.get("aggregate") or {}
    conditions = result.get("conditions") or []
    return {
        "mesh_outcome": (result.get("mesh") or {}).get("outcome"),
        "mesh_min_volume": (result.get("mesh") or {}).get("min_volume"),
        "mesh_min_quality": (result.get("mesh") or {}).get("min_quality"),
        "aggregate_admissibility": aggregate.get("admissibility"),
        "profile_status": aggregate.get("profile_status"),
        "n_conditions": aggregate.get("n_conditions"),
        "n_valid": aggregate.get("n_valid"),
        "n_invalid": aggregate.get("n_invalid"),
        "n_inconclusive": aggregate.get("n_inconclusive"),
        "timeout_attempts": _timeout_count(result),
        "executed_seed_alpha0_deg": _executed_seed_values(result),
        "conditions": [
            {
                "condition_id": condition.get("condition_id"),
                "outcome_class": condition.get("outcome_class"),
                "outcome": condition.get("outcome"),
                "realized_aoa_deg": (condition.get("measurements") or {}).get(
                    "realized_aoa_deg"
                ),
            }
            for condition in conditions
        ],
    }


def _watchdog_margin_observation(
    results: Sequence[Mapping[str, Any]],
    protocol: GradingProtocol,
    requirements: Mapping[str, Any],
) -> dict[str, Any]:
    solve_fraction = float(requirements["valid_solve_watchdog_max_fraction"])
    solve_limit = float(requirements["max_valid_solve_wall_seconds"])
    mesh_factor = float(requirements["mesh_watchdog_minimum_margin_factor"])
    mesh_limit = float(requirements["max_valid_mesh_wall_seconds"])
    expected_solve_limit = (
        float(protocol.payload["execution"]["solve_timeout_seconds_per_attempt"])
        * solve_fraction
    )
    expected_mesh_limit = (
        float(protocol.payload["execution"]["mesh_timeout_seconds"]) / mesh_factor
    )
    if not math.isclose(solve_limit, expected_solve_limit, rel_tol=0.0, abs_tol=1e-12):
        raise QualificationError("solve watchdog margin does not match the protocol timeout")
    if not math.isclose(mesh_limit, expected_mesh_limit, rel_tol=0.0, abs_tol=1e-12):
        raise QualificationError("mesh watchdog margin does not match the protocol timeout")

    solve_walls: list[float] = []
    mesh_walls: list[float] = []
    missing: list[str] = []
    for result in results:
        control_id = str(result.get("wing_id"))
        mesh = result.get("mesh") or {}
        if mesh.get("outcome") in {"valid", "marginal"}:
            candidates = [
                attempt for attempt in (mesh.get("attempts") or [])
                if attempt.get("outcome") in {"valid", "marginal"}
            ]
            if len(candidates) != 1 or not _finite(
                (candidates[0].get("execution") or {}).get("wall_seconds")
            ):
                missing.append(f"{control_id}: valid mesh lacks one final wall time")
            else:
                mesh_walls.append(float(candidates[0]["execution"]["wall_seconds"]))
        for condition in result.get("conditions") or []:
            if condition.get("outcome_class") != "valid":
                continue
            candidates = [
                attempt for attempt in (condition.get("attempts") or [])
                if attempt.get("outcome_class") == "valid"
            ]
            if len(candidates) != 1 or not _finite(
                (candidates[0].get("execution") or {}).get("wall_seconds")
            ):
                missing.append(
                    f"{control_id}/{condition.get('condition_id')}: valid solve lacks one final wall time"
                )
            else:
                solve_walls.append(float(candidates[0]["execution"]["wall_seconds"]))
    max_solve = max(solve_walls) if solve_walls else None
    max_mesh = max(mesh_walls) if mesh_walls else None
    passed = (
        not missing
        and max_solve is not None
        and max_mesh is not None
        and max_solve <= solve_limit
        and max_mesh <= mesh_limit
    )
    return {
        "valid_solve_watchdog_max_fraction": solve_fraction,
        "max_valid_solve_wall_seconds_allowed": solve_limit,
        "max_valid_solve_wall_seconds_observed": max_solve,
        "mesh_watchdog_minimum_margin_factor": mesh_factor,
        "max_valid_mesh_wall_seconds_allowed": mesh_limit,
        "max_valid_mesh_wall_seconds_observed": max_mesh,
        "missing_wall_time_evidence": missing,
        "passed": passed,
    }


def _seed_contract_failures(
    result: Mapping[str, Any],
    seed_contract: Mapping[str, Any],
    tolerances: Mapping[str, Any],
) -> list[str]:
    expected = float(seed_contract["candidate_alpha0_deg"])
    tolerance = float(tolerances["seed_identity_abs_tolerance_deg"])
    failures = []
    for condition in result.get("conditions") or []:
        for attempt_index, attempt in enumerate(condition.get("attempts") or []):
            seed = (attempt.get("measurements") or {}).get("seed_alpha0_deg")
            if not _finite(seed):
                failures.append(
                    f"{condition.get('condition_id')} attempt {attempt_index} lacks a finite seed"
                )
            elif not math.isclose(float(seed), expected, rel_tol=0.0, abs_tol=tolerance):
                failures.append(f"executed seed {seed} does not match candidate seed {expected}")
    return failures


def _all_three_failures(
    result: Mapping[str, Any], acceptance: Mapping[str, Any]
) -> list[str]:
    aggregate = result.get("aggregate") or {}
    conditions = result.get("conditions") or []
    failures: list[str] = []
    checks = (
        (len(conditions) == acceptance["required_condition_results"], "condition count is not 3"),
        (
            aggregate.get("n_valid") == acceptance["required_valid_conditions"],
            "valid-condition count is not 3",
        ),
        (
            aggregate.get("admissibility") == acceptance["required_aggregate_admissibility"],
            "aggregate admissibility is not pass",
        ),
        (
            aggregate.get("n_invalid") == acceptance["invalid_conditions_allowed"],
            "invalid-condition count is nonzero",
        ),
        (
            aggregate.get("n_inconclusive") == acceptance["inconclusive_conditions_allowed"],
            "inconclusive-condition count is nonzero",
        ),
        (_timeout_count(result) <= acceptance["timeouts_allowed"], "a timeout attempt occurred"),
    )
    failures.extend(message for passed, message in checks if not passed)
    return failures


def _exploratory_failures(
    result: Mapping[str, Any], acceptance: Mapping[str, Any]
) -> list[str]:
    aggregate = result.get("aggregate") or {}
    failures = []
    if len(result.get("conditions") or []) != acceptance["required_condition_results"]:
        failures.append("condition count is not 3")
    if aggregate.get("admissibility") not in acceptance["accepted_observation_classes"]:
        failures.append("aggregate observation class is unsupported")
    return failures


def _runaway_failures(
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    tolerances: Mapping[str, Any],
) -> list[str]:
    lower, upper = (float(value) for value in acceptance["aoa_deg_inclusive"])
    padding = float(tolerances["aoa_boundary_padding_deg"])
    aggregate = result.get("aggregate") or {}
    conditions = result.get("conditions") or []
    failures: list[str] = []
    if len(conditions) != acceptance["required_condition_results"]:
        failures.append("condition count is not 3")
    if aggregate.get("profile_status") != acceptance["required_profile_status"]:
        failures.append("runaway control did not complete a conclusive profile")
    if aggregate.get("n_inconclusive") != acceptance["inconclusive_conditions_allowed"]:
        failures.append("runaway control contains an inconclusive condition")
    if _timeout_count(result) > acceptance["timeouts_allowed"]:
        failures.append("runaway control contains a timeout attempt")

    out_of_range_seen = False
    for condition in conditions:
        aoa = (condition.get("measurements") or {}).get("realized_aoa_deg")
        if _finite(aoa) and not (lower - padding <= float(aoa) <= upper + padding):
            out_of_range_seen = True
            if condition.get("outcome_class") not in acceptance[
                "out_of_range_condition_allowed_outcomes"
            ]:
                failures.append(
                    f"{condition.get('condition_id')} accepted out-of-range AoA {float(aoa)}"
                )
                continue
            completed_attempts = [
                attempt for attempt in (condition.get("attempts") or [])
                if attempt.get("outcome_class") in {"valid", "invalid"}
            ]
            final_attempt = completed_attempts[-1] if completed_attempts else {}
            gates = final_attempt.get("gate_decisions") or {}
            if (
                "invalid_aoa" not in (condition.get("reason_codes") or [])
                or "invalid_aoa" not in (final_attempt.get("reason_codes") or [])
                or gates.get("aoa_inclusive") is not False
                or gates.get("solvecl_converged") is not True
                or gates.get("solvecl_threshold_converged") is not True
                or gates.get("solver_flags_clear") is not True
                or gates.get("numeric") is not True
            ):
                failures.append(
                    f"{condition.get('condition_id')} did not prove an otherwise complete AoA-gate rejection"
                )
    if out_of_range_seen and aggregate.get("admissibility") != acceptance[
        "aggregate_admissibility_if_any_out_of_range"
    ]:
        failures.append("aggregate did not fail after an out-of-range AoA")
    if acceptance.get("out_of_range_observation_required") is not True:
        raise QualificationError("runaway control must preregister an exercised rejection branch")
    if not out_of_range_seen:
        failures.append("runaway control did not exercise an out-of-range AoA rejection")
    return failures


def _unmeshable_failures(
    result: Mapping[str, Any],
    acceptance: Mapping[str, Any],
    tolerances: Mapping[str, Any],
) -> list[str]:
    mesh = result.get("mesh") or {}
    aggregate = result.get("aggregate") or {}
    failures: list[str] = []
    if mesh.get("outcome") != acceptance["required_mesh_outcome"]:
        failures.append("mesh outcome is not conclusive unmeshable")
    if aggregate.get("admissibility") != acceptance["required_aggregate_admissibility"]:
        failures.append("aggregate admissibility is not fail")
    if _timeout_count(result) > acceptance["timeouts_allowed"]:
        failures.append("unmeshable control contains a timeout attempt")
    metrics = [mesh.get("min_volume"), mesh.get("min_quality")]
    threshold = float(tolerances["mesh_nonpositive_threshold"])
    if threshold != float(acceptance["nonpositive_threshold"]):
        raise QualificationError("mesh threshold disagrees with campaign tolerances")
    if not any(_finite(value) and float(value) <= threshold for value in metrics):
        failures.append("neither final mesh metric is nonpositive")
    if acceptance["condition_solves_allowed_after_mesh_failure"] is False:
        if any(condition.get("attempts") for condition in result.get("conditions") or []):
            failures.append("a condition solve ran after conclusive mesh failure")
    return failures


def evaluate_control(
    control: Mapping[str, Any],
    result: Mapping[str, Any],
    seed_contract: Mapping[str, Any],
    tolerances: Mapping[str, Any],
) -> dict[str, Any]:
    acceptance = control.get("acceptance") or {}
    rule_id = acceptance.get("rule_id")
    failures = _seed_contract_failures(result, seed_contract, tolerances)
    if rule_id == "all_three_conditions_valid":
        failures.extend(_all_three_failures(result, acceptance))
    elif rule_id == "exploratory_record_only":
        failures.extend(_exploratory_failures(result, acceptance))
    elif rule_id == "out_of_range_aoa_must_not_be_accepted":
        failures.extend(_runaway_failures(result, acceptance, tolerances))
    elif rule_id == "reproduce_nonpositive_mesh_invalidity":
        failures.extend(_unmeshable_failures(result, acceptance, tolerances))
    else:
        raise QualificationError(f"unsupported qualification rule: {rule_id!r}")
    return {
        "control_id": control["control_id"],
        "campaign_gate": acceptance.get("campaign_gate") is True,
        "rule_id": rule_id,
        "acceptance_passed": not failures,
        "failure_reasons": failures,
        "observed": _observed_summary(result),
    }


def _validate_result_identity(
    *,
    result: Mapping[str, Any],
    control: Mapping[str, Any],
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    protocol: GradingProtocol,
    result_validator: ResultValidator,
    verify_artifacts: bool,
    artifact_store: Path | None,
) -> dict[str, str]:
    control_id = control["control_id"]
    if result.get("wing_id") != control_id:
        raise QualificationError(f"result identity mismatch for {control_id}")
    if result.get("official") is not False:
        raise QualificationError(f"{control_id} is not a non-official candidate result")
    if result.get("legacy_evidence_used_for_verdict") is not False:
        raise QualificationError(f"{control_id} used legacy evidence for its verdict")
    protocol_row = result.get("protocol") or {}
    if (
        protocol_row.get("protocol_id") != protocol.protocol_id
        or protocol_row.get("protocol_status") != "qualification_required"
        or protocol_row.get("canonical_sha256") != protocol.sha256
    ):
        raise QualificationError(f"{control_id} is bound to the wrong candidate protocol")
    manifest_row = result.get("benchmark_manifest") or {}
    if (
        manifest_row.get("manifest_id") != manifest.get("manifest_id")
        or manifest_row.get("canonical_sha256") != manifest_sha256
        or manifest_row.get("official_benchmark") is not False
    ):
        raise QualificationError(f"{control_id} is bound to the wrong qualification manifest")
    geometry = result.get("geometry") or {}
    if geometry.get("sha256") != control.get("geometry_sha256"):
        raise QualificationError(f"{control_id} is bound to the wrong geometry hash")
    try:
        embedded_hash = geometry_sha256(geometry.get("geom_38d"))
    except (TypeError, ValueError) as exc:
        raise QualificationError(f"{control_id} embeds malformed geometry: {exc}") from exc
    if embedded_hash != control.get("geometry_sha256"):
        raise QualificationError(f"{control_id} embedded geometry differs from the manifest")
    runtime_identity = _runtime_evidence_identity(result, protocol, control_id)
    wing = qualification_control_as_wing(control)
    try:
        result_validator(
            result,
            protocol,
            wing,
            manifest_sha256,
            verify_artifacts=verify_artifacts,
            artifact_store=artifact_store,
        )
    except (ProtocolError, OSError, ValueError, KeyError) as exc:
        raise QualificationError(f"{control_id} result contract failed: {exc}") from exc
    return runtime_identity


def build_qualification_envelope(
    *,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_canonical_sha256: str,
    manifest_file_sha256: str,
    protocol: GradingProtocol,
    evidence_bundle_root: Path,
    repo_root: Path = REPO_ROOT,
    result_validator: ResultValidator = validate_result,
    verify_artifacts: bool = True,
) -> dict[str, Any]:
    """Validate exact campaign inputs and build a deterministic hashed record."""
    evidence_bundle_root = Path(evidence_bundle_root).resolve()
    publication_contract = _publication_contract(manifest)
    publication_ledger, published_documents = _load_publication_results(
        evidence_bundle_root,
        manifest,
        require_exact_root=False,
    )
    if (
        publication_ledger.get("protocol_sha256") != protocol.sha256
        or publication_ledger.get("manifest_sha256")
        != manifest_canonical_sha256
    ):
        raise QualificationError(
            "qualification publication ledger is bound to a different protocol or manifest"
        )
    artifact_store = _campaign_artifact_store(
        evidence_bundle_root, exact_root=False
    )
    controls = manifest.get("controls") or []
    if tuple(control.get("control_id") for control in controls) != CONTROL_IDS:
        raise QualificationError("manifest control roster is not the exact frozen seven")
    campaign = manifest.get("campaign_acceptance") or {}
    seed_contract = campaign.get("seed_contract") or {}
    tolerances = campaign.get("acceptance_tolerances") or {}
    evidence_requirements = campaign.get("campaign_evidence_requirements") or {}

    if verify_artifacts is not True:
        # This branch exists only for isolated unit tests using an injected
        # validator.  It can never produce promotion-eligible evidence.
        artifact_files_verified = False
    else:
        artifact_files_verified = True
    evaluations: list[dict[str, Any]] = []
    runtime_identities: list[dict[str, str]] = []
    campaign_results: list[dict[str, Any]] = []
    for control in controls:
        control_id = control["control_id"]
        result, result_raw = published_documents[control_id]
        result_evidence = _result_evidence(control_id, result, result_raw)
        runtime_identity = _validate_result_identity(
            result=result,
            control=control,
            manifest=manifest,
            manifest_sha256=manifest_canonical_sha256,
            protocol=protocol,
            result_validator=result_validator,
            verify_artifacts=verify_artifacts,
            artifact_store=artifact_store,
        )
        runtime_identities.append(runtime_identity)
        campaign_results.append(result)
        evaluation = evaluate_control(control, result, seed_contract, tolerances)
        evaluation["result_evidence"] = result_evidence
        evaluations.append(evaluation)

    distinct_runtime_identities = {
        tuple(sorted(identity.items())) for identity in runtime_identities
    }
    if len(distinct_runtime_identities) != 1:
        raise QualificationError(
            "the seven qualification results do not share one run, authorization, attestation, and runtime"
        )
    qualified_execution_identity = runtime_identities[0]

    gate_ids = campaign.get("gate_control_ids")
    if gate_ids != [row["control_id"] for row in evaluations if row["campaign_gate"]]:
        raise QualificationError("manifest gate-control roster disagrees with control rules")
    gate_rules_passed = all(
        row["acceptance_passed"] for row in evaluations if row["campaign_gate"]
    )
    watchdog_margin = _watchdog_margin_observation(
        campaign_results, protocol, evidence_requirements
    )
    gate_passed = (
        gate_rules_passed
        and artifact_files_verified
        and watchdog_margin["passed"] is True
    )
    artifact_sha256s = _validate_artifact_store_inventory(
        artifact_store, campaign_results
    )
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "qualification_id": manifest["manifest_id"],
        "qualification_status": "passed" if gate_passed else "failed",
        "official": False,
        "official_results_authorized": False,
        "execution_authorized_by_this_record": False,
        "candidate_protocol": {
            "path": manifest["protocol_reference"]["path"],
            "protocol_id": protocol.protocol_id,
            "protocol_status": protocol.payload["protocol_status"],
            "official_grading_enabled": protocol.payload["official_grading_enabled"],
            "canonical_sha256": protocol.sha256,
            "file_sha256": manifest["protocol_reference"]["file_sha256"],
        },
        "qualification_manifest": {
            "path": _manifest_relative_path(Path(manifest_path), repo_root),
            "manifest_id": manifest["manifest_id"],
            "schema_version": manifest["schema_version"],
            "canonical_sha256": manifest_canonical_sha256,
            "file_sha256": manifest_file_sha256,
            "publication_contract": publication_contract,
        },
        "evidence_bundle": {
            "schema_version": EVIDENCE_BUNDLE_SCHEMA_VERSION,
            "results_directory": RESULTS_DIRECTORY,
            "artifact_store": ARTIFACT_STORE,
            "publication_ledger_directory": PUBLICATION_LEDGER_DIRECTORY,
            "publication_inventory": PUBLICATION_INVENTORY_FILENAME,
            "result_path_semantics": "bundle-relative-posix",
            "artifact_path_semantics": "content-addressed-by-recorded-sha256",
            "result_file_count": len(CONTROL_IDS),
            "artifact_sha256s": artifact_sha256s,
        },
        "seed_contract": copy.deepcopy(seed_contract),
        "acceptance_tolerances": copy.deepcopy(tolerances),
        "campaign_evidence_requirements": copy.deepcopy(evidence_requirements),
        "watchdog_margin": watchdog_margin,
        "qualified_execution_identity": qualified_execution_identity,
        "artifact_files_cryptographically_verified": artifact_files_verified,
        "publication_ledger": publication_ledger,
        "controls": evaluations,
        "campaign_decision": {
            "gate_control_ids": list(gate_ids),
            "exploratory_control_ids": list(campaign.get("exploratory_control_ids") or []),
            "n_gate_controls": len(gate_ids),
            "n_gate_controls_passing_rules": sum(
                row["acceptance_passed"] for row in evaluations if row["campaign_gate"]
            ),
            "all_gate_control_rules_passed": gate_rules_passed,
            "watchdog_margin_passed": watchdog_margin["passed"],
            "all_gate_controls_passed": gate_passed,
        },
        "promotion": {
            "eligible_for_separate_manual_freeze_review": gate_passed,
            "automatic_promotion_allowed": False,
            "promotion_performed": False,
            "official_authorization_granted": False,
            "release_lock_requirement": (
                "A separately reviewed release lock must bind this envelope's "
                "qualification_record_sha256 before any official run."
            ),
        },
    }
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "hash_semantics": HASH_SEMANTICS,
        "qualification_record_sha256": protocol_sha256(record),
        "qualification_record": record,
    }
    validate_qualification_envelope(envelope)
    return envelope


def _exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise QualificationError(f"{label} structure changed")
    return value


def _verify_envelope_evidence_files(
    envelope: Mapping[str, Any], evidence_bundle_root: Path
) -> None:
    """Reopen every result/artifact and reproduce the complete qualification."""
    evidence_bundle_root = Path(evidence_bundle_root).resolve()
    expected_root_entries = {
        QUALIFICATION_RECORD_FILENAME,
        RESULTS_DIRECTORY,
        ARTIFACT_STORE.split("/", 1)[0],
        PUBLICATION_LEDGER_DIRECTORY,
        PUBLICATION_INVENTORY_FILENAME,
    }
    if (
        not evidence_bundle_root.is_dir()
        or evidence_bundle_root.is_symlink()
        or {path.name for path in evidence_bundle_root.iterdir()} != expected_root_entries
    ):
        raise QualificationError("qualification evidence-bundle root layout changed")
    envelope_path = evidence_bundle_root / QUALIFICATION_RECORD_FILENAME
    if not envelope_path.is_file() or envelope_path.is_symlink():
        raise QualificationError("qualification bundle record is not a regular local file")
    stored_envelope = _strict_json_object(envelope_path)
    if canonical_json_bytes(stored_envelope) != canonical_json_bytes(envelope):
        raise QualificationError("qualification bundle record differs from the reviewed envelope")
    results_directory = _resolve_bundle_path(
        evidence_bundle_root, RESULTS_DIRECTORY, "results directory"
    )
    if (
        not results_directory.is_dir()
        or results_directory.is_symlink()
        or sorted(path.name for path in results_directory.iterdir())
        != sorted(f"{control_id}.json" for control_id in CONTROL_IDS)
        or any(not path.is_file() or path.is_symlink() for path in results_directory.iterdir())
    ):
        raise QualificationError("qualification result-bundle inventory changed")
    artifacts_parent = _resolve_bundle_path(
        evidence_bundle_root, ARTIFACT_STORE.split("/", 1)[0], "artifacts directory"
    )
    if (
        not artifacts_parent.is_dir()
        or artifacts_parent.is_symlink()
        or [path.name for path in artifacts_parent.iterdir()] != [ARTIFACT_STORE.split("/", 1)[1]]
    ):
        raise QualificationError("qualification artifact-bundle layout changed")
    record = envelope["qualification_record"]
    manifest_row = record["qualification_manifest"]
    manifest_relative = manifest_row["path"]
    if not isinstance(manifest_relative, str):
        raise QualificationError("qualification manifest path is missing")
    manifest_path = (REPO_ROOT / manifest_relative).resolve()
    try:
        manifest_path.relative_to(REPO_ROOT.resolve())
    except ValueError as exc:
        raise QualificationError("qualification manifest path escapes the repository") from exc
    manifest, manifest_sha, manifest_file_sha, protocol = load_qualification_context(
        manifest_path
    )
    bundle = record["evidence_bundle"]
    artifact_store = _resolve_bundle_path(
        evidence_bundle_root, bundle["artifact_store"], "artifact store"
    )
    if not artifact_store.is_dir() or artifact_store.is_symlink():
        raise QualificationError("qualification artifact store is not a local directory")
    actual_artifacts: list[str] = []
    for path in artifact_store.iterdir():
        if (
            not path.is_file()
            or path.is_symlink()
            or not _full_sha256(path.name)
        ):
            raise QualificationError(
                f"qualification artifact store contains an unexpected entry: {path.name}"
            )
        actual_artifacts.append(path.name)
    if sorted(actual_artifacts) != bundle["artifact_sha256s"]:
        raise QualificationError("qualification artifact-store inventory changed")
    for row in record["controls"]:
        evidence = row["result_evidence"]
        path = _resolve_bundle_path(
            evidence_bundle_root, evidence["path"], "qualification result evidence"
        )
        if not path.is_file():
            raise QualificationError(f"qualification result evidence is missing: {path}")
        raw = path.read_bytes()
        parsed = _parse_json_object_bytes(raw, path)
        if (
            len(raw) != evidence["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != evidence["file_sha256"]
            or protocol_sha256(parsed) != evidence["canonical_sha256"]
        ):
            raise QualificationError(f"qualification result evidence changed: {path}")
    rebuilt = build_qualification_envelope(
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_canonical_sha256=manifest_sha,
        manifest_file_sha256=manifest_file_sha,
        protocol=protocol,
        evidence_bundle_root=evidence_bundle_root,
        result_validator=validate_result,
        verify_artifacts=True,
    )
    if canonical_json_bytes(rebuilt) != canonical_json_bytes(envelope):
        raise QualificationError("qualification envelope does not reproduce from its evidence files")


def validate_qualification_envelope(
    envelope: Mapping[str, Any],
    *,
    verify_evidence_files: bool = False,
    evidence_bundle_root: Path | None = None,
) -> None:
    _exact_keys(envelope, {
        "schema_version", "hash_semantics", "qualification_record_sha256",
        "qualification_record",
    }, "qualification envelope")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise QualificationError("qualification envelope schema mismatch")
    if envelope.get("hash_semantics") != HASH_SEMANTICS:
        raise QualificationError("qualification record hash semantics changed")
    record = envelope.get("qualification_record")
    if not isinstance(record, Mapping) or record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise QualificationError("qualification record schema mismatch")
    if protocol_sha256(record) != envelope.get("qualification_record_sha256"):
        raise QualificationError("qualification record content hash mismatch")
    _exact_keys(record, {
        "schema_version", "qualification_id", "qualification_status", "official",
        "official_results_authorized", "execution_authorized_by_this_record",
        "candidate_protocol", "qualification_manifest", "evidence_bundle", "seed_contract",
        "acceptance_tolerances", "campaign_evidence_requirements", "watchdog_margin",
        "qualified_execution_identity", "artifact_files_cryptographically_verified",
        "publication_ledger", "controls", "campaign_decision", "promotion",
    }, "qualification record")
    candidate = _exact_keys(record.get("candidate_protocol"), {
        "path", "protocol_id", "protocol_status", "official_grading_enabled",
        "canonical_sha256", "file_sha256",
    }, "candidate protocol identity")
    if (
        not isinstance(candidate.get("path"), str)
        or not isinstance(candidate.get("protocol_id"), str)
        or candidate.get("protocol_status") != "qualification_required"
        or candidate.get("official_grading_enabled") is not False
        or not _full_sha256(candidate.get("canonical_sha256"))
        or not _full_sha256(candidate.get("file_sha256"))
    ):
        raise QualificationError("candidate protocol identity is incomplete")
    manifest_row = _exact_keys(record.get("qualification_manifest"), {
        "path", "manifest_id", "schema_version", "canonical_sha256", "file_sha256",
        "publication_contract",
    }, "qualification manifest identity")
    if (
        not isinstance(manifest_row.get("path"), str)
        or not isinstance(manifest_row.get("manifest_id"), str)
        or manifest_row.get("schema_version") != "cfd-protocol-qualification-manifest-v1"
        or record.get("qualification_id") != manifest_row.get("manifest_id")
        or not _full_sha256(manifest_row.get("canonical_sha256"))
        or not _full_sha256(manifest_row.get("file_sha256"))
    ):
        raise QualificationError("qualification manifest identity is incomplete")
    bundle = _exact_keys(record.get("evidence_bundle"), {
        "schema_version", "results_directory", "artifact_store",
        "publication_ledger_directory", "publication_inventory",
        "result_path_semantics", "artifact_path_semantics", "result_file_count",
        "artifact_sha256s",
    }, "qualification evidence bundle")
    artifact_sha256s = bundle.get("artifact_sha256s")
    if (
        bundle.get("schema_version") != EVIDENCE_BUNDLE_SCHEMA_VERSION
        or bundle.get("results_directory") != RESULTS_DIRECTORY
        or bundle.get("artifact_store") != ARTIFACT_STORE
        or bundle.get("publication_ledger_directory")
        != PUBLICATION_LEDGER_DIRECTORY
        or bundle.get("publication_inventory") != PUBLICATION_INVENTORY_FILENAME
        or bundle.get("result_path_semantics") != "bundle-relative-posix"
        or bundle.get("artifact_path_semantics")
        != "content-addressed-by-recorded-sha256"
        or bundle.get("result_file_count") != len(CONTROL_IDS)
        or not isinstance(artifact_sha256s, list)
        or artifact_sha256s != sorted(set(artifact_sha256s))
        or not all(_full_sha256(value) for value in artifact_sha256s)
    ):
        raise QualificationError("qualification evidence-bundle contract changed")
    seed_contract = record.get("seed_contract") or {}
    selection_record = seed_contract.get("candidate_selection_record") or {}
    if (
        seed_contract.get("candidate_policy") != "fixed_per_condition"
        or seed_contract.get("candidate_alpha0_deg") != 6.6401110468
        or seed_contract.get("candidate_source")
        != "superwing-successful-target-cl-band-median-v1"
        or seed_contract.get("candidate_selection_rule")
        != "exact_median_successful_cases_in_cl_0.39_0.43_no_rounding"
        or selection_record.get("path") != "configs/cfd/seed_aoa_selection_v1.json"
        or not _full_sha256(selection_record.get("file_sha256"))
        or selection_record.get("schema_version")
        != "wingbench-cfd-seed-aoa-selection-v1"
        or selection_record.get("canonical_seed_alpha0_deg") != 6.6401110468
        or seed_contract.get("candidate_applies_to_every_wing_and_condition") is not True
        or seed_contract.get("candidate_future_reselection_allowed") is not False
        or seed_contract.get("candidate_condition_ids") != ["mach_q1", "mach_q2", "mach_q3"]
        or seed_contract.get("candidate_seed_required_for_every_executed_attempt") is not True
        or seed_contract.get("alternate_diagnostics_are_qualification_results") is not False
        or not isinstance(seed_contract.get("alternate_seed_diagnostics"), list)
        or len(seed_contract["alternate_seed_diagnostics"]) != 2
        or any(
            row.get("control_id") != "dataset_1216"
            or row.get("alpha0_deg") != 7.5
            or row.get("qualification_gate") is not False
            or row.get("historical_only") is not True
            for row in seed_contract["alternate_seed_diagnostics"]
        )
    ):
        raise QualificationError("qualification seed contract changed")
    if record.get("acceptance_tolerances") != EXPECTED_ACCEPTANCE_TOLERANCES:
        raise QualificationError("qualification acceptance tolerances changed")
    if record.get("campaign_evidence_requirements") != EXPECTED_EVIDENCE_REQUIREMENTS:
        raise QualificationError("qualification evidence requirements changed")
    identity = _exact_keys(record.get("qualified_execution_identity"), {
        "run_id", "execution_authorization_sha256", "runtime_attestation_sha256",
        "solver_environment_sha256", "final_child_image_manifest_digest",
        "final_child_image_linux_amd64_digest",
    }, "qualified execution identity")
    if (
        not isinstance(identity.get("run_id"), str)
        or not identity["run_id"]
        or not all(_full_sha256(identity.get(name)) for name in (
            "execution_authorization_sha256", "runtime_attestation_sha256",
            "solver_environment_sha256",
        ))
        or not _image_digest(identity.get("final_child_image_manifest_digest"))
        or not _image_digest(identity.get("final_child_image_linux_amd64_digest"))
    ):
        raise QualificationError("qualified execution identity is incomplete")
    controls = record.get("controls")
    if not isinstance(controls, list) or [row.get("control_id") for row in controls] != list(CONTROL_IDS):
        raise QualificationError("qualification record does not contain the exact seven controls")
    for row in controls:
        control_id = row["control_id"]
        _exact_keys(row, {
            "control_id", "campaign_gate", "rule_id", "acceptance_passed",
            "failure_reasons", "observed", "result_evidence",
        }, f"qualification control {control_id}")
        expected_gate = control_id in GATE_CONTROL_IDS
        if row.get("campaign_gate") is not expected_gate:
            raise QualificationError(f"{control_id} campaign-gate role changed")
        if row.get("rule_id") != RULE_BY_CONTROL[control_id]:
            raise QualificationError(f"{control_id} acceptance rule changed")
        failures = row.get("failure_reasons")
        acceptance_passed = row.get("acceptance_passed")
        if (
            not isinstance(failures, list)
            or not all(isinstance(reason, str) and reason for reason in failures)
            or type(acceptance_passed) is not bool
            or acceptance_passed is not (not bool(failures))
        ):
            raise QualificationError(f"{control_id} acceptance result is inconsistent")
        observed = row.get("observed")
        if not isinstance(observed, Mapping) or not isinstance(observed.get("conditions"), list):
            raise QualificationError(f"{control_id} observation summary is incomplete")
        evidence = _exact_keys(row.get("result_evidence"), {
            "path", "file_sha256", "canonical_sha256", "size_bytes",
        }, f"{control_id} result evidence")
        if (
            not isinstance(evidence.get("path"), str)
            or evidence["path"] != f"{RESULTS_DIRECTORY}/{control_id}.json"
            or not _full_sha256(evidence.get("file_sha256"))
            or not _full_sha256(evidence.get("canonical_sha256"))
            or isinstance(evidence.get("size_bytes"), bool)
            or not isinstance(evidence.get("size_bytes"), int)
            or evidence["size_bytes"] <= 0
        ):
            raise QualificationError(f"{control_id} result evidence is incomplete")
    try:
        publication_contract = validate_manifest_publication_contract(
            {"publication_contract": manifest_row.get("publication_contract")},
            ordered_manifest_wing_ids=CONTROL_IDS,
            expected_recovery_allowed=False,
        )
        validate_publication_ledger_record(
            record.get("publication_ledger"),
            ordered_manifest_wing_ids=CONTROL_IDS,
            result_evidence_by_wing={
                row["control_id"]: row["result_evidence"] for row in controls
            },
            expected_campaign_publication_id=publication_contract[
                "campaign_publication_id"
            ],
            expected_publication_bucket=publication_contract[
                "publication_bucket"
            ],
            allow_recovery=False,
        )
        publication_ledger = record.get("publication_ledger")
        if (
            not isinstance(publication_ledger, Mapping)
            or publication_ledger.get("protocol_sha256")
            != candidate.get("canonical_sha256")
            or publication_ledger.get("manifest_sha256")
            != manifest_row.get("canonical_sha256")
        ):
            raise CampaignPublicationError(
                "qualification ledger protocol or manifest identity changed"
            )
    except CampaignPublicationError as exc:
        raise QualificationError(
            f"qualification publication ledger is invalid: {exc}"
        ) from exc
    decision = record.get("campaign_decision") or {}
    _exact_keys(decision, {
        "gate_control_ids", "exploratory_control_ids", "n_gate_controls",
        "n_gate_controls_passing_rules", "all_gate_control_rules_passed",
        "watchdog_margin_passed", "all_gate_controls_passed",
    }, "qualification campaign decision")
    if (
        decision.get("gate_control_ids") != list(GATE_CONTROL_IDS)
        or decision.get("exploratory_control_ids") != list(EXPLORATORY_CONTROL_IDS)
        or decision.get("n_gate_controls") != len(GATE_CONTROL_IDS)
    ):
        raise QualificationError("qualification campaign roster changed")
    gate_rows = [row for row in controls if row.get("campaign_gate") is True]
    rules_passed = all(row.get("acceptance_passed") is True for row in gate_rows)
    passing_rule_count = sum(row.get("acceptance_passed") is True for row in gate_rows)
    if decision.get("n_gate_controls_passing_rules") != passing_rule_count:
        raise QualificationError("qualification passing-gate count is inconsistent")
    if decision.get("all_gate_control_rules_passed") is not rules_passed:
        raise QualificationError("qualification control-rule decision is internally inconsistent")
    watchdog = record.get("watchdog_margin") or {}
    _exact_keys(watchdog, {
        "valid_solve_watchdog_max_fraction", "max_valid_solve_wall_seconds_allowed",
        "max_valid_solve_wall_seconds_observed", "mesh_watchdog_minimum_margin_factor",
        "max_valid_mesh_wall_seconds_allowed", "max_valid_mesh_wall_seconds_observed",
        "missing_wall_time_evidence", "passed",
    }, "qualification watchdog observation")
    if (
        watchdog.get("valid_solve_watchdog_max_fraction")
        != EXPECTED_EVIDENCE_REQUIREMENTS["valid_solve_watchdog_max_fraction"]
        or watchdog.get("max_valid_solve_wall_seconds_allowed")
        != EXPECTED_EVIDENCE_REQUIREMENTS["max_valid_solve_wall_seconds"]
        or watchdog.get("mesh_watchdog_minimum_margin_factor")
        != EXPECTED_EVIDENCE_REQUIREMENTS["mesh_watchdog_minimum_margin_factor"]
        or watchdog.get("max_valid_mesh_wall_seconds_allowed")
        != EXPECTED_EVIDENCE_REQUIREMENTS["max_valid_mesh_wall_seconds"]
        or not isinstance(watchdog.get("missing_wall_time_evidence"), list)
    ):
        raise QualificationError("qualification watchdog limits changed")
    watchdog_passed = (
        watchdog.get("passed") is True
        and not (watchdog.get("missing_wall_time_evidence") or [])
        and _finite(watchdog.get("max_valid_solve_wall_seconds_observed"))
        and _finite(watchdog.get("max_valid_solve_wall_seconds_allowed"))
        and float(watchdog["max_valid_solve_wall_seconds_observed"])
        <= float(watchdog["max_valid_solve_wall_seconds_allowed"])
        and _finite(watchdog.get("max_valid_mesh_wall_seconds_observed"))
        and _finite(watchdog.get("max_valid_mesh_wall_seconds_allowed"))
        and float(watchdog["max_valid_mesh_wall_seconds_observed"])
        <= float(watchdog["max_valid_mesh_wall_seconds_allowed"])
    )
    if decision.get("watchdog_margin_passed") is not watchdog_passed:
        raise QualificationError("qualification watchdog decision is internally inconsistent")
    passed = (
        rules_passed
        and record.get("artifact_files_cryptographically_verified") is True
        and watchdog_passed
    )
    if decision.get("all_gate_controls_passed") is not passed:
        raise QualificationError("qualification campaign decision is internally inconsistent")
    if record.get("qualification_status") != ("passed" if passed else "failed"):
        raise QualificationError("qualification status is internally inconsistent")
    promotion = record.get("promotion") or {}
    _exact_keys(promotion, {
        "eligible_for_separate_manual_freeze_review", "automatic_promotion_allowed",
        "promotion_performed", "official_authorization_granted", "release_lock_requirement",
    }, "qualification promotion decision")
    if (
        record.get("official") is not False
        or record.get("official_results_authorized") is not False
        or record.get("execution_authorized_by_this_record") is not False
        or promotion.get("automatic_promotion_allowed") is not False
        or promotion.get("promotion_performed") is not False
        or promotion.get("official_authorization_granted") is not False
        or promotion.get("eligible_for_separate_manual_freeze_review") is not passed
        or promotion.get("release_lock_requirement") != (
            "A separately reviewed release lock must bind this envelope's "
            "qualification_record_sha256 before any official run."
        )
    ):
        raise QualificationError("qualification record must never authorize or perform promotion")
    if verify_evidence_files:
        if evidence_bundle_root is None:
            raise QualificationError(
                "full qualification verification requires the evidence-bundle root"
            )
        _verify_envelope_evidence_files(envelope, Path(evidence_bundle_root).resolve())


def render_envelope(envelope: Mapping[str, Any]) -> str:
    return json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_immutable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable qualification record: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.link(temporary, path)
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def materialize_evidence_bundle(
    *,
    bundle_root: Path,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_canonical_sha256: str,
    manifest_file_sha256: str,
    protocol: GradingProtocol,
    campaign_root: Path,
) -> dict[str, Any]:
    """Copy one verified publication export into an immutable evidence bundle."""
    supplied_bundle_root = Path(bundle_root)
    if supplied_bundle_root.exists() or supplied_bundle_root.is_symlink():
        raise FileExistsError(f"refusing to overwrite evidence bundle: {bundle_root}")
    bundle_root = supplied_bundle_root.resolve()
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = bundle_root.with_name(f".{bundle_root.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir()
    try:
        source_value = Path(campaign_root)
        source_artifact_store = _campaign_artifact_store(
            source_value, exact_root=True
        )
        source_root = source_value.resolve()
        source_publication_ledger, source_results = _load_publication_results(
            source_root,
            manifest,
            require_exact_root=False,
        )
        if (
            source_publication_ledger.get("protocol_sha256") != protocol.sha256
            or source_publication_ledger.get("manifest_sha256")
            != manifest_canonical_sha256
        ):
            raise QualificationError(
                "qualification publication export is bound to a different protocol or manifest"
            )
        source_result_values = [source_results[value][0] for value in CONTROL_IDS]
        _validate_artifact_store_inventory(
            source_artifact_store, source_result_values
        )

        controls_by_id = {
            str(control["control_id"]): control for control in manifest.get("controls") or []
        }
        results_directory = temporary / RESULTS_DIRECTORY
        artifact_store = temporary / ARTIFACT_STORE
        publication_directory = temporary / PUBLICATION_LEDGER_DIRECTORY
        results_directory.mkdir()
        artifact_store.mkdir(parents=True)
        publication_directory.mkdir()
        for source in sorted(
            (source_root / PUBLICATION_LEDGER_DIRECTORY).iterdir(),
            key=lambda path: path.name,
        ):
            destination = publication_directory / source.name
            with destination.open("xb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        inventory_source = source_root / PUBLICATION_INVENTORY_FILENAME
        with (temporary / PUBLICATION_INVENTORY_FILENAME).open("xb") as stream:
            stream.write(inventory_source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())

        for control_id in CONTROL_IDS:
            result, raw = source_results[control_id]
            control = controls_by_id.get(control_id)
            if control is None:
                raise QualificationError(f"manifest lacks qualification control {control_id}")
            _validate_result_identity(
                result=result,
                control=control,
                manifest=manifest,
                manifest_sha256=manifest_canonical_sha256,
                protocol=protocol,
                result_validator=validate_result,
                verify_artifacts=True,
                artifact_store=source_artifact_store,
            )
            bundled_path = results_directory / f"{control_id}.json"
            with bundled_path.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            for record in _iter_artifact_records(result):
                if record.get("present") is not True:
                    continue
                try:
                    artifact_bytes = _verified_artifact_bytes(
                        record, source_artifact_store
                    )
                except ProtocolError as exc:
                    raise QualificationError(
                        f"{control_id} artifact failed while bundling: {exc}"
                    ) from exc
                if artifact_bytes is None:
                    raise QualificationError(
                        f"{control_id} present artifact produced no bytes"
                    )
                destination = artifact_store / str(record["sha256"])
                if destination.exists():
                    if destination.read_bytes() != artifact_bytes:
                        raise QualificationError(
                            "content-addressed artifact collision while bundling"
                        )
                    continue
                with destination.open("xb") as stream:
                    stream.write(artifact_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())

        envelope = build_qualification_envelope(
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_canonical_sha256=manifest_canonical_sha256,
            manifest_file_sha256=manifest_file_sha256,
            protocol=protocol,
            evidence_bundle_root=temporary,
            result_validator=validate_result,
            verify_artifacts=True,
        )
        if canonical_json_bytes(
            envelope["qualification_record"]["publication_ledger"]
        ) != canonical_json_bytes(source_publication_ledger):
            raise QualificationError(
                "qualification publication export changed while its bundle was materialized"
            )
        _write_immutable(
            temporary / QUALIFICATION_RECORD_FILENAME, render_envelope(envelope)
        )
        validate_qualification_envelope(
            envelope,
            verify_evidence_files=True,
            evidence_bundle_root=temporary,
        )
        os.rename(temporary, bundle_root)
        return envelope
    except Exception:
        if temporary.exists():
            shutil.rmtree(temporary)
        raise


def load_evidence_bundle(bundle_root: Path) -> dict[str, Any]:
    supplied_root = Path(bundle_root)
    if supplied_root.is_symlink():
        raise QualificationError(
            "qualification evidence-bundle root cannot be a symlink"
        )
    bundle_root = supplied_root.resolve()
    envelope = _strict_json_object(bundle_root / QUALIFICATION_RECORD_FILENAME)
    validate_qualification_envelope(
        envelope,
        verify_evidence_files=True,
        evidence_bundle_root=bundle_root,
    )
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST_PATH)
    parser.add_argument(
        "--campaign-dir",
        type=Path,
        help="complete exported WORM publication ledger plus artifacts/sha256",
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        required=True,
        help="immutable relocatable evidence-bundle directory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fully replay and verify an existing evidence bundle",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            if args.campaign_dir is not None:
                raise QualificationError(
                    "--check reads the bundle and does not accept a source campaign"
                )
            envelope = load_evidence_bundle(args.bundle_dir)
        else:
            if args.campaign_dir is None:
                raise QualificationError(
                    "bundle creation requires one complete --campaign-dir export"
                )
            manifest, manifest_sha, manifest_file_sha, protocol = load_qualification_context(
                args.manifest
            )
            envelope = materialize_evidence_bundle(
                bundle_root=args.bundle_dir,
                manifest=manifest,
                manifest_path=args.manifest,
                manifest_canonical_sha256=manifest_sha,
                manifest_file_sha256=manifest_file_sha,
                protocol=protocol,
                campaign_root=args.campaign_dir,
            )
    except (QualificationError, FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    status = envelope["qualification_record"]["qualification_status"]
    digest = envelope["qualification_record_sha256"]
    print(f"Qualification {status}: {digest}")
    return 0 if status == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
