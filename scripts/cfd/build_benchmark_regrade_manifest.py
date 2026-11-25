#!/usr/bin/env python3
"""Build and validate the self-contained benchmark redevelopment manifest.

This module is deliberately data-only: it reads historical winner/result JSONs,
embeds geometry vectors, and never invokes CFD or reads an official label cache.
The referenced grading protocol is a qualification candidate, not an official
benchmark release or an authorization to execute it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.campaign_publication import (
    MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
    MINIMUM_RETENTION_DAYS,
    PUBLICATION_CONTRACT,
    PUBLICATION_CONTRACT_SCHEMA_VERSION,
    PUBLICATION_CONTRACT_SHA256,
)
from scripts.cfd.geometry_hash import GEOMETRY_HASH_ALGORITHM, geometry_sha256
from scripts.cfd.grading_protocol import validate_protocol


DEFAULT_OUTPUT = REPO_ROOT / "configs" / "cfd" / "benchmark_redevelopment_v1.json"
PROTOCOL_PATH = "configs/cfd/final_protocol_candidate_v2.json"
MANIFEST_ID = "superwing-benchmark-redevelopment-v1"
QUALIFICATION_PUBLICATION_ID = "superwing-fixed-cl-v5-protocol-qualification-v1"
PUBLICATION_STORAGE_BINDING_PATH = "configs/cfd/publication_storage_binding_v1.json"
PUBLICATION_STORAGE_BINDING_SCHEMA_VERSION = (
    "wingbench-cfd-publication-storage-binding-v1"
)
PENDING_PUBLICATION_STATUS = "pending_publication_bucket_pinning"
READY_PUBLICATION_STATUS = "ready"


def _canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_sha256(payload: Any) -> str:
    """Fingerprint a JSON payload using the grading-protocol canonicalization."""
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_publication_storage_binding(repo_root: Path) -> dict[str, Any]:
    path = repo_root / PUBLICATION_STORAGE_BINDING_PATH
    try:
        binding = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read publication storage binding {path}: {exc}") from exc
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
    if not isinstance(binding, dict) or set(binding) != expected_keys:
        raise ValueError("publication storage binding structure changed")
    if (
        binding.get("schema_version") != PUBLICATION_STORAGE_BINDING_SCHEMA_VERSION
        or binding.get("storage_provider") != "aws_s3"
        or binding.get("qualification_campaign_publication_id")
        != QUALIFICATION_PUBLICATION_ID
        or binding.get("benchmark_campaign_publication_id") != MANIFEST_ID
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
    ordered_wing_ids: list[str],
    binding: dict[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "campaign_publication_id": binding["benchmark_campaign_publication_id"],
        "publication_bucket": binding["bucket"],
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "ordered_wing_ids_sha256": canonical_json_sha256(ordered_wing_ids),
        "recovery_allowed": True,
    }


def _execution_readiness(binding: dict[str, Any]) -> dict[str, Any]:
    ready = binding["status"] == "pinned"
    return {
        "ready": ready,
        "status": READY_PUBLICATION_STATUS if ready else PENDING_PUBLICATION_STATUS,
    }


def _validate_publication_metadata(
    manifest: dict[str, Any],
    ordered_wing_ids: list[str],
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
        "ordered_wing_ids_sha256": canonical_json_sha256(ordered_wing_ids),
        "recovery_allowed": True,
    }
    if contract != expected_contract:
        errors.append("benchmark publication contract changed")
    expected_readiness = {
        "ready": bucket is not None,
        "status": (
            READY_PUBLICATION_STATUS
            if bucket is not None
            else PENDING_PUBLICATION_STATUS
        ),
    }
    if manifest.get("execution_readiness") != expected_readiness:
        errors.append("benchmark execution readiness disagrees with publication pinning")

    if verify_files:
        try:
            binding = _load_publication_storage_binding(repo_root)
        except (OSError, ValueError) as exc:
            errors.append(f"cannot verify publication storage binding: {exc}")
        else:
            if contract != _publication_contract(ordered_wing_ids, binding):
                errors.append("publication contract does not match the storage binding")
            if manifest.get("execution_readiness") != _execution_readiness(binding):
                errors.append("execution readiness does not match the storage binding")
    return errors


def _json_pointer(payload: Any, pointer: str) -> Any:
    if pointer == "":
        return payload
    if not pointer.startswith("/"):
        raise ValueError(f"invalid JSON pointer: {pointer!r}")
    current = payload
    for raw_token in pointer[1:].split("/"):
        token = raw_token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            current = current[int(token)]
        else:
            current = current[token]
    return current


@dataclass(frozen=True)
class Source:
    path: str
    pointer: str = "/geom_38d"
    embedded_geom: tuple[float, ...] | None = None
    embedded_file_sha256: str | None = None


@dataclass(frozen=True)
class Evidence:
    path: str
    evidence_type: str = "whole_wing_fixed_cl"


@dataclass(frozen=True)
class WingSpec:
    wing_id: str
    legacy_name: str
    family: str
    source: Source
    corpus_class: str
    campaign_role: str
    method_cohort: str | None = None
    score_population: bool = False
    source_type: str = "optimizer_generated"
    evidence: tuple[Evidence, ...] = ()
    provenance_note: str | None = None


# These two controls existed only in ignored local artifacts. Their exact JSON
# float values and original file hashes are frozen here so a clean clone can
# regenerate the same self-contained manifest.
_V1_S42 = (
    38.12588882446289, 4.574501991271973, 3.8944003582000732,
    10.14837646484375, 0.37326839566230774, 0.40926069021224976,
    -0.29305410385131836, 0.6039220094680786, 0.9614388346672058,
    0.9847501516342163, 0.37410593032836914, 0.8792963027954102,
    0.6821455955505371, -2.771954298019409, -2.062793731689453,
    -1.2407774925231934, -1.5089845657348633, 0.18754176795482635,
    0.10082302242517471, 0.10978983342647552, 0.416289359331131,
    0.1674216240644455, 0.10761608183383942, 0.6541566848754883,
    -0.09511145949363708, 0.4207511246204376, 0.31831926107406616,
    -0.11648818105459213, -0.01746905967593193, -0.24021179974079132,
    -0.01787053979933262, -0.2694218158721924, 0.09449435770511627,
    -0.07870333641767502, -0.16032084822654724, 0.10279874503612518,
    0.18339771032333374, 0.13469316065311432,
)
_V1_S43 = (
    32.388797760009766, 5.418972492218018, 1.6517887115478516,
    10.101066589355469, 0.20327772200107574, 0.359917014837265,
    0.8787736892700195, 0.6679915189743042, 0.9117698669433594,
    0.9858037233352661, 0.5184799432754517, 0.7983346581459045,
    0.4300987124443054, -3.1837117671966553, -2.479727268218994,
    -1.2569888830184937, -1.9313673973083496, 0.12345988303422928,
    0.10216507315635681, 0.22877612709999084, 0.1479925811290741,
    0.10975159704685211, -0.2920599579811096, 0.4987252950668335,
    0.08000744134187698, 0.23850320279598236, 0.11505185067653656,
    -0.12990513443946838, -0.06086543947458267, -0.27550655603408813,
    -0.13738416135311127, -0.0779213011264801, 0.05108630657196045,
    0.1329691857099533, -0.1632763296365738, 0.24272421002388,
    0.06169344484806061, 0.1533956527709961,
)


def _source(path: str, pointer: str = "/geom_38d") -> Source:
    return Source(path=path, pointer=pointer)


def _evidence(path: str, evidence_type: str = "whole_wing_fixed_cl") -> Evidence:
    return Evidence(path=path, evidence_type=evidence_type)


def _legacy(
    wing_id: str,
    legacy_name: str,
    family: str,
    source: Source,
    evidence: Iterable[Evidence],
    *,
    campaign_role: str = "benchmark_evidence",
    method_cohort: str | None = None,
    score_population: bool = False,
    source_type: str = "optimizer_generated",
    provenance_note: str | None = None,
) -> WingSpec:
    return WingSpec(
        wing_id=wing_id,
        legacy_name=legacy_name,
        family=family,
        source=source,
        corpus_class="legacy_whole_grade",
        campaign_role=campaign_role,
        method_cohort=method_cohort,
        score_population=score_population,
        source_type=source_type,
        evidence=tuple(evidence),
        provenance_note=provenance_note,
    )


def _incomplete(
    wing_id: str,
    legacy_name: str,
    family: str,
    source: Source,
    evidence: Iterable[Evidence] = (),
    *,
    intended_method_cohort: str | None = None,
    provenance_note: str | None = None,
) -> WingSpec:
    return WingSpec(
        wing_id=wing_id,
        legacy_name=legacy_name,
        family=family,
        source=source,
        corpus_class="selected_incomplete",
        campaign_role="selected_incomplete",
        method_cohort=intended_method_cohort,
        score_population=False,
        evidence=tuple(evidence),
        provenance_note=provenance_note,
    )


def wing_specs() -> list[WingSpec]:
    specs: list[WingSpec] = []

    for name in ("run1", "run3", "run4", "run5", "run6", "run7", "run8", "run9", "run10", "run11"):
        evidence = [
            _evidence(f"data/cfd/regrade_faithful/results/{name}.json"),
            _evidence(f"data/cfd/ground_truth/{name}.json", "whole_wing_fixed_cl_superseded"),
        ]
        specs.append(_legacy(
            f"l5_originals__{name}", name, "l5_originals",
            _source(f"data/winners/{name}_winner.json"), evidence,
            campaign_role="score_population", method_cohort="l5_originals",
            score_population=True,
        ))

    for index in range(8):
        name = f"grow_{index:02d}"
        specs.append(_legacy(
            f"defended_grow1__{name}", name, "defended_grow_batch_1",
            _source(f"data/cfd/grow_candidates/{name}_winner.json"),
            [_evidence(f"data/cfd/grow_grade/{name}.json")],
            campaign_role="score_population", method_cohort="defended_optimizer",
            score_population=True,
        ))

    for index in range(3):
        name = f"cand_{index:02d}"
        specs.append(_legacy(
            f"defended_manifold__{name}", name, "defended_manifold_batch",
            _source(
                "data/cfd/manifold_s42s43/audit.json",
                f"/candidates/{name}/geom_38d",
            ),
            [_evidence(f"data/cfd/manifold_grade_set/grade/{name}.json")],
            campaign_role="score_population", method_cohort="defended_optimizer",
            score_population=True,
            provenance_note=(
                "Geometry materialized from the tracked manifold audit; "
                "historical winner JSON was ignored."
            ),
        ))

    for index in (0, 1, 2, 3, 4, 5, 7, 9):
        name = f"grow2_{index:02d}"
        specs.append(_legacy(
            f"defended_grow2__{name}", name, "defended_grow_batch_2",
            _source(f"data/cfd/grow_s47_candidates/{name}_winner.json"),
            [_evidence(f"data/cfd/grow_s47_grade/{name}.json")],
            campaign_role="score_population", method_cohort="defended_optimizer",
            score_population=True,
        ))

    for index in range(11):
        name = f"sel_cand_{index:02d}"
        specs.append(_legacy(
            f"fresh_attack__{name}", name, "fresh_attack",
            _source(f"data/cfd/attack_s42s43/{name}_winner.json"),
            [_evidence(f"data/cfd/attack_s42s43/grade/{name}.json")],
            campaign_role="score_population", method_cohort="fresh_attack",
            score_population=True,
        ))

    fullcap_results = {
        "fullcap_run1": "data/cfd/ground_truth_fullcap/fullcap_run1.json",
        "fullcap_run2": "data/cfd/regrade_faithful/results/fullcap_run2.json",
        "fullcap_run4": "data/cfd/regrade_faithful/results/fullcap_run4.json",
        "fullcap_run5": "data/cfd/regrade_faithful/results/fullcap_run5.json",
        "fullcap_run6": "data/cfd/regrade_faithful/results/fullcap_run6.json",
    }
    for name, result in fullcap_results.items():
        specs.append(_legacy(
            f"fullcap__{name}", name, "fullcap_defense_arc",
            _source(f"data/winners/{name}_winner.json"), [_evidence(result)],
        ))

    specs.extend([
        _legacy(
            "tier2__cand_03", "tier2_cand_03", "tier2_attainability",
            _source("data/cfd/tier2_candidates/cand_03_winner.json"),
            [_evidence("data/cfd/tier2_grade_hardmach/cand_03.json")],
        ),
        _legacy(
            "tier2__cand_07", "tier2_cand_07", "tier2_attainability",
            _source("data/cfd/tier2_candidates/cand_07_winner.json"),
            [
                _evidence("data/cfd/regrade_faithful/results/tier2_cand_07.json"),
                _evidence(
                    "data/cfd/tier2_grade_hardmach/cand_07.json",
                    "whole_wing_fixed_cl_superseded",
                ),
            ],
        ),
        _legacy(
            "active_learning__r0_iter5", "r0_iter5", "active_learning",
            _source("data/cfd/r0_grade_candidates/r0_iter5_winner.json"),
            [_evidence("data/cfd/regrade_faithful/results/r0_iter5.json")],
            provenance_note="Same geometry as the R1 shortlist alias cand_08.",
        ),
        _legacy(
            "active_learning__r1_cand_01", "r1_cand_01", "active_learning",
            _source("data/cfd/r1_candidates/cand_01_winner.json"),
            [_evidence("data/cfd/regrade_faithful/results/r1_cand_01.json")],
        ),
        _legacy(
            "active_learning__r1_cand_02", "r1_cand_02", "active_learning",
            _source("data/cfd/r1_candidates/cand_02_winner.json"),
            [_evidence("data/cfd/r1_grade/cand_02.json")],
        ),
        _legacy(
            "active_learning__r1_cand_06", "r1_cand_06", "active_learning",
            _source("data/cfd/r1_candidates/cand_06_winner.json"),
            [
                _evidence("data/cfd/regrade_faithful/results/r1_cand_06.json"),
                _evidence("data/cfd/r1_grade/cand_06_partial/cand_06_partial.json", "partial_fixed_cl"),
            ],
        ),
        _legacy(
            "human_control__v12", "v12", "human_control",
            _source("data/winners/v12_winner.json"),
            [_evidence("data/cfd/regrade_faithful/results/v12.json")],
            source_type="human_designed",
        ),
    ])

    for name, geom, source_hash in (
        ("v1_s42", _V1_S42, "b5fc0116a4656a82d600570c655a3f9d89d35ed8962c2d89ec0b6096211e7b6d"),
        ("v1_s43", _V1_S43, "2bfc2317d234f0f3c160c8ba7e43aa360ec230f6d74d60844be61509d7aa015d"),
    ):
        specs.append(_legacy(
            f"manifold_control__{name}", name, "manifold_controls",
            Source(
                path=f"data/cfd/manifold_grade_set/{name}_winner.json",
                embedded_geom=geom,
                embedded_file_sha256=source_hash,
            ),
            [_evidence(f"data/cfd/manifold_grade_set/grade/{name}.json")],
            provenance_note=(
                "Exact geometry materialized from an ignored local artifact "
                "for clean-clone reproducibility."
            ),
        ))

    for index in range(4):
        name = f"sel_cand_{index:02d}"
        specs.append(_legacy(
            f"joint_attack__{name}", name, "joint_attack",
            _source(f"data/cfd/joint_s42s43/{name}_winner.json"),
            [_evidence(f"data/cfd/joint_s42s43/grade/{name}.json")],
        ))

    for index in (6, 8):
        name = f"grow2_{index:02d}"
        specs.append(_incomplete(
            f"defended_grow2__{name}", name, "defended_grow_batch_2",
            _source(f"data/cfd/grow_s47_candidates/{name}_winner.json"),
            intended_method_cohort="defended_optimizer",
            provenance_note=(
                "Selected as P0 to fill the missing grow2 grade in the historical "
                "overnight plan; that plan was ignored, so no external evidence "
                "path is required by this self-contained manifest."
            ),
        ))

    for index in (1, 4, 5, 6, 8):
        name = f"cand_{index:02d}"
        specs.append(_incomplete(
            f"tier2_incomplete__{name}", f"tier2_{name}", "tier2_attainability",
            _source(f"data/cfd/tier2_candidates/{name}_winner.json"),
            [_evidence(f"data/cfd/tier2_screen/{name}_screen.json", "convergence_screen")],
            provenance_note="In-box Tier-2 shortlist member with screen evidence but no complete grade.",
        ))

    specs.extend([
        _incomplete(
            "active_learning_incomplete__r1_cand_07", "r1_cand_07", "active_learning",
            _source("data/cfd/r1_candidates/cand_07_winner.json"),
            [_evidence("data/cfd/r1_screen/cand_07_screen.json", "convergence_screen")],
        ),
        _incomplete(
            "active_learning_incomplete__r0_iter2", "r0_iter2", "active_learning",
            _source("data/cfd/r0_grade_candidates/r0_iter2_winner.json"),
            [
                _evidence(
                    "data/cfd/r0_grade/cond0/r0_iter2_cond0.json",
                    "single_condition_fixed_cl",
                )
            ],
        ),
        _incomplete(
            "historical_incomplete__fixedcl_run1", "fixedcl_run1", "fixed_cl_historical",
            _source("data/winners/fixedcl_run1_winner.json"),
            provenance_note="Selected historical fixed-CL winner; no complete result was found.",
        ),
        _incomplete(
            "historical_incomplete__fullcap_iter3", "fullcap_iter3", "fixed_cl_historical",
            _source("data/winners/fullcap_iter3_winner.json"),
            provenance_note="Selected intermediate full-capability winner; no complete result was found.",
        ),
    ])
    return specs


def _load_source(repo_root: Path, source: Source) -> tuple[list[float], dict[str, Any]]:
    source_path = repo_root / source.path
    if source.embedded_geom is not None:
        values = [float(value) for value in source.embedded_geom]
        if source_path.exists() and source.embedded_file_sha256:
            actual = file_sha256(source_path)
            if actual != source.embedded_file_sha256:
                raise ValueError(
                    f"materialized source hash changed for {source.path}: "
                    f"{actual} != {source.embedded_file_sha256}"
                )
        return values, {
            "path": source.path,
            "json_pointer": source.pointer,
            "file_sha256": source.embedded_file_sha256,
            "tracked": False,
            "materialized_in_manifest": True,
            "source_availability_required": False,
        }

    if not source_path.is_file():
        raise FileNotFoundError(f"missing canonical source: {source.path}")
    payload = json.loads(source_path.read_text())
    values = [float(value) for value in _json_pointer(payload, source.pointer)]
    return values, {
        "path": source.path,
        "json_pointer": source.pointer,
        "file_sha256": file_sha256(source_path),
        "tracked": True,
        "materialized_in_manifest": False,
        "source_availability_required": True,
    }


def _load_evidence(repo_root: Path, evidence: Evidence) -> dict[str, Any]:
    path = repo_root / evidence.path
    if not path.is_file():
        raise FileNotFoundError(f"missing legacy evidence: {evidence.path}")
    payload = json.loads(path.read_text())
    row: dict[str, Any] = {
        "path": evidence.path,
        "file_sha256": file_sha256(path),
        "evidence_type": evidence.evidence_type,
    }
    if isinstance(payload, dict):
        for source_key, target_key in (
            ("grid_version", "grid_version"),
            ("status", "legacy_status"),
            ("n_conditions", "n_conditions"),
            ("n_valid", "n_valid"),
        ):
            if source_key in payload:
                row[target_key] = payload[source_key]
        verdict = payload.get("verdict")
        if isinstance(verdict, dict) and "label" in verdict:
            row["legacy_status"] = verdict["label"]
    row["protocol_provenance_complete"] = False
    row["legacy_only"] = True
    return row


def build_manifest(repo_root: Path = REPO_ROOT) -> dict[str, Any]:
    protocol_file = repo_root / PROTOCOL_PATH
    if not protocol_file.is_file():
        raise FileNotFoundError(f"missing protocol candidate: {PROTOCOL_PATH}")
    protocol_payload = json.loads(protocol_file.read_text())
    publication_binding = _load_publication_storage_binding(repo_root)
    validate_protocol(protocol_payload, verify_files=True)
    protocol_sha = canonical_json_sha256(protocol_payload)

    wings: list[dict[str, Any]] = []
    for spec in wing_specs():
        geom, source = _load_source(repo_root, spec.source)
        geom_hash = geometry_sha256(geom)
        provenance: dict[str, Any] = {
            "family": spec.family,
            "campaign_role": spec.campaign_role,
            "method_cohort": spec.method_cohort,
            "source_type": spec.source_type,
            "geometry_schema": "superwing-38d-v1",
            "geometry_convention": "configs-generated-v1",
        }
        if spec.provenance_note:
            provenance["note"] = spec.provenance_note
        wings.append({
            "wing_id": spec.wing_id,
            "legacy_name": spec.legacy_name,
            "geometry_sha256": geom_hash,
            "geom_38d": geom,
            "canonical_source": source,
            "provenance": provenance,
            "corpus_class": spec.corpus_class,
            "score_population": spec.score_population,
            "legacy_evidence": [
                _load_evidence(repo_root, item) for item in spec.evidence
            ],
            "regrade": {
                "required": True,
                "state": "blocked_pending_protocol_qualification",
                "protocol_sha256": protocol_sha,
                "official_result": False,
            },
        })

    class_counts = {
        name: sum(wing["corpus_class"] == name for wing in wings)
        for name in ("legacy_whole_grade", "selected_incomplete")
    }
    role_names = sorted({wing["provenance"]["campaign_role"] for wing in wings})
    role_counts = {
        name: sum(wing["provenance"]["campaign_role"] == name for wing in wings)
        for name in role_names
    }
    cohort_names = sorted({
        wing["provenance"]["method_cohort"]
        for wing in wings
        if wing["score_population"]
    })
    cohort_counts = {
        name: sum(
            wing["score_population"]
            and wing["provenance"]["method_cohort"] == name
            for wing in wings
        )
        for name in cohort_names
    }
    return {
        "schema_version": "benchmark-redevelopment-manifest-v1",
        "manifest_id": MANIFEST_ID,
        "manifest_status": "qualification_required",
        "official_benchmark": False,
        "execution_authorized": False,
        "official_labels_or_scores_created": False,
        "execution_readiness": _execution_readiness(publication_binding),
        "publication_contract": _publication_contract(
            [wing["wing_id"] for wing in wings], publication_binding
        ),
        "purpose": (
            "Self-contained geometry inventory for qualifying and then rebuilding "
            "the legacy SuperWing benchmark under one standardized protocol."
        ),
        "protocol_reference": {
            "path": PROTOCOL_PATH,
            "sha256": protocol_sha,
            "hash_semantics": "sha256(canonical compact sorted-key JSON)",
            "status": "qualification_required",
            "official": False,
            "qualified": False,
        },
        "geometry_hash": {
            "algorithm": GEOMETRY_HASH_ALGORITHM,
            "schema": "superwing-geom38-f64le-v1",
        },
        "summary": {
            "total_wings": len(wings),
            "counts_by_corpus_class": class_counts,
            "score_population_count": sum(wing["score_population"] for wing in wings),
            "counts_by_campaign_role": role_counts,
            "score_population_counts_by_method_cohort": cohort_counts,
            "all_geometries_embedded": True,
        },
        "scope_rules": {
            "included": [
                "58 distinct geometries with legacy whole-wing fixed-CL grades",
                "11 selected benchmark-experiment geometries lacking a complete grade",
            ],
            "excluded": [
                "post-benchmark MFCEI optimizer-development candidates",
                "fixed-AoA and seed-selection protocol diagnostics",
                "screen interpolation and perturbation controls",
                "duplicate paths and duplicate solver or mesh reruns",
                "dataset-sourced controls requiring running_info geometry reconstruction",
            ],
            "optimizer_redevelopment_deferred": True,
        },
        "wings": wings,
    }


def validate_manifest(
    manifest: dict[str, Any],
    repo_root: Path = REPO_ROOT,
    *,
    verify_files: bool = True,
) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != "benchmark-redevelopment-manifest-v1":
        errors.append("unexpected schema_version")
    if manifest.get("manifest_status") != "qualification_required":
        errors.append("manifest must remain qualification_required")
    if manifest.get("official_benchmark") is not False:
        errors.append("manifest must not claim official benchmark status")
    if manifest.get("execution_authorized") is not False:
        errors.append("manifest must not authorize execution")

    expected_wing_ids = [spec.wing_id for spec in wing_specs()]
    errors.extend(
        _validate_publication_metadata(
            manifest,
            expected_wing_ids,
            repo_root,
            verify_files=verify_files,
        )
    )

    protocol = manifest.get("protocol_reference", {})
    if protocol.get("path") != PROTOCOL_PATH:
        errors.append("wrong protocol candidate path")
    if protocol.get("status") != "qualification_required":
        errors.append("protocol reference must remain qualification_required")
    if protocol.get("official") is not False or protocol.get("qualified") is not False:
        errors.append("protocol reference must be non-official and unqualified")
    if verify_files:
        path = repo_root / PROTOCOL_PATH
        if not path.is_file():
            errors.append(f"missing protocol candidate: {PROTOCOL_PATH}")
        else:
            current_protocol = json.loads(path.read_text())
            try:
                validate_protocol(current_protocol, verify_files=True)
            except ValueError as exc:
                errors.append(f"protocol scientific contract failed: {exc}")
            actual = canonical_json_sha256(current_protocol)
            if actual != protocol.get("sha256"):
                errors.append("protocol canonical JSON hash mismatch")

    wings = manifest.get("wings")
    if not isinstance(wings, list):
        return errors + ["wings must be a list"]
    if len(wings) != 69:
        errors.append(f"expected 69 wings, got {len(wings)}")
    if [wing.get("wing_id") for wing in wings if isinstance(wing, dict)] != expected_wing_ids:
        errors.append("wings must remain in the exact fixed campaign order")

    wing_ids: set[str] = set()
    geometry_hashes: set[str] = set()
    for index, wing in enumerate(wings):
        prefix = f"wings[{index}]"
        wing_id = wing.get("wing_id")
        if not isinstance(wing_id, str) or not wing_id:
            errors.append(f"{prefix}: missing wing_id")
        elif wing_id in wing_ids:
            errors.append(f"{prefix}: duplicate wing_id {wing_id}")
        wing_ids.add(wing_id)

        geom = wing.get("geom_38d")
        try:
            actual_geom_hash = geometry_sha256(geom)
        except (TypeError, ValueError) as exc:
            errors.append(f"{prefix}: invalid geometry: {exc}")
            continue
        if wing.get("geometry_sha256") != actual_geom_hash:
            errors.append(f"{prefix}: geometry hash mismatch")
        if actual_geom_hash in geometry_hashes:
            errors.append(f"{prefix}: duplicate geometry {actual_geom_hash}")
        geometry_hashes.add(actual_geom_hash)

        if wing.get("corpus_class") not in {"legacy_whole_grade", "selected_incomplete"}:
            errors.append(f"{prefix}: invalid corpus_class")
        regrade = wing.get("regrade", {})
        if regrade.get("state") != "blocked_pending_protocol_qualification":
            errors.append(f"{prefix}: regrade must be qualification-blocked")
        if regrade.get("official_result") is not False:
            errors.append(f"{prefix}: regrade cannot be official yet")
        if regrade.get("protocol_sha256") != protocol.get("sha256"):
            errors.append(f"{prefix}: protocol fingerprint mismatch")

        source = wing.get("canonical_source", {})
        if verify_files:
            source_path = repo_root / str(source.get("path", ""))
            required = source.get("source_availability_required") is True
            if required and not source_path.is_file():
                errors.append(f"{prefix}: missing canonical source {source.get('path')}")
            if source_path.is_file() and file_sha256(source_path) != source.get("file_sha256"):
                errors.append(f"{prefix}: canonical source file hash mismatch")
            if source_path.is_file() and source.get("materialized_in_manifest") is not True:
                try:
                    source_payload = json.loads(source_path.read_text())
                    source_geom = _json_pointer(source_payload, source["json_pointer"])
                    if geometry_sha256(source_geom) != actual_geom_hash:
                        errors.append(f"{prefix}: source geometry does not match embedded geometry")
                except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                    errors.append(f"{prefix}: cannot validate source geometry: {exc}")

        for evidence in wing.get("legacy_evidence", []):
            if evidence.get("legacy_only") is not True:
                errors.append(f"{prefix}: evidence must be marked legacy_only")
            if verify_files:
                evidence_path = repo_root / str(evidence.get("path", ""))
                if not evidence_path.is_file():
                    errors.append(f"{prefix}: missing legacy evidence {evidence.get('path')}")
                elif file_sha256(evidence_path) != evidence.get("file_sha256"):
                    errors.append(f"{prefix}: legacy evidence file hash mismatch")

    class_counts = {
        name: sum(wing.get("corpus_class") == name for wing in wings)
        for name in ("legacy_whole_grade", "selected_incomplete")
    }
    if class_counts != {"legacy_whole_grade": 58, "selected_incomplete": 11}:
        errors.append(f"wrong corpus class counts: {class_counts}")
    if sum(wing.get("score_population") is True for wing in wings) != 40:
        errors.append("expected 40 score-population wings")
    if manifest.get("summary", {}).get("counts_by_corpus_class") != class_counts:
        errors.append("summary corpus class counts do not match wings")
    if manifest.get("summary", {}).get("total_wings") != len(wings):
        errors.append("summary total_wings does not match wings")
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

    generated = build_manifest(REPO_ROOT)
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
        committed = json.loads(output.read_text())
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
