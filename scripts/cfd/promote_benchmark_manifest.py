#!/usr/bin/env python3
"""Promote the qualified 69-wing inventory into an official manifest.

Promotion is deliberately a pure, deterministic metadata operation.  It never
runs CFD, changes a wing, or authorizes execution.  The command succeeds only
after the qualification envelope can be reproduced from all of its evidence
files and a separate frozen protocol is proven scientifically identical to the
qualified candidate.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.build_benchmark_regrade_manifest import (  # noqa: E402
    validate_manifest as validate_candidate_manifest,
)
from scripts.cfd.evaluate_protocol_qualification import (  # noqa: E402
    QualificationError,
    validate_qualification_envelope,
)
from scripts.cfd.grading_protocol import (  # noqa: E402
    ProtocolError,
    protocol_sha256,
    sha256_file,
    validate_protocol,
)


SOURCE_CANDIDATE_PATH = "configs/cfd/benchmark_redevelopment_v1.json"
REVIEW_TEMPLATE_PATH = "configs/cfd/benchmark_release_review_template_v1.json"
REVIEW_SCHEMA_VERSION = "wingbench-cfd-benchmark-release-review-v1"
RELEASE_SCHEMA_VERSION = "wingbench-cfd-benchmark-release-metadata-v1"
MANIFEST_SCHEMA_VERSION = "benchmark-redevelopment-manifest-v1"
QUALIFICATION_ENVELOPE_SCHEMA = "wingbench-cfd-qualification-record-envelope-v1"
HASH_SEMANTICS = "sha256(canonical compact sorted-key JSON)"
EXPECTED_WING_COUNT = 69
ALLOWED_MANIFEST_PROMOTION_FIELDS = frozenset({
    "manifest_id",
    "manifest_status",
    "official_benchmark",
    "protocol_reference",
    "release",
})
ALLOWED_PROTOCOL_PROMOTION_FIELDS = (
    "benchmark_release_id",
    "official_grading_enabled",
    "promotion",
    "protocol_id",
    "protocol_revision",
    "protocol_status",
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_SAFE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


class PromotionError(ValueError):
    """Promotion inputs do not prove one reviewed official release."""


def canonical_json_bytes(payload: Any) -> bytes:
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"value is not canonical JSON: {exc}") from exc


def canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise PromotionError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                PromotionError(f"non-finite JSON constant {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, PromotionError) as exc:
        raise PromotionError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise PromotionError(f"{label} must be a JSON object")
    canonical_json_bytes(payload)
    return payload


def _repo_file(
    path: str | os.PathLike[str],
    repo_root: Path,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(path, (str, os.PathLike)):
        raise PromotionError(f"{label} path must be a string")
    candidate = Path(path)
    resolved = (
        (repo_root / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    try:
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise PromotionError(f"{label} must be inside the repository") from exc
    if not resolved.is_file():
        raise PromotionError(f"{label} is not a regular file: {relative}")
    return resolved, relative


def _repo_directory(
    path: str | os.PathLike[str],
    repo_root: Path,
    label: str,
) -> tuple[Path, str]:
    if not isinstance(path, (str, os.PathLike)):
        raise PromotionError(f"{label} path must be a string")
    candidate = Path(path)
    resolved = (
        (repo_root / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    try:
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise PromotionError(f"{label} must be inside the repository") from exc
    if not resolved.is_dir():
        raise PromotionError(f"{label} is not a directory: {relative}")
    return resolved, relative


def _full_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _require_safe_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or _SAFE_ID_RE.fullmatch(value) is None:
        raise PromotionError(f"{label} must be a non-empty portable identifier")
    return value


def _reviewed_at(value: Any) -> str:
    if not isinstance(value, str):
        raise PromotionError("reviewed_at must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PromotionError("reviewed_at must be a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        raise PromotionError("reviewed_at must use UTC")
    return value


def validate_review_metadata(payload: Mapping[str, Any]) -> dict[str, Any]:
    expected = {
        "schema_version",
        "release_id",
        "official_manifest_id",
        "review_status",
        "reviewed_by",
        "reviewed_at",
        "review_note",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise PromotionError("release review metadata has unexpected or missing fields")
    if payload.get("schema_version") != REVIEW_SCHEMA_VERSION:
        raise PromotionError("unsupported release review schema")
    if payload.get("review_status") != "approved":
        raise PromotionError("release review must be explicitly approved")
    release_id = _require_safe_id(payload.get("release_id"), "release_id")
    manifest_id = _require_safe_id(
        payload.get("official_manifest_id"), "official_manifest_id"
    )
    reviewed_by = payload.get("reviewed_by")
    review_note = payload.get("review_note")
    if not isinstance(reviewed_by, str) or not reviewed_by.strip():
        raise PromotionError("reviewed_by must identify the human reviewer")
    if not isinstance(review_note, str) or not review_note.strip():
        raise PromotionError("review_note must document the manual freeze review")
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "release_id": release_id,
        "official_manifest_id": manifest_id,
        "review_status": "approved",
        "reviewed_by": reviewed_by,
        "reviewed_at": _reviewed_at(payload.get("reviewed_at")),
        "review_note": review_note,
    }


def _validate_source_candidate(
    payload: dict[str, Any],
    repo_root: Path,
    *,
    verify_files: bool,
) -> None:
    try:
        errors = validate_candidate_manifest(payload, repo_root, verify_files=verify_files)
    except (KeyError, TypeError, ValueError) as exc:
        raise PromotionError(f"source candidate manifest is malformed: {exc}") from exc
    if errors:
        raise PromotionError("source candidate manifest is invalid: " + "; ".join(errors))
    if (
        payload.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or payload.get("manifest_status") != "qualification_required"
        or payload.get("official_benchmark") is not False
        or payload.get("execution_authorized") is not False
        or payload.get("official_labels_or_scores_created") is not False
    ):
        raise PromotionError("source candidate is not the non-official qualification inventory")
    wings = payload.get("wings")
    if not isinstance(wings, list) or len(wings) != EXPECTED_WING_COUNT:
        raise PromotionError(f"source candidate must contain exactly {EXPECTED_WING_COUNT} wings")


def _validated_qualification(
    envelope: dict[str, Any],
    *,
    envelope_path: str,
    envelope_file_sha256: str,
    evidence_bundle_root: Path,
    evidence_bundle_relative: str,
    source_protocol_reference: Mapping[str, Any],
    repo_root: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    # This is intentionally unconditional.  Promotion must reopen every result
    # and raw artifact; validating only the envelope's self-hash is insufficient.
    try:
        validate_qualification_envelope(
            envelope,
            verify_evidence_files=True,
            evidence_bundle_root=evidence_bundle_root,
        )
    except QualificationError as exc:
        raise PromotionError(f"qualification envelope failed full verification: {exc}") from exc

    if envelope.get("schema_version") != QUALIFICATION_ENVELOPE_SCHEMA:
        raise PromotionError("unsupported qualification envelope schema")
    record = envelope.get("qualification_record")
    if not isinstance(record, dict):
        raise PromotionError("qualification envelope lacks its record")
    record_sha = envelope.get("qualification_record_sha256")
    decision = record.get("campaign_decision") or {}
    promotion = record.get("promotion") or {}
    evidence_bundle = record.get("evidence_bundle")
    if (
        not _full_sha256(record_sha)
        or protocol_sha256(record) != record_sha
        or record.get("qualification_status") != "passed"
        or record.get("artifact_files_cryptographically_verified") is not True
        or decision.get("all_gate_controls_passed") is not True
        or promotion.get("eligible_for_separate_manual_freeze_review") is not True
        or promotion.get("automatic_promotion_allowed") is not False
        or promotion.get("promotion_performed") is not False
        or promotion.get("official_authorization_granted") is not False
    ):
        raise PromotionError("qualification record is not eligible for manual promotion")
    if not isinstance(evidence_bundle, dict):
        raise PromotionError("qualification record lacks its evidence-bundle contract")

    candidate = record.get("candidate_protocol") or {}
    qualification_manifest = record.get("qualification_manifest") or {}
    source_sha = source_protocol_reference.get("sha256")
    source_path = source_protocol_reference.get("path")
    if (
        candidate.get("canonical_sha256") != source_sha
        or candidate.get("path") != source_path
        or candidate.get("protocol_status") != "qualification_required"
        or candidate.get("official_grading_enabled") is not False
    ):
        raise PromotionError("qualification did not evaluate the source manifest's candidate protocol")

    manifest_path_value = qualification_manifest.get("path")
    if not isinstance(manifest_path_value, str):
        raise PromotionError("qualification record lacks its manifest path")
    manifest_path, manifest_relative = _repo_file(
        manifest_path_value, repo_root, "qualification manifest"
    )
    manifest_payload = _load_json_object(manifest_path, "qualification manifest")
    if (
        qualification_manifest.get("manifest_id") != manifest_payload.get("manifest_id")
        or qualification_manifest.get("canonical_sha256")
        != canonical_json_sha256(manifest_payload)
        or qualification_manifest.get("file_sha256") != sha256_file(manifest_path)
    ):
        raise PromotionError("qualification record does not bind the qualification manifest bytes")
    snapshot = manifest_payload.get("protocol_snapshot")
    if (
        not isinstance(snapshot, dict)
        or canonical_json_sha256(snapshot) != candidate.get("canonical_sha256")
        or (manifest_payload.get("protocol_reference") or {}).get("sha256")
        != candidate.get("canonical_sha256")
    ):
        raise PromotionError("qualification manifest does not bind its candidate protocol snapshot")

    identity = {
        "envelope_path": envelope_path,
        "envelope_canonical_sha256": canonical_json_sha256(envelope),
        "envelope_file_sha256": envelope_file_sha256,
        "evidence_bundle_root": evidence_bundle_relative,
        "evidence_bundle_contract_sha256": canonical_json_sha256(evidence_bundle),
        "qualification_record_sha256": record_sha,
        "qualification_id": record.get("qualification_id"),
        "qualification_manifest_path": manifest_relative,
        "qualification_manifest_id": qualification_manifest.get("manifest_id"),
        "qualification_manifest_sha256": qualification_manifest.get("canonical_sha256"),
        "qualified_candidate_protocol_path": candidate.get("path"),
        "qualified_candidate_protocol_sha256": candidate.get("canonical_sha256"),
    }
    if any(value is None for value in identity.values()):
        raise PromotionError("qualification identity is incomplete")
    return identity, snapshot, record


def _validated_frozen_protocol(
    payload: dict[str, Any],
    *,
    path: str,
    file_sha256: str,
    candidate_snapshot: dict[str, Any],
    qualification: Mapping[str, Any],
    release_id: str,
    verify_files: bool,
) -> dict[str, Any]:
    try:
        validate_protocol(payload, verify_files=verify_files)
        validate_protocol(candidate_snapshot, verify_files=verify_files)
    except ProtocolError as exc:
        raise PromotionError(f"protocol contract validation failed: {exc}") from exc
    candidate_path = qualification["qualified_candidate_protocol_path"]
    if path == candidate_path:
        raise PromotionError("official protocol must be a separate file from the qualified candidate")
    if (
        payload.get("protocol_status") != "frozen"
        or payload.get("official_grading_enabled") is not True
        or payload.get("benchmark_release_id") != release_id
    ):
        raise PromotionError("official protocol must be frozen and enabled for the reviewed release")
    if payload.get("protocol_id") == candidate_snapshot.get("protocol_id"):
        raise PromotionError("official protocol must have a distinct promoted protocol_id")

    promotion = payload.get("promotion")
    expected_promotion = {
        "source_candidate_protocol_sha256": qualification[
            "qualified_candidate_protocol_sha256"
        ],
        "qualification_record_sha256": qualification["qualification_record_sha256"],
        "qualification_manifest_sha256": qualification["qualification_manifest_sha256"],
    }
    if promotion != expected_promotion:
        raise PromotionError("official protocol promotion metadata is not exactly qualification-bound")

    allowed = set(ALLOWED_PROTOCOL_PROMOTION_FIELDS)
    candidate_scientific = {
        key: value for key, value in candidate_snapshot.items() if key not in allowed
    }
    official_scientific = {
        key: value for key, value in payload.items() if key not in allowed
    }
    if canonical_json_bytes(candidate_scientific) != canonical_json_bytes(official_scientific):
        raise PromotionError("official protocol is not scientifically identical to the qualified candidate")
    return {
        "path": path,
        "protocol_id": payload["protocol_id"],
        "canonical_sha256": canonical_json_sha256(payload),
        "file_sha256": file_sha256,
        "status": "frozen",
        "official": True,
        "qualified": True,
        "benchmark_release_id": release_id,
        "qualified_candidate_sha256": qualification[
            "qualified_candidate_protocol_sha256"
        ],
    }


def _release_metadata(
    *,
    review: Mapping[str, Any],
    review_path: str,
    review_file_sha256: str,
    source_path: str,
    source: Mapping[str, Any],
    source_file_sha256: str,
    qualification: Mapping[str, Any],
    official_protocol: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": RELEASE_SCHEMA_VERSION,
        "release_id": review["release_id"],
        "review_status": "approved",
        "reviewed_by": review["reviewed_by"],
        "reviewed_at": review["reviewed_at"],
        "review_note": review["review_note"],
        "release_review": {
            "path": review_path,
            "schema_version": review["schema_version"],
            "canonical_sha256": canonical_json_sha256(review),
            "file_sha256": review_file_sha256,
        },
        "source_candidate_manifest": {
            "path": source_path,
            "schema_version": source["schema_version"],
            "manifest_id": source["manifest_id"],
            "canonical_sha256": canonical_json_sha256(source),
            "file_sha256": source_file_sha256,
            "wings_canonical_sha256": canonical_json_sha256(source["wings"]),
        },
        "qualification": copy.deepcopy(dict(qualification)),
        "protocol_promotion": {
            "qualified_candidate_protocol_path": qualification[
                "qualified_candidate_protocol_path"
            ],
            "qualified_candidate_protocol_sha256": qualification[
                "qualified_candidate_protocol_sha256"
            ],
            "official_protocol_path": official_protocol["path"],
            "official_protocol_sha256": official_protocol["canonical_sha256"],
            "scientifically_identical": True,
            "allowed_promotion_fields": list(ALLOWED_PROTOCOL_PROMOTION_FIELDS),
        },
    }


def build_official_manifest(
    *,
    qualification_envelope_path: str | os.PathLike[str],
    qualification_evidence_bundle_root: str | os.PathLike[str],
    official_protocol_path: str | os.PathLike[str],
    review_metadata_path: str | os.PathLike[str],
    source_candidate_path: str | os.PathLike[str] = SOURCE_CANDIDATE_PATH,
    repo_root: Path = REPO_ROOT,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Build one official manifest without writing it or authorizing compute.

    ``verify_files=False`` exists for isolated synthetic-fixture tests.  The CLI
    never exposes that switch, and qualification evidence replay is mandatory
    in both modes.
    """
    source_path, source_relative = _repo_file(
        source_candidate_path, repo_root, "source candidate manifest"
    )
    if source_relative != SOURCE_CANDIDATE_PATH:
        raise PromotionError(
            f"source candidate must be the frozen inventory at {SOURCE_CANDIDATE_PATH}"
        )
    envelope_path, envelope_relative = _repo_file(
        qualification_envelope_path, repo_root, "qualification envelope"
    )
    evidence_bundle_root, evidence_bundle_relative = _repo_directory(
        qualification_evidence_bundle_root,
        repo_root,
        "qualification evidence bundle",
    )
    protocol_path, protocol_relative = _repo_file(
        official_protocol_path, repo_root, "official protocol"
    )
    review_path, review_relative = _repo_file(
        review_metadata_path, repo_root, "release review"
    )

    source = _load_json_object(source_path, "source candidate manifest")
    _validate_source_candidate(source, repo_root, verify_files=verify_files)
    review = validate_review_metadata(_load_json_object(review_path, "release review"))
    envelope = _load_json_object(envelope_path, "qualification envelope")
    qualification, candidate_snapshot, _ = _validated_qualification(
        envelope,
        envelope_path=envelope_relative,
        envelope_file_sha256=sha256_file(envelope_path),
        evidence_bundle_root=evidence_bundle_root,
        evidence_bundle_relative=evidence_bundle_relative,
        source_protocol_reference=source.get("protocol_reference") or {},
        repo_root=repo_root,
    )
    protocol_payload = _load_json_object(protocol_path, "official protocol")
    official_protocol = _validated_frozen_protocol(
        protocol_payload,
        path=protocol_relative,
        file_sha256=sha256_file(protocol_path),
        candidate_snapshot=candidate_snapshot,
        qualification=qualification,
        release_id=review["release_id"],
        verify_files=verify_files,
    )

    manifest = copy.deepcopy(source)
    manifest["manifest_id"] = review["official_manifest_id"]
    manifest["manifest_status"] = "frozen"
    manifest["official_benchmark"] = True
    manifest["protocol_reference"] = {
        "path": official_protocol["path"],
        "sha256": official_protocol["canonical_sha256"],
        "file_sha256": official_protocol["file_sha256"],
        "hash_semantics": HASH_SEMANTICS,
        "status": "frozen",
        "official": True,
        "qualified": True,
        "benchmark_release_id": review["release_id"],
        "qualified_candidate_sha256": official_protocol["qualified_candidate_sha256"],
    }
    manifest["release"] = _release_metadata(
        review=review,
        review_path=review_relative,
        review_file_sha256=sha256_file(review_path),
        source_path=source_relative,
        source=source,
        source_file_sha256=sha256_file(source_path),
        qualification=qualification,
        official_protocol=official_protocol,
    )
    validate_official_manifest(manifest, repo_root=repo_root, verify_files=verify_files)
    return manifest


def validate_official_manifest(
    manifest: Mapping[str, Any],
    *,
    repo_root: Path = REPO_ROOT,
    verify_files: bool = True,
) -> None:
    """Reopen and verify every bound promotion input for an official manifest."""
    if not isinstance(manifest, Mapping):
        raise PromotionError("official manifest must be an object")
    canonical_json_bytes(manifest)
    release = manifest.get("release")
    if (
        not isinstance(release, Mapping)
        or release.get("schema_version") != RELEASE_SCHEMA_VERSION
    ):
        raise PromotionError("official manifest lacks release metadata")
    source_identity = release.get("source_candidate_manifest")
    qualification_identity = release.get("qualification")
    promotion_identity = release.get("protocol_promotion")
    review_identity = release.get("release_review")
    if not all(isinstance(value, Mapping) for value in (
        source_identity, qualification_identity, promotion_identity, review_identity
    )):
        raise PromotionError("official manifest release bindings are incomplete")
    expected_source_identity_keys = {
        "path", "schema_version", "manifest_id", "canonical_sha256",
        "file_sha256", "wings_canonical_sha256",
    }
    if set(source_identity) != expected_source_identity_keys:
        raise PromotionError("source candidate binding has unexpected or missing fields")

    source_path, source_relative = _repo_file(
        source_identity.get("path"), repo_root, "bound source candidate"
    )
    if source_relative != SOURCE_CANDIDATE_PATH:
        raise PromotionError("official manifest binds the wrong source candidate path")
    source = _load_json_object(source_path, "bound source candidate")
    _validate_source_candidate(source, repo_root, verify_files=verify_files)
    if (
        source_identity.get("schema_version") != source.get("schema_version")
        or source_identity.get("manifest_id") != source.get("manifest_id")
        or source_identity.get("canonical_sha256") != canonical_json_sha256(source)
        or source_identity.get("file_sha256") != sha256_file(source_path)
        or source_identity.get("wings_canonical_sha256")
        != canonical_json_sha256(source["wings"])
    ):
        raise PromotionError("source candidate manifest binding changed")

    if set(manifest) != set(source) | {"release"}:
        raise PromotionError("official manifest changed fields outside the release promotion surface")
    for key in set(source) - ALLOWED_MANIFEST_PROMOTION_FIELDS:
        if canonical_json_bytes(manifest.get(key)) != canonical_json_bytes(source.get(key)):
            raise PromotionError(f"official manifest changed non-release field {key!r}")
    if canonical_json_bytes(manifest.get("wings")) != canonical_json_bytes(source.get("wings")):
        raise PromotionError("official wings payload is not canonically identical to all 69 source wings")
    if (
        manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION
        or manifest.get("manifest_id") == source.get("manifest_id")
        or manifest.get("manifest_status") != "frozen"
        or manifest.get("official_benchmark") is not True
        or manifest.get("execution_authorized") is not False
        or manifest.get("official_labels_or_scores_created") is not False
    ):
        raise PromotionError("official manifest status is inconsistent or authorizes execution/results")

    expected_review_identity_keys = {
        "path", "schema_version", "canonical_sha256", "file_sha256",
    }
    if set(review_identity) != expected_review_identity_keys:
        raise PromotionError("release review binding has unexpected or missing fields")
    review_path, _ = _repo_file(
        review_identity.get("path"), repo_root, "bound release review"
    )
    review_payload = _load_json_object(review_path, "bound release review")
    review = validate_review_metadata(review_payload)
    if (
        review_identity.get("schema_version") != review.get("schema_version")
        or review_identity.get("canonical_sha256") != canonical_json_sha256(review)
        or review_identity.get("file_sha256") != sha256_file(review_path)
    ):
        raise PromotionError("release review binding changed")
    copied_review = validate_review_metadata({
        "schema_version": REVIEW_SCHEMA_VERSION,
        "release_id": release.get("release_id"),
        "official_manifest_id": manifest.get("manifest_id"),
        "review_status": release.get("review_status"),
        "reviewed_by": release.get("reviewed_by"),
        "reviewed_at": release.get("reviewed_at"),
        "review_note": release.get("review_note"),
    })
    if canonical_json_bytes(copied_review) != canonical_json_bytes(review):
        raise PromotionError("copied release review metadata changed")
    expected_release_keys = {
        "schema_version", "release_id", "review_status", "reviewed_by",
        "reviewed_at", "review_note", "release_review", "source_candidate_manifest",
        "qualification", "protocol_promotion",
    }
    if set(release) != expected_release_keys:
        raise PromotionError("release metadata has unexpected or missing fields")

    envelope_path, envelope_relative = _repo_file(
        qualification_identity.get("envelope_path"), repo_root, "bound qualification envelope"
    )
    evidence_bundle_root, evidence_bundle_relative = _repo_directory(
        qualification_identity.get("evidence_bundle_root"),
        repo_root,
        "bound qualification evidence bundle",
    )
    envelope = _load_json_object(envelope_path, "bound qualification envelope")
    qualification, candidate_snapshot, _ = _validated_qualification(
        envelope,
        envelope_path=envelope_relative,
        envelope_file_sha256=sha256_file(envelope_path),
        evidence_bundle_root=evidence_bundle_root,
        evidence_bundle_relative=evidence_bundle_relative,
        source_protocol_reference=source.get("protocol_reference") or {},
        repo_root=repo_root,
    )
    if canonical_json_bytes(dict(qualification_identity)) != canonical_json_bytes(qualification):
        raise PromotionError("qualification binding changed")

    reference = manifest.get("protocol_reference")
    if not isinstance(reference, Mapping):
        raise PromotionError("official manifest lacks its protocol reference")
    protocol_path, protocol_relative = _repo_file(
        reference.get("path"), repo_root, "bound official protocol"
    )
    protocol_payload = _load_json_object(protocol_path, "bound official protocol")
    official_protocol = _validated_frozen_protocol(
        protocol_payload,
        path=protocol_relative,
        file_sha256=sha256_file(protocol_path),
        candidate_snapshot=candidate_snapshot,
        qualification=qualification,
        release_id=review["release_id"],
        verify_files=verify_files,
    )
    expected_reference = {
        "path": official_protocol["path"],
        "sha256": official_protocol["canonical_sha256"],
        "file_sha256": official_protocol["file_sha256"],
        "hash_semantics": HASH_SEMANTICS,
        "status": "frozen",
        "official": True,
        "qualified": True,
        "benchmark_release_id": review["release_id"],
        "qualified_candidate_sha256": official_protocol["qualified_candidate_sha256"],
    }
    if canonical_json_bytes(dict(reference)) != canonical_json_bytes(expected_reference):
        raise PromotionError("official protocol reference changed")
    expected_promotion_identity = {
        "qualified_candidate_protocol_path": qualification[
            "qualified_candidate_protocol_path"
        ],
        "qualified_candidate_protocol_sha256": qualification[
            "qualified_candidate_protocol_sha256"
        ],
        "official_protocol_path": official_protocol["path"],
        "official_protocol_sha256": official_protocol["canonical_sha256"],
        "scientifically_identical": True,
        "allowed_promotion_fields": list(ALLOWED_PROTOCOL_PROMOTION_FIELDS),
    }
    if canonical_json_bytes(dict(promotion_identity)) != canonical_json_bytes(
        expected_promotion_identity
    ):
        raise PromotionError("protocol promotion identity changed")


def render_manifest(manifest: Mapping[str, Any]) -> str:
    return json.dumps(manifest, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_immutable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable official manifest: {path}")
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-candidate", default=SOURCE_CANDIDATE_PATH)
    parser.add_argument("--qualification-envelope", required=True)
    parser.add_argument(
        "--qualification-evidence-bundle",
        required=True,
        help="repository-contained bundle with results/ and content-addressed artifacts/",
    )
    parser.add_argument("--official-protocol", required=True)
    parser.add_argument(
        "--review-metadata",
        required=True,
        help=f"approved review record created from {REVIEW_TEMPLATE_PATH}",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify that --output already equals the deterministic promotion",
    )
    args = parser.parse_args(argv)
    try:
        generated = build_official_manifest(
            source_candidate_path=args.source_candidate,
            qualification_envelope_path=args.qualification_envelope,
            qualification_evidence_bundle_root=args.qualification_evidence_bundle,
            official_protocol_path=args.official_protocol,
            review_metadata_path=args.review_metadata,
            repo_root=REPO_ROOT,
            verify_files=True,
        )
        rendered = render_manifest(generated)
        output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
        output = output.resolve()
        try:
            output.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise PromotionError("official output must be inside the repository") from exc
        if args.check:
            committed = _load_json_object(output, "official manifest")
            validate_official_manifest(committed, repo_root=REPO_ROOT, verify_files=True)
            if output.read_text() != rendered:
                raise PromotionError("official manifest is not deterministic/current")
            print(f"OK: {output} is valid and current")
            return 0
        _write_immutable(output, rendered)
        print(f"Wrote immutable official manifest {output}")
        return 0
    except (PromotionError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
