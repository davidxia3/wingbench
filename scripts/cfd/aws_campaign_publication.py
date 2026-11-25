#!/usr/bin/env python3
"""AWS CLI I/O for the immutable CFD campaign publication contract.

The scientific publication schema lives in :mod:`campaign_publication`.  This
module supplies the deliberately small, synchronous AWS boundary used by a
worker and by an independent reader/exporter.  It has no boto3 dependency and
accepts an injected command runner so the failure semantics can be tested
without contacting AWS.

Publisher safety properties:

* artifact bytes are copied to private, verified snapshots before upload;
* every S3 write uses ``If-None-Match: *`` and COMPLIANCE retention;
* terminal-entry conditional failures are always fatal; an artifact-only 412
  may be resolved by one narrow GetObject-by-digest operation whose exact
  bytes and non-null VersionId must verify; and
* the local journal is created atomically only after the terminal entry has a
  non-null S3 version identity.

Exporter safety properties:

* bucket versioning and Object Lock are checked through the reader runner;
* every ListObjectVersions page is traversed explicitly;
* ledger keys must be exact; referenced artifacts must exist, while harmless
  one-version retained digest blobs left by losing terminal attempts are allowed;
  duplicate versions, delete markers, and malformed artifact keys are fatal;
* exact referenced object versions are downloaded and checked for COMPLIANCE
  retention, while retained digest-key orphan metadata is preserved; and
* the destination is assembled privately, replayed through the shared ledger
  validator, then installed under a no-overwrite export lock.

The shared ``publication_inventory.json`` v2 sidecar persists complete ledger
and artifact-prefix observations.  Its rows remain an operator-generated AWS
API observation rather than a cryptographic attestation of AWS enforcement.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.cfd.campaign_publication import (
    INVENTORY_OBSERVATION_TRUST_MODEL,
    MINIMUM_RETENTION_DAYS,
    PUBLICATION_ENTRY_SCHEMA_VERSION,
    PUBLICATION_EXPORT_SCHEMA_VERSION,
    PUBLICATION_INVENTORY_FILENAME,
    PUBLICATION_INVENTORY_SCHEMA_VERSION,
    PUBLICATION_LEDGER_DIRECTORY,
    CampaignPublicationError,
    _artifact_expectations_from_exports as _shared_artifact_expectations_from_exports,
    build_publication_export,
    load_campaign_publication_export,
    parse_json_object_bytes,
    publication_entry_filename,
    publication_object_key,
)
from scripts.cfd.grading_protocol import canonical_json_bytes, protocol_sha256


ARTIFACT_DIRECTORY = "artifacts/sha256"
DEFAULT_RETENTION_DAYS = MINIMUM_RETENTION_DAYS + 1
_ARTIFACT_FIELDS = {"path", "present", "size_bytes", "sha256"}
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_SAFE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]")


class AwsCampaignPublicationError(CampaignPublicationError):
    """AWS publication or independent export failed closed."""


@dataclass(frozen=True)
class AwsCliCompleted:
    """Minimal subprocess result returned by an injected AWS CLI runner."""

    returncode: int
    stdout: bytes = b""
    stderr: bytes = b""


AwsCliRunner = Callable[[Sequence[str]], AwsCliCompleted]


class SubprocessAwsCliRunner:
    """Run AWS CLI without a shell, optionally using ambient role credentials."""

    def __init__(
        self,
        *,
        profile: str | None = None,
        region: str | None = None,
        executable: str = "aws",
    ) -> None:
        if profile is not None and (not isinstance(profile, str) or not profile):
            raise ValueError("AWS profile cannot be empty")
        self._prefix = [executable]
        if profile is not None:
            self._prefix.extend(["--profile", profile])
        if region is not None:
            if not isinstance(region, str) or not region:
                raise ValueError("AWS region cannot be empty")
            self._prefix.extend(["--region", region])

    def __call__(self, arguments: Sequence[str]) -> AwsCliCompleted:
        try:
            completed = subprocess.run(
                [*self._prefix, *arguments],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
                shell=False,
            )
        except OSError as exc:
            raise AwsCampaignPublicationError(
                f"cannot execute AWS CLI: {exc}"
            ) from exc
        return AwsCliCompleted(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _safe_component(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or _SAFE_COMPONENT_RE.fullmatch(value) is None
        or value in {".", ".."}
    ):
        raise AwsCampaignPublicationError(f"{label} is not a safe path component")
    return value


def _bucket_name(value: Any) -> str:
    if (
        not isinstance(value, str)
        or _BUCKET_RE.fullmatch(value) is None
        or ".." in value
        or ".-" in value
        or "-." in value
    ):
        raise AwsCampaignPublicationError(
            "publication bucket must be a DNS-compatible S3 bucket name"
        )
    return value


def artifact_object_key(campaign_publication_id: str, digest: str) -> str:
    campaign_id = _safe_component(
        campaign_publication_id, "campaign publication ID"
    )
    if not isinstance(digest, str) or _SHA256_RE.fullmatch(digest) is None:
        raise AwsCampaignPublicationError("artifact SHA-256 is invalid")
    return f"campaigns/{campaign_id}/{ARTIFACT_DIRECTORY}/{digest}"


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AwsCampaignPublicationError("clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _format_utc(value: datetime) -> str:
    value = _utc(value)
    text = value.isoformat(timespec="microseconds").replace("+00:00", "Z")
    return text.replace(".000000Z", "Z")


def _parse_aws_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise AwsCampaignPublicationError(f"{label} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AwsCampaignPublicationError(f"{label} is not a timestamp") from exc
    if parsed.tzinfo is None:
        raise AwsCampaignPublicationError(f"{label} lacks an explicit timezone")
    return parsed.astimezone(timezone.utc)


def _json_result(completed: AwsCliCompleted, label: str) -> Mapping[str, Any]:
    if not isinstance(completed, AwsCliCompleted):
        raise AwsCampaignPublicationError(f"{label} runner returned an invalid result")
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        if len(detail) > 500:
            detail = detail[:500] + "..."
        raise AwsCampaignPublicationError(
            f"{label} failed closed (exit {completed.returncode})"
            + (f": {detail}" if detail else "")
        )
    try:
        value = json.loads(completed.stdout)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AwsCampaignPublicationError(
            f"{label} returned ambiguous non-JSON success"
        ) from exc
    if not isinstance(value, Mapping):
        raise AwsCampaignPublicationError(f"{label} did not return a JSON object")
    return value


def _run_json(runner: AwsCliRunner, arguments: Sequence[str], label: str) -> Mapping[str, Any]:
    try:
        completed = runner(list(arguments))
    except AwsCampaignPublicationError:
        raise
    except Exception as exc:
        # A transport exception is ambiguous: the request may have committed.
        # Retrying a conditional publication here would blur that distinction.
        raise AwsCampaignPublicationError(
            f"{label} transport outcome is ambiguous; refusing to retry: {exc}"
        ) from exc
    return _json_result(completed, label)


def _exact_file_bytes(path: Path, *, label: str) -> bytes:
    supplied = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(supplied, flags)
    except OSError as exc:
        raise AwsCampaignPublicationError(f"cannot open {label} {supplied}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AwsCampaignPublicationError(f"{label} must be a regular file")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise AwsCampaignPublicationError(f"{label} changed while it was read")
        raw = b"".join(chunks)
        if len(raw) != before.st_size:
            raise AwsCampaignPublicationError(f"{label} size changed while it was read")
        return raw
    finally:
        os.close(descriptor)


def _hash_regular_file(path: Path, *, label: str) -> tuple[int, str]:
    """Hash a stable regular file without loading a potentially large grid."""
    supplied = Path(path)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(supplied, flags)
    except OSError as exc:
        raise AwsCampaignPublicationError(f"cannot open {label} {supplied}: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise AwsCampaignPublicationError(f"{label} must be a regular file")
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or size != before.st_size:
            raise AwsCampaignPublicationError(f"{label} changed while it was hashed")
        return size, digest.hexdigest()
    finally:
        os.close(descriptor)


def _artifact_records(value: Any) -> list[Mapping[str, Any]]:
    records: list[Mapping[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            artifact_like = "present" in item and bool(
                {"path", "size_bytes", "sha256"}.intersection(item)
            )
            if artifact_like and not _ARTIFACT_FIELDS.issubset(item):
                raise AwsCampaignPublicationError(
                    "artifact-like result record is missing required fields"
                )
            if _ARTIFACT_FIELDS.issubset(item):
                records.append(item)
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(value)
    return records


def _validate_artifact_record_shape(record: Mapping[str, Any], *, label: str) -> None:
    present = record.get("present")
    path = record.get("path")
    if type(present) is not bool or not isinstance(path, str) or not path:
        raise AwsCampaignPublicationError(f"{label} has an invalid artifact record")
    if present is False:
        if record.get("size_bytes") is not None or record.get("sha256") is not None:
            raise AwsCampaignPublicationError(
                f"{label} absent artifact must have null size and SHA-256"
            )
        return
    size = record.get("size_bytes")
    digest = record.get("sha256")
    if (
        isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
        or not isinstance(digest, str)
        or _SHA256_RE.fullmatch(digest) is None
    ):
        raise AwsCampaignPublicationError(f"{label} present artifact metadata is invalid")


def _snapshot_artifact(
    record: Mapping[str, Any],
    *,
    destination: Path,
    label: str,
) -> tuple[str, int]:
    """Copy one source file once, hashing the exact private upload snapshot."""
    _validate_artifact_record_shape(record, label=label)
    if record["present"] is not True:
        raise AssertionError("only present artifacts can be snapshotted")
    source = Path(str(record["path"]))
    if not source.is_absolute():
        raise AwsCampaignPublicationError(f"{label} path must be absolute")
    source_flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        source_flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, source_flags)
    except OSError as exc:
        raise AwsCampaignPublicationError(f"cannot open {label}: {exc}") from exc
    try:
        before = os.fstat(source_fd)
        if not stat.S_ISREG(before.st_mode):
            raise AwsCampaignPublicationError(f"{label} must be a regular file")
        try:
            destination_fd = os.open(
                destination,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o400,
            )
        except OSError as exc:
            raise AwsCampaignPublicationError(
                f"cannot create private {label} snapshot: {exc}"
            ) from exc
        digest = hashlib.sha256()
        size = 0
        try:
            while True:
                chunk = os.read(source_fd, 1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                size += len(chunk)
                view = memoryview(chunk)
                while view:
                    written = os.write(destination_fd, view)
                    view = view[written:]
            os.fsync(destination_fd)
        finally:
            os.close(destination_fd)
        after = os.fstat(source_fd)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ):
            raise AwsCampaignPublicationError(f"{label} changed while it was snapshotted")
        observed = digest.hexdigest()
        if size != record["size_bytes"] or observed != record["sha256"]:
            raise AwsCampaignPublicationError(
                f"{label} bytes disagree with the result artifact record"
            )
        return observed, size
    finally:
        os.close(source_fd)


def _write_atomic_no_overwrite(path: Path, raw: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        view = memoryview(raw)
        while view:
            written = os.write(descriptor, view)
            view = view[written:]
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        # link(2), unlike replace/rename, fails atomically if path exists.
        os.link(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    except FileExistsError as exc:
        raise AwsCampaignPublicationError(
            f"refusing to overwrite local publication file: {path}"
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _link_verified_file_no_overwrite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except FileExistsError as exc:
        raise AwsCampaignPublicationError(
            f"refusing to overwrite local publication file: {destination}"
        ) from exc
    directory_fd = os.open(destination.parent, os.O_RDONLY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _put_object_once(
    *,
    runner: AwsCliRunner,
    bucket: str,
    key: str,
    body: Path,
    retain_until: str,
    label: str,
) -> dict[str, str]:
    response = _run_json(
        runner,
        [
            "s3api",
            "put-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--body",
            str(body),
            "--if-none-match",
            "*",
            "--object-lock-mode",
            "COMPLIANCE",
            "--object-lock-retain-until-date",
            retain_until,
            "--output",
            "json",
        ],
        label,
    )
    version_id = response.get("VersionId")
    if (
        not isinstance(version_id, str)
        or not version_id
        or version_id == "null"
    ):
        raise AwsCampaignPublicationError(
            f"{label} reported success without a durable non-null VersionId"
        )
    return {
        "storage_provider": "aws_s3",
        "bucket": bucket,
        "key": key,
        "version_id": version_id,
    }


def _put_artifact_or_verify_existing(
    *,
    runner: AwsCliRunner,
    bucket: str,
    key: str,
    body: Path,
    retain_until: str,
    expected_digest: str,
    expected_size: int,
    reuse_download_path: Path,
) -> dict[str, str]:
    """Conditionally create an artifact, narrowly resolving only a true 412.

    Repeated content is normal across wings.  The writer therefore has a
    narrowly scoped ``GetObject`` path for a digest key after S3 explicitly
    reports PreconditionFailed/412.  It never lists the namespace and never
    reads a terminal ledger key.  Any other write failure remains fatal.
    """
    arguments = [
        "s3api",
        "put-object",
        "--bucket",
        bucket,
        "--key",
        key,
        "--body",
        str(body),
        "--if-none-match",
        "*",
        "--object-lock-mode",
        "COMPLIANCE",
        "--object-lock-retain-until-date",
        retain_until,
        "--output",
        "json",
    ]
    try:
        completed = runner(arguments)
    except AwsCampaignPublicationError:
        raise
    except Exception as exc:
        raise AwsCampaignPublicationError(
            "artifact publication transport outcome is ambiguous; refusing to retry"
        ) from exc
    if not isinstance(completed, AwsCliCompleted):
        raise AwsCampaignPublicationError("artifact publisher returned an invalid result")
    if completed.returncode == 0:
        response = _json_result(completed, f"conditional artifact publication {expected_digest}")
        version_id = response.get("VersionId")
        if not isinstance(version_id, str) or not version_id or version_id == "null":
            raise AwsCampaignPublicationError(
                "artifact publication reported success without a durable non-null VersionId"
            )
        return {
            "storage_provider": "aws_s3",
            "bucket": bucket,
            "key": key,
            "version_id": version_id,
        }

    error = completed.stderr.decode("utf-8", errors="replace")
    # AWS CLI normally renders the HTTP 412 service code as
    # ``An error occurred (PreconditionFailed)`` without the numeric status.
    if re.search(r"\(PreconditionFailed\)", error, flags=re.IGNORECASE) is None:
        _json_result(completed, f"conditional artifact publication {expected_digest}")
        raise AssertionError("nonzero AWS result unexpectedly accepted")
    if reuse_download_path.exists() or reuse_download_path.is_symlink():
        raise AwsCampaignPublicationError("artifact reuse download path already exists")
    response = _run_json(
        runner,
        [
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--output",
            "json",
            str(reuse_download_path),
        ],
        f"existing content-addressed artifact read {expected_digest}",
    )
    version_id = response.get("VersionId")
    content_length = response.get("ContentLength")
    if not isinstance(version_id, str) or not version_id or version_id == "null":
        raise AwsCampaignPublicationError(
            "existing artifact read lacks a durable non-null VersionId"
        )
    observed_size, observed_digest = _hash_regular_file(
        reuse_download_path, label="existing content-addressed artifact"
    )
    if (
        isinstance(content_length, bool)
        or not isinstance(content_length, int)
        or content_length != expected_size
        or observed_size != expected_size
        or observed_digest != expected_digest
    ):
        raise AwsCampaignPublicationError(
            "existing content-addressed artifact bytes do not match their digest key"
        )
    reuse_download_path.unlink()
    return {
        "storage_provider": "aws_s3",
        "bucket": bucket,
        "key": key,
        "version_id": version_id,
    }


def _validate_predecessor(
    *,
    result: Mapping[str, Any],
    sequence_index: int,
    ordered_manifest_wing_ids: Sequence[str],
    campaign_publication_id: str,
    bucket: str,
    previous_entry_sha256: str | None,
    previous_entry_object: Mapping[str, Any] | None,
) -> None:
    if sequence_index == 0:
        if previous_entry_sha256 is not None or previous_entry_object is not None:
            raise AwsCampaignPublicationError(
                "sequence zero cannot be given a publication predecessor"
            )
        return
    if (
        not isinstance(previous_entry_sha256, str)
        or _SHA256_RE.fullmatch(previous_entry_sha256) is None
        or not isinstance(previous_entry_object, Mapping)
    ):
        raise AwsCampaignPublicationError(
            "nonzero publication requires the exact prior entry and object identity"
        )
    expected_key = publication_object_key(
        campaign_publication_id,
        sequence_index - 1,
        ordered_manifest_wing_ids[sequence_index - 1],
    )
    if set(previous_entry_object) != {
        "storage_provider",
        "bucket",
        "key",
        "version_id",
    } or previous_entry_object != {
        "storage_provider": "aws_s3",
        "bucket": bucket,
        "key": expected_key,
        "version_id": previous_entry_object.get("version_id"),
    }:
        raise AwsCampaignPublicationError("prior publication object identity changed")
    if not isinstance(previous_entry_object.get("version_id"), str) or not previous_entry_object[
        "version_id"
    ] or previous_entry_object["version_id"] == "null":
        raise AwsCampaignPublicationError("prior publication VersionId is invalid")

    authorization = result.get("execution_authorization")
    snapshot = authorization.get("snapshot") if isinstance(authorization, Mapping) else None
    binding = snapshot.get("campaign_publication") if isinstance(snapshot, Mapping) else None
    if not isinstance(binding, Mapping):
        raise AwsCampaignPublicationError("result lacks campaign publication authorization")
    start = binding.get("start_sequence_index")
    if start == sequence_index and (
        binding.get("previous_entry_sha256") != previous_entry_sha256
        or binding.get("previous_entry_object") != previous_entry_object
    ):
        raise AwsCampaignPublicationError(
            "recovery result is not bound to the supplied WORM predecessor"
        )


def publish_terminal_result(
    *,
    result_json_path: Path,
    sequence_index: int,
    ordered_manifest_wing_ids: Sequence[str],
    previous_entry_sha256: str | None,
    previous_entry_object: Mapping[str, Any] | None,
    campaign_publication_id: str,
    publication_bucket: str,
    allow_recovery: bool,
    journal_root: Path,
    writer_runner: AwsCliRunner,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    retention_days: int = DEFAULT_RETENTION_DAYS,
) -> dict[str, Any]:
    """Publish one terminal result and return its new hash-chain head.

    This call is intentionally synchronous.  A caller must not advance to the
    next manifest slot until it receives the returned ``entry_sha256`` and
    ``object``.  No AWS operation in this function is retried.
    """
    campaign_id = _safe_component(campaign_publication_id, "campaign publication ID")
    bucket = _bucket_name(publication_bucket)
    ordered = list(ordered_manifest_wing_ids)
    if (
        not ordered
        or len(set(ordered)) != len(ordered)
        or not all(
            isinstance(wing_id, str) and _SAFE_COMPONENT_RE.fullmatch(wing_id)
            for wing_id in ordered
        )
        or isinstance(sequence_index, bool)
        or not isinstance(sequence_index, int)
        or sequence_index < 0
        or sequence_index >= len(ordered)
    ):
        raise AwsCampaignPublicationError("publication roster or sequence is invalid")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int):
        raise AwsCampaignPublicationError("retention days must be an integer")
    if retention_days < DEFAULT_RETENTION_DAYS:
        raise AwsCampaignPublicationError(
            "publisher requires at least 91 days to preserve a full 90-day S3 interval"
        )
    started_at = _utc(clock())
    retain_until = _format_utc(started_at + timedelta(days=retention_days))

    result_raw = _exact_file_bytes(Path(result_json_path), label="terminal result JSON")
    result = parse_json_object_bytes(result_raw, result_json_path)
    _validate_predecessor(
        result=result,
        sequence_index=sequence_index,
        ordered_manifest_wing_ids=ordered,
        campaign_publication_id=campaign_id,
        bucket=bucket,
        previous_entry_sha256=previous_entry_sha256,
        previous_entry_object=previous_entry_object,
    )

    # Build once with a non-null placeholder VersionId.  Version identity is
    # outside /entry, so these are exactly the bytes subsequently put to S3.
    draft_export = build_publication_export(
        result_json_bytes=result_raw,
        sequence_index=sequence_index,
        ordered_manifest_wing_ids=ordered,
        previous_entry_sha256=previous_entry_sha256,
        expected_campaign_publication_id=campaign_id,
        bucket=bucket,
        version_id="pending-version-id",
        allow_recovery=allow_recovery,
    )
    entry_raw = canonical_json_bytes(draft_export["entry"])

    with tempfile.TemporaryDirectory(prefix="wingbench-publication-") as temporary_name:
        temporary = Path(temporary_name)
        snapshots = temporary / "artifacts"
        snapshots.mkdir(mode=0o700)
        unique_artifacts: dict[str, tuple[Path, int]] = {}
        records = _artifact_records(result)
        for record_index, record in enumerate(records):
            label = f"artifact record {record_index}"
            _validate_artifact_record_shape(record, label=label)
            if record["present"] is not True:
                continue
            candidate = snapshots / f"record-{record_index:06d}"
            digest, size = _snapshot_artifact(
                record,
                destination=candidate,
                label=label,
            )
            previous = unique_artifacts.get(digest)
            if previous is None:
                unique_artifacts[digest] = (candidate, size)
            else:
                candidate.unlink()
                if previous[1] != size:
                    raise AwsCampaignPublicationError(
                        "duplicate artifact digest has inconsistent sizes"
                    )

        artifact_objects: list[dict[str, str]] = []
        for digest in sorted(unique_artifacts):
            snapshot, size = unique_artifacts[digest]
            artifact_objects.append(
                _put_artifact_or_verify_existing(
                    runner=writer_runner,
                    bucket=bucket,
                    key=artifact_object_key(campaign_id, digest),
                    body=snapshot,
                    retain_until=retain_until,
                    expected_digest=digest,
                    expected_size=size,
                    reuse_download_path=temporary / f"existing-{digest}",
                )
            )

        entry_path = temporary / "terminal-entry.json"
        _write_atomic_no_overwrite(entry_path, entry_raw)
        object_identity = _put_object_once(
            runner=writer_runner,
            bucket=bucket,
            key=publication_object_key(
                campaign_id, sequence_index, ordered[sequence_index]
            ),
            body=entry_path,
            retain_until=retain_until,
            label=f"conditional terminal publication {sequence_index}",
        )

    export = build_publication_export(
        result_json_bytes=result_raw,
        sequence_index=sequence_index,
        ordered_manifest_wing_ids=ordered,
        previous_entry_sha256=previous_entry_sha256,
        expected_campaign_publication_id=campaign_id,
        bucket=bucket,
        version_id=object_identity["version_id"],
        allow_recovery=allow_recovery,
    )
    if export["entry"] != draft_export["entry"] or canonical_json_bytes(
        export["entry"]
    ) != entry_raw:
        raise AwsCampaignPublicationError("terminal entry changed after S3 publication")
    journal_path = (
        Path(journal_root)
        / PUBLICATION_LEDGER_DIRECTORY
        / publication_entry_filename(sequence_index, ordered[sequence_index])
    )
    journal_raw = json.dumps(
        export,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8") + b"\n"
    _write_atomic_no_overwrite(journal_path, journal_raw)
    return {
        "entry_sha256": export["entry_sha256"],
        "object": export["object"],
        "artifact_objects": artifact_objects,
        "retain_until": retain_until,
        "journal_path": str(journal_path.resolve()),
    }


def _list_all_object_versions(
    *,
    runner: AwsCliRunner,
    bucket: str,
    prefix: str,
    label: str,
) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]], int]:
    versions: list[Mapping[str, Any]] = []
    delete_markers: list[Mapping[str, Any]] = []
    key_marker: str | None = None
    version_marker: str | None = None
    seen_markers: set[tuple[str | None, str | None]] = set()
    page_count = 0
    while True:
        marker = (key_marker, version_marker)
        if marker in seen_markers:
            raise AwsCampaignPublicationError(f"{label} pagination marker repeated")
        seen_markers.add(marker)
        arguments = [
            "s3api",
            "list-object-versions",
            "--bucket",
            bucket,
            "--prefix",
            prefix,
            "--no-paginate",
            "--output",
            "json",
        ]
        if key_marker is not None:
            arguments.extend(["--key-marker", key_marker])
        if version_marker is not None:
            arguments.extend(["--version-id-marker", version_marker])
        page = _run_json(runner, arguments, f"{label} page {page_count + 1}")
        page_count += 1
        page_versions = page.get("Versions", [])
        page_deletes = page.get("DeleteMarkers", [])
        truncated = page.get("IsTruncated")
        if not isinstance(page_versions, list) or not all(
            isinstance(row, Mapping) for row in page_versions
        ):
            raise AwsCampaignPublicationError(f"{label} returned malformed Versions")
        if not isinstance(page_deletes, list) or not all(
            isinstance(row, Mapping) for row in page_deletes
        ):
            raise AwsCampaignPublicationError(f"{label} returned malformed DeleteMarkers")
        if type(truncated) is not bool:
            raise AwsCampaignPublicationError(f"{label} lacks explicit IsTruncated")
        versions.extend(page_versions)
        delete_markers.extend(page_deletes)
        if truncated is False:
            break
        next_key = page.get("NextKeyMarker")
        next_version = page.get("NextVersionIdMarker")
        if not isinstance(next_key, str) or not next_key:
            raise AwsCampaignPublicationError(
                f"{label} truncated page lacks NextKeyMarker"
            )
        if next_version is not None and not isinstance(next_version, str):
            raise AwsCampaignPublicationError(
                f"{label} has invalid NextVersionIdMarker"
            )
        key_marker = next_key
        version_marker = next_version
    return versions, delete_markers, page_count


def _normalized_version_listing(
    *,
    versions: Sequence[Mapping[str, Any]],
    delete_markers: Sequence[Mapping[str, Any]],
    label: str,
) -> tuple[tuple[tuple[Any, ...], ...], tuple[tuple[Any, ...], ...]]:
    """Normalize the security-relevant fields of one complete S3 listing.

    The exporter deliberately compares two independently paginated observations.
    Keeping multiplicity in the sorted tuples makes an added duplicate version or
    delete marker visible instead of collapsing it into a set.
    """
    normalized_versions: list[tuple[Any, ...]] = []
    for index, row in enumerate(versions):
        key = row.get("Key")
        version_id = row.get("VersionId")
        is_latest = row.get("IsLatest")
        size = row.get("Size")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or type(is_latest) is not bool
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise AwsCampaignPublicationError(
                f"{label} version {index} has malformed identity metadata"
            )
        modified = _parse_aws_timestamp(
            row.get("LastModified"), f"{label} version {index} LastModified"
        )
        normalized_versions.append(
            (key, version_id, is_latest, _format_utc(modified), size)
        )

    normalized_deletes: list[tuple[Any, ...]] = []
    for index, row in enumerate(delete_markers):
        key = row.get("Key")
        version_id = row.get("VersionId")
        is_latest = row.get("IsLatest")
        if (
            not isinstance(key, str)
            or not key
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or type(is_latest) is not bool
        ):
            raise AwsCampaignPublicationError(
                f"{label} delete marker {index} has malformed identity metadata"
            )
        modified = _parse_aws_timestamp(
            row.get("LastModified"),
            f"{label} delete marker {index} LastModified",
        )
        normalized_deletes.append(
            (key, version_id, is_latest, _format_utc(modified))
        )
    return tuple(sorted(normalized_versions)), tuple(sorted(normalized_deletes))


def _validate_bucket_controls(*, runner: AwsCliRunner, bucket: str) -> None:
    versioning = _run_json(
        runner,
        [
            "s3api",
            "get-bucket-versioning",
            "--bucket",
            bucket,
            "--output",
            "json",
        ],
        "bucket versioning observation",
    )
    if versioning.get("Status") != "Enabled":
        raise AwsCampaignPublicationError("publication bucket versioning is not Enabled")
    lock = _run_json(
        runner,
        [
            "s3api",
            "get-object-lock-configuration",
            "--bucket",
            bucket,
            "--output",
            "json",
        ],
        "bucket Object Lock observation",
    )
    configuration = lock.get("ObjectLockConfiguration")
    if not isinstance(configuration, Mapping) or configuration.get(
        "ObjectLockEnabled"
    ) != "Enabled":
        raise AwsCampaignPublicationError("publication bucket Object Lock is not Enabled")


def _single_versions_by_expected_key(
    *,
    versions: Sequence[Mapping[str, Any]],
    delete_markers: Sequence[Mapping[str, Any]],
    expected_keys: set[str],
    label: str,
) -> dict[str, Mapping[str, Any]]:
    if delete_markers:
        raise AwsCampaignPublicationError(f"{label} contains a delete marker")
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in versions:
        key = row.get("Key")
        version_id = row.get("VersionId")
        if (
            not isinstance(key, str)
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
        ):
            raise AwsCampaignPublicationError(f"{label} contains an invalid object version")
        grouped.setdefault(key, []).append(row)
    observed_keys = set(grouped)
    if observed_keys != expected_keys:
        missing = sorted(expected_keys - observed_keys)
        extra = sorted(observed_keys - expected_keys)
        raise AwsCampaignPublicationError(
            f"{label} key inventory changed (missing={missing}, extra={extra})"
        )
    for key, rows in grouped.items():
        identities = {str(row.get("VersionId")) for row in rows}
        if len(rows) != 1 or len(identities) != 1:
            raise AwsCampaignPublicationError(
                f"{label} key {key} has duplicate object versions"
            )
        if rows[0].get("IsLatest") is not True:
            raise AwsCampaignPublicationError(f"{label} key {key} is not its sole latest version")
    return {key: rows[0] for key, rows in grouped.items()}


def _artifact_versions_by_key(
    *,
    versions: Sequence[Mapping[str, Any]],
    delete_markers: Sequence[Mapping[str, Any]],
    campaign_publication_id: str,
    referenced_keys: set[str],
) -> tuple[dict[str, Mapping[str, Any]], dict[str, Mapping[str, Any]]]:
    """Validate the whole artifact prefix while allowing harmless digest extras."""
    if delete_markers:
        raise AwsCampaignPublicationError("publication artifacts contain a delete marker")
    prefix = f"campaigns/{campaign_publication_id}/{ARTIFACT_DIRECTORY}/"
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in versions:
        key = row.get("Key")
        version_id = row.get("VersionId")
        if (
            not isinstance(key, str)
            or not key.startswith(prefix)
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
        ):
            raise AwsCampaignPublicationError(
                "publication artifacts contain an invalid object version"
            )
        digest = key[len(prefix):]
        if _SHA256_RE.fullmatch(digest) is None or key != artifact_object_key(
            campaign_publication_id, digest
        ):
            raise AwsCampaignPublicationError(
                "publication artifacts contain a non-digest object key"
            )
        grouped.setdefault(key, []).append(row)

    missing = sorted(referenced_keys - set(grouped))
    if missing:
        raise AwsCampaignPublicationError(
            f"publication artifacts key inventory changed (missing={missing})"
        )
    for key, rows in grouped.items():
        identities = {str(row.get("VersionId")) for row in rows}
        if len(rows) != 1 or len(identities) != 1:
            raise AwsCampaignPublicationError(
                f"publication artifacts key {key} has duplicate object versions"
            )
        row = rows[0]
        size = row.get("Size")
        _parse_aws_timestamp(
            row.get("LastModified"), f"publication artifact {key} LastModified"
        )
        if (
            row.get("IsLatest") is not True
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
        ):
            raise AwsCampaignPublicationError(
                f"publication artifacts key {key} is not one sole latest version"
            )
    all_versions = {key: rows[0] for key, rows in grouped.items()}
    return (
        {key: all_versions[key] for key in referenced_keys},
        all_versions,
    )


def _download_exact_version(
    *,
    runner: AwsCliRunner,
    bucket: str,
    row: Mapping[str, Any],
    destination: Path,
    observed_at: datetime,
    label: str,
    load_bytes: bool = True,
) -> tuple[bytes | None, dict[str, Any]]:
    key = row.get("Key")
    version_id = row.get("VersionId")
    if not isinstance(key, str) or not isinstance(version_id, str):
        raise AwsCampaignPublicationError(f"{label} version identity is invalid")
    if destination.exists() or destination.is_symlink():
        raise AwsCampaignPublicationError(f"refusing to overwrite download {destination}")
    response = _run_json(
        runner,
        [
            "s3api",
            "get-object",
            "--bucket",
            bucket,
            "--key",
            key,
            "--version-id",
            version_id,
            "--output",
            "json",
            str(destination),
        ],
        label,
    )
    if response.get("VersionId") != version_id:
        raise AwsCampaignPublicationError(f"{label} returned a different VersionId")
    last_modified = _parse_aws_timestamp(response.get("LastModified"), f"{label} LastModified")
    retention_response = _run_json(
        runner,
        [
            "s3api",
            "get-object-retention",
            "--bucket",
            bucket,
            "--key",
            key,
            "--version-id",
            version_id,
            "--output",
            "json",
        ],
        f"{label} GetObjectRetention",
    )
    retention = retention_response.get("Retention")
    if not isinstance(retention, Mapping):
        raise AwsCampaignPublicationError(f"{label} retention observation is malformed")
    retain_until = _parse_aws_timestamp(
        retention.get("RetainUntilDate"), f"{label} retention"
    )
    if retention.get("Mode") != "COMPLIANCE":
        raise AwsCampaignPublicationError(f"{label} is not COMPLIANCE locked")
    if (
        last_modified > observed_at
        or retain_until < last_modified + timedelta(days=MINIMUM_RETENTION_DAYS)
        or retain_until <= observed_at
    ):
        raise AwsCampaignPublicationError(f"{label} retention is insufficient")
    listed_last_modified = _parse_aws_timestamp(
        row.get("LastModified"), f"{label} listed LastModified"
    )
    if listed_last_modified != last_modified:
        raise AwsCampaignPublicationError(f"{label} LastModified changed across observations")
    size, object_digest = _hash_regular_file(destination, label=label)
    content_length = response.get("ContentLength")
    listed_size = row.get("Size")
    if (
        isinstance(content_length, bool)
        or not isinstance(content_length, int)
        or content_length != size
        or isinstance(listed_size, bool)
        or not isinstance(listed_size, int)
        or listed_size != size
    ):
        raise AwsCampaignPublicationError(f"{label} size changed across observations")
    metadata = {
        "key": key,
        "version_id": version_id,
        "is_latest": True,
        "last_modified": _format_utc(last_modified),
        "size_bytes": size,
        "object_sha256": object_digest,
        "object_lock_mode": "COMPLIANCE",
        "retain_until": _format_utc(retain_until),
    }
    raw = _exact_file_bytes(destination, label=label) if load_bytes else None
    return raw, metadata


def _observe_exact_version_retention(
    *,
    runner: AwsCliRunner,
    bucket: str,
    row: Mapping[str, Any],
    observed_at: datetime,
    label: str,
) -> dict[str, Any]:
    """Check one unreferenced artifact version without downloading its bytes."""
    key = row.get("Key")
    version_id = row.get("VersionId")
    size = row.get("Size")
    if (
        not isinstance(key, str)
        or not isinstance(version_id, str)
        or not version_id
        or version_id == "null"
        or row.get("IsLatest") is not True
        or isinstance(size, bool)
        or not isinstance(size, int)
        or size < 0
    ):
        raise AwsCampaignPublicationError(f"{label} version identity is invalid")
    last_modified = _parse_aws_timestamp(
        row.get("LastModified"), f"{label} listed LastModified"
    )
    retention_response = _run_json(
        runner,
        [
            "s3api",
            "get-object-retention",
            "--bucket",
            bucket,
            "--key",
            key,
            "--version-id",
            version_id,
            "--output",
            "json",
        ],
        f"{label} GetObjectRetention",
    )
    retention = retention_response.get("Retention")
    if not isinstance(retention, Mapping) or retention.get("Mode") != "COMPLIANCE":
        raise AwsCampaignPublicationError(f"{label} is not COMPLIANCE locked")
    retain_until = _parse_aws_timestamp(
        retention.get("RetainUntilDate"), f"{label} retention"
    )
    if (
        last_modified > observed_at
        or retain_until < last_modified + timedelta(days=MINIMUM_RETENTION_DAYS)
        or retain_until <= observed_at
    ):
        raise AwsCampaignPublicationError(f"{label} retention is insufficient")
    digest = key.rsplit("/", 1)[-1]
    if _SHA256_RE.fullmatch(digest) is None:
        raise AwsCampaignPublicationError(f"{label} lacks a digest-key identity")
    return {
        "key": key,
        "version_id": version_id,
        "is_latest": True,
        "last_modified": _format_utc(last_modified),
        "size_bytes": size,
        # Orphan bytes are intentionally not downloaded.  Their digest identity
        # is the content-addressed key fixed by the publication namespace.
        "object_sha256": digest,
        "object_lock_mode": "COMPLIANCE",
        "retain_until": _format_utc(retain_until),
    }


def preflight_campaign_publication(
    *,
    ordered_manifest_wing_ids: Sequence[str],
    campaign_publication_id: str,
    publication_bucket: str,
    start_sequence_index: int,
    previous_entry_sha256: str | None,
    previous_entry_object: Mapping[str, Any] | None,
    reader_runner: AwsCliRunner,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Verify the exact durable campaign head before any grading compute starts.

    The caller must use credentials that can read bucket controls, list versions,
    and read the retained predecessor.  This is an observation gate rather than a
    lock; the writer's conditional terminal PUT remains the final concurrency gate.
    """
    campaign_id = _safe_component(campaign_publication_id, "campaign publication ID")
    bucket = _bucket_name(publication_bucket)
    ordered = list(ordered_manifest_wing_ids)
    start = start_sequence_index
    if (
        not ordered
        or len(set(ordered)) != len(ordered)
        or not all(
            isinstance(wing_id, str) and _SAFE_COMPONENT_RE.fullmatch(wing_id)
            for wing_id in ordered
        )
        or isinstance(start, bool)
        or not isinstance(start, int)
        or start < 0
        or start >= len(ordered)
    ):
        raise AwsCampaignPublicationError("publication preflight roster or start is invalid")
    if start == 0:
        if previous_entry_sha256 is not None or previous_entry_object is not None:
            raise AwsCampaignPublicationError(
                "sequence-zero publication preflight requires a null predecessor"
            )
    elif (
        not isinstance(previous_entry_sha256, str)
        or _SHA256_RE.fullmatch(previous_entry_sha256) is None
        or not isinstance(previous_entry_object, Mapping)
    ):
        raise AwsCampaignPublicationError(
            "recovery publication preflight requires an exact predecessor"
        )

    observation_started_at = _utc(clock())
    _validate_bucket_controls(runner=reader_runner, bucket=bucket)
    ledger_prefix = f"campaigns/{campaign_id}/{PUBLICATION_LEDGER_DIRECTORY}/"
    versions, delete_markers, pages = _list_all_object_versions(
        runner=reader_runner,
        bucket=bucket,
        prefix=ledger_prefix,
        label="publication preflight ledger ListObjectVersions",
    )
    expected_keys = {
        publication_object_key(campaign_id, index, ordered[index])
        for index in range(start)
    }
    by_key = _single_versions_by_expected_key(
        versions=versions,
        delete_markers=delete_markers,
        expected_keys=expected_keys,
        label="publication preflight ledger",
    )
    initial_observation = _normalized_version_listing(
        versions=versions,
        delete_markers=delete_markers,
        label="publication preflight ledger",
    )

    predecessor_metadata: dict[str, Any] | None = None
    if start > 0:
        assert isinstance(previous_entry_sha256, str)
        assert isinstance(previous_entry_object, Mapping)
        predecessor_key = publication_object_key(
            campaign_id, start - 1, ordered[start - 1]
        )
        expected_object_keys = {
            "storage_provider", "bucket", "key", "version_id"
        }
        if set(previous_entry_object) != expected_object_keys:
            raise AwsCampaignPublicationError(
                "publication preflight predecessor object structure changed"
            )
        listed = by_key[predecessor_key]
        expected_object = {
            "storage_provider": "aws_s3",
            "bucket": bucket,
            "key": predecessor_key,
            "version_id": listed.get("VersionId"),
        }
        if dict(previous_entry_object) != expected_object:
            raise AwsCampaignPublicationError(
                "publication preflight predecessor object is not the listed campaign head"
            )
        with tempfile.TemporaryDirectory(prefix="wingbench-publication-preflight-") as name:
            raw, predecessor_metadata = _download_exact_version(
                runner=reader_runner,
                bucket=bucket,
                row=listed,
                destination=Path(name) / "predecessor-entry.json",
                observed_at=observation_started_at,
                label="publication preflight predecessor",
            )
        if raw is None:
            raise AssertionError("preflight predecessor download unexpectedly omitted bytes")
        entry = parse_json_object_bytes(raw, "publication preflight predecessor")
        if (
            raw != canonical_json_bytes(entry)
            or entry.get("schema_version") != PUBLICATION_ENTRY_SCHEMA_VERSION
            or entry.get("campaign_publication_id") != campaign_id
            or entry.get("sequence_index") != start - 1
            or entry.get("manifest_wing_id") != ordered[start - 1]
            or protocol_sha256(entry) != previous_entry_sha256
        ):
            raise AwsCampaignPublicationError(
                "publication preflight predecessor bytes or hash changed"
            )

    final_versions, final_deletes, final_pages = _list_all_object_versions(
        runner=reader_runner,
        bucket=bucket,
        prefix=ledger_prefix,
        label="publication preflight final ledger ListObjectVersions",
    )
    final_observation = _normalized_version_listing(
        versions=final_versions,
        delete_markers=final_deletes,
        label="publication preflight final ledger",
    )
    if final_observation != initial_observation:
        raise AwsCampaignPublicationError(
            "publication ledger changed during pre-compute preflight"
        )
    observed_at = _utc(clock())
    if observed_at < observation_started_at:
        raise AwsCampaignPublicationError(
            "reader clock moved backwards during publication preflight"
        )
    if predecessor_metadata is not None:
        _metadata_valid_at(
            predecessor_metadata,
            observed_at=observed_at,
            label="publication preflight predecessor",
        )
    return {
        "preflight_status": "passed",
        "campaign_publication_id": campaign_id,
        "publication_bucket": bucket,
        "start_sequence_index": start,
        "verified_prefix_entry_count": start,
        "remaining_suffix_entry_count": len(ordered) - start,
        "previous_entry_sha256": previous_entry_sha256,
        "previous_entry_object": (
            dict(previous_entry_object) if previous_entry_object is not None else None
        ),
        "bucket_versioning_status": "Enabled",
        "bucket_object_lock_status": "Enabled",
        "initial_list_pages": pages,
        "final_list_pages": final_pages,
        "observed_at": _format_utc(observed_at),
    }


def _metadata_valid_at(
    metadata: Mapping[str, Any], *, observed_at: datetime, label: str
) -> None:
    last_modified = _parse_aws_timestamp(
        metadata.get("last_modified"), f"{label} last_modified"
    )
    retain_until = _parse_aws_timestamp(
        metadata.get("retain_until"), f"{label} retain_until"
    )
    if (
        last_modified > observed_at
        or retain_until < last_modified + timedelta(days=MINIMUM_RETENTION_DAYS)
        or retain_until <= observed_at
    ):
        raise AwsCampaignPublicationError(
            f"{label} was not sufficiently retained at completed observation time"
        )


def export_campaign(
    *,
    ordered_manifest_wing_ids: Sequence[str],
    campaign_publication_id: str,
    publication_bucket: str,
    allow_recovery: bool,
    destination_root: Path,
    reader_runner: AwsCliRunner,
    clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
) -> dict[str, Any]:
    """Export and independently verify one complete AWS campaign namespace."""
    campaign_id = _safe_component(campaign_publication_id, "campaign publication ID")
    bucket = _bucket_name(publication_bucket)
    ordered = list(ordered_manifest_wing_ids)
    if (
        not ordered
        or len(set(ordered)) != len(ordered)
        or not all(
            isinstance(wing_id, str) and _SAFE_COMPONENT_RE.fullmatch(wing_id)
            for wing_id in ordered
        )
    ):
        raise AwsCampaignPublicationError("publication roster is invalid")
    destination = Path(destination_root).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() or destination.is_symlink():
        raise AwsCampaignPublicationError(
            f"refusing to overwrite campaign export root: {destination}"
        )
    observation_started_at = _utc(clock())
    _validate_bucket_controls(runner=reader_runner, bucket=bucket)

    ledger_prefix = f"campaigns/{campaign_id}/{PUBLICATION_LEDGER_DIRECTORY}/"
    ledger_versions, ledger_deletes, ledger_pages = _list_all_object_versions(
        runner=reader_runner,
        bucket=bucket,
        prefix=ledger_prefix,
        label="publication ledger ListObjectVersions",
    )
    expected_ledger_keys = {
        publication_object_key(campaign_id, index, wing_id)
        for index, wing_id in enumerate(ordered)
    }
    ledger_by_key = _single_versions_by_expected_key(
        versions=ledger_versions,
        delete_markers=ledger_deletes,
        expected_keys=expected_ledger_keys,
        label="publication ledger",
    )
    initial_ledger_observation = _normalized_version_listing(
        versions=ledger_versions,
        delete_markers=ledger_deletes,
        label="publication ledger",
    )

    staging = Path(
        tempfile.mkdtemp(prefix=f".{destination.name}.staging-", dir=destination.parent)
    )
    lock_path = destination.parent / f".{destination.name}.export.lock"
    lock_descriptor: int | None = None
    installed = False
    try:
        ledger_directory = staging / PUBLICATION_LEDGER_DIRECTORY
        ledger_directory.mkdir(mode=0o700)
        artifact_directory = staging / ARTIFACT_DIRECTORY
        artifact_directory.mkdir(parents=True, mode=0o700)
        downloads = staging / ".downloads"
        downloads.mkdir(mode=0o700)

        exports: list[dict[str, Any]] = []
        ledger_inventory_versions: list[dict[str, Any]] = []
        for index, wing_id in enumerate(ordered):
            key = publication_object_key(campaign_id, index, wing_id)
            raw, metadata = _download_exact_version(
                runner=reader_runner,
                bucket=bucket,
                row=ledger_by_key[key],
                destination=downloads / f"ledger-{index:06d}",
                observed_at=observation_started_at,
                label=f"publication ledger version {index}",
            )
            if raw is None:
                raise AssertionError("ledger download unexpectedly omitted bytes")
            entry = parse_json_object_bytes(raw, f"publication ledger version {index}")
            if raw != canonical_json_bytes(entry):
                raise AwsCampaignPublicationError(
                    f"publication ledger version {index} is not canonical entry bytes"
                )
            if entry.get("schema_version") != PUBLICATION_ENTRY_SCHEMA_VERSION:
                raise AwsCampaignPublicationError(
                    f"publication ledger version {index} entry schema changed"
                )
            export = {
                "schema_version": PUBLICATION_EXPORT_SCHEMA_VERSION,
                "entry": entry,
                "entry_sha256": protocol_sha256(entry),
                "object": {
                    "storage_provider": "aws_s3",
                    "bucket": bucket,
                    "key": key,
                    "version_id": metadata["version_id"],
                },
            }
            exports.append(export)
            # Shared inventory hashes /entry, which is exactly the S3 object.
            metadata["object_sha256"] = protocol_sha256(entry)
            ledger_inventory_versions.append(metadata)
            _write_atomic_no_overwrite(
                ledger_directory / publication_entry_filename(index, wing_id),
                json.dumps(
                    export,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n",
            )

        try:
            artifact_expectations = _shared_artifact_expectations_from_exports(exports)
        except CampaignPublicationError as exc:
            raise AwsCampaignPublicationError(
                f"publication artifact requirements are invalid: {exc}"
            ) from exc
        artifact_prefix = f"campaigns/{campaign_id}/{ARTIFACT_DIRECTORY}/"
        artifact_versions, artifact_deletes, artifact_pages = _list_all_object_versions(
            runner=reader_runner,
            bucket=bucket,
            prefix=artifact_prefix,
            label="publication artifact ListObjectVersions",
        )
        expected_artifact_keys = {
            artifact_object_key(campaign_id, digest)
            for digest in artifact_expectations
        }
        artifact_by_key, all_artifact_by_key = _artifact_versions_by_key(
            versions=artifact_versions,
            delete_markers=artifact_deletes,
            campaign_publication_id=campaign_id,
            referenced_keys=expected_artifact_keys,
        )
        initial_artifact_observation = _normalized_version_listing(
            versions=artifact_versions,
            delete_markers=artifact_deletes,
            label="publication artifacts",
        )
        artifact_inventory_versions: list[dict[str, Any]] = []
        for digest in sorted(artifact_expectations):
            key = artifact_object_key(campaign_id, digest)
            artifact_download = downloads / f"artifact-{digest}"
            raw, metadata = _download_exact_version(
                runner=reader_runner,
                bucket=bucket,
                row=artifact_by_key[key],
                destination=artifact_download,
                observed_at=observation_started_at,
                label=f"publication artifact {digest}",
                load_bytes=False,
            )
            if (
                raw is not None
                or metadata["size_bytes"] != artifact_expectations[digest]
                or metadata["object_sha256"] != digest
            ):
                raise AwsCampaignPublicationError(
                    f"publication artifact {digest} bytes changed"
                )
            artifact_inventory_versions.append(metadata)
            _link_verified_file_no_overwrite(
                artifact_download, artifact_directory / digest
            )

        extra_artifact_metadata: list[dict[str, Any]] = []
        for key in sorted(set(all_artifact_by_key) - expected_artifact_keys):
            digest = key.rsplit("/", 1)[-1]
            extra_artifact_metadata.append(
                _observe_exact_version_retention(
                    runner=reader_runner,
                    bucket=bucket,
                    row=all_artifact_by_key[key],
                    observed_at=observation_started_at,
                    label=f"unreferenced publication artifact {digest}",
                )
            )

        final_ledger_versions, final_ledger_deletes, final_ledger_pages = (
            _list_all_object_versions(
                runner=reader_runner,
                bucket=bucket,
                prefix=ledger_prefix,
                label="final publication ledger ListObjectVersions",
            )
        )
        final_artifact_versions, final_artifact_deletes, final_artifact_pages = (
            _list_all_object_versions(
                runner=reader_runner,
                bucket=bucket,
                prefix=artifact_prefix,
                label="final publication artifact ListObjectVersions",
            )
        )
        final_ledger_observation = _normalized_version_listing(
            versions=final_ledger_versions,
            delete_markers=final_ledger_deletes,
            label="final publication ledger",
        )
        final_artifact_observation = _normalized_version_listing(
            versions=final_artifact_versions,
            delete_markers=final_artifact_deletes,
            label="final publication artifacts",
        )
        if final_ledger_observation != initial_ledger_observation:
            raise AwsCampaignPublicationError(
                "publication ledger changed during export observation"
            )
        if final_artifact_observation != initial_artifact_observation:
            raise AwsCampaignPublicationError(
                "publication artifacts changed during export observation"
            )

        observed_at = _utc(clock())
        if observed_at < observation_started_at:
            raise AwsCampaignPublicationError("reader clock moved backwards during export")
        for index, metadata in enumerate(ledger_inventory_versions):
            _metadata_valid_at(
                metadata,
                observed_at=observed_at,
                label=f"publication ledger version {index}",
            )
        for index, metadata in enumerate(artifact_inventory_versions):
            _metadata_valid_at(
                metadata,
                observed_at=observed_at,
                label=f"publication artifact version {index}",
            )
        for index, metadata in enumerate(extra_artifact_metadata):
            _metadata_valid_at(
                metadata,
                observed_at=observed_at,
                label=f"unreferenced publication artifact version {index}",
            )

        all_artifact_metadata = sorted(
            [*artifact_inventory_versions, *extra_artifact_metadata],
            key=lambda row: str(row["key"]),
        )
        inventory = {
            "schema_version": PUBLICATION_INVENTORY_SCHEMA_VERSION,
            "campaign_publication_id": campaign_id,
            "bucket": bucket,
            "prefix": ledger_prefix,
            "observation_trust_model": INVENTORY_OBSERVATION_TRUST_MODEL,
            "observed_at": _format_utc(observed_at),
            "aws_observation_apis": [
                "s3:ListObjectVersions",
                "s3:GetObjectRetention",
            ],
            "list_object_versions_complete": True,
            "is_truncated": False,
            "conditional_create_header": "If-None-Match: *",
            "versioning_status": "Enabled",
            "required_object_lock_mode": "COMPLIANCE",
            "minimum_retention_days": MINIMUM_RETENTION_DAYS,
            "delete_markers": [],
            "versions": ledger_inventory_versions,
            "artifact_prefix": artifact_prefix,
            "artifact_list_object_versions_complete": True,
            "artifact_is_truncated": False,
            "artifact_delete_markers": [],
            "artifact_versions": all_artifact_metadata,
        }
        _write_atomic_no_overwrite(
            staging / PUBLICATION_INVENTORY_FILENAME,
            json.dumps(
                inventory,
                indent=2,
                sort_keys=True,
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            + b"\n",
        )
        shutil.rmtree(downloads)

        # The shared validator replays the hash chain and exact embedded result
        # authorization before anything becomes visible at destination_root.
        ledger_record, documents = load_campaign_publication_export(
            staging,
            ordered_manifest_wing_ids=ordered,
            expected_campaign_publication_id=campaign_id,
            expected_publication_bucket=bucket,
            allow_recovery=allow_recovery,
            require_exact_root=False,
        )
        lock_descriptor = os.open(
            lock_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        if destination.exists() or destination.is_symlink():
            raise AwsCampaignPublicationError(
                f"refusing to overwrite campaign export root: {destination}"
            )
        os.rename(staging, destination)
        installed = True
        directory_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        return {
            "destination_root": str(destination),
            "entry_count": len(exports),
            "artifact_count": len(artifact_expectations),
            "terminal_entry_sha256": ledger_record["terminal_entry_sha256"],
            "ledger_list_pages": ledger_pages,
            "artifact_list_pages": artifact_pages,
            "ledger_final_list_pages": final_ledger_pages,
            "artifact_final_list_pages": final_artifact_pages,
            "artifact_observed_count": len(all_artifact_by_key),
            "artifact_unreferenced_count": (
                len(all_artifact_by_key) - len(expected_artifact_keys)
            ),
            "result_ids": sorted(documents),
            "artifact_inventory_persisted": True,
        }
    except Exception:
        if not installed:
            shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if lock_descriptor is not None:
            os.close(lock_descriptor)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass


def _ordered_ids(path: Path) -> list[str]:
    raw = _exact_file_bytes(path, label="ordered wing IDs JSON")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AwsCampaignPublicationError("ordered wing IDs file is invalid JSON") from exc
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise AwsCampaignPublicationError("ordered wing IDs file must contain a JSON list")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    publish = subparsers.add_parser("publish-result")
    publish.add_argument("--result-json", type=Path, required=True)
    publish.add_argument("--sequence-index", type=int, required=True)
    publish.add_argument("--ordered-wing-ids-json", type=Path, required=True)
    publish.add_argument("--previous-entry-sha256")
    publish.add_argument("--previous-entry-object-json", type=Path)
    publish.add_argument("--campaign-publication-id", required=True)
    publish.add_argument("--publication-bucket", required=True)
    publish.add_argument("--allow-recovery", action="store_true")
    publish.add_argument("--journal-root", type=Path, required=True)
    publish.add_argument(
        "--writer-profile",
        help="optional local AWS profile; omit to use EC2 instance-role credentials",
    )
    publish.add_argument("--region")
    publish.add_argument("--retention-days", type=int, default=DEFAULT_RETENTION_DAYS)

    export = subparsers.add_parser("export-campaign")
    export.add_argument("--ordered-wing-ids-json", type=Path, required=True)
    export.add_argument("--campaign-publication-id", required=True)
    export.add_argument("--publication-bucket", required=True)
    export.add_argument("--allow-recovery", action="store_true")
    export.add_argument("--destination-root", type=Path, required=True)
    export.add_argument(
        "--reader-profile",
        help="optional independent-reader AWS profile; omit for an attached reader role",
    )
    export.add_argument("--region")

    arguments = parser.parse_args()
    ordered = _ordered_ids(arguments.ordered_wing_ids_json)
    if arguments.command == "publish-result":
        previous_object = None
        if arguments.previous_entry_object_json is not None:
            previous_object = parse_json_object_bytes(
                _exact_file_bytes(
                    arguments.previous_entry_object_json,
                    label="previous entry object JSON",
                ),
                arguments.previous_entry_object_json,
            )
        receipt = publish_terminal_result(
            result_json_path=arguments.result_json,
            sequence_index=arguments.sequence_index,
            ordered_manifest_wing_ids=ordered,
            previous_entry_sha256=arguments.previous_entry_sha256,
            previous_entry_object=previous_object,
            campaign_publication_id=arguments.campaign_publication_id,
            publication_bucket=arguments.publication_bucket,
            allow_recovery=arguments.allow_recovery,
            journal_root=arguments.journal_root,
            writer_runner=SubprocessAwsCliRunner(
                profile=arguments.writer_profile, region=arguments.region
            ),
            retention_days=arguments.retention_days,
        )
        print(json.dumps(receipt, indent=2, sort_keys=True))
        return 0
    summary = export_campaign(
        ordered_manifest_wing_ids=ordered,
        campaign_publication_id=arguments.campaign_publication_id,
        publication_bucket=arguments.publication_bucket,
        allow_recovery=arguments.allow_recovery,
        destination_root=arguments.destination_root,
        reader_runner=SubprocessAwsCliRunner(
            profile=arguments.reader_profile, region=arguments.region
        ),
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
