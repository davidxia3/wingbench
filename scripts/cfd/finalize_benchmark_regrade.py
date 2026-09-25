#!/usr/bin/env python3
"""Finalize one complete official 69-wing benchmark regrade evidence set.

This command does not run CFD and does not define a benchmark-wide score.  It
accepts exactly one frozen official whole-wing result for every wing in one
frozen official manifest, revalidates every result and its artifacts through
the production grader, and materializes a deterministic completion bundle with
bundle-relative results and a content-addressed artifact store.  The bundle can
be moved and fully replayed after producer work directories disappear.  A
completion record never authorizes execution or replaces any source result.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.grade_benchmark import (  # noqa: E402
    _verified_artifact_bytes,
    load_manifest,
    validate_result,
)
from scripts.cfd.campaign_publication import (  # noqa: E402
    CampaignPublicationError,
    PUBLICATION_INVENTORY_FILENAME,
    PUBLICATION_LEDGER_DIRECTORY,
    load_campaign_publication_export,
    validate_manifest_publication_contract,
    validate_publication_ledger_record,
)
from scripts.cfd.grading_protocol import (  # noqa: E402
    GradingProtocol,
    ProtocolError,
    canonical_json_bytes,
    load_protocol,
    protocol_sha256,
    sha256_file,
)


ENVELOPE_SCHEMA_VERSION = "wingbench-cfd-benchmark-completion-envelope-v1"
RECORD_SCHEMA_VERSION = "wingbench-cfd-benchmark-completion-record-v1"
HASH_SEMANTICS = "sha256(canonical compact sorted-key JSON at /completion_record)"
EXPECTED_MANIFEST_SCHEMA = "benchmark-redevelopment-manifest-v1"
EXPECTED_RESULT_COUNT = 69
EVIDENCE_BUNDLE_SCHEMA_VERSION = "wingbench-cfd-benchmark-completion-bundle-v1"
RESULTS_DIRECTORY = "results"
ARTIFACT_STORE = "artifacts/sha256"
COMPLETION_RECORD_FILENAME = "completion_record.json"
ResultValidator = Callable[..., None]


class BenchmarkCompletionError(ProtocolError):
    """The submitted corpus cannot form one trustworthy completion record."""


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _full_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError(f"duplicate JSON object key {key!r}")
        payload[key] = value
    return payload


def _parse_json_object_bytes(raw: bytes, label: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise BenchmarkCompletionError(f"cannot read JSON {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise BenchmarkCompletionError(f"expected a JSON object in {label}")
    return payload


def _strict_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise BenchmarkCompletionError(f"cannot read JSON {path}: {exc}") from exc
    return _parse_json_object_bytes(raw, path), raw


def _relative_or_absolute(path: Path, repo_root: Path = REPO_ROOT) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(repo_root.resolve()))
    except ValueError:
        return str(resolved)


def _bundle_relative_path(path: Path, bundle_root: Path) -> str:
    try:
        relative = path.resolve().relative_to(bundle_root.resolve())
    except ValueError as exc:
        raise BenchmarkCompletionError(
            "benchmark completion evidence must be inside its bundle"
        ) from exc
    if not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise BenchmarkCompletionError(
            "benchmark completion evidence has an unsafe bundle-relative path"
        )
    return relative.as_posix()


def _resolve_bundle_path(bundle_root: Path, relative: Any, label: str) -> Path:
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise BenchmarkCompletionError(
            f"{label} must be a non-empty bundle-relative path"
        )
    root = Path(bundle_root).resolve()
    relative_path = Path(relative)
    if any(part in {"", ".", ".."} for part in relative_path.parts):
        raise BenchmarkCompletionError(f"{label} has an unsafe bundle-relative path")
    candidate = root / relative_path
    try:
        candidate.resolve().relative_to(root)
    except ValueError as exc:
        raise BenchmarkCompletionError(f"{label} escapes the evidence bundle") from exc
    return candidate


def _iter_artifact_records(value: Any):
    """Yield every artifact-shaped record embedded in a whole-wing result."""
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
                    raise BenchmarkCompletionError(
                        "present artifact record lacks a full SHA-256"
                    )
                digests.add(digest)
    return sorted(digests)


def _campaign_artifact_store(
    campaign_export_root: Path,
    results: Sequence[Mapping[str, Any]],
) -> Path:
    """Validate the exact content-addressed artifact inventory beside the ledger."""
    supplied = Path(campaign_export_root)
    if supplied.is_symlink():
        raise BenchmarkCompletionError("campaign publication export cannot be a symlink")
    root = supplied.resolve()
    expected_root_names = {
        PUBLICATION_LEDGER_DIRECTORY,
        PUBLICATION_INVENTORY_FILENAME,
        ARTIFACT_STORE.split("/", 1)[0],
    }
    if (
        not root.is_dir()
        or {path.name for path in root.iterdir()} != expected_root_names
    ):
        raise BenchmarkCompletionError(
            "campaign publication export has a non-canonical root inventory"
        )
    artifacts_parent = root / ARTIFACT_STORE.split("/", 1)[0]
    store_name = ARTIFACT_STORE.split("/", 1)[1]
    if (
        not artifacts_parent.is_dir()
        or artifacts_parent.is_symlink()
        or {path.name for path in artifacts_parent.iterdir()} != {store_name}
    ):
        raise BenchmarkCompletionError("campaign artifact layout changed")
    store = artifacts_parent / store_name
    if not store.is_dir() or store.is_symlink():
        raise BenchmarkCompletionError("campaign artifact store is not a directory")
    expected_digests = _referenced_artifact_sha256s(results)
    observed_digests: list[str] = []
    for path in store.iterdir():
        if (
            not path.is_file()
            or path.is_symlink()
            or not _full_sha256(path.name)
            or sha256_file(path) != path.name
        ):
            raise BenchmarkCompletionError(
                f"campaign artifact store contains an invalid entry: {path.name}"
            )
        observed_digests.append(path.name)
    if sorted(observed_digests) != expected_digests:
        raise BenchmarkCompletionError(
            "campaign artifact store is not the exact published artifact inventory"
        )
    return store


def _validate_frozen_official_context(
    manifest: Mapping[str, Any], protocol: GradingProtocol
) -> list[Mapping[str, Any]]:
    try:
        protocol.require_mode("official")
    except ProtocolError as exc:
        raise BenchmarkCompletionError(
            f"completion requires a frozen official protocol: {exc}"
        ) from exc
    if manifest.get("schema_version") != EXPECTED_MANIFEST_SCHEMA:
        raise BenchmarkCompletionError("unsupported benchmark completion manifest schema")
    if not isinstance(manifest.get("manifest_id"), str) or not manifest["manifest_id"]:
        raise BenchmarkCompletionError("frozen manifest lacks a manifest ID")
    reference = _as_mapping(manifest.get("protocol_reference"))
    checks = {
        "manifest_status": manifest.get("manifest_status") == "frozen",
        "official_benchmark": manifest.get("official_benchmark") is True,
        # The immutable manifest never authorizes billable work.  Each result's
        # separately approved authorization is validated below instead.
        "manifest_does_not_authorize_execution": (
            manifest.get("execution_authorized") is False
        ),
        "publication_storage_ready": manifest.get("execution_readiness")
        == {"ready": True, "status": "ready"},
        "protocol_sha256": reference.get("sha256") == protocol.sha256,
        "protocol_status": reference.get("status") == "frozen",
        "protocol_official": reference.get("official") is True,
        "protocol_qualified": reference.get("qualified") is True,
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise BenchmarkCompletionError(
            "manifest is not the frozen official benchmark release: " + ", ".join(failed)
        )
    wings = manifest.get("wings")
    if not isinstance(wings, list) or len(wings) != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError(
            f"completion requires exactly {EXPECTED_RESULT_COUNT} manifest wings"
        )
    wing_ids: list[str] = []
    for index, wing in enumerate(wings):
        if not isinstance(wing, Mapping):
            raise BenchmarkCompletionError(f"manifest wing {index} is not an object")
        wing_id = wing.get("wing_id")
        if not isinstance(wing_id, str) or not wing_id:
            raise BenchmarkCompletionError(f"manifest wing {index} lacks a wing_id")
        if type(wing.get("score_population")) is not bool:
            raise BenchmarkCompletionError(f"manifest wing {wing_id} lacks a score-population flag")
        if not isinstance(wing.get("corpus_class"), str):
            raise BenchmarkCompletionError(f"manifest wing {wing_id} lacks a corpus class")
        wing_ids.append(wing_id)
    if len(set(wing_ids)) != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("frozen manifest contains duplicate wing IDs")
    summary = _as_mapping(manifest.get("summary"))
    if summary.get("total_wings") != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("manifest summary does not bind the 69-wing roster")
    expected_score_population = sum(wing["score_population"] is True for wing in wings)
    if summary.get("score_population_count") != expected_score_population:
        raise BenchmarkCompletionError("manifest score-population summary is inconsistent")
    try:
        validate_manifest_publication_contract(
            manifest,
            ordered_manifest_wing_ids=wing_ids,
            expected_recovery_allowed=True,
        )
    except CampaignPublicationError as exc:
        raise BenchmarkCompletionError(
            f"manifest publication contract failed: {exc}"
        ) from exc
    return wings


def load_completion_context(
    manifest_path: Path,
    *,
    repo_root: Path = REPO_ROOT,
) -> tuple[dict[str, Any], str, str, GradingProtocol, str]:
    """Load and cross-bind the one official manifest and frozen protocol."""
    manifest_path = Path(manifest_path).resolve()
    manifest, raw = _strict_json_object(manifest_path)
    reference = _as_mapping(manifest.get("protocol_reference"))
    protocol_value = reference.get("path")
    if not isinstance(protocol_value, str) or not protocol_value:
        raise BenchmarkCompletionError("benchmark manifest lacks a protocol path")
    protocol_path = (repo_root / protocol_value).resolve()
    try:
        protocol_path.relative_to(repo_root.resolve())
    except ValueError as exc:
        raise BenchmarkCompletionError("benchmark protocol path escapes the repository") from exc
    protocol = load_protocol(protocol_path, verify_files=True)
    try:
        loaded_manifest, manifest_sha256 = load_manifest(manifest_path, protocol)
    except (ProtocolError, OSError, ValueError) as exc:
        raise BenchmarkCompletionError(f"benchmark manifest failed validation: {exc}") from exc
    if canonical_json_bytes(loaded_manifest) != canonical_json_bytes(manifest):
        raise BenchmarkCompletionError("benchmark manifest changed while it was being loaded")
    _validate_frozen_official_context(manifest, protocol)
    manifest_file_sha256 = hashlib.sha256(raw).hexdigest()
    protocol_file_sha256 = sha256_file(protocol_path)
    if sha256_file(manifest_path) != manifest_file_sha256:
        raise BenchmarkCompletionError("benchmark manifest changed while it was being loaded")
    return (
        manifest,
        manifest_sha256,
        manifest_file_sha256,
        protocol,
        protocol_file_sha256,
    )


def _result_paths_by_wing(
    paths: Sequence[Path],
    expected_ids: Sequence[str],
    *,
    evidence_bundle_root: Path,
) -> dict[str, tuple[Path, dict[str, Any], dict[str, Any]]]:
    expected = set(expected_ids)
    by_wing: dict[str, tuple[Path, dict[str, Any], dict[str, Any]]] = {}
    unexpected: list[str] = []
    for path in paths:
        result, raw = _strict_json_object(Path(path).resolve())
        wing_id = result.get("wing_id")
        if not isinstance(wing_id, str) or wing_id not in expected:
            unexpected.append(f"{path.name}:{wing_id!r}")
            continue
        if wing_id in by_wing:
            raise BenchmarkCompletionError(f"duplicate official result for {wing_id}")
        relative_path = _bundle_relative_path(path, evidence_bundle_root)
        if relative_path != f"{RESULTS_DIRECTORY}/{wing_id}.json":
            raise BenchmarkCompletionError(
                f"{wing_id} result is not at its exact bundle-relative path"
            )
        by_wing[wing_id] = (
            Path(path).resolve(),
            result,
            {
                "path": relative_path,
                "file_sha256": hashlib.sha256(raw).hexdigest(),
                "canonical_sha256": protocol_sha256(result),
                "size_bytes": len(raw),
            },
        )
    if unexpected:
        raise BenchmarkCompletionError(
            "results contain non-manifest wing IDs: " + ", ".join(unexpected)
        )
    missing = [wing_id for wing_id in expected_ids if wing_id not in by_wing]
    if missing:
        raise BenchmarkCompletionError("missing official results: " + ", ".join(missing))
    return by_wing


def _bundle_result_paths(
    evidence_bundle_root: Path, expected_ids: Sequence[str]
) -> list[Path]:
    results_directory = _resolve_bundle_path(
        evidence_bundle_root, RESULTS_DIRECTORY, "results directory"
    )
    expected_names = sorted(f"{wing_id}.json" for wing_id in expected_ids)
    if (
        not results_directory.is_dir()
        or results_directory.is_symlink()
        or sorted(path.name for path in results_directory.iterdir()) != expected_names
        or any(
            not path.is_file() or path.is_symlink()
            for path in results_directory.iterdir()
        )
    ):
        raise BenchmarkCompletionError(
            "completion bundle must contain the exact 69-result inventory"
        )
    return [results_directory / f"{wing_id}.json" for wing_id in expected_ids]


def _validated_identity(
    *,
    result: Mapping[str, Any],
    wing: Mapping[str, Any],
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    protocol: GradingProtocol,
    result_validator: ResultValidator,
    artifact_store: Path | None,
) -> dict[str, Any]:
    wing_id = str(wing["wing_id"])
    if result.get("official") is not True:
        raise BenchmarkCompletionError(f"{wing_id} is not an official whole-wing result")
    if result.get("legacy_evidence_used_for_verdict") is not False:
        raise BenchmarkCompletionError(f"{wing_id} used legacy evidence for its verdict")
    protocol_row = _as_mapping(result.get("protocol"))
    if (
        protocol_row.get("protocol_id") != protocol.protocol_id
        or protocol_row.get("protocol_status") != "frozen"
        or protocol_row.get("canonical_sha256") != protocol.sha256
    ):
        raise BenchmarkCompletionError(f"{wing_id} is bound to a different protocol")
    manifest_row = _as_mapping(result.get("benchmark_manifest"))
    if (
        manifest_row.get("manifest_id") != manifest.get("manifest_id")
        or manifest_row.get("canonical_sha256") != manifest_sha256
        or manifest_row.get("official_benchmark") is not True
    ):
        raise BenchmarkCompletionError(f"{wing_id} is bound to a different manifest")

    release = _as_mapping(result.get("release_lock"))
    release_snapshot = _as_mapping(release.get("snapshot"))
    release_sha256 = release.get("canonical_sha256")
    if (
        not _full_sha256(release_sha256)
        or protocol_sha256(release_snapshot) != release_sha256
        or release_snapshot.get("benchmark_release_id")
        != protocol.payload.get("benchmark_release_id")
        or release.get("benchmark_release_id")
        != protocol.payload.get("benchmark_release_id")
    ):
        raise BenchmarkCompletionError(f"{wing_id} lacks its exact frozen release lock")

    authorization = _as_mapping(result.get("execution_authorization"))
    authorization_snapshot = _as_mapping(authorization.get("snapshot"))
    authorization_sha256 = authorization.get("canonical_sha256")
    authorization_wing_ids = authorization_snapshot.get("wing_ids")
    if (
        authorization.get("test_only") is True
        or authorization.get("test_only_fake_runner") is True
        or authorization_snapshot.get("test_only") is True
        or authorization_snapshot.get("test_only_fake_runner") is True
        or not isinstance(authorization.get("authorization_id"), str)
        or not authorization["authorization_id"]
        or authorization_snapshot.get("authorization_id") != authorization.get("authorization_id")
        or not _full_sha256(authorization_sha256)
        or protocol_sha256(authorization_snapshot) != authorization_sha256
        or authorization_snapshot.get("authorized_mode") != "official"
        or authorization_snapshot.get("official_labels_authorized") is not True
        or not isinstance(authorization_wing_ids, list)
        or not authorization_wing_ids
        or not all(isinstance(value, str) and value for value in authorization_wing_ids)
        or len(set(authorization_wing_ids)) != len(authorization_wing_ids)
        or wing_id not in authorization_wing_ids
    ):
        raise BenchmarkCompletionError(f"{wing_id} contains test-only or non-official evidence")

    run_id = result.get("run_id")
    if (
        not isinstance(run_id, str)
        or not run_id
        or authorization_snapshot.get("run_id") != run_id
    ):
        raise BenchmarkCompletionError(f"{wing_id} lacks an authorization-bound run ID")
    runtime = _as_mapping(result.get("runtime"))
    runtime_fingerprint = _as_mapping(runtime.get("solver_environment"))
    runtime_fingerprint_sha256 = runtime.get("solver_environment_sha256")
    runtime_attestation = _as_mapping(runtime.get("runtime_attestation"))
    runtime_attestation_sha256 = runtime.get("runtime_attestation_sha256")
    if (
        runtime_fingerprint.get("schema_version") != "superwing-runtime-fingerprint-v2"
        or runtime_fingerprint.get("status") != "complete"
        or not _full_sha256(runtime_fingerprint_sha256)
        or protocol_sha256(runtime_fingerprint) != runtime_fingerprint_sha256
        or not _full_sha256(runtime_attestation_sha256)
        or protocol_sha256(runtime_attestation) != runtime_attestation_sha256
        or runtime_attestation.get("runtime_fingerprint_sha256")
        != runtime_fingerprint_sha256
        or authorization.get("runtime_attestation_sha256") != runtime_attestation_sha256
        or authorization_snapshot.get("runtime_attestation_sha256")
        != runtime_attestation_sha256
    ):
        raise BenchmarkCompletionError(f"{wing_id} lacks complete attested runtime evidence")

    try:
        result_validator(
            result,
            protocol,
            wing,
            manifest_sha256,
            verify_artifacts=True,
            artifact_store=artifact_store,
        )
    except (ProtocolError, OSError, ValueError, KeyError, TypeError) as exc:
        raise BenchmarkCompletionError(f"{wing_id} result contract failed: {exc}") from exc
    return {
        "run_id": run_id,
        "execution_authorization_id": str(authorization.get("authorization_id")),
        "execution_authorization_sha256": str(authorization_sha256),
        "runtime_attestation_sha256": str(runtime_attestation_sha256),
        "release_lock_sha256": str(release_sha256),
        "runtime_fingerprint_sha256": str(runtime_fingerprint_sha256),
        "runtime_fingerprint_schema_version": str(runtime_fingerprint["schema_version"]),
        "authorization_wing_ids": list(authorization_wing_ids),
    }


def _aggregate_summary(result: Mapping[str, Any], wing_id: str) -> dict[str, Any]:
    aggregate = _as_mapping(result.get("aggregate"))
    admissibility = aggregate.get("admissibility")
    profile_status = aggregate.get("profile_status")
    score_eligible = aggregate.get("score_eligible")
    if admissibility not in {"pass", "fail", "undetermined"}:
        raise BenchmarkCompletionError(f"{wing_id} has an unsupported admissibility")
    if profile_status not in {"complete", "incomplete"}:
        raise BenchmarkCompletionError(f"{wing_id} has an unsupported profile status")
    if type(score_eligible) is not bool:
        raise BenchmarkCompletionError(f"{wing_id} lacks a boolean score-eligibility verdict")
    if score_eligible is not (admissibility == "pass" and profile_status == "complete"):
        raise BenchmarkCompletionError(f"{wing_id} score eligibility is internally inconsistent")
    return {
        "admissibility": admissibility,
        "profile_status": profile_status,
        "score_eligible": score_eligible,
    }


def _counter(labels: Sequence[str], values: Sequence[str]) -> dict[str, int]:
    counts = Counter(values)
    return {label: counts[label] for label in labels}


def build_completion_envelope(
    *,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_sha256: str,
    manifest_file_sha256: str,
    protocol: GradingProtocol,
    protocol_file_sha256: str,
    evidence_bundle_root: Path,
    allow_interruption_recovery: bool = False,
    result_validator: ResultValidator = validate_result,
) -> dict[str, Any]:
    """Validate all 69 official results and build their deterministic record."""
    wings = _validate_frozen_official_context(manifest, protocol)
    if protocol_sha256(manifest) != manifest_sha256:
        raise BenchmarkCompletionError("manifest canonical hash changed before finalization")
    manifest_path = Path(manifest_path).resolve()
    if not manifest_path.is_file() or sha256_file(manifest_path) != manifest_file_sha256:
        raise BenchmarkCompletionError("manifest file changed before finalization")
    protocol_path = Path(protocol.path).resolve()
    if not protocol_path.is_file() or sha256_file(protocol_path) != protocol_file_sha256:
        raise BenchmarkCompletionError("protocol file changed before finalization")

    evidence_bundle_root = Path(evidence_bundle_root).resolve()
    artifact_store = _resolve_bundle_path(
        evidence_bundle_root, ARTIFACT_STORE, "artifact store"
    )
    wing_ids = [str(wing["wing_id"]) for wing in wings]
    try:
        publication_contract = validate_manifest_publication_contract(
            manifest,
            ordered_manifest_wing_ids=wing_ids,
            expected_recovery_allowed=True,
        )
        publication_ledger, published_documents = load_campaign_publication_export(
            evidence_bundle_root,
            ordered_manifest_wing_ids=wing_ids,
            expected_campaign_publication_id=publication_contract[
                "campaign_publication_id"
            ],
            expected_publication_bucket=publication_contract[
                "publication_bucket"
            ],
            allow_recovery=True,
            require_exact_root=False,
        )
    except CampaignPublicationError as exc:
        raise BenchmarkCompletionError(
            f"campaign publication ledger failed: {exc}"
        ) from exc
    if (
        publication_ledger["protocol_sha256"] != protocol.sha256
        or publication_ledger["manifest_sha256"] != manifest_sha256
    ):
        raise BenchmarkCompletionError(
            "campaign publication ledger is bound to a different protocol or manifest"
        )
    result_paths = _bundle_result_paths(evidence_bundle_root, wing_ids)
    paths_by_wing = _result_paths_by_wing(
        result_paths,
        wing_ids,
        evidence_bundle_root=evidence_bundle_root,
    )
    for wing_id, (_path, result, evidence) in paths_by_wing.items():
        published_result, published_raw = published_documents[wing_id]
        if (
            canonical_json_bytes(result) != canonical_json_bytes(published_result)
            or hashlib.sha256(published_raw).hexdigest() != evidence["file_sha256"]
            or len(published_raw) != evidence["size_bytes"]
        ):
            raise BenchmarkCompletionError(
                f"extracted result {wing_id} differs from its terminal publication"
            )
    common_release_locks: set[str] = set()
    common_runtime_fingerprints: set[str] = set()
    run_authorizations: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    run_authorized_scopes: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    run_wing_ids: dict[str, list[str]] = defaultdict(list)
    rows: list[dict[str, Any]] = []
    bundled_results: list[dict[str, Any]] = []

    for wing in wings:
        wing_id = str(wing["wing_id"])
        result_path, result, evidence = paths_by_wing[wing_id]
        identity = _validated_identity(
            result=result,
            wing=wing,
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            protocol=protocol,
            result_validator=result_validator,
            artifact_store=artifact_store,
        )
        bundled_results.append(result)
        observed = _aggregate_summary(result, wing_id)
        common_release_locks.add(identity["release_lock_sha256"])
        common_runtime_fingerprints.add(identity["runtime_fingerprint_sha256"])
        run_id = identity["run_id"]
        run_authorizations[run_id].add((
            identity["execution_authorization_id"],
            identity["execution_authorization_sha256"],
            identity["runtime_attestation_sha256"],
        ))
        run_authorized_scopes[run_id].add(
            tuple(identity["authorization_wing_ids"])
        )
        run_wing_ids[run_id].append(wing_id)
        if sha256_file(result_path) != evidence["file_sha256"]:
            raise BenchmarkCompletionError(f"{wing_id} result file changed during finalization")
        provenance = _as_mapping(wing.get("provenance"))
        rows.append({
            "wing_id": wing_id,
            "corpus_class": wing["corpus_class"],
            "manifest_score_population": wing["score_population"],
            "method_cohort": provenance.get("method_cohort"),
            **observed,
            "execution": {
                "run_id": run_id,
                "execution_authorization_sha256": identity[
                    "execution_authorization_sha256"
                ],
                "runtime_attestation_sha256": identity[
                    "runtime_attestation_sha256"
                ],
            },
            "result_evidence": evidence,
        })

    if len(common_release_locks) != 1:
        raise BenchmarkCompletionError("official results do not share one release lock")
    if len(common_runtime_fingerprints) != 1:
        raise BenchmarkCompletionError("official results do not share one runtime fingerprint")
    for run_id, identities in run_authorizations.items():
        if len(identities) != 1:
            raise BenchmarkCompletionError(
                f"authorized run {run_id} contains multiple authorization or attestation identities"
            )
        if len(run_authorized_scopes[run_id]) != 1:
            raise BenchmarkCompletionError(
                f"authorized run {run_id} contains multiple ordered wing scopes"
            )
    if sha256_file(manifest_path) != manifest_file_sha256:
        raise BenchmarkCompletionError("manifest file changed during finalization")
    if sha256_file(protocol_path) != protocol_file_sha256:
        raise BenchmarkCompletionError("protocol file changed during finalization")
    manifest_index = {wing_id: index for index, wing_id in enumerate(wing_ids)}
    run_ids = sorted(
        run_authorizations,
        key=lambda run_id: manifest_index[run_wing_ids[run_id][0]],
    )
    if len(run_ids) > 1 and allow_interruption_recovery is not True:
        raise BenchmarkCompletionError(
            "multiple authorized run IDs require --allow-interruption-recovery"
        )
    run_rows = []
    cursor = 0
    for run_id in run_ids:
        authorization_id, authorization_sha256, attestation_sha256 = next(
            iter(run_authorizations[run_id])
        )
        authorized_wing_ids = list(next(iter(run_authorized_scopes[run_id])))
        observed_wing_ids = run_wing_ids[run_id]
        remaining_suffix = wing_ids[cursor:]
        if authorized_wing_ids != remaining_suffix:
            raise BenchmarkCompletionError(
                f"authorized run {run_id} does not cover the exact remaining manifest suffix"
            )
        if observed_wing_ids != authorized_wing_ids[:len(observed_wing_ids)]:
            raise BenchmarkCompletionError(
                f"authorized run {run_id} violates first-complete sequential-prefix semantics"
            )
        cursor += len(observed_wing_ids)
        run_rows.append({
            "run_id": run_id,
            "execution_authorization_id": authorization_id,
            "execution_authorization_sha256": authorization_sha256,
            "runtime_attestation_sha256": attestation_sha256,
            "authorized_wing_ids": authorized_wing_ids,
            "wing_ids": observed_wing_ids,
        })
    if cursor != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("authorized runs omit terminal manifest results")

    admissibilities = [str(row["admissibility"]) for row in rows]
    profiles = [str(row["profile_status"]) for row in rows]
    eligible = sum(row["score_eligible"] is True for row in rows)
    score_population = sum(row["manifest_score_population"] is True for row in rows)
    score_population_eligible = sum(
        row["manifest_score_population"] is True and row["score_eligible"] is True
        for row in rows
    )
    release_lock_sha256 = next(iter(common_release_locks))
    runtime_fingerprint_sha256 = next(iter(common_runtime_fingerprints))
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "completion_id": (
            f"{manifest['manifest_id']}-official-regrade-completion-v1"
        ),
        "completion_status": "complete",
        "official_results_verified": True,
        "artifact_files_cryptographically_verified": True,
        "legacy_evidence_used_for_verdict": False,
        "execution_authorized_by_this_record": False,
        "protocol": {
            "path": _relative_or_absolute(protocol_path),
            "protocol_id": protocol.protocol_id,
            "protocol_status": "frozen",
            "canonical_sha256": protocol.sha256,
            "file_sha256": protocol_file_sha256,
        },
        "benchmark_manifest": {
            "path": _relative_or_absolute(manifest_path),
            "manifest_id": manifest["manifest_id"],
            "schema_version": manifest["schema_version"],
            "manifest_status": "frozen",
            "official_benchmark": True,
            "canonical_sha256": manifest_sha256,
            "file_sha256": manifest_file_sha256,
            "total_wings": EXPECTED_RESULT_COUNT,
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
            "result_file_count": EXPECTED_RESULT_COUNT,
            "artifact_sha256s": _referenced_artifact_sha256s(bundled_results),
        },
        "publication_ledger": publication_ledger,
        "shared_evidence_identity": {
            "benchmark_release_id": protocol.payload["benchmark_release_id"],
            "release_lock_sha256": release_lock_sha256,
            "runtime_fingerprint_schema_version": "superwing-runtime-fingerprint-v2",
            "runtime_fingerprint_sha256": runtime_fingerprint_sha256,
        },
        "run_partition": {
            "mode": (
                "single_authorized_run"
                if len(run_ids) == 1 else "explicit_interruption_recovery"
            ),
            "interruption_recovery_explicitly_acknowledged": (
                len(run_ids) > 1 and allow_interruption_recovery is True
            ),
            "n_authorized_runs": len(run_ids),
            "runs": run_rows,
        },
        "summary": {
            "total_manifest_wings": EXPECTED_RESULT_COUNT,
            "official_result_count": len(rows),
            "all_manifest_wings_have_exactly_one_result": True,
            "admissibility_counts": _counter(
                ("pass", "fail", "undetermined"), admissibilities
            ),
            "profile_status_counts": _counter(
                ("complete", "incomplete"), profiles
            ),
            "score_eligible_count": eligible,
            "score_ineligible_count": len(rows) - eligible,
            "manifest_score_population_count": score_population,
            "manifest_score_population_eligible_count": score_population_eligible,
            "manifest_score_population_ineligible_count": (
                score_population - score_population_eligible
            ),
        },
        "wings": rows,
        "benchmark_score_aggregation": {
            "performed": False,
            "scope": "per-wing eligibility only",
        },
    }
    envelope = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "hash_semantics": HASH_SEMANTICS,
        "completion_record_sha256": protocol_sha256(record),
        "completion_record": record,
    }
    validate_completion_envelope(envelope)
    return envelope


def _verify_envelope_evidence_files(
    envelope: Mapping[str, Any], evidence_bundle_root: Path
) -> None:
    """Reopen the exact bundle inventory and reproduce the completion record."""
    supplied_root = Path(evidence_bundle_root)
    if supplied_root.is_symlink():
        raise BenchmarkCompletionError("completion evidence-bundle root cannot be a symlink")
    root = supplied_root.resolve()
    expected_root_entries = {
        COMPLETION_RECORD_FILENAME,
        RESULTS_DIRECTORY,
        ARTIFACT_STORE.split("/", 1)[0],
        PUBLICATION_LEDGER_DIRECTORY,
        PUBLICATION_INVENTORY_FILENAME,
    }
    if (
        not root.is_dir()
        or {path.name for path in root.iterdir()} != expected_root_entries
    ):
        raise BenchmarkCompletionError("completion evidence-bundle root layout changed")
    envelope_path = root / COMPLETION_RECORD_FILENAME
    if not envelope_path.is_file() or envelope_path.is_symlink():
        raise BenchmarkCompletionError("completion bundle record is not a regular local file")
    stored_envelope, _raw = _strict_json_object(envelope_path)
    if canonical_json_bytes(stored_envelope) != canonical_json_bytes(envelope):
        raise BenchmarkCompletionError(
            "completion bundle record differs from the reviewed envelope"
        )

    record = envelope["completion_record"]
    result_names = [f"{row['wing_id']}.json" for row in record["wings"]]
    results_directory = _resolve_bundle_path(root, RESULTS_DIRECTORY, "results directory")
    if (
        not results_directory.is_dir()
        or results_directory.is_symlink()
        or sorted(path.name for path in results_directory.iterdir())
        != sorted(result_names)
        or any(
            not path.is_file() or path.is_symlink()
            for path in results_directory.iterdir()
        )
    ):
        raise BenchmarkCompletionError("completion result-bundle inventory changed")

    artifacts_parent_name, artifact_store_name = ARTIFACT_STORE.split("/", 1)
    artifacts_parent = _resolve_bundle_path(root, artifacts_parent_name, "artifacts directory")
    if (
        not artifacts_parent.is_dir()
        or artifacts_parent.is_symlink()
        or sorted(path.name for path in artifacts_parent.iterdir()) != [artifact_store_name]
    ):
        raise BenchmarkCompletionError("completion artifact-bundle layout changed")
    artifact_store = _resolve_bundle_path(root, ARTIFACT_STORE, "artifact store")
    if not artifact_store.is_dir() or artifact_store.is_symlink():
        raise BenchmarkCompletionError("completion artifact store is not a regular directory")
    expected_artifacts = record["evidence_bundle"]["artifact_sha256s"]
    actual_artifacts: list[str] = []
    for path in artifact_store.iterdir():
        if (
            not path.is_file()
            or path.is_symlink()
            or not _full_sha256(path.name)
            or sha256_file(path) != path.name
        ):
            raise BenchmarkCompletionError(
                f"completion artifact store contains an invalid entry: {path.name}"
            )
        actual_artifacts.append(path.name)
    if sorted(actual_artifacts) != expected_artifacts:
        raise BenchmarkCompletionError("completion artifact-store inventory changed")

    manifest_value = record["benchmark_manifest"]["path"]
    manifest_path = Path(str(manifest_value))
    if not manifest_path.is_absolute():
        manifest_path = REPO_ROOT / manifest_path
    (
        manifest,
        manifest_sha256,
        manifest_file_sha256,
        protocol,
        protocol_file_sha256,
    ) = load_completion_context(manifest_path)

    for row in record["wings"]:
        evidence = row["result_evidence"]
        result_path = _resolve_bundle_path(
            root, evidence["path"], "completion result evidence"
        )
        if not result_path.is_file() or result_path.is_symlink():
            raise BenchmarkCompletionError(
                f"completion result evidence is missing: {result_path}"
            )
        raw = result_path.read_bytes()
        parsed = _parse_json_object_bytes(raw, result_path)
        if (
            len(raw) != evidence["size_bytes"]
            or hashlib.sha256(raw).hexdigest() != evidence["file_sha256"]
            or protocol_sha256(parsed) != evidence["canonical_sha256"]
        ):
            raise BenchmarkCompletionError(
                f"completion result evidence changed: {result_path}"
            )

    rebuilt = build_completion_envelope(
        manifest=manifest,
        manifest_path=manifest_path,
        manifest_sha256=manifest_sha256,
        manifest_file_sha256=manifest_file_sha256,
        protocol=protocol,
        protocol_file_sha256=protocol_file_sha256,
        evidence_bundle_root=root,
        allow_interruption_recovery=(
            record["run_partition"]["mode"] == "explicit_interruption_recovery"
        ),
        result_validator=validate_result,
    )
    if canonical_json_bytes(rebuilt) != canonical_json_bytes(envelope):
        raise BenchmarkCompletionError(
            "completion envelope does not reproduce from its bundled evidence"
        )


def _exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise BenchmarkCompletionError(f"{label} structure changed")
    return value


def validate_completion_envelope(
    envelope: Mapping[str, Any],
    *,
    verify_evidence_files: bool = False,
    evidence_bundle_root: Path | None = None,
) -> None:
    """Validate the deterministic record's structure and internal arithmetic."""
    _exact_keys(envelope, {
        "schema_version", "hash_semantics", "completion_record_sha256",
        "completion_record",
    }, "completion envelope")
    if envelope.get("schema_version") != ENVELOPE_SCHEMA_VERSION:
        raise BenchmarkCompletionError("completion envelope schema mismatch")
    if envelope.get("hash_semantics") != HASH_SEMANTICS:
        raise BenchmarkCompletionError("completion hash semantics changed")
    record = envelope.get("completion_record")
    if not isinstance(record, Mapping) or record.get("schema_version") != RECORD_SCHEMA_VERSION:
        raise BenchmarkCompletionError("completion record schema mismatch")
    if protocol_sha256(record) != envelope.get("completion_record_sha256"):
        raise BenchmarkCompletionError("completion record content hash mismatch")
    _exact_keys(record, {
        "schema_version", "completion_id", "completion_status",
        "official_results_verified", "artifact_files_cryptographically_verified",
        "legacy_evidence_used_for_verdict", "execution_authorized_by_this_record",
        "protocol", "benchmark_manifest", "evidence_bundle", "shared_evidence_identity",
        "publication_ledger", "run_partition", "summary", "wings",
        "benchmark_score_aggregation",
    }, "completion record")
    if (
        record.get("completion_status") != "complete"
        or record.get("official_results_verified") is not True
        or record.get("artifact_files_cryptographically_verified") is not True
        or record.get("legacy_evidence_used_for_verdict") is not False
        or record.get("execution_authorized_by_this_record") is not False
    ):
        raise BenchmarkCompletionError("completion record trust flags are inconsistent")

    protocol_row = _exact_keys(record.get("protocol"), {
        "path", "protocol_id", "protocol_status", "canonical_sha256", "file_sha256",
    }, "completion protocol")
    if (
        not isinstance(protocol_row.get("path"), str)
        or not isinstance(protocol_row.get("protocol_id"), str)
        or protocol_row.get("protocol_status") != "frozen"
        or not _full_sha256(protocol_row.get("canonical_sha256"))
        or not _full_sha256(protocol_row.get("file_sha256"))
    ):
        raise BenchmarkCompletionError("completion protocol identity is incomplete")
    manifest_row = _exact_keys(record.get("benchmark_manifest"), {
        "path", "manifest_id", "schema_version", "manifest_status",
        "official_benchmark", "canonical_sha256", "file_sha256", "total_wings",
        "publication_contract",
    }, "completion manifest")
    if (
        not isinstance(manifest_row.get("path"), str)
        or not isinstance(manifest_row.get("manifest_id"), str)
        or manifest_row.get("schema_version") != EXPECTED_MANIFEST_SCHEMA
        or manifest_row.get("manifest_status") != "frozen"
        or manifest_row.get("official_benchmark") is not True
        or manifest_row.get("total_wings") != EXPECTED_RESULT_COUNT
        or not _full_sha256(manifest_row.get("canonical_sha256"))
        or not _full_sha256(manifest_row.get("file_sha256"))
    ):
        raise BenchmarkCompletionError("completion manifest identity is incomplete")
    publication_contract = manifest_row.get("publication_contract")
    if not isinstance(publication_contract, Mapping):
        raise BenchmarkCompletionError("completion manifest publication contract is missing")
    bundle = _exact_keys(record.get("evidence_bundle"), {
        "schema_version", "results_directory", "artifact_store",
        "publication_ledger_directory", "publication_inventory",
        "result_path_semantics", "artifact_path_semantics", "result_file_count",
        "artifact_sha256s",
    }, "completion evidence bundle")
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
        or bundle.get("result_file_count") != EXPECTED_RESULT_COUNT
        or not isinstance(artifact_sha256s, list)
        or artifact_sha256s != sorted(set(artifact_sha256s))
        or any(not _full_sha256(value) for value in artifact_sha256s)
    ):
        raise BenchmarkCompletionError("completion evidence-bundle contract changed")
    identity = _exact_keys(record.get("shared_evidence_identity"), {
        "benchmark_release_id", "release_lock_sha256",
        "runtime_fingerprint_schema_version", "runtime_fingerprint_sha256",
    }, "shared evidence identity")
    if (
        not isinstance(identity.get("benchmark_release_id"), str)
        or not identity["benchmark_release_id"]
        or not _full_sha256(identity.get("release_lock_sha256"))
        or identity.get("runtime_fingerprint_schema_version")
        != "superwing-runtime-fingerprint-v2"
        or not _full_sha256(identity.get("runtime_fingerprint_sha256"))
    ):
        raise BenchmarkCompletionError("shared evidence identity is incomplete")

    wings = record.get("wings")
    if not isinstance(wings, list) or len(wings) != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("completion record does not contain exactly 69 wings")
    wing_ids: list[str] = []
    for row in wings:
        _exact_keys(row, {
            "wing_id", "corpus_class", "manifest_score_population", "method_cohort",
            "admissibility", "profile_status", "score_eligible", "execution",
            "result_evidence",
        }, "completion wing")
        wing_id = row.get("wing_id")
        if not isinstance(wing_id, str) or not wing_id:
            raise BenchmarkCompletionError("completion wing lacks an ID")
        wing_ids.append(wing_id)
        if type(row.get("manifest_score_population")) is not bool:
            raise BenchmarkCompletionError(f"{wing_id} score-population flag changed")
        if row.get("admissibility") not in {"pass", "fail", "undetermined"}:
            raise BenchmarkCompletionError(f"{wing_id} admissibility changed")
        if row.get("profile_status") not in {"complete", "incomplete"}:
            raise BenchmarkCompletionError(f"{wing_id} profile status changed")
        expected_eligible = (
            row.get("admissibility") == "pass"
            and row.get("profile_status") == "complete"
        )
        if type(row.get("score_eligible")) is not bool or row["score_eligible"] is not expected_eligible:
            raise BenchmarkCompletionError(f"{wing_id} score eligibility changed")
        execution = _exact_keys(row.get("execution"), {
            "run_id", "execution_authorization_sha256", "runtime_attestation_sha256",
        }, f"{wing_id} execution")
        if (
            not isinstance(execution.get("run_id"), str)
            or not execution["run_id"]
            or not _full_sha256(execution.get("execution_authorization_sha256"))
            or not _full_sha256(execution.get("runtime_attestation_sha256"))
        ):
            raise BenchmarkCompletionError(f"{wing_id} execution identity is incomplete")
        evidence = _exact_keys(row.get("result_evidence"), {
            "path", "file_sha256", "canonical_sha256", "size_bytes",
        }, f"{wing_id} result evidence")
        if (
            evidence.get("path") != f"{RESULTS_DIRECTORY}/{wing_id}.json"
            or not _full_sha256(evidence.get("file_sha256"))
            or not _full_sha256(evidence.get("canonical_sha256"))
            or isinstance(evidence.get("size_bytes"), bool)
            or not isinstance(evidence.get("size_bytes"), int)
            or evidence["size_bytes"] <= 0
        ):
            raise BenchmarkCompletionError(f"{wing_id} result evidence is incomplete")
    if len(set(wing_ids)) != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("completion record contains duplicate wing IDs")
    try:
        normalized_publication_contract = validate_manifest_publication_contract(
            {"publication_contract": publication_contract},
            ordered_manifest_wing_ids=wing_ids,
            expected_recovery_allowed=True,
        )
        validate_publication_ledger_record(
            record.get("publication_ledger"),
            ordered_manifest_wing_ids=wing_ids,
            result_evidence_by_wing={
                row["wing_id"]: row["result_evidence"] for row in wings
            },
            expected_campaign_publication_id=normalized_publication_contract[
                "campaign_publication_id"
            ],
            expected_publication_bucket=normalized_publication_contract[
                "publication_bucket"
            ],
            allow_recovery=True,
        )
    except CampaignPublicationError as exc:
        raise BenchmarkCompletionError(
            f"completion publication ledger is invalid: {exc}"
        ) from exc

    run_partition = _exact_keys(record.get("run_partition"), {
        "mode", "interruption_recovery_explicitly_acknowledged",
        "n_authorized_runs", "runs",
    }, "run partition")
    runs = run_partition.get("runs")
    if not isinstance(runs, list) or not runs:
        raise BenchmarkCompletionError("completion record lacks authorized runs")
    expected_mode = "single_authorized_run" if len(runs) == 1 else "explicit_interruption_recovery"
    if (
        run_partition.get("mode") != expected_mode
        or run_partition.get("n_authorized_runs") != len(runs)
        or run_partition.get("interruption_recovery_explicitly_acknowledged")
        is not (len(runs) > 1)
    ):
        raise BenchmarkCompletionError("completion run-partition decision is inconsistent")
    run_ids: set[str] = set()
    roster: list[str] = []
    cursor = 0
    execution_by_wing = {row["wing_id"]: row["execution"] for row in wings}
    for run in runs:
        _exact_keys(run, {
            "run_id", "execution_authorization_id", "execution_authorization_sha256",
            "runtime_attestation_sha256", "authorized_wing_ids", "wing_ids",
        }, "authorized run")
        run_id = run.get("run_id")
        if not isinstance(run_id, str) or not run_id or run_id in run_ids:
            raise BenchmarkCompletionError("completion record has an invalid run ID")
        run_ids.add(run_id)
        if (
            not isinstance(run.get("execution_authorization_id"), str)
            or not run["execution_authorization_id"]
            or not _full_sha256(run.get("execution_authorization_sha256"))
            or not _full_sha256(run.get("runtime_attestation_sha256"))
            or not isinstance(run.get("authorized_wing_ids"), list)
            or not isinstance(run.get("wing_ids"), list)
        ):
            raise BenchmarkCompletionError(f"authorized run {run_id} is incomplete")
        remaining_suffix = wing_ids[cursor:]
        if run["authorized_wing_ids"] != remaining_suffix:
            raise BenchmarkCompletionError(
                f"run {run_id} authorization does not cover the remaining suffix"
            )
        if (
            not run["wing_ids"]
            or run["wing_ids"]
            != run["authorized_wing_ids"][:len(run["wing_ids"])]
        ):
            raise BenchmarkCompletionError(
                f"run {run_id} violates first-complete sequential-prefix semantics"
            )
        for wing_id in run["wing_ids"]:
            if wing_id not in execution_by_wing:
                raise BenchmarkCompletionError(f"run {run_id} contains an unknown wing")
            execution = execution_by_wing[wing_id]
            if (
                execution["run_id"] != run_id
                or execution["execution_authorization_sha256"]
                != run["execution_authorization_sha256"]
                or execution["runtime_attestation_sha256"]
                != run["runtime_attestation_sha256"]
            ):
                raise BenchmarkCompletionError(f"run {run_id} disagrees with wing {wing_id}")
            roster.append(wing_id)
        cursor += len(run["wing_ids"])
    if roster != wing_ids or cursor != EXPECTED_RESULT_COUNT:
        raise BenchmarkCompletionError("authorized run partition does not cover the exact wing roster")

    summary = _exact_keys(record.get("summary"), {
        "total_manifest_wings", "official_result_count",
        "all_manifest_wings_have_exactly_one_result", "admissibility_counts",
        "profile_status_counts", "score_eligible_count", "score_ineligible_count",
        "manifest_score_population_count", "manifest_score_population_eligible_count",
        "manifest_score_population_ineligible_count",
    }, "completion summary")
    expected_admissibility = _counter(
        ("pass", "fail", "undetermined"),
        [str(row["admissibility"]) for row in wings],
    )
    expected_profiles = _counter(
        ("complete", "incomplete"),
        [str(row["profile_status"]) for row in wings],
    )
    eligible_count = sum(row["score_eligible"] is True for row in wings)
    population_count = sum(row["manifest_score_population"] is True for row in wings)
    population_eligible = sum(
        row["manifest_score_population"] is True and row["score_eligible"] is True
        for row in wings
    )
    if (
        summary.get("total_manifest_wings") != EXPECTED_RESULT_COUNT
        or summary.get("official_result_count") != EXPECTED_RESULT_COUNT
        or summary.get("all_manifest_wings_have_exactly_one_result") is not True
        or summary.get("admissibility_counts") != expected_admissibility
        or summary.get("profile_status_counts") != expected_profiles
        or summary.get("score_eligible_count") != eligible_count
        or summary.get("score_ineligible_count") != EXPECTED_RESULT_COUNT - eligible_count
        or summary.get("manifest_score_population_count") != population_count
        or summary.get("manifest_score_population_eligible_count") != population_eligible
        or summary.get("manifest_score_population_ineligible_count")
        != population_count - population_eligible
    ):
        raise BenchmarkCompletionError("completion summary is internally inconsistent")
    aggregation = _exact_keys(
        record.get("benchmark_score_aggregation"), {"performed", "scope"},
        "benchmark score aggregation",
    )
    if aggregation != {"performed": False, "scope": "per-wing eligibility only"}:
        raise BenchmarkCompletionError("completion record must not invent an overall score")
    if verify_evidence_files:
        if evidence_bundle_root is None:
            raise BenchmarkCompletionError(
                "full completion validation requires evidence_bundle_root"
            )
        _verify_envelope_evidence_files(envelope, evidence_bundle_root)


def render_envelope(envelope: Mapping[str, Any]) -> str:
    return json.dumps(envelope, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_immutable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable completion record: {path}")
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


def materialize_completion_bundle(
    *,
    bundle_root: Path,
    manifest: Mapping[str, Any],
    manifest_path: Path,
    manifest_sha256: str,
    manifest_file_sha256: str,
    protocol: GradingProtocol,
    protocol_file_sha256: str,
    campaign_export_root: Path,
    allow_interruption_recovery: bool = False,
) -> dict[str, Any]:
    """Copy all verified results and artifacts into one immutable local bundle."""
    bundle_root = Path(bundle_root)
    if bundle_root.exists() or bundle_root.is_symlink():
        raise FileExistsError(f"refusing to overwrite evidence bundle: {bundle_root}")
    bundle_root = bundle_root.resolve()
    bundle_root.parent.mkdir(parents=True, exist_ok=True)
    temporary = bundle_root.with_name(f".{bundle_root.name}.{uuid.uuid4().hex}.tmp")
    temporary.mkdir()
    try:
        wings = _validate_frozen_official_context(manifest, protocol)
        wings_by_id = {str(wing["wing_id"]): wing for wing in wings}
        expected_ids = [str(wing["wing_id"]) for wing in wings]
        try:
            publication_contract = validate_manifest_publication_contract(
                manifest,
                ordered_manifest_wing_ids=expected_ids,
                expected_recovery_allowed=True,
            )
            source_publication_ledger, published_documents = (
                load_campaign_publication_export(
                    campaign_export_root,
                    ordered_manifest_wing_ids=expected_ids,
                    expected_campaign_publication_id=publication_contract[
                        "campaign_publication_id"
                    ],
                    expected_publication_bucket=publication_contract[
                        "publication_bucket"
                    ],
                    allow_recovery=True,
                    require_exact_root=False,
                )
            )
        except CampaignPublicationError as exc:
            raise BenchmarkCompletionError(
                f"campaign publication export failed: {exc}"
            ) from exc
        source_results: dict[str, tuple[Path, dict[str, Any], bytes]] = {}
        for wing_id, (result, raw) in published_documents.items():
            source_results[wing_id] = (
                Path(campaign_export_root).resolve(), result, raw
            )
        source_artifact_store = _campaign_artifact_store(
            campaign_export_root,
            [published_documents[wing_id][0] for wing_id in expected_ids],
        )

        results_directory = temporary / RESULTS_DIRECTORY
        artifact_store = temporary / ARTIFACT_STORE
        publication_directory = temporary / PUBLICATION_LEDGER_DIRECTORY
        results_directory.mkdir()
        artifact_store.mkdir(parents=True)
        publication_directory.mkdir()
        source_export_root = Path(campaign_export_root).resolve()
        for source in sorted(
            (source_export_root / PUBLICATION_LEDGER_DIRECTORY).iterdir(),
            key=lambda path: path.name,
        ):
            with (publication_directory / source.name).open("xb") as stream:
                stream.write(source.read_bytes())
                stream.flush()
                os.fsync(stream.fileno())
        inventory_source = source_export_root / PUBLICATION_INVENTORY_FILENAME
        with (temporary / PUBLICATION_INVENTORY_FILENAME).open("xb") as stream:
            stream.write(inventory_source.read_bytes())
            stream.flush()
            os.fsync(stream.fileno())
        for wing_id in expected_ids:
            _source_root, result, raw = source_results[wing_id]
            _validated_identity(
                result=result,
                wing=wings_by_id[wing_id],
                manifest=manifest,
                manifest_sha256=manifest_sha256,
                protocol=protocol,
                result_validator=validate_result,
                artifact_store=source_artifact_store,
            )
            _aggregate_summary(result, wing_id)
            bundled_path = results_directory / f"{wing_id}.json"
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
                    raise BenchmarkCompletionError(
                        f"{wing_id} artifact failed while bundling: {exc}"
                    ) from exc
                if artifact_bytes is None:
                    raise BenchmarkCompletionError(
                        f"{wing_id} present artifact produced no bytes"
                    )
                digest = str(record["sha256"])
                destination = artifact_store / digest
                if destination.exists():
                    if destination.is_symlink() or destination.read_bytes() != artifact_bytes:
                        raise BenchmarkCompletionError(
                            "content-addressed artifact collision while bundling"
                        )
                    continue
                with destination.open("xb") as stream:
                    stream.write(artifact_bytes)
                    stream.flush()
                    os.fsync(stream.fileno())

        envelope = build_completion_envelope(
            manifest=manifest,
            manifest_path=manifest_path,
            manifest_sha256=manifest_sha256,
            manifest_file_sha256=manifest_file_sha256,
            protocol=protocol,
            protocol_file_sha256=protocol_file_sha256,
            evidence_bundle_root=temporary,
            allow_interruption_recovery=allow_interruption_recovery,
            result_validator=validate_result,
        )
        if canonical_json_bytes(
            envelope["completion_record"]["publication_ledger"]
        ) != canonical_json_bytes(source_publication_ledger):
            raise BenchmarkCompletionError(
                "campaign publication export changed while the bundle was materialized"
            )
        _write_immutable(
            temporary / COMPLETION_RECORD_FILENAME,
            render_envelope(envelope),
        )
        validate_completion_envelope(
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


def load_completion_bundle(bundle_root: Path) -> dict[str, Any]:
    """Load and fully replay one immutable, relocatable completion bundle."""
    supplied_root = Path(bundle_root)
    if supplied_root.is_symlink():
        raise BenchmarkCompletionError("completion evidence-bundle root cannot be a symlink")
    root = supplied_root.resolve()
    record_path = root / COMPLETION_RECORD_FILENAME
    if (
        not root.is_dir()
        or not record_path.is_file()
        or record_path.is_symlink()
    ):
        raise BenchmarkCompletionError(
            "completion bundle record is not a regular local file"
        )
    envelope, _raw = _strict_json_object(record_path)
    validate_completion_envelope(
        envelope,
        verify_evidence_files=True,
        evidence_bundle_root=root,
    )
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--campaign-dir",
        type=Path,
        help="exact exported WORM ledger, inventory, and artifact campaign root",
    )
    parser.add_argument(
        "--allow-interruption-recovery",
        action="store_true",
        help="explicitly acknowledge that the 69 results span authorized run IDs",
    )
    parser.add_argument(
        "--bundle-dir",
        type=Path,
        required=True,
        help="immutable relocatable completion-bundle directory",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="fully replay and verify an existing completion bundle",
    )
    args = parser.parse_args(argv)

    try:
        if args.check:
            if (
                args.manifest is not None
                or args.campaign_dir is not None
                or args.allow_interruption_recovery
            ):
                raise BenchmarkCompletionError(
                    "--check reads the bundle and does not accept source inputs"
                )
            envelope = load_completion_bundle(args.bundle_dir)
        else:
            if args.manifest is None or args.campaign_dir is None:
                raise BenchmarkCompletionError(
                    "bundle creation requires --manifest and --campaign-dir"
                )
            manifest, manifest_sha, manifest_file_sha, protocol, protocol_file_sha = (
                load_completion_context(args.manifest)
            )
            envelope = materialize_completion_bundle(
                bundle_root=args.bundle_dir,
                manifest=manifest,
                manifest_path=args.manifest,
                manifest_sha256=manifest_sha,
                manifest_file_sha256=manifest_file_sha,
                protocol=protocol,
                protocol_file_sha256=protocol_file_sha,
                campaign_export_root=args.campaign_dir,
                allow_interruption_recovery=args.allow_interruption_recovery,
            )
    except (BenchmarkCompletionError, FileExistsError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    digest = envelope["completion_record_sha256"]
    print(f"Official 69-wing evidence complete: {digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
