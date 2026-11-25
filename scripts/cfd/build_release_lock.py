#!/usr/bin/env python3
"""Build the immutable metadata lock for one reviewed official CFD release.

This command is deliberately incapable of running or authorizing compute.  It
rebuilds the promoted official manifest from the qualification evidence bundle,
frozen protocol, and approved human-review record, then emits the exact release
identity consumed by the official grader.  The output is deterministic and may
be written only once; ``--check`` fully replays the inputs and verifies the
committed bytes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.grade_benchmark import RESULT_SCHEMA_VERSION  # noqa: E402
from scripts.cfd.grading_protocol import (  # noqa: E402
    ProtocolError,
    sha256_file,
    validate_protocol,
)
from scripts.cfd.promote_benchmark_manifest import (  # noqa: E402
    PromotionError,
    SOURCE_CANDIDATE_PATH,
    build_official_manifest,
    validate_review_metadata,
)


SCHEMA_VERSION = "wingbench-cfd-release-lock-v1"
RELEASE_STATUS = "frozen"
LOCK_NOTE = (
    "Immutable metadata lock for the reviewed official benchmark release; "
    "this record does not authorize compute."
)
_SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
_EXPECTED_KEYS = frozenset({
    "schema_version",
    "release_status",
    "benchmark_release_id",
    "protocol_id",
    "protocol_sha256",
    "manifest_id",
    "manifest_sha256",
    "source_candidate_manifest_sha256",
    "qualified_candidate_protocol_id",
    "qualified_candidate_protocol_sha256",
    "qualification_id",
    "qualification_manifest_id",
    "qualification_manifest_sha256",
    "qualification_envelope_sha256",
    "qualification_evidence_bundle_contract_sha256",
    "qualification_record_sha256",
    "qualification_passed",
    "result_schema_version",
    "official_benchmark",
    "release_review",
    "reviewed_at",
    "note",
})
_EXPECTED_REVIEW_KEYS = frozenset({
    "path",
    "schema_version",
    "canonical_sha256",
    "file_sha256",
    "review_status",
    "reviewed_by",
    "reviewed_at",
    "review_note",
})


class ReleaseLockError(ValueError):
    """The inputs do not prove one deterministic official release lock."""


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
        raise ReleaseLockError(f"value is not canonical JSON: {exc}") from exc


def canonical_json_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReleaseLockError(f"duplicate JSON object key {key!r}")
        result[key] = value
    return result


def _load_json_object(path: Path, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(),
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ReleaseLockError(f"non-finite JSON constant {value}")
            ),
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ReleaseLockError) as exc:
        raise ReleaseLockError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ReleaseLockError(f"{label} must be a JSON object")
    canonical_json_bytes(payload)
    return payload


def _repo_file(
    path: str | os.PathLike[str], repo_root: Path, label: str
) -> tuple[Path, str]:
    if not isinstance(path, (str, os.PathLike)):
        raise ReleaseLockError(f"{label} path must be a string")
    candidate = Path(path)
    resolved = (
        (repo_root / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    try:
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ReleaseLockError(f"{label} must be inside the repository") from exc
    if not resolved.is_file():
        raise ReleaseLockError(f"{label} is not a regular file: {relative}")
    return resolved, relative


def _repo_directory(
    path: str | os.PathLike[str], repo_root: Path, label: str
) -> tuple[Path, str]:
    if not isinstance(path, (str, os.PathLike)):
        raise ReleaseLockError(f"{label} path must be a string")
    candidate = Path(path)
    resolved = (
        (repo_root / candidate).resolve()
        if not candidate.is_absolute()
        else candidate.resolve()
    )
    try:
        relative = resolved.relative_to(repo_root.resolve()).as_posix()
    except ValueError as exc:
        raise ReleaseLockError(f"{label} must be inside the repository") from exc
    if not resolved.is_dir():
        raise ReleaseLockError(f"{label} is not a directory: {relative}")
    return resolved, relative


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ReleaseLockError(f"{label} must be an object")
    return value


def _full_sha256(value: Any) -> bool:
    return isinstance(value, str) and _SHA256_RE.fullmatch(value) is not None


def _nonempty_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReleaseLockError(f"{label} must be a non-empty string")
    return value


def validate_release_lock_payload(
    payload: Mapping[str, Any], *, expected: Mapping[str, Any] | None = None
) -> None:
    """Validate the lock's closed schema and, optionally, exact identity."""
    if not isinstance(payload, Mapping) or set(payload) != _EXPECTED_KEYS:
        raise ReleaseLockError("release lock has unexpected or missing fields")
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ReleaseLockError("unsupported release-lock schema")
    if payload.get("release_status") != RELEASE_STATUS:
        raise ReleaseLockError("release lock is not frozen")
    for key in (
        "benchmark_release_id",
        "protocol_id",
        "manifest_id",
        "qualified_candidate_protocol_id",
        "qualification_id",
        "qualification_manifest_id",
        "reviewed_at",
        "note",
    ):
        _nonempty_string(payload.get(key), key)
    for key in (
        "protocol_sha256",
        "manifest_sha256",
        "source_candidate_manifest_sha256",
        "qualified_candidate_protocol_sha256",
        "qualification_manifest_sha256",
        "qualification_envelope_sha256",
        "qualification_evidence_bundle_contract_sha256",
        "qualification_record_sha256",
    ):
        if not _full_sha256(payload.get(key)):
            raise ReleaseLockError(f"{key} must be a full lowercase SHA-256")
    if payload.get("qualification_passed") is not True:
        raise ReleaseLockError("release lock does not record passed qualification")
    if payload.get("official_benchmark") is not True:
        raise ReleaseLockError("release lock is not for an official benchmark")
    if payload.get("result_schema_version") != RESULT_SCHEMA_VERSION:
        raise ReleaseLockError("release lock result schema changed")
    if payload.get("note") != LOCK_NOTE:
        raise ReleaseLockError("release lock note changed")

    review = _mapping(payload.get("release_review"), "release review identity")
    if set(review) != _EXPECTED_REVIEW_KEYS:
        raise ReleaseLockError("release review identity has unexpected or missing fields")
    for key in ("path", "schema_version", "reviewed_by", "reviewed_at", "review_note"):
        _nonempty_string(review.get(key), f"release_review.{key}")
    for key in ("canonical_sha256", "file_sha256"):
        if not _full_sha256(review.get(key)):
            raise ReleaseLockError(
                f"release_review.{key} must be a full lowercase SHA-256"
            )
    if review.get("review_status") != "approved":
        raise ReleaseLockError("release review identity is not approved")
    if payload.get("reviewed_at") != review.get("reviewed_at"):
        raise ReleaseLockError("top-level reviewed_at differs from the bound review")
    canonical_json_bytes(payload)
    if expected is not None and canonical_json_bytes(payload) != canonical_json_bytes(expected):
        raise ReleaseLockError("release lock is not deterministic/current")


def build_release_lock(
    *,
    official_manifest_path: str | os.PathLike[str],
    official_protocol_path: str | os.PathLike[str],
    qualification_envelope_path: str | os.PathLike[str],
    qualification_evidence_bundle_root: str | os.PathLike[str],
    review_metadata_path: str | os.PathLike[str],
    source_candidate_path: str | os.PathLike[str] = SOURCE_CANDIDATE_PATH,
    repo_root: Path = REPO_ROOT,
    verify_files: bool = True,
) -> dict[str, Any]:
    """Replay all promotion inputs and return one non-authorizing release lock.

    ``verify_files=False`` exists only for isolated synthetic-fixture tests.  The
    CLI never exposes it, and the promoted-manifest helper still performs full
    qualification evidence replay in either mode.
    """
    manifest_path, _ = _repo_file(
        official_manifest_path, repo_root, "official manifest"
    )
    protocol_path, protocol_relative = _repo_file(
        official_protocol_path, repo_root, "official protocol"
    )
    envelope_path, envelope_relative = _repo_file(
        qualification_envelope_path, repo_root, "qualification envelope"
    )
    evidence_root, evidence_relative = _repo_directory(
        qualification_evidence_bundle_root,
        repo_root,
        "qualification evidence bundle",
    )
    review_path, review_relative = _repo_file(
        review_metadata_path, repo_root, "release review"
    )
    source_path, source_relative = _repo_file(
        source_candidate_path, repo_root, "source candidate manifest"
    )
    if source_relative != SOURCE_CANDIDATE_PATH:
        raise ReleaseLockError(
            f"source candidate must be the canonical inventory at {SOURCE_CANDIDATE_PATH}"
        )

    manifest = _load_json_object(manifest_path, "official manifest")
    protocol = _load_json_object(protocol_path, "official protocol")
    envelope = _load_json_object(envelope_path, "qualification envelope")
    review = validate_review_metadata(_load_json_object(review_path, "release review"))
    source = _load_json_object(source_path, "source candidate manifest")

    # Rebuilding, rather than merely trusting manifest.release, reopens every
    # qualification result/raw artifact and all promotion inputs.  The helper
    # also independently validates the generated official manifest before it
    # returns.
    try:
        rebuilt_manifest = build_official_manifest(
            source_candidate_path=source_path,
            qualification_envelope_path=envelope_path,
            qualification_evidence_bundle_root=evidence_root,
            official_protocol_path=protocol_path,
            review_metadata_path=review_path,
            repo_root=repo_root,
            verify_files=verify_files,
        )
        validate_protocol(protocol, verify_files=verify_files)
    except (PromotionError, ProtocolError) as exc:
        raise ReleaseLockError(f"official release replay failed: {exc}") from exc
    if canonical_json_bytes(manifest) != canonical_json_bytes(rebuilt_manifest):
        raise ReleaseLockError(
            "official manifest is not the deterministic promotion of the supplied inputs"
        )

    release = _mapping(manifest.get("release"), "official manifest release metadata")
    source_identity = _mapping(
        release.get("source_candidate_manifest"), "source candidate identity"
    )
    qualification = _mapping(release.get("qualification"), "qualification identity")
    manifest_review = _mapping(release.get("release_review"), "release review binding")
    protocol_reference = _mapping(
        manifest.get("protocol_reference"), "official protocol reference"
    )
    record = _mapping(envelope.get("qualification_record"), "qualification record")
    candidate = _mapping(record.get("candidate_protocol"), "qualified candidate protocol")
    qualification_manifest = _mapping(
        record.get("qualification_manifest"), "qualification manifest identity"
    )
    evidence_contract = _mapping(
        record.get("evidence_bundle"), "qualification evidence-bundle contract"
    )

    envelope_sha256 = canonical_json_sha256(envelope)
    record_sha256 = canonical_json_sha256(record)
    evidence_contract_sha256 = canonical_json_sha256(evidence_contract)
    protocol_canonical_sha256 = canonical_json_sha256(protocol)
    source_canonical_sha256 = canonical_json_sha256(source)
    checks = {
        "manifest official": manifest.get("official_benchmark") is True,
        "manifest frozen": manifest.get("manifest_status") == "frozen",
        "protocol path": protocol_reference.get("path") == protocol_relative,
        "protocol canonical hash": (
            protocol_reference.get("sha256") == protocol_canonical_sha256
        ),
        "protocol file hash": (
            protocol_reference.get("file_sha256") == sha256_file(protocol_path)
        ),
        "protocol frozen": protocol.get("protocol_status") == "frozen",
        "protocol official": protocol.get("official_grading_enabled") is True,
        "release id": (
            release.get("release_id")
            == protocol.get("benchmark_release_id")
            == review.get("release_id")
        ),
        "manifest id": manifest.get("manifest_id") == review.get("official_manifest_id"),
        "source path": source_identity.get("path") == source_relative,
        "source canonical hash": (
            source_identity.get("canonical_sha256") == source_canonical_sha256
        ),
        "source file hash": source_identity.get("file_sha256") == sha256_file(source_path),
        "envelope path": qualification.get("envelope_path") == envelope_relative,
        "envelope canonical hash": (
            qualification.get("envelope_canonical_sha256") == envelope_sha256
        ),
        "envelope file hash": (
            qualification.get("envelope_file_sha256") == sha256_file(envelope_path)
        ),
        "evidence root": qualification.get("evidence_bundle_root") == evidence_relative,
        "evidence contract": (
            qualification.get("evidence_bundle_contract_sha256")
            == evidence_contract_sha256
        ),
        "qualification record": (
            envelope.get("qualification_record_sha256")
            == record_sha256
            == qualification.get("qualification_record_sha256")
        ),
        "qualification passed": record.get("qualification_status") == "passed",
        "qualification id": (
            qualification.get("qualification_id") == record.get("qualification_id")
        ),
        "qualification manifest id": (
            qualification.get("qualification_manifest_id")
            == qualification_manifest.get("manifest_id")
        ),
        "qualification manifest hash": (
            qualification.get("qualification_manifest_sha256")
            == qualification_manifest.get("canonical_sha256")
        ),
        "candidate path": (
            qualification.get("qualified_candidate_protocol_path")
            == candidate.get("path")
        ),
        "candidate hash": (
            qualification.get("qualified_candidate_protocol_sha256")
            == candidate.get("canonical_sha256")
        ),
        "review path": manifest_review.get("path") == review_relative,
        "review schema": manifest_review.get("schema_version") == review.get("schema_version"),
        "review canonical hash": (
            manifest_review.get("canonical_sha256") == canonical_json_sha256(review)
        ),
        "review file hash": manifest_review.get("file_sha256") == sha256_file(review_path),
    }
    failed = [label for label, passed in checks.items() if not passed]
    if failed:
        raise ReleaseLockError(
            "official release identity is inconsistent: " + ", ".join(failed)
        )

    lock = {
        "schema_version": SCHEMA_VERSION,
        "release_status": RELEASE_STATUS,
        "benchmark_release_id": release["release_id"],
        "protocol_id": protocol["protocol_id"],
        "protocol_sha256": protocol_canonical_sha256,
        "manifest_id": manifest["manifest_id"],
        "manifest_sha256": canonical_json_sha256(manifest),
        "source_candidate_manifest_sha256": source_canonical_sha256,
        "qualified_candidate_protocol_id": candidate["protocol_id"],
        "qualified_candidate_protocol_sha256": candidate["canonical_sha256"],
        "qualification_id": record["qualification_id"],
        "qualification_manifest_id": qualification_manifest["manifest_id"],
        "qualification_manifest_sha256": qualification_manifest["canonical_sha256"],
        "qualification_envelope_sha256": envelope_sha256,
        "qualification_evidence_bundle_contract_sha256": evidence_contract_sha256,
        "qualification_record_sha256": record_sha256,
        "qualification_passed": True,
        "result_schema_version": RESULT_SCHEMA_VERSION,
        "official_benchmark": True,
        "release_review": {
            "path": review_relative,
            "schema_version": review["schema_version"],
            "canonical_sha256": canonical_json_sha256(review),
            "file_sha256": sha256_file(review_path),
            "review_status": "approved",
            "reviewed_by": review["reviewed_by"],
            "reviewed_at": review["reviewed_at"],
            "review_note": review["review_note"],
        },
        "reviewed_at": review["reviewed_at"],
        "note": LOCK_NOTE,
    }
    validate_release_lock_payload(lock)
    return lock


def render_release_lock(payload: Mapping[str, Any]) -> str:
    validate_release_lock_payload(payload)
    return json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"


def _write_immutable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable release lock: {path}")
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
    parser.add_argument("--official-manifest", required=True)
    parser.add_argument("--official-protocol", required=True)
    parser.add_argument("--qualification-envelope", required=True)
    parser.add_argument("--qualification-evidence-bundle", required=True)
    parser.add_argument("--review-metadata", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--check",
        action="store_true",
        help="replay all inputs and require the existing output bytes to match",
    )
    args = parser.parse_args(argv)
    try:
        generated = build_release_lock(
            source_candidate_path=args.source_candidate,
            official_manifest_path=args.official_manifest,
            official_protocol_path=args.official_protocol,
            qualification_envelope_path=args.qualification_envelope,
            qualification_evidence_bundle_root=args.qualification_evidence_bundle,
            review_metadata_path=args.review_metadata,
            repo_root=REPO_ROOT,
            verify_files=True,
        )
        rendered = render_release_lock(generated)
        output = args.output if args.output.is_absolute() else REPO_ROOT / args.output
        output = output.resolve()
        try:
            output.relative_to(REPO_ROOT.resolve())
        except ValueError as exc:
            raise ReleaseLockError("release-lock output must be inside the repository") from exc
        if args.check:
            committed = _load_json_object(output, "release lock")
            validate_release_lock_payload(committed, expected=generated)
            if output.read_text() != rendered:
                raise ReleaseLockError("release lock is not deterministic/current")
            print(f"OK: {output} is valid and current")
            return 0
        _write_immutable(output, rendered)
        print(f"Wrote immutable release lock {output}")
        return 0
    except (ReleaseLockError, PromotionError, ProtocolError, FileExistsError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
