#!/usr/bin/env python3
"""Build the self-contained, non-official v5 qualification manifest.

The manifest fixes the seven preregistered controls to exact binary64
geometries and fingerprints every geometry source and historical-evidence
artifact.  It never runs CFD.  ``execution_authorized`` remains false because
materializing a campaign definition is not authorization to spend compute.
"""
from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.dataset_geometry import DatasetGeometryResolver, ID_RESOLUTION
from scripts.cfd.campaign_publication import (
    MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
    MINIMUM_RETENTION_DAYS,
    PUBLICATION_CONTRACT,
    PUBLICATION_CONTRACT_SCHEMA_VERSION,
    PUBLICATION_CONTRACT_SHA256,
)
from scripts.cfd.geometry_hash import GEOMETRY_HASH_ALGORITHM, geometry_sha256
from scripts.cfd.grading_protocol import (
    protocol_sha256,
    sha256_file,
    validate_protocol,
)


SCHEMA_VERSION = "cfd-protocol-qualification-manifest-v1"
MANIFEST_ID = "superwing-fixed-cl-v5-protocol-qualification-v1"
DEFAULT_OUTPUT = REPO_ROOT / "configs" / "cfd" / "protocol_qualification_manifest_v1.json"
PROTOCOL_PATH = "configs/cfd/final_protocol_candidate_v2.json"
GENERATOR_PATH = "scripts/cfd/build_protocol_qualification_manifest.py"
QUALIFICATION_EVALUATOR_PATH = "scripts/cfd/evaluate_protocol_qualification.py"
DATASET_CONFIGS_PATH = "data/superwing/configs.dat"
DATASET_RUNNING_INFO_PATH = "data/superwing/running_info.jsonl"
DATASET_RESOLVER_PATH = "scripts/cfd/dataset_geometry.py"
PUBLICATION_STORAGE_BINDING_PATH = "configs/cfd/publication_storage_binding_v1.json"
PUBLICATION_STORAGE_BINDING_SCHEMA_VERSION = (
    "wingbench-cfd-publication-storage-binding-v1"
)
BENCHMARK_PUBLICATION_ID = "superwing-benchmark-redevelopment-v1"
PENDING_PUBLICATION_STATUS = "pending_publication_bucket_pinning"
READY_PUBLICATION_STATUS = "ready"

CONTROL_IDS = (
    "l5_originals__run11",
    "fullcap__fullcap_run1",
    "active_learning__r0_iter5",
    "dataset_1216",
    "pool_ordinal_0057",
    "active_learning__r1_cand_06",
    "l5_originals__run1",
)
REQUIRED_THREE_OF_THREE = frozenset(CONTROL_IDS[:4])
CAMPAIGN_GATE_IDS = (
    "l5_originals__run11",
    "fullcap__fullcap_run1",
    "active_learning__r0_iter5",
    "dataset_1216",
    "active_learning__r1_cand_06",
    "l5_originals__run1",
)


@dataclass(frozen=True)
class BenchmarkControlSpec:
    control_id: str
    source_path: str
    evidence_path: str
    expected_role: str


BENCHMARK_SPECS = {
    "l5_originals__run11": BenchmarkControlSpec(
        control_id="l5_originals__run11",
        source_path="data/winners/run11_winner.json",
        evidence_path="data/cfd/regrade_faithful/results/run11.json",
        expected_role="low_trim_prior_3_of_3_valid_anchor",
    ),
    "fullcap__fullcap_run1": BenchmarkControlSpec(
        control_id="fullcap__fullcap_run1",
        source_path="data/winners/fullcap_run1_winner.json",
        evidence_path="data/cfd/ground_truth_fullcap/fullcap_run1.json",
        expected_role="mid_trim_prior_3_of_3_valid_anchor",
    ),
    "active_learning__r0_iter5": BenchmarkControlSpec(
        control_id="active_learning__r0_iter5",
        source_path="data/cfd/r0_grade_candidates/r0_iter5_winner.json",
        evidence_path="data/cfd/regrade_faithful/results/r0_iter5.json",
        expected_role="high_trim_prior_3_of_3_valid_anchor",
    ),
    "active_learning__r1_cand_06": BenchmarkControlSpec(
        control_id="active_learning__r1_cand_06",
        source_path="data/cfd/r1_candidates/cand_06_winner.json",
        evidence_path="data/cfd/regrade_faithful/results/r1_cand_06.json",
        expected_role="branch_runaway_sensitivity",
    ),
    "l5_originals__run1": BenchmarkControlSpec(
        control_id="l5_originals__run1",
        source_path="data/winners/run1_winner.json",
        evidence_path="data/cfd/regrade_faithful/results/run1.json",
        expected_role="conclusively_unmeshable_negative",
    ),
}

SEED_EXPERIMENT_EVIDENCE_ROOT = (
    "data/legacy/cfd/qualification-controls/seed-experiments-2026-07-13"
)
DATASET_1216_EVIDENCE = (
    f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/runs/exp3_running_m785_s65/results/"
    "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126_screen.json",
    f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/runs/exp4_running_m820_s75/results/"
    "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126_screen.json",
    f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/runs/exp1b_running_m853_s75_t7200/results/"
    "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126_screen.json",
)
DATASET_1216_SEED_ARTIFACTS = (
    (
        f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/cfd_cases_exp3_running_m785_s65/"
        "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126/0/conds.json",
        6.5,
    ),
    (
        f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/cfd_cases_exp4_running_m820_s75/"
        "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126/1/conds.json",
        7.5,
    ),
    (
        f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/cfd_cases_exp1b_running_m853_s75_t7200/"
        "mfcei_xdata_seed0_stage0_0164_rootadj_1p0126/2/conds.json",
        7.5,
    ),
)
POOL_0057_PARTIAL_ARTIFACTS = (
    f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/cfd_cases_exp5_pool0057_m853_s65/"
    "pool_0057_running/2/conds.json",
    f"{SEED_EXPERIMENT_EVIDENCE_ROOT}/cfd_cases_exp5_pool0057_m853_s65/"
    "pool_0057_running/2/adflow.log",
)


def _load_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON object {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"expected a JSON object in {path}")
    return payload


def _load_publication_storage_binding(repo_root: Path) -> dict[str, Any]:
    binding = _load_json_object(repo_root / PUBLICATION_STORAGE_BINDING_PATH)
    expected_keys = {
        "schema_version",
        "status",
        "storage_provider",
        "bucket",
        "qualification_campaign_publication_id",
        "benchmark_campaign_publication_id",
        "s3_object_lock_mode",
        "minimum_retention_days",
    }
    if set(binding) != expected_keys:
        raise ValueError("publication storage binding structure changed")
    if (
        binding.get("schema_version") != PUBLICATION_STORAGE_BINDING_SCHEMA_VERSION
        or binding.get("storage_provider") != "aws_s3"
        or binding.get("qualification_campaign_publication_id") != MANIFEST_ID
        or binding.get("benchmark_campaign_publication_id") != BENCHMARK_PUBLICATION_ID
        or binding.get("s3_object_lock_mode")
        != PUBLICATION_CONTRACT["s3_object_lock_mode"]
        or binding.get("minimum_retention_days") != MINIMUM_RETENTION_DAYS
    ):
        raise ValueError("publication storage binding contract changed")
    status = binding.get("status")
    bucket = binding.get("bucket")
    if status == "unpinned":
        if bucket is not None:
            raise ValueError("unpinned publication storage binding requires a null bucket")
    elif status == "pinned":
        if not isinstance(bucket, str) or not bucket.strip():
            raise ValueError("pinned publication storage binding requires a non-empty bucket")
    else:
        raise ValueError("publication storage binding status must be pinned or unpinned")
    return binding


def _publication_contract(
    ordered_control_ids: list[str],
    binding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "campaign_publication_id": binding[
            "qualification_campaign_publication_id"
        ],
        "publication_bucket": binding["bucket"],
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "ordered_wing_ids_sha256": protocol_sha256(ordered_control_ids),
        "recovery_allowed": False,
    }


def _execution_readiness(binding: dict[str, Any]) -> dict[str, Any]:
    ready = binding["status"] == "pinned"
    return {
        "ready": ready,
        "status": READY_PUBLICATION_STATUS if ready else PENDING_PUBLICATION_STATUS,
    }


def _validate_publication_metadata(
    manifest: dict[str, Any],
    repo_root: Path,
    *,
    verify_files: bool,
) -> list[str]:
    errors: list[str] = []
    contract = manifest.get("publication_contract")
    expected_contract_keys = {
        "schema_version",
        "campaign_publication_id",
        "publication_bucket",
        "publication_contract_schema_version",
        "publication_contract_sha256",
        "ordered_wing_ids_sha256",
        "recovery_allowed",
    }
    if not isinstance(contract, dict) or set(contract) != expected_contract_keys:
        return ["publication contract structure changed"]
    bucket = contract.get("publication_bucket")
    if bucket is not None and (not isinstance(bucket, str) or not bucket.strip()):
        errors.append("publication bucket must be null or a non-empty string")
    expected_contract = {
        "schema_version": MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "campaign_publication_id": MANIFEST_ID,
        "publication_bucket": bucket,
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "ordered_wing_ids_sha256": protocol_sha256(list(CONTROL_IDS)),
        "recovery_allowed": False,
    }
    if contract != expected_contract:
        errors.append("qualification publication contract changed")
    expected_readiness = {
        "ready": bucket is not None,
        "status": (
            READY_PUBLICATION_STATUS
            if bucket is not None
            else PENDING_PUBLICATION_STATUS
        ),
    }
    if manifest.get("execution_readiness") != expected_readiness:
        errors.append("qualification execution readiness disagrees with publication pinning")

    if verify_files:
        try:
            binding = _load_publication_storage_binding(repo_root)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot verify publication storage binding: {exc}")
        else:
            if contract != _publication_contract(list(CONTROL_IDS), binding):
                errors.append("publication contract does not match the storage binding")
            if manifest.get("execution_readiness") != _execution_readiness(binding):
                errors.append("execution readiness does not match the storage binding")
    return errors


def _canonical_source(repo_root: Path, relative_path: str) -> tuple[list[float], dict[str, Any]]:
    path = repo_root / relative_path
    payload = _load_json_object(path)
    if "geom_38d" not in payload:
        raise ValueError(f"canonical geometry source has no geom_38d: {relative_path}")
    geometry = [float(value) for value in payload["geom_38d"]]
    # geometry_sha256 performs the dimension, numeric-type, and finiteness checks.
    geometry_sha256(geometry)
    source = {
        "source_type": "benchmark_geometry_json",
        "path": relative_path,
        "json_pointer": "/geom_38d",
        "file_sha256": sha256_file(path),
        "geometry_convention": "configs-generated-v1",
    }
    return geometry, source


def _legacy_evidence(repo_root: Path, relative_path: str) -> dict[str, Any]:
    path = repo_root / relative_path
    payload = _load_json_object(path)
    rows = payload.get("per_condition")
    if not isinstance(rows, list):
        raise ValueError(f"legacy evidence has no per_condition list: {relative_path}")
    realized_aoa = [float(row["AoA"]) for row in rows if isinstance(row, dict) and "AoA" in row]
    return {
        "path": relative_path,
        "file_sha256": sha256_file(path),
        "evidence_type": "legacy_whole_wing_fixed_cl",
        "legacy_only": True,
        "protocol_provenance_complete": False,
        "legacy_status": payload.get("status"),
        "n_conditions": payload.get("n_conditions"),
        "n_valid": payload.get("n_valid"),
        "mesh_min_volume": payload.get("mesh_min_vol"),
        "mesh_min_quality": payload.get("mesh_min_quality"),
        "realized_aoa_deg": realized_aoa,
    }


def _all_three_valid_acceptance() -> dict[str, Any]:
    return {
        "rule_id": "all_three_conditions_valid",
        "campaign_gate": True,
        "required_condition_results": 3,
        "required_valid_conditions": 3,
        "required_aggregate_admissibility": "pass",
        "invalid_conditions_allowed": 0,
        "inconclusive_conditions_allowed": 0,
        "timeouts_allowed": 0,
    }


def _benchmark_control(
    repo_root: Path,
    spec: BenchmarkControlSpec,
    aoa_bounds: list[float],
) -> dict[str, Any]:
    geometry, source = _canonical_source(repo_root, spec.source_path)
    evidence = _legacy_evidence(repo_root, spec.evidence_path)
    if spec.control_id in REQUIRED_THREE_OF_THREE:
        if (
            evidence["legacy_status"] != "ok"
            or evidence["n_conditions"] != 3
            or evidence["n_valid"] != 3
            or len(evidence["realized_aoa_deg"]) != 3
        ):
            raise ValueError(f"{spec.control_id} is no longer a prior 3/3-valid anchor")
        acceptance = _all_three_valid_acceptance()
    elif spec.control_id == "active_learning__r1_cand_06":
        out_of_range = [
            value
            for value in evidence["realized_aoa_deg"]
            if value < aoa_bounds[0] or value > aoa_bounds[1]
        ]
        if not out_of_range:
            raise ValueError("r1_cand_06 legacy evidence no longer contains an AoA runaway")
        acceptance = {
            "rule_id": "out_of_range_aoa_must_not_be_accepted",
            "campaign_gate": True,
            "required_condition_results": 3,
            "required_profile_status": "complete",
            "inconclusive_conditions_allowed": 0,
            "timeouts_allowed": 0,
            "aoa_deg_inclusive": list(aoa_bounds),
            "out_of_range_observation_required": True,
            "out_of_range_condition_allowed_outcomes": ["invalid"],
            "aggregate_admissibility_if_any_out_of_range": "fail",
            "violation": (
                "Any condition classified valid, or aggregate classified pass, when its "
                "realized AoA is outside the inclusive protocol bounds."
            ),
        }
    elif spec.control_id == "l5_originals__run1":
        metrics = (evidence["mesh_min_volume"], evidence["mesh_min_quality"])
        if evidence["legacy_status"] != "unmeshable" or not any(
            isinstance(value, (int, float)) and value <= 0 for value in metrics
        ):
            raise ValueError("run1 legacy evidence no longer proves nonpositive mesh invalidity")
        acceptance = {
            "rule_id": "reproduce_nonpositive_mesh_invalidity",
            "campaign_gate": True,
            "required_mesh_outcome": "unmeshable",
            "required_aggregate_admissibility": "fail",
            "timeouts_allowed": 0,
            "nonpositive_mesh_metric_any_of": ["min_volume", "min_quality"],
            "nonpositive_operator": "<= 0",
            "nonpositive_threshold": 0.0,
            "condition_solves_allowed_after_mesh_failure": False,
        }
    else:  # pragma: no cover - guarded by the fixed spec table
        raise AssertionError(f"unhandled benchmark control: {spec.control_id}")

    control = {
        "control_id": spec.control_id,
        "control_type": "benchmark_wing",
        "benchmark_wing_id": spec.control_id,
        "expected_role": spec.expected_role,
        "geometry_sha256": geometry_sha256(geometry),
        "geom_38d": geometry,
        "canonical_source": source,
        "historical_evidence": evidence,
        "acceptance": acceptance,
    }
    return _add_provenance_hash(control)


def _dataset_resolution_source(
    repo_root: Path,
    resolver: DatasetGeometryResolver,
    dataset_index: int,
    expected_source_id: int,
) -> tuple[list[float], dict[str, Any]]:
    resolution = resolver.resolve(dataset_index)
    if resolution.source_design_id != expected_source_id or resolution.running_id != expected_source_id:
        raise ValueError(
            f"dataset_index {dataset_index} resolved to source/running "
            f"{resolution.source_design_id}/{resolution.running_id}, expected {expected_source_id}"
        )
    provenance = resolution.provenance
    source = {
        "source_type": "physical_dataset_running_info_shape",
        "dataset_index": dataset_index,
        "source_design_id": resolution.source_design_id,
        "running_id": resolution.running_id,
        "id_resolution": ID_RESOLUTION,
        "running_info_line_number": provenance["running_info_line_number"],
        "geometry_field": "shape",
        "rootadj_index": provenance["rootadj_index"],
        "configs_rootadj": provenance["configs_rootadj"],
        "physical_running_info_rootadj": provenance["running_info_rootadj"],
        "non_rootadj_exact_match": provenance["non_rootadj_exact_match"],
        "precision": provenance["precision"],
        "resolver": {
            "path": DATASET_RESOLVER_PATH,
            "file_sha256": sha256_file(repo_root / DATASET_RESOLVER_PATH),
        },
        "source_files": {
            "configs_dat": {
                "path": DATASET_CONFIGS_PATH,
                "file_sha256": sha256_file(repo_root / DATASET_CONFIGS_PATH),
            },
            "running_info_jsonl": {
                "path": DATASET_RUNNING_INFO_PATH,
                "file_sha256": sha256_file(repo_root / DATASET_RUNNING_INFO_PATH),
            },
        },
    }
    return list(resolution.geometry_38d), source


def _dataset_1216_historical_evidence(repo_root: Path) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    if len(DATASET_1216_EVIDENCE) != len(DATASET_1216_SEED_ARTIFACTS):
        raise ValueError("dataset 1216 evidence/seed artifact roster mismatch")
    for relative_path, (seed_path, expected_seed) in zip(
        DATASET_1216_EVIDENCE, DATASET_1216_SEED_ARTIFACTS
    ):
        path = repo_root / relative_path
        payload = _load_json_object(path)
        rows = payload.get("per_condition")
        if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
            raise ValueError(f"expected one condition in seed evidence: {relative_path}")
        row = rows[0]
        verdict = row.get("verdict")
        if not isinstance(verdict, dict) or verdict.get("label") != "converged":
            raise ValueError(f"seed evidence is no longer converged: {relative_path}")
        output = row.get("output")
        if not isinstance(output, dict):
            raise ValueError(f"seed evidence has no output: {relative_path}")
        seed_payload = _load_json_object(repo_root / seed_path)
        if (
            float(seed_payload.get("AA")) != expected_seed
            or float(seed_payload.get("M3")) != float(row.get("M"))
            or float(seed_payload.get("CL_targ")) != float(row.get("CL_targ"))
        ):
            raise ValueError(f"seed identity artifact changed: {seed_path}")
        records.append({
            "path": relative_path,
            "file_sha256": sha256_file(path),
            "condition_index": row.get("cond_idx"),
            "mach": row.get("M"),
            "cl_target": row.get("CL_targ"),
            "legacy_label": verdict.get("label"),
            "realized_aoa_deg": output.get("AA"),
            "diagnostic_seed_alpha0_deg": expected_seed,
            "seed_identity_artifact": {
                "path": seed_path,
                "file_sha256": sha256_file(repo_root / seed_path),
                "seed_json_pointer": "/AA",
            },
        })
    records.sort(key=lambda row: row["condition_index"])
    if [row["condition_index"] for row in records] != [0, 1, 2]:
        raise ValueError("dataset 1216 evidence does not cover condition indices 0, 1, 2")
    return {
        "evidence_type": "nonofficial_seed_diagnostic_single_condition_runs",
        "legacy_only": True,
        "protocol_provenance_complete": False,
        "qualification_result": False,
        "historical_summary": {"n_conditions": 3, "n_valid": 3},
        "records": records,
        "note": (
            "These diagnostics establish the seed false-negative role but used historical "
            "single-condition runs; only a fresh run under the referenced protocol can qualify."
        ),
    }


def _seed_contract(repo_root: Path, protocol_payload: dict[str, Any]) -> dict[str, Any]:
    conditions = protocol_payload["conditions"]
    seed_policy = protocol_payload["seed_policy"]
    selection_record_path = seed_policy["selection_record_path"]
    selection_record = json.loads((repo_root / selection_record_path).read_text())
    alternate_diagnostics = []
    for condition_index in (1, 2):
        artifact_path, alpha0_deg = DATASET_1216_SEED_ARTIFACTS[condition_index]
        alternate_diagnostics.append({
            "control_id": "dataset_1216",
            "condition_index": condition_index,
            "condition_id": conditions[condition_index]["condition_id"],
            "mach": float(conditions[condition_index]["mach"]),
            "alpha0_deg": alpha0_deg,
            "qualification_gate": False,
            "historical_only": True,
            "seed_identity_artifact": {
                "path": artifact_path,
                "file_sha256": sha256_file(repo_root / artifact_path),
                "seed_json_pointer": "/AA",
            },
            "purpose": (
                "Pin the known historical alternate-seed diagnostic; it cannot "
                "replace a candidate result."
            ),
        })
    return {
        "candidate_policy": "fixed_per_condition",
        "candidate_alpha0_deg": float(seed_policy["alpha0_deg"]),
        "candidate_source": seed_policy["source"],
        "candidate_selection_rule": seed_policy["selection_rule"],
        "candidate_selection_record": {
            "path": selection_record_path,
            "file_sha256": sha256_file(repo_root / selection_record_path),
            "schema_version": selection_record["schema_version"],
            "canonical_seed_alpha0_deg": selection_record["canonical_seed_alpha0_deg"],
        },
        "candidate_applies_to_every_wing_and_condition": seed_policy[
            "applies_to_every_wing_and_condition"
        ],
        "candidate_future_reselection_allowed": seed_policy[
            "future_reselection_allowed"
        ],
        "candidate_condition_ids": [condition["condition_id"] for condition in conditions],
        "candidate_seed_required_for_every_executed_attempt": True,
        "alternate_seed_diagnostics": alternate_diagnostics,
        "alternate_diagnostics_are_qualification_results": False,
    }


def _acceptance_tolerances() -> dict[str, Any]:
    return {
        "seed_identity_abs_tolerance_deg": 1e-12,
        "condition_identity_abs_tolerance": 1e-12,
        "aoa_boundary_padding_deg": 0.0,
        "mesh_nonpositive_threshold": 0.0,
        "count_comparison": "exact_integer_equality",
        "timeout_count_unit": "process_attempt",
    }


def _campaign_evidence_requirements() -> dict[str, Any]:
    return {
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


def _dataset_1216_control(
    repo_root: Path,
    resolver: DatasetGeometryResolver,
) -> dict[str, Any]:
    geometry, source = _dataset_resolution_source(repo_root, resolver, 1208, 1216)
    control = {
        "control_id": "dataset_1216",
        "control_type": "physical_dataset_wing",
        "expected_role": "seed_false_negative_required_valid_anchor",
        "geometry_sha256": geometry_sha256(geometry),
        "geom_38d": geometry,
        "canonical_source": source,
        "historical_evidence": _dataset_1216_historical_evidence(repo_root),
        "acceptance": _all_three_valid_acceptance(),
    }
    return _add_provenance_hash(control)


def _pool_0057_historical_evidence(repo_root: Path) -> dict[str, Any]:
    artifacts = []
    for relative_path in POOL_0057_PARTIAL_ARTIFACTS:
        path = repo_root / relative_path
        if not path.is_file():
            raise FileNotFoundError(f"missing pool 0057 partial artifact: {relative_path}")
        artifacts.append({"path": relative_path, "file_sha256": sha256_file(path)})
    return {
        "evidence_type": "stopped_seed_generalization_diagnostic",
        "legacy_only": True,
        "protocol_provenance_complete": False,
        "qualification_result": False,
        "completion_state": "stopped_before_result",
        "artifacts": artifacts,
    }


def _pool_0057_control(
    repo_root: Path,
    resolver: DatasetGeometryResolver,
) -> dict[str, Any]:
    geometry, source = _dataset_resolution_source(repo_root, resolver, 3874, 5339)
    source["pool_ordinal"] = "0057"
    control = {
        "control_id": "pool_ordinal_0057",
        "control_type": "physical_dataset_wing",
        "expected_role": "unfinished_seed_generalization_exploratory",
        "geometry_sha256": geometry_sha256(geometry),
        "geom_38d": geometry,
        "canonical_source": source,
        "historical_evidence": _pool_0057_historical_evidence(repo_root),
        "acceptance": {
            "rule_id": "exploratory_record_only",
            "campaign_gate": False,
            "required_condition_results": 3,
            "accepted_observation_classes": ["pass", "fail", "undetermined"],
            "qualification_effect": "none",
        },
    }
    return _add_provenance_hash(control)


def _provenance_material(control: dict[str, Any]) -> dict[str, Any]:
    return {
        "control_id": control.get("control_id"),
        "geometry_sha256": control.get("geometry_sha256"),
        "canonical_source": control.get("canonical_source"),
        "historical_evidence": control.get("historical_evidence"),
    }


def _add_provenance_hash(control: dict[str, Any]) -> dict[str, Any]:
    control["provenance_sha256"] = protocol_sha256(_provenance_material(control))
    control["provenance_hash_semantics"] = "sha256(canonical compact sorted-key JSON)"
    return control


def _assert_trim_order(controls: list[dict[str, Any]]) -> None:
    by_id = {control["control_id"]: control for control in controls}
    anchors = [
        by_id["l5_originals__run11"],
        by_id["fullcap__fullcap_run1"],
        by_id["active_learning__r0_iter5"],
    ]
    aoas = [control["historical_evidence"]["realized_aoa_deg"] for control in anchors]
    if not (max(aoas[0]) < min(aoas[1]) and max(aoas[1]) < min(aoas[2])):
        raise ValueError("the selected benchmark anchors no longer span low, mid, and high trim")


def build_manifest(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    protocol_path = repo_root / PROTOCOL_PATH
    protocol_payload = _load_json_object(protocol_path)
    publication_binding = _load_publication_storage_binding(repo_root)
    validate_protocol(protocol_payload, verify_files=True)
    if (
        protocol_payload.get("protocol_status") != "qualification_required"
        or protocol_payload.get("official_grading_enabled") is not False
    ):
        raise ValueError("qualification must reference a non-official qualification-required protocol")
    aoa_bounds = [float(value) for value in protocol_payload["condition_gates"]["aoa_deg_inclusive"]]
    protocol_hash = protocol_sha256(protocol_payload)

    resolver = DatasetGeometryResolver.from_files(
        repo_root / DATASET_CONFIGS_PATH,
        repo_root / DATASET_RUNNING_INFO_PATH,
    )
    controls = [
        _benchmark_control(repo_root, BENCHMARK_SPECS["l5_originals__run11"], aoa_bounds),
        _benchmark_control(repo_root, BENCHMARK_SPECS["fullcap__fullcap_run1"], aoa_bounds),
        _benchmark_control(repo_root, BENCHMARK_SPECS["active_learning__r0_iter5"], aoa_bounds),
        _dataset_1216_control(repo_root, resolver),
        _pool_0057_control(repo_root, resolver),
        _benchmark_control(repo_root, BENCHMARK_SPECS["active_learning__r1_cand_06"], aoa_bounds),
        _benchmark_control(repo_root, BENCHMARK_SPECS["l5_originals__run1"], aoa_bounds),
    ]
    _assert_trim_order(controls)

    return {
        "schema_version": SCHEMA_VERSION,
        "manifest_id": MANIFEST_ID,
        "manifest_status": "qualification_required",
        "official": False,
        "execution_authorized": False,
        "official_results_authorized": False,
        "execution_readiness": _execution_readiness(publication_binding),
        "publication_contract": _publication_contract(
            list(CONTROL_IDS), publication_binding
        ),
        "purpose": (
            "Preregister the exact seven-control campaign that must pass before the v5 "
            "grading protocol can be frozen or used to redevelop the benchmark."
        ),
        "generator": {
            "path": GENERATOR_PATH,
            "file_sha256": sha256_file(repo_root / GENERATOR_PATH),
        },
        "qualification_evaluator": {
            "path": QUALIFICATION_EVALUATOR_PATH,
            "file_sha256": sha256_file(repo_root / QUALIFICATION_EVALUATOR_PATH),
            "record_schema_version": "wingbench-cfd-protocol-qualification-record-v1",
            "automatic_promotion": False,
        },
        "protocol_reference": {
            "path": PROTOCOL_PATH,
            "protocol_id": protocol_payload["protocol_id"],
            "protocol_status": protocol_payload["protocol_status"],
            "official_grading_enabled": protocol_payload["official_grading_enabled"],
            "sha256": protocol_hash,
            "hash_semantics": "sha256(canonical compact sorted-key JSON)",
            "file_sha256": sha256_file(protocol_path),
        },
        "protocol_snapshot": copy.deepcopy(protocol_payload),
        "geometry_hash": {
            "algorithm": GEOMETRY_HASH_ALGORITHM,
            "schema": "superwing-geom38-f64le-v1",
        },
        "campaign_acceptance": {
            "exact_control_count": 7,
            "control_substitution_allowed": False,
            "gate_control_ids": list(CAMPAIGN_GATE_IDS),
            "exploratory_control_ids": ["pool_ordinal_0057"],
            "all_gate_controls_must_pass": True,
            "seed_contract": _seed_contract(repo_root, protocol_payload),
            "acceptance_tolerances": _acceptance_tolerances(),
            "campaign_evidence_requirements": _campaign_evidence_requirements(),
            "result_identity_requirements": {
                "protocol_sha256": protocol_hash,
                "geometry_sha256_must_match_control": True,
                "protocol_snapshot_must_match": True,
            },
            "promotion_rule": (
                "Only a reviewed, complete qualification record passing every gate may be "
                "used to create a separate frozen protocol; this manifest and its outputs "
                "remain non-official."
            ),
        },
        "controls": controls,
    }


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path = REPO_ROOT,
    *,
    verify_files: bool = True,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append("unexpected schema_version")
    if manifest.get("manifest_status") != "qualification_required":
        errors.append("manifest must remain qualification_required")
    if manifest.get("official") is not False or manifest.get("official_results_authorized") is not False:
        errors.append("manifest and results must remain non-official")
    if manifest.get("execution_authorized") is not False:
        errors.append("manifest must not authorize execution")
    errors.extend(
        _validate_publication_metadata(
            manifest, repo_root, verify_files=verify_files
        )
    )

    protocol_ref = manifest.get("protocol_reference")
    protocol_snapshot = manifest.get("protocol_snapshot")
    if not isinstance(protocol_ref, dict) or not isinstance(protocol_snapshot, dict):
        errors.append("protocol reference and snapshot are required")
        protocol_ref = {}
        protocol_snapshot = {}
    if protocol_ref.get("path") != PROTOCOL_PATH:
        errors.append("wrong protocol path")
    if protocol_ref.get("protocol_status") != "qualification_required":
        errors.append("referenced protocol must remain qualification_required")
    if protocol_ref.get("official_grading_enabled") is not False:
        errors.append("referenced protocol must remain non-official")
    try:
        snapshot_hash = protocol_sha256(protocol_snapshot)
    except (TypeError, ValueError) as exc:
        errors.append(f"invalid protocol snapshot: {exc}")
    else:
        if snapshot_hash != protocol_ref.get("sha256"):
            errors.append("protocol snapshot hash mismatch")

    if verify_files:
        protocol_path = repo_root / PROTOCOL_PATH
        try:
            current_protocol = _load_json_object(protocol_path)
            validate_protocol(current_protocol, verify_files=True)
            if current_protocol != protocol_snapshot:
                errors.append("protocol snapshot does not match current protocol")
            if protocol_sha256(current_protocol) != protocol_ref.get("sha256"):
                errors.append("current protocol canonical hash mismatch")
            if sha256_file(protocol_path) != protocol_ref.get("file_sha256"):
                errors.append("current protocol file hash mismatch")
        except (OSError, ValueError) as exc:
            errors.append(f"cannot verify current protocol: {exc}")
        generator = manifest.get("generator", {})
        generator_path = repo_root / str(generator.get("path", ""))
        if not generator_path.is_file() or sha256_file(generator_path) != generator.get("file_sha256"):
            errors.append("generator source hash mismatch")
        evaluator = manifest.get("qualification_evaluator", {})
        evaluator_path = repo_root / str(evaluator.get("path", ""))
        if (
            evaluator.get("path") != QUALIFICATION_EVALUATOR_PATH
            or not evaluator_path.is_file()
            or sha256_file(evaluator_path) != evaluator.get("file_sha256")
        ):
            errors.append("qualification evaluator source hash mismatch")

    evaluator = manifest.get("qualification_evaluator", {})
    if (
        evaluator.get("path") != QUALIFICATION_EVALUATOR_PATH
        or evaluator.get("record_schema_version")
        != "wingbench-cfd-protocol-qualification-record-v1"
        or evaluator.get("automatic_promotion") is not False
    ):
        errors.append("qualification evaluator contract changed")

    controls = manifest.get("controls")
    if not isinstance(controls, list):
        return errors + ["controls must be a list"]
    ids = [control.get("control_id") for control in controls if isinstance(control, dict)]
    if tuple(ids) != CONTROL_IDS:
        errors.append(f"controls must be the exact preregistered seven in order: {CONTROL_IDS}")
    if len(controls) != 7:
        errors.append(f"expected exactly 7 controls, got {len(controls)}")

    geometry_hashes: set[str] = set()
    for index, control in enumerate(controls):
        prefix = f"controls[{index}]"
        if not isinstance(control, dict):
            errors.append(f"{prefix}: control must be an object")
            continue
        try:
            observed_hash = geometry_sha256(control.get("geom_38d"))
        except (TypeError, ValueError) as exc:
            errors.append(f"{prefix}: invalid geometry: {exc}")
            continue
        if control.get("geometry_sha256") != observed_hash:
            errors.append(f"{prefix}: geometry hash mismatch")
        if observed_hash in geometry_hashes:
            errors.append(f"{prefix}: duplicate control geometry")
        geometry_hashes.add(observed_hash)
        expected_provenance = protocol_sha256(_provenance_material(control))
        if control.get("provenance_sha256") != expected_provenance:
            errors.append(f"{prefix}: provenance hash mismatch")

        source = control.get("canonical_source")
        if not isinstance(source, dict):
            errors.append(f"{prefix}: canonical_source must be an object")
            continue
        if verify_files:
            if control.get("control_type") == "benchmark_wing":
                source_path = repo_root / str(source.get("path", ""))
                try:
                    payload = _load_json_object(source_path)
                    if sha256_file(source_path) != source.get("file_sha256"):
                        errors.append(f"{prefix}: canonical source file hash mismatch")
                    if geometry_sha256(payload.get("geom_38d")) != observed_hash:
                        errors.append(f"{prefix}: canonical source geometry mismatch")
                except (OSError, TypeError, ValueError) as exc:
                    errors.append(f"{prefix}: cannot verify canonical source: {exc}")
            elif control.get("control_type") == "physical_dataset_wing":
                for row in source.get("source_files", {}).values():
                    path = repo_root / str(row.get("path", ""))
                    if not path.is_file() or sha256_file(path) != row.get("file_sha256"):
                        errors.append(f"{prefix}: dataset source file hash mismatch")
                resolver_row = source.get("resolver", {})
                resolver_path = repo_root / str(resolver_row.get("path", ""))
                if (
                    not resolver_path.is_file()
                    or sha256_file(resolver_path) != resolver_row.get("file_sha256")
                ):
                    errors.append(f"{prefix}: dataset resolver hash mismatch")

        evidence = control.get("historical_evidence")
        if not isinstance(evidence, dict) or evidence.get("legacy_only") is not True:
            errors.append(f"{prefix}: historical evidence must be explicitly legacy-only")
        elif verify_files:
            evidence_rows = []
            if "path" in evidence:
                evidence_rows.append(evidence)
            evidence_rows.extend(evidence.get("records", []))
            evidence_rows.extend(evidence.get("artifacts", []))
            for evidence_row in evidence_rows:
                path = repo_root / str(evidence_row.get("path", ""))
                if not path.is_file() or sha256_file(path) != evidence_row.get("file_sha256"):
                    errors.append(f"{prefix}: historical evidence file hash mismatch")
                seed_artifact = evidence_row.get("seed_identity_artifact")
                if isinstance(seed_artifact, dict):
                    seed_path = repo_root / str(seed_artifact.get("path", ""))
                    if (
                        not seed_path.is_file()
                        or sha256_file(seed_path) != seed_artifact.get("file_sha256")
                    ):
                        errors.append(f"{prefix}: seed identity artifact hash mismatch")

    by_id = {
        control.get("control_id"): control
        for control in controls
        if isinstance(control, dict) and isinstance(control.get("control_id"), str)
    }
    for control_id, spec in BENCHMARK_SPECS.items():
        control = by_id.get(control_id, {})
        source = control.get("canonical_source", {})
        evidence = control.get("historical_evidence", {})
        if control.get("expected_role") != spec.expected_role:
            errors.append(f"{control_id}: expected role changed")
        if source.get("path") != spec.source_path or source.get("json_pointer") != "/geom_38d":
            errors.append(f"{control_id}: canonical source identity changed")
        if evidence.get("path") != spec.evidence_path:
            errors.append(f"{control_id}: historical evidence identity changed")

    physical_expectations = {
        "dataset_1216": (1208, 1216, None, "seed_false_negative_required_valid_anchor"),
        "pool_ordinal_0057": (
            3874,
            5339,
            "0057",
            "unfinished_seed_generalization_exploratory",
        ),
    }
    for control_id, (dataset_index, source_id, pool_ordinal, role) in physical_expectations.items():
        control = by_id.get(control_id, {})
        source = control.get("canonical_source", {})
        if control.get("expected_role") != role:
            errors.append(f"{control_id}: expected role changed")
        if (
            source.get("dataset_index") != dataset_index
            or source.get("source_design_id") != source_id
            or source.get("running_id") != source_id
            or source.get("pool_ordinal") != pool_ordinal
        ):
            errors.append(f"{control_id}: physical dataset identity changed")
        if verify_files:
            try:
                resolver = DatasetGeometryResolver.from_files(
                    repo_root / DATASET_CONFIGS_PATH,
                    repo_root / DATASET_RUNNING_INFO_PATH,
                )
                resolution = resolver.resolve(dataset_index)
                if (
                    resolution.source_design_id != source_id
                    or resolution.running_id != source_id
                    or geometry_sha256(resolution.geometry_38d) != control.get("geometry_sha256")
                ):
                    errors.append(f"{control_id}: physical geometry resolution changed")
            except (OSError, TypeError, ValueError) as exc:
                errors.append(f"{control_id}: cannot verify physical geometry resolution: {exc}")

    observed_three_of_three = {
        control_id
        for control_id, control in by_id.items()
        if control.get("acceptance", {}).get("required_valid_conditions") == 3
    }
    if observed_three_of_three != REQUIRED_THREE_OF_THREE:
        errors.append("only the three legacy anchors and dataset 1216 may require 3/3 valid")
    for control_id in REQUIRED_THREE_OF_THREE:
        acceptance = by_id.get(control_id, {}).get("acceptance", {})
        if acceptance != _all_three_valid_acceptance():
            errors.append(f"{control_id}: 3/3-valid acceptance rule changed")
    for control_id in CONTROL_IDS[:3]:
        evidence = by_id.get(control_id, {}).get("historical_evidence", {})
        if (
            evidence.get("legacy_status") != "ok"
            or evidence.get("n_conditions") != 3
            or evidence.get("n_valid") != 3
        ):
            errors.append(f"{control_id}: prior 3/3-valid evidence changed")
    evidence_1216 = by_id.get("dataset_1216", {}).get("historical_evidence", {})
    if evidence_1216.get("historical_summary") != {"n_conditions": 3, "n_valid": 3}:
        errors.append("dataset 1216 seed false-negative evidence changed")
    exploratory = by_id.get("pool_ordinal_0057", {}).get("acceptance", {})
    if (
        exploratory.get("rule_id") != "exploratory_record_only"
        or exploratory.get("campaign_gate") is not False
    ):
        errors.append("pool ordinal 0057 must remain non-gating exploratory evidence")
    runaway = by_id.get("active_learning__r1_cand_06", {}).get("acceptance", {})
    expected_bounds = protocol_snapshot.get("condition_gates", {}).get("aoa_deg_inclusive")
    if (
        runaway.get("rule_id") != "out_of_range_aoa_must_not_be_accepted"
        or runaway.get("required_condition_results") != 3
        or runaway.get("required_profile_status") != "complete"
        or runaway.get("inconclusive_conditions_allowed") != 0
        or runaway.get("timeouts_allowed") != 0
        or runaway.get("aoa_deg_inclusive") != expected_bounds
        or runaway.get("out_of_range_observation_required") is not True
        or runaway.get("out_of_range_condition_allowed_outcomes") != ["invalid"]
        or runaway.get("aggregate_admissibility_if_any_out_of_range") != "fail"
    ):
        errors.append("r1_cand_06 must reject every out-of-range AoA")
    mesh_negative = by_id.get("l5_originals__run1", {}).get("acceptance", {})
    mesh_evidence = by_id.get("l5_originals__run1", {}).get("historical_evidence", {})
    if (
        mesh_negative.get("rule_id") != "reproduce_nonpositive_mesh_invalidity"
        or mesh_negative.get("required_mesh_outcome") != "unmeshable"
        or mesh_negative.get("required_aggregate_admissibility") != "fail"
        or mesh_negative.get("timeouts_allowed") != 0
        or mesh_negative.get("nonpositive_operator") != "<= 0"
        or mesh_negative.get("nonpositive_threshold") != 0.0
        or mesh_negative.get("condition_solves_allowed_after_mesh_failure") is not False
    ):
        errors.append("run1 must reproduce nonpositive unmeshable invalidity")
    if mesh_evidence.get("legacy_status") != "unmeshable" or not any(
        isinstance(value, (int, float)) and value <= 0
        for value in (mesh_evidence.get("mesh_min_volume"), mesh_evidence.get("mesh_min_quality"))
    ):
        errors.append("run1 historical evidence must retain a nonpositive mesh metric")

    campaign = manifest.get("campaign_acceptance", {})
    if campaign.get("exact_control_count") != 7:
        errors.append("campaign must require exactly seven controls")
    if campaign.get("gate_control_ids") != list(CAMPAIGN_GATE_IDS):
        errors.append("campaign gate control IDs changed")
    if campaign.get("exploratory_control_ids") != ["pool_ordinal_0057"]:
        errors.append("campaign exploratory control IDs changed")
    if campaign.get("control_substitution_allowed") is not False:
        errors.append("control substitution must remain forbidden")
    if campaign.get("all_gate_controls_must_pass") is not True:
        errors.append("all campaign gate controls must pass")
    expected_seed_contract = _seed_contract(repo_root, protocol_snapshot)
    if campaign.get("seed_contract") != expected_seed_contract:
        errors.append("campaign seed contract changed")
    if campaign.get("acceptance_tolerances") != _acceptance_tolerances():
        errors.append("campaign acceptance tolerances changed")
    if campaign.get("campaign_evidence_requirements") != _campaign_evidence_requirements():
        errors.append("campaign evidence requirements changed")
    if verify_files:
        for diagnostic in expected_seed_contract["alternate_seed_diagnostics"]:
            artifact = diagnostic["seed_identity_artifact"]
            path = repo_root / artifact["path"]
            if not path.is_file() or sha256_file(path) != artifact["file_sha256"]:
                errors.append("alternate seed diagnostic artifact hash mismatch")
    identity = campaign.get("result_identity_requirements", {})
    if identity.get("protocol_sha256") != protocol_ref.get("sha256"):
        errors.append("campaign result protocol hash mismatch")
    if (
        identity.get("geometry_sha256_must_match_control") is not True
        or identity.get("protocol_snapshot_must_match") is not True
    ):
        errors.append("campaign result identity checks must remain mandatory")
    return errors


def render_manifest(manifest: dict[str, Any]) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate the committed manifest and require byte-for-byte regeneration",
    )
    args = parser.parse_args(argv)

    try:
        generated = build_manifest(REPO_ROOT)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    errors = validate_manifest(generated, REPO_ROOT)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 2
    rendered = render_manifest(generated)

    output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
    if args.check:
        if not output.is_file():
            print(f"ERROR: missing manifest: {output}", file=sys.stderr)
            return 1
        try:
            committed = _load_json_object(output)
        except ValueError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        committed_errors = validate_manifest(committed, REPO_ROOT)
        if committed_errors:
            for error in committed_errors:
                print(f"ERROR: {error}", file=sys.stderr)
            return 1
        if output.read_text() != rendered:
            print("ERROR: committed manifest is not deterministic/current", file=sys.stderr)
            return 1
        print(f"OK: {output} is valid and current")
        return 0

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(rendered)
    print(f"Wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
