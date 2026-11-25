#!/usr/bin/env python3
"""Shared immutable publication contract for CFD campaign terminal results.

One terminal result is one conditionally-created, fixed-index publication
object.  Its payload embeds the exact result JSON bytes and hash-chains the
previous terminal publication.  A campaign export adds the durable S3
bucket/key/version identity for every WORM object.  Consumers validate a
complete export; they never reconstruct first-publication history from mutable
worker result directories.

This module validates exported evidence but cannot itself prove that AWS
versioning, Object Lock retention, or put-only IAM were enabled.  Those are
external controls and are explicitly recorded as the trust boundary.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.cfd.grading_protocol import (
    ProtocolError,
    canonical_json_bytes,
    protocol_sha256,
)


PUBLICATION_LEDGER_DIRECTORY = "publication_ledger"
PUBLICATION_INVENTORY_FILENAME = "publication_inventory.json"
MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION = (
    "wingbench-cfd-manifest-publication-contract-v1"
)
PUBLICATION_CONTRACT_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-publication-contract-v1"
)
AUTHORIZATION_BINDING_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-publication-authorization-v1"
)
PUBLICATION_ENTRY_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-terminal-publication-v1"
)
PUBLICATION_EXPORT_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-terminal-publication-export-v1"
)
PUBLICATION_LEDGER_RECORD_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-publication-ledger-record-v2"
)
PUBLICATION_INVENTORY_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-publication-inventory-v2"
)
RESULT_JSON_ENCODING = "base64-exact-json-bytes"
LEDGER_HASH_SEMANTICS = "sha256(canonical compact sorted-key JSON at /entry)"
EXPORTED_WORM_TRUST_MODEL = (
    "exported_aws_s3_versioned_object_lock_ledger_with_external_enforcement"
)
INVENTORY_OBSERVATION_TRUST_MODEL = (
    "operator_generated_aws_api_observation_not_cryptographic_attestation"
)
MINIMUM_RETENTION_DAYS = 90

PUBLICATION_CONTRACT: dict[str, Any] = {
    "schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
    "ordered_roster": "frozen_manifest_order",
    "terminal_publication_atomicity": (
        "one conditionally-created fixed-index object embeds exact result JSON bytes"
    ),
    "conditional_create_header": "If-None-Match: *",
    "result_json_encoding": RESULT_JSON_ENCODING,
    "durable_object_bytes": "canonical compact sorted-key JSON entry payload",
    "hash_chain": LEDGER_HASH_SEMANTICS,
    "s3_versioning_status": "Enabled",
    "s3_object_lock_mode": "COMPLIANCE",
    "minimum_retention_days": MINIMUM_RETENTION_DAYS,
    "complete_inventory": (
        "persisted ListObjectVersions plus GetObjectRetention observations for "
        "both ledger and artifact prefixes; no delete markers or duplicate versions"
    ),
    "artifact_binding": (
        "referenced artifact digest and size requirements are derived from exact "
        "result JSON bytes embedded in the retained ledger"
    ),
    "publisher_permissions": "put_only_without_overwrite_or_delete",
    "terminal_slot_key": (
        "campaigns/{campaign_publication_id}/publication_ledger/"
        "{sequence_index:06d}-{manifest_wing_id}.json"
    ),
    "zero_terminal_runs": (
        "not represented because they published no terminal result"
    ),
}
PUBLICATION_CONTRACT_SHA256 = protocol_sha256(PUBLICATION_CONTRACT)


class CampaignPublicationError(ProtocolError):
    """A campaign publication binding or exported ledger is invalid."""


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


def parse_json_object_bytes(raw: bytes, label: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            raw,
            parse_constant=_reject_json_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise CampaignPublicationError(f"cannot read JSON {label}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CampaignPublicationError(f"expected a JSON object in {label}")
    return payload


def _read_json_object(path: Path) -> tuple[dict[str, Any], bytes]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise CampaignPublicationError(f"cannot read JSON {path}: {exc}") from exc
    return parse_json_object_bytes(raw, path), raw


def _exact_keys(value: Any, expected: set[str], label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != expected:
        raise CampaignPublicationError(f"{label} structure changed")
    return value


def _safe_component(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value) is None
        or value in {".", ".."}
    ):
        raise CampaignPublicationError(f"{label} must be a safe path component")
    return value


def publication_entry_filename(sequence_index: int, manifest_wing_id: str) -> str:
    if isinstance(sequence_index, bool) or not isinstance(sequence_index, int):
        raise CampaignPublicationError("publication sequence index must be an integer")
    if sequence_index < 0:
        raise CampaignPublicationError("publication sequence index cannot be negative")
    wing_id = _safe_component(manifest_wing_id, "manifest wing ID")
    return f"{sequence_index:06d}-{wing_id}.json"


def publication_object_key(
    campaign_publication_id: str,
    sequence_index: int,
    manifest_wing_id: str,
) -> str:
    campaign_id = _safe_component(
        campaign_publication_id, "campaign publication ID"
    )
    return (
        f"campaigns/{campaign_id}/{PUBLICATION_LEDGER_DIRECTORY}/"
        f"{publication_entry_filename(sequence_index, manifest_wing_id)}"
    )


def _validate_object_identity(
    value: Any,
    *,
    label: str,
    expected_key: str | None = None,
) -> dict[str, str]:
    identity = _exact_keys(
        value,
        {"storage_provider", "bucket", "key", "version_id"},
        label,
    )
    if (
        identity.get("storage_provider") != "aws_s3"
        or not isinstance(identity.get("bucket"), str)
        or not identity["bucket"]
        or not isinstance(identity.get("key"), str)
        or not identity["key"]
        or not isinstance(identity.get("version_id"), str)
        or not identity["version_id"]
        or identity["version_id"] == "null"
        or (expected_key is not None and identity["key"] != expected_key)
    ):
        raise CampaignPublicationError(f"{label} is not a durable versioned S3 object")
    return {
        "storage_provider": str(identity["storage_provider"]),
        "bucket": str(identity["bucket"]),
        "key": str(identity["key"]),
        "version_id": str(identity["version_id"]),
    }


def validate_manifest_publication_contract(
    manifest: Mapping[str, Any],
    *,
    ordered_manifest_wing_ids: Sequence[str],
    expected_recovery_allowed: bool | None = None,
) -> dict[str, Any]:
    """Return the immutable publication namespace fixed by one manifest."""
    ordered = _validate_ordered_ids(ordered_manifest_wing_ids)
    contract = _exact_keys(
        manifest.get("publication_contract"),
        {
            "schema_version",
            "campaign_publication_id",
            "publication_bucket",
            "publication_contract_schema_version",
            "publication_contract_sha256",
            "ordered_wing_ids_sha256",
            "recovery_allowed",
        },
        "manifest publication contract",
    )
    campaign_id = _safe_component(
        contract.get("campaign_publication_id"), "campaign publication ID"
    )
    bucket = contract.get("publication_bucket")
    recovery_allowed = contract.get("recovery_allowed")
    if (
        not isinstance(bucket, str)
        or not bucket
        or contract.get("schema_version")
        != MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION
        or contract.get("publication_contract_schema_version")
        != PUBLICATION_CONTRACT_SCHEMA_VERSION
        or contract.get("publication_contract_sha256")
        != PUBLICATION_CONTRACT_SHA256
        or contract.get("ordered_wing_ids_sha256") != protocol_sha256(ordered)
        or type(recovery_allowed) is not bool
        or (
            expected_recovery_allowed is not None
            and recovery_allowed is not expected_recovery_allowed
        )
    ):
        raise CampaignPublicationError(
            "manifest does not pin an executable publication namespace and contract"
        )
    return {
        "schema_version": MANIFEST_PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "campaign_publication_id": campaign_id,
        "publication_bucket": bucket,
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "ordered_wing_ids_sha256": protocol_sha256(ordered),
        "recovery_allowed": recovery_allowed,
    }


def validate_authorization_publication_binding(
    authorization: Mapping[str, Any],
    *,
    ordered_manifest_wing_ids: Sequence[str],
    requested_wing_ids: Sequence[str],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_recovery: bool,
) -> dict[str, Any]:
    """Validate the pre-compute publication gate embedded in authorization."""
    ordered = list(ordered_manifest_wing_ids)
    requested = list(requested_wing_ids)
    if (
        not ordered
        or len(set(ordered)) != len(ordered)
        or not all(isinstance(value, str) and value for value in ordered)
    ):
        raise CampaignPublicationError("manifest publication roster is invalid")
    binding = _exact_keys(
        authorization.get("campaign_publication"),
        {
            "schema_version",
            "campaign_publication_id",
            "publication_bucket",
            "publication_contract_schema_version",
            "publication_contract_sha256",
            "start_sequence_index",
            "previous_entry_sha256",
            "previous_entry_object",
        },
        "campaign publication authorization binding",
    )
    campaign_id = _safe_component(
        binding.get("campaign_publication_id"), "campaign publication ID"
    )
    expected_campaign_id = _safe_component(
        expected_campaign_publication_id, "expected campaign publication ID"
    )
    publication_bucket = binding.get("publication_bucket")
    start = binding.get("start_sequence_index")
    if (
        binding.get("schema_version") != AUTHORIZATION_BINDING_SCHEMA_VERSION
        or campaign_id != expected_campaign_id
        or not isinstance(publication_bucket, str)
        or publication_bucket != expected_publication_bucket
        or not publication_bucket
        or binding.get("publication_contract_schema_version")
        != PUBLICATION_CONTRACT_SCHEMA_VERSION
        or binding.get("publication_contract_sha256")
        != PUBLICATION_CONTRACT_SHA256
        or isinstance(start, bool)
        or not isinstance(start, int)
        or start < 0
        or start >= len(ordered)
        or requested != ordered[start:]
    ):
        raise CampaignPublicationError(
            "authorization does not bind the exact remaining publication suffix"
        )
    if not allow_recovery and start != 0:
        raise CampaignPublicationError(
            "this campaign forbids publication recovery after sequence zero"
        )
    previous_sha = binding.get("previous_entry_sha256")
    previous_object = binding.get("previous_entry_object")
    if start == 0:
        if previous_sha is not None or previous_object is not None:
            raise CampaignPublicationError(
                "sequence-zero authorization must have a null publication predecessor"
            )
        normalized_previous_object = None
    else:
        if not _full_sha256(previous_sha):
            raise CampaignPublicationError(
                "recovery authorization lacks the previous terminal entry SHA"
            )
        expected_key = publication_object_key(campaign_id, start - 1, ordered[start - 1])
        normalized_previous_object = _validate_object_identity(
            previous_object,
            label="recovery authorization predecessor object",
            expected_key=expected_key,
        )
        if normalized_previous_object["bucket"] != expected_publication_bucket:
            raise CampaignPublicationError(
                "recovery authorization predecessor is in the wrong publication bucket"
            )
    return {
        "schema_version": AUTHORIZATION_BINDING_SCHEMA_VERSION,
        "campaign_publication_id": campaign_id,
        "publication_bucket": publication_bucket,
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "start_sequence_index": start,
        "previous_entry_sha256": previous_sha,
        "previous_entry_object": normalized_previous_object,
    }


def _authorization_identity(result: Mapping[str, Any]) -> tuple[str, str, Mapping[str, Any]]:
    authorization = result.get("execution_authorization")
    if not isinstance(authorization, Mapping):
        raise CampaignPublicationError("published result lacks execution authorization")
    authorization_id = authorization.get("authorization_id")
    authorization_sha = authorization.get("canonical_sha256")
    snapshot = authorization.get("snapshot")
    if (
        not isinstance(authorization_id, str)
        or not authorization_id
        or not _full_sha256(authorization_sha)
        or not isinstance(snapshot, Mapping)
        or protocol_sha256(snapshot) != authorization_sha
    ):
        raise CampaignPublicationError("published result authorization identity is invalid")
    return authorization_id, str(authorization_sha), snapshot


def build_publication_export(
    *,
    result_json_bytes: bytes,
    sequence_index: int,
    ordered_manifest_wing_ids: Sequence[str],
    previous_entry_sha256: str | None,
    expected_campaign_publication_id: str,
    bucket: str,
    version_id: str,
    allow_recovery: bool,
) -> dict[str, Any]:
    """Build one exported terminal-publication envelope for a trusted exporter."""
    result = parse_json_object_bytes(result_json_bytes, "embedded result JSON")
    ordered = list(ordered_manifest_wing_ids)
    if sequence_index < 0 or sequence_index >= len(ordered):
        raise CampaignPublicationError("publication sequence index is outside the manifest")
    wing_id = ordered[sequence_index]
    if result.get("wing_id") != wing_id:
        raise CampaignPublicationError("published result is outside its fixed manifest slot")
    run_id = _safe_component(result.get("run_id"), "published result run ID")
    authorization_id, authorization_sha, authorization = _authorization_identity(result)
    requested = authorization.get("wing_ids")
    if not isinstance(requested, list):
        raise CampaignPublicationError("published result authorization lacks wing scope")
    binding = validate_authorization_publication_binding(
        authorization,
        ordered_manifest_wing_ids=ordered,
        requested_wing_ids=requested,
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=bucket,
        allow_recovery=allow_recovery,
    )
    if wing_id not in requested or sequence_index < binding["start_sequence_index"]:
        raise CampaignPublicationError("published result is outside its authorized suffix")
    if sequence_index == 0:
        if previous_entry_sha256 is not None:
            raise CampaignPublicationError("first publication cannot have a predecessor")
    elif not _full_sha256(previous_entry_sha256):
        raise CampaignPublicationError("publication hash chain is missing its predecessor")
    protocol_row = result.get("protocol")
    manifest_row = result.get("benchmark_manifest")
    protocol_sha = (
        protocol_row.get("canonical_sha256")
        if isinstance(protocol_row, Mapping)
        else None
    )
    manifest_sha = (
        manifest_row.get("canonical_sha256")
        if isinstance(manifest_row, Mapping)
        else None
    )
    if not _full_sha256(protocol_sha) or not _full_sha256(manifest_sha):
        raise CampaignPublicationError(
            "published result lacks protocol and manifest identities"
        )
    entry = {
        "schema_version": PUBLICATION_ENTRY_SCHEMA_VERSION,
        "publication_status": "terminal",
        "campaign_publication_id": binding["campaign_publication_id"],
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "sequence_index": sequence_index,
        "manifest_wing_id": wing_id,
        "protocol_sha256": protocol_sha,
        "manifest_sha256": manifest_sha,
        "run_id": run_id,
        "execution_authorization_id": authorization_id,
        "execution_authorization_sha256": authorization_sha,
        "previous_entry_sha256": previous_entry_sha256,
        "result_json_encoding": RESULT_JSON_ENCODING,
        "result_json_base64": base64.b64encode(result_json_bytes).decode("ascii"),
        "result_size_bytes": len(result_json_bytes),
        "result_file_sha256": hashlib.sha256(result_json_bytes).hexdigest(),
        "result_canonical_sha256": protocol_sha256(result),
    }
    object_identity = {
        "storage_provider": "aws_s3",
        "bucket": bucket,
        "key": publication_object_key(
            binding["campaign_publication_id"], sequence_index, wing_id
        ),
        "version_id": version_id,
    }
    _validate_object_identity(
        object_identity,
        label="publication ledger object",
        expected_key=object_identity["key"],
    )
    return {
        "schema_version": PUBLICATION_EXPORT_SCHEMA_VERSION,
        "entry": entry,
        "entry_sha256": protocol_sha256(entry),
        "object": object_identity,
    }


def _decode_and_validate_export(
    export: Mapping[str, Any],
    *,
    sequence_index: int,
    ordered_manifest_wing_ids: Sequence[str],
    previous_entry_sha256: str | None,
    previous_object: Mapping[str, Any] | None,
    active_segment: tuple[str, str, dict[str, Any]] | None,
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_recovery: bool,
) -> tuple[dict[str, Any], bytes, dict[str, Any], tuple[str, str, dict[str, Any]]]:
    _exact_keys(export, {"schema_version", "entry", "entry_sha256", "object"}, "publication export")
    if export.get("schema_version") != PUBLICATION_EXPORT_SCHEMA_VERSION:
        raise CampaignPublicationError("publication export schema changed")
    entry = _exact_keys(
        export.get("entry"),
        {
            "schema_version",
            "publication_status",
            "campaign_publication_id",
            "publication_contract_schema_version",
            "publication_contract_sha256",
            "sequence_index",
            "manifest_wing_id",
            "protocol_sha256",
            "manifest_sha256",
            "run_id",
            "execution_authorization_id",
            "execution_authorization_sha256",
            "previous_entry_sha256",
            "result_json_encoding",
            "result_json_base64",
            "result_size_bytes",
            "result_file_sha256",
            "result_canonical_sha256",
        },
        "terminal publication entry",
    )
    ordered = list(ordered_manifest_wing_ids)
    wing_id = ordered[sequence_index]
    if (
        entry.get("schema_version") != PUBLICATION_ENTRY_SCHEMA_VERSION
        or entry.get("publication_status") != "terminal"
        or entry.get("publication_contract_schema_version")
        != PUBLICATION_CONTRACT_SCHEMA_VERSION
        or entry.get("publication_contract_sha256")
        != PUBLICATION_CONTRACT_SHA256
        or entry.get("sequence_index") != sequence_index
        or entry.get("manifest_wing_id") != wing_id
        or entry.get("previous_entry_sha256") != previous_entry_sha256
        or entry.get("result_json_encoding") != RESULT_JSON_ENCODING
        or protocol_sha256(entry) != export.get("entry_sha256")
        or not _full_sha256(entry.get("protocol_sha256"))
        or not _full_sha256(entry.get("manifest_sha256"))
        or not _full_sha256(entry.get("execution_authorization_sha256"))
        or not _full_sha256(entry.get("result_file_sha256"))
        or not _full_sha256(entry.get("result_canonical_sha256"))
    ):
        raise CampaignPublicationError(
            f"terminal publication entry {sequence_index} identity or hash changed"
        )
    campaign_id = _safe_component(
        entry.get("campaign_publication_id"), "campaign publication ID"
    )
    if campaign_id != expected_campaign_publication_id:
        raise CampaignPublicationError("terminal publication uses a different campaign namespace")
    run_id = _safe_component(entry.get("run_id"), "published result run ID")
    authorization_id = entry.get("execution_authorization_id")
    if not isinstance(authorization_id, str) or not authorization_id:
        raise CampaignPublicationError("terminal publication lacks authorization ID")
    expected_key = publication_object_key(campaign_id, sequence_index, wing_id)
    object_identity = _validate_object_identity(
        export.get("object"),
        label="publication ledger object",
        expected_key=expected_key,
    )
    if object_identity["bucket"] != expected_publication_bucket:
        raise CampaignPublicationError("terminal publication uses a different storage bucket")
    encoded = entry.get("result_json_base64")
    if not isinstance(encoded, str):
        raise CampaignPublicationError("terminal publication lacks encoded result bytes")
    try:
        result_raw = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise CampaignPublicationError("terminal publication result encoding is invalid") from exc
    result = parse_json_object_bytes(result_raw, f"publication entry {sequence_index} result")
    result_authorization_id, result_authorization_sha, authorization = (
        _authorization_identity(result)
    )
    requested = authorization.get("wing_ids")
    if not isinstance(requested, list):
        raise CampaignPublicationError("published result authorization lacks wing scope")
    binding = validate_authorization_publication_binding(
        authorization,
        ordered_manifest_wing_ids=ordered,
        requested_wing_ids=requested,
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=expected_publication_bucket,
        allow_recovery=allow_recovery,
    )
    protocol_row = result.get("protocol")
    manifest_row = result.get("benchmark_manifest")
    if (
        result.get("wing_id") != wing_id
        or result.get("run_id") != run_id
        or result_authorization_id != authorization_id
        or result_authorization_sha != entry.get("execution_authorization_sha256")
        or not isinstance(protocol_row, Mapping)
        or protocol_row.get("canonical_sha256") != entry.get("protocol_sha256")
        or not isinstance(manifest_row, Mapping)
        or manifest_row.get("canonical_sha256") != entry.get("manifest_sha256")
        or len(result_raw) != entry.get("result_size_bytes")
        or hashlib.sha256(result_raw).hexdigest() != entry.get("result_file_sha256")
        or protocol_sha256(result) != entry.get("result_canonical_sha256")
        or binding["campaign_publication_id"] != campaign_id
    ):
        raise CampaignPublicationError(
            f"terminal publication {wing_id} does not bind its exact result bytes"
        )
    segment_identity = (run_id, result_authorization_sha)
    if active_segment is None or segment_identity != active_segment[:2]:
        if binding["start_sequence_index"] != sequence_index:
            raise CampaignPublicationError(
                f"run {run_id} does not start at its authorized ledger sequence"
            )
        if sequence_index == 0:
            if binding["previous_entry_sha256"] is not None:
                raise CampaignPublicationError("initial run has a publication predecessor")
        else:
            if (
                binding["previous_entry_sha256"] != previous_entry_sha256
                or binding["previous_entry_object"] != previous_object
            ):
                raise CampaignPublicationError(
                    f"recovery run {run_id} is not bound to the prior WORM ledger head"
                )
        next_segment = (run_id, result_authorization_sha, binding)
    else:
        if binding != active_segment[2]:
            raise CampaignPublicationError(
                f"run {run_id} contains multiple publication authorization bindings"
            )
        next_segment = active_segment
    return result, result_raw, object_identity, next_segment


def _validate_ordered_ids(ordered_manifest_wing_ids: Sequence[str]) -> list[str]:
    ordered = list(ordered_manifest_wing_ids)
    if (
        not ordered
        or len(set(ordered)) != len(ordered)
        or not all(isinstance(value, str) and value for value in ordered)
    ):
        raise CampaignPublicationError("campaign publication roster is invalid")
    for wing_id in ordered:
        _safe_component(wing_id, "manifest wing ID")
    return ordered


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CampaignPublicationError(f"{label} must be an explicit UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CampaignPublicationError(f"{label} is not a valid UTC timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise CampaignPublicationError(f"{label} must be UTC")
    return parsed.astimezone(timezone.utc)


_ARTIFACT_FIELDS = {"path", "present", "size_bytes", "sha256"}
_SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")


def _artifact_records(value: Any) -> list[Mapping[str, Any]]:
    """Find artifact records using the same structural rule as the publisher."""
    records: list[Mapping[str, Any]] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            artifact_like = "present" in item and bool(
                {"path", "size_bytes", "sha256"}.intersection(item)
            )
            if artifact_like and not _ARTIFACT_FIELDS.issubset(item):
                raise CampaignPublicationError(
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


def _artifact_expectations_from_exports(
    exports: Sequence[Mapping[str, Any]],
) -> dict[str, int]:
    """Derive authoritative artifact digest/size requirements from ledger bytes."""
    expectations: dict[str, int] = {}
    for export_index, export in enumerate(exports):
        entry = export.get("entry") if isinstance(export, Mapping) else None
        encoded = entry.get("result_json_base64") if isinstance(entry, Mapping) else None
        if not isinstance(encoded, str):
            raise CampaignPublicationError(
                f"ledger entry {export_index} lacks embedded result bytes"
            )
        try:
            result_raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise CampaignPublicationError(
                f"ledger entry {export_index} has invalid result encoding"
            ) from exc
        result = parse_json_object_bytes(result_raw, f"ledger result {export_index}")
        for record_index, record in enumerate(_artifact_records(result)):
            label = f"ledger result {export_index} artifact {record_index}"
            present = record.get("present")
            path = record.get("path")
            if type(present) is not bool or not isinstance(path, str) or not path:
                raise CampaignPublicationError(f"{label} has an invalid artifact record")
            if present is False:
                if record.get("size_bytes") is not None or record.get("sha256") is not None:
                    raise CampaignPublicationError(
                        f"{label} absent artifact must have null size and SHA-256"
                    )
                continue
            size = record.get("size_bytes")
            digest = record.get("sha256")
            if (
                isinstance(size, bool)
                or not isinstance(size, int)
                or size < 0
                or not isinstance(digest, str)
                or _SHA256_PATTERN.fullmatch(digest) is None
            ):
                raise CampaignPublicationError(
                    f"{label} present artifact metadata is invalid"
                )
            prior = expectations.get(digest)
            if prior is not None and prior != size:
                raise CampaignPublicationError(
                    "one artifact digest is associated with conflicting sizes"
                )
            expectations[digest] = size
    return expectations


def _artifact_object_key(campaign_publication_id: str, digest: str) -> str:
    if _SHA256_PATTERN.fullmatch(digest) is None:
        raise CampaignPublicationError("artifact SHA-256 is invalid")
    return f"campaigns/{campaign_publication_id}/artifacts/sha256/{digest}"


def build_publication_inventory(
    *,
    exports: Sequence[Mapping[str, Any]],
    campaign_publication_id: str,
    publication_bucket: str,
    observed_at: str,
    retain_until: str,
    last_modified: str | None = None,
    artifact_versions: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the strict sidecar emitted from AWS version/retention observations."""
    campaign_id = _safe_component(
        campaign_publication_id, "campaign publication ID"
    )
    observed = _parse_utc_timestamp(observed_at, "inventory observed_at")
    modified_value = observed_at if last_modified is None else last_modified
    modified = _parse_utc_timestamp(modified_value, "inventory last_modified")
    retained = _parse_utc_timestamp(retain_until, "inventory retain_until")
    if (
        modified > observed
        or retained < modified + timedelta(days=MINIMUM_RETENTION_DAYS)
        or retained <= observed
    ):
        raise CampaignPublicationError("inventory retention is shorter than 90 days")
    versions: list[dict[str, Any]] = []
    for export in exports:
        entry = export.get("entry") if isinstance(export, Mapping) else None
        if not isinstance(entry, Mapping):
            raise CampaignPublicationError("cannot inventory a malformed publication export")
        object_identity = _validate_object_identity(
            export.get("object"), label="publication ledger object"
        )
        if object_identity["bucket"] != publication_bucket:
            raise CampaignPublicationError("inventory export bucket changed")
        versions.append({
            "key": object_identity["key"],
            "version_id": object_identity["version_id"],
            "is_latest": True,
            "last_modified": modified_value,
            "size_bytes": len(canonical_json_bytes(entry)),
            "object_sha256": protocol_sha256(entry),
            "object_lock_mode": "COMPLIANCE",
            "retain_until": retain_until,
        })
    artifact_rows = [] if artifact_versions is None else [dict(row) for row in artifact_versions]
    artifact_rows.sort(key=lambda row: str(row.get("key", "")))
    inventory = {
        "schema_version": PUBLICATION_INVENTORY_SCHEMA_VERSION,
        "campaign_publication_id": campaign_id,
        "bucket": publication_bucket,
        "prefix": f"campaigns/{campaign_id}/{PUBLICATION_LEDGER_DIRECTORY}/",
        "observation_trust_model": INVENTORY_OBSERVATION_TRUST_MODEL,
        "observed_at": observed_at,
        "aws_observation_apis": ["s3:ListObjectVersions", "s3:GetObjectRetention"],
        "list_object_versions_complete": True,
        "is_truncated": False,
        "conditional_create_header": "If-None-Match: *",
        "versioning_status": "Enabled",
        "required_object_lock_mode": "COMPLIANCE",
        "minimum_retention_days": MINIMUM_RETENTION_DAYS,
        "delete_markers": [],
        "versions": versions,
        "artifact_prefix": f"campaigns/{campaign_id}/artifacts/sha256/",
        "artifact_list_object_versions_complete": True,
        "artifact_is_truncated": False,
        "artifact_delete_markers": [],
        "artifact_versions": artifact_rows,
    }
    _validate_publication_inventory(
        inventory,
        exports=exports,
        expected_campaign_publication_id=campaign_id,
        expected_publication_bucket=publication_bucket,
    )
    return inventory


def _validate_publication_inventory(
    inventory: Mapping[str, Any],
    *,
    exports: Sequence[Mapping[str, Any]],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
) -> None:
    _exact_keys(inventory, {
        "schema_version", "campaign_publication_id", "bucket", "prefix",
        "observation_trust_model", "observed_at", "aws_observation_apis",
        "list_object_versions_complete", "is_truncated",
        "conditional_create_header", "versioning_status",
        "required_object_lock_mode", "minimum_retention_days",
        "delete_markers", "versions",
        "artifact_prefix", "artifact_list_object_versions_complete",
        "artifact_is_truncated", "artifact_delete_markers",
        "artifact_versions",
    }, "publication inventory")
    observed_at = _parse_utc_timestamp(
        inventory.get("observed_at"), "inventory observed_at"
    )
    versions = inventory.get("versions")
    artifact_versions = inventory.get("artifact_versions")
    if (
        inventory.get("schema_version") != PUBLICATION_INVENTORY_SCHEMA_VERSION
        or inventory.get("campaign_publication_id")
        != expected_campaign_publication_id
        or inventory.get("bucket") != expected_publication_bucket
        or inventory.get("prefix")
        != f"campaigns/{expected_campaign_publication_id}/{PUBLICATION_LEDGER_DIRECTORY}/"
        or inventory.get("observation_trust_model")
        != INVENTORY_OBSERVATION_TRUST_MODEL
        or inventory.get("aws_observation_apis")
        != ["s3:ListObjectVersions", "s3:GetObjectRetention"]
        or inventory.get("list_object_versions_complete") is not True
        or inventory.get("is_truncated") is not False
        or inventory.get("conditional_create_header") != "If-None-Match: *"
        or inventory.get("versioning_status") != "Enabled"
        or inventory.get("required_object_lock_mode") != "COMPLIANCE"
        or inventory.get("minimum_retention_days") != MINIMUM_RETENTION_DAYS
        or inventory.get("delete_markers") != []
        or not isinstance(versions, list)
        or len(versions) != len(exports)
        or inventory.get("artifact_prefix")
        != f"campaigns/{expected_campaign_publication_id}/artifacts/sha256/"
        or inventory.get("artifact_list_object_versions_complete") is not True
        or inventory.get("artifact_is_truncated") is not False
        or inventory.get("artifact_delete_markers") != []
        or not isinstance(artifact_versions, list)
    ):
        raise CampaignPublicationError(
            "publication inventory is not a complete no-delete WORM observation"
        )
    observed_keys: set[str] = set()
    observed_versions: set[tuple[str, str]] = set()
    for index, (version, export) in enumerate(zip(versions, exports)):
        _exact_keys(version, {
            "key", "version_id", "is_latest", "last_modified", "size_bytes",
            "object_sha256", "object_lock_mode", "retain_until",
        }, f"publication inventory version {index}")
        object_identity = _validate_object_identity(
            export.get("object"), label=f"publication ledger object {index}"
        )
        entry = export.get("entry")
        retained_until = _parse_utc_timestamp(
            version.get("retain_until"), f"inventory version {index} retain_until"
        )
        last_modified = _parse_utc_timestamp(
            version.get("last_modified"), f"inventory version {index} last_modified"
        )
        if (
            version.get("key") != object_identity["key"]
            or version.get("version_id") != object_identity["version_id"]
            or version.get("is_latest") is not True
            or version.get("size_bytes") != len(canonical_json_bytes(entry))
            or version.get("object_sha256") != protocol_sha256(entry)
            or version.get("object_lock_mode") != "COMPLIANCE"
            or last_modified > observed_at
            or retained_until
            < last_modified + timedelta(days=MINIMUM_RETENTION_DAYS)
            or retained_until <= observed_at
        ):
            raise CampaignPublicationError(
                f"publication inventory version {index} disagrees with its WORM entry"
            )
        key = str(version["key"])
        key_version = (key, str(version["version_id"]))
        if key in observed_keys or key_version in observed_versions:
            raise CampaignPublicationError(
                "publication inventory contains duplicate keys or object versions"
            )
        observed_keys.add(key)
        observed_versions.add(key_version)

    artifact_expectations = _artifact_expectations_from_exports(exports)
    expected_artifact_keys = {
        _artifact_object_key(expected_campaign_publication_id, digest): (digest, size)
        for digest, size in artifact_expectations.items()
    }
    artifact_observed_keys: set[str] = set()
    artifact_observed_versions: set[tuple[str, str]] = set()
    artifact_order: list[str] = []
    for index, version in enumerate(artifact_versions):
        _exact_keys(version, {
            "key", "version_id", "is_latest", "last_modified", "size_bytes",
            "object_sha256", "object_lock_mode", "retain_until",
        }, f"publication artifact inventory version {index}")
        key = version.get("key")
        prefix = f"campaigns/{expected_campaign_publication_id}/artifacts/sha256/"
        if not isinstance(key, str) or not key.startswith(prefix):
            raise CampaignPublicationError(
                f"publication artifact inventory version {index} has a malformed key"
            )
        digest = key[len(prefix):]
        version_id = version.get("version_id")
        size = version.get("size_bytes")
        retained_until = _parse_utc_timestamp(
            version.get("retain_until"),
            f"artifact inventory version {index} retain_until",
        )
        last_modified = _parse_utc_timestamp(
            version.get("last_modified"),
            f"artifact inventory version {index} last_modified",
        )
        if (
            _SHA256_PATTERN.fullmatch(digest) is None
            or key != _artifact_object_key(expected_campaign_publication_id, digest)
            or not isinstance(version_id, str)
            or not version_id
            or version_id == "null"
            or version.get("is_latest") is not True
            or isinstance(size, bool)
            or not isinstance(size, int)
            or size < 0
            or version.get("object_sha256") != digest
            or version.get("object_lock_mode") != "COMPLIANCE"
            or last_modified > observed_at
            or retained_until
            < last_modified + timedelta(days=MINIMUM_RETENTION_DAYS)
            or retained_until <= observed_at
        ):
            raise CampaignPublicationError(
                f"publication artifact inventory version {index} is not a retained digest object"
            )
        key_version = (key, version_id)
        if key in artifact_observed_keys or key_version in artifact_observed_versions:
            raise CampaignPublicationError(
                "publication artifact inventory contains duplicate keys or object versions"
            )
        artifact_observed_keys.add(key)
        artifact_observed_versions.add(key_version)
        artifact_order.append(key)

    if artifact_order != sorted(artifact_order):
        raise CampaignPublicationError(
            "publication artifact inventory is not in canonical key order"
        )
    missing_artifacts = sorted(set(expected_artifact_keys) - artifact_observed_keys)
    if missing_artifacts:
        raise CampaignPublicationError(
            f"publication artifact inventory is missing referenced keys: {missing_artifacts}"
        )
    for key, (_digest, expected_size) in expected_artifact_keys.items():
        row = next(version for version in artifact_versions if version.get("key") == key)
        if row.get("size_bytes") != expected_size:
            raise CampaignPublicationError(
                f"publication artifact inventory size disagrees with embedded result bytes for {key}"
            )


def load_campaign_publication_export(
    campaign_export_root: Path,
    *,
    ordered_manifest_wing_ids: Sequence[str],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_recovery: bool,
    require_exact_root: bool = True,
) -> tuple[dict[str, Any], dict[str, tuple[dict[str, Any], bytes]]]:
    """Load the exact exported WORM chain and its authoritative result bytes."""
    ordered = _validate_ordered_ids(ordered_manifest_wing_ids)
    supplied_root = Path(campaign_export_root)
    if supplied_root.is_symlink():
        raise CampaignPublicationError("campaign publication export root cannot be a symlink")
    root = supplied_root.resolve()
    if not root.is_dir():
        raise CampaignPublicationError(f"campaign publication export does not exist: {root}")
    root_entries = list(root.iterdir())
    required_root_entries = {
        PUBLICATION_LEDGER_DIRECTORY, PUBLICATION_INVENTORY_FILENAME
    }
    if (
        (
            {path.name for path in root_entries} != required_root_entries
            if require_exact_root
            else not required_root_entries.issubset(
                {path.name for path in root_entries}
            )
        )
        or not (root / PUBLICATION_LEDGER_DIRECTORY).is_dir()
        or (root / PUBLICATION_LEDGER_DIRECTORY).is_symlink()
        or not (root / PUBLICATION_INVENTORY_FILENAME).is_file()
        or (root / PUBLICATION_INVENTORY_FILENAME).is_symlink()
    ):
        raise CampaignPublicationError(
            "campaign publication export must contain only its ledger and inventory"
        )
    ledger_directory = root / PUBLICATION_LEDGER_DIRECTORY
    expected_names = [
        publication_entry_filename(index, wing_id)
        for index, wing_id in enumerate(ordered)
    ]
    entries = list(ledger_directory.iterdir())
    if (
        sorted(path.name for path in entries) != sorted(expected_names)
        or any(not path.is_file() or path.is_symlink() for path in entries)
    ):
        raise CampaignPublicationError(
            "campaign publication ledger export inventory is incomplete or non-canonical"
        )

    rows: list[dict[str, Any]] = []
    documents: dict[str, tuple[dict[str, Any], bytes]] = {}
    previous_sha: str | None = None
    previous_object: dict[str, str] | None = None
    active_segment: tuple[str, str, dict[str, Any]] | None = None
    seen_segments: set[tuple[str, str]] = set()
    campaign_ids: set[str] = set()
    protocol_hashes: set[str] = set()
    manifest_hashes: set[str] = set()
    buckets: set[str] = set()
    object_versions: set[tuple[str, str, str]] = set()
    for index, wing_id in enumerate(ordered):
        path = ledger_directory / publication_entry_filename(index, wing_id)
        export, raw = _read_json_object(path)
        prior_segment = active_segment
        result, result_raw, object_identity, active_segment = _decode_and_validate_export(
            export,
            sequence_index=index,
            ordered_manifest_wing_ids=ordered,
            previous_entry_sha256=previous_sha,
            previous_object=previous_object,
            active_segment=active_segment,
            expected_campaign_publication_id=expected_campaign_publication_id,
            expected_publication_bucket=expected_publication_bucket,
            allow_recovery=allow_recovery,
        )
        segment_key = active_segment[:2]
        if prior_segment is None or segment_key != prior_segment[:2]:
            if segment_key in seen_segments:
                raise CampaignPublicationError(
                    "a publication run/authorization segment reappears non-contiguously"
                )
            seen_segments.add(segment_key)
        entry = export["entry"]
        evidence = {
            "path": f"{PUBLICATION_LEDGER_DIRECTORY}/{path.name}",
            "file_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_sha256": protocol_sha256(export),
            "size_bytes": len(raw),
        }
        rows.append({
            "export": export,
            "evidence": evidence,
        })
        documents[wing_id] = (result, result_raw)
        previous_sha = str(export["entry_sha256"])
        previous_object = object_identity
        campaign_ids.add(str(entry["campaign_publication_id"]))
        protocol_hashes.add(str(entry["protocol_sha256"]))
        manifest_hashes.add(str(entry["manifest_sha256"]))
        buckets.add(object_identity["bucket"])
        object_version = (
            object_identity["bucket"],
            object_identity["key"],
            object_identity["version_id"],
        )
        if object_version in object_versions:
            raise CampaignPublicationError("publication ledger reuses a durable object version")
        object_versions.add(object_version)
    if (
        campaign_ids != {expected_campaign_publication_id}
        or buckets != {expected_publication_bucket}
        or len(protocol_hashes) != 1
        or len(manifest_hashes) != 1
    ):
        raise CampaignPublicationError(
            "publication ledger does not share one campaign, protocol, manifest, and bucket"
        )
    inventory_path = root / PUBLICATION_INVENTORY_FILENAME
    inventory, inventory_raw = _read_json_object(inventory_path)
    exports = [row["export"] for row in rows]
    _validate_publication_inventory(
        inventory,
        exports=exports,
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=expected_publication_bucket,
    )
    record = {
        "schema_version": PUBLICATION_LEDGER_RECORD_SCHEMA_VERSION,
        "campaign_publication_id": next(iter(campaign_ids)),
        "publication_contract_schema_version": PUBLICATION_CONTRACT_SCHEMA_VERSION,
        "publication_contract_sha256": PUBLICATION_CONTRACT_SHA256,
        "entry_hash_semantics": LEDGER_HASH_SEMANTICS,
        "source_trust_model": EXPORTED_WORM_TRUST_MODEL,
        "external_enforcement_required": {
            "s3_versioning": True,
            "conditional_create_header": "If-None-Match: *",
            "s3_object_lock_mode": "COMPLIANCE",
            "minimum_retention_days": MINIMUM_RETENTION_DAYS,
            "complete_ledger_and_artifact_version_inventory_export": True,
            "zero_ledger_and_artifact_delete_markers_and_duplicate_versions": True,
            "referenced_artifacts_bound_to_embedded_result_bytes": True,
            "publisher_put_only_without_overwrite_or_delete": True,
            "aws_enforcement_verified_by_this_offline_validator": False,
        },
        "terminal_publications_only": True,
        "zero_terminal_runs": (
            "not represented because they published no terminal result"
        ),
        "directory": PUBLICATION_LEDGER_DIRECTORY,
        "entry_count": len(rows),
        "protocol_sha256": next(iter(protocol_hashes)),
        "manifest_sha256": next(iter(manifest_hashes)),
        "storage_provider": "aws_s3",
        "bucket": next(iter(buckets)),
        "terminal_entry_sha256": previous_sha,
        "inventory": {
            "payload": inventory,
            "evidence": {
                "path": PUBLICATION_INVENTORY_FILENAME,
                "file_sha256": hashlib.sha256(inventory_raw).hexdigest(),
                "canonical_sha256": protocol_sha256(inventory),
                "size_bytes": len(inventory_raw),
            },
        },
        "entries": rows,
    }
    validate_publication_ledger_record(
        record,
        ordered_manifest_wing_ids=ordered,
        result_evidence_by_wing=None,
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=expected_publication_bucket,
        allow_recovery=allow_recovery,
    )
    return record, documents


def validate_publication_ledger_record(
    record: Mapping[str, Any],
    *,
    ordered_manifest_wing_ids: Sequence[str],
    result_evidence_by_wing: Mapping[str, Mapping[str, Any]] | None,
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_recovery: bool,
) -> None:
    """Validate the ledger copy embedded in a completion/qualification record."""
    ordered = _validate_ordered_ids(ordered_manifest_wing_ids)
    _exact_keys(record, {
        "schema_version", "campaign_publication_id",
        "publication_contract_schema_version", "publication_contract_sha256",
        "entry_hash_semantics", "source_trust_model",
        "external_enforcement_required", "terminal_publications_only",
        "zero_terminal_runs", "directory", "entry_count", "protocol_sha256",
        "manifest_sha256", "storage_provider", "bucket",
        "terminal_entry_sha256", "inventory", "entries",
    }, "publication ledger record")
    enforcement = _exact_keys(record.get("external_enforcement_required"), {
        "s3_versioning", "conditional_create_header", "s3_object_lock_mode",
        "minimum_retention_days",
        "complete_ledger_and_artifact_version_inventory_export",
        "zero_ledger_and_artifact_delete_markers_and_duplicate_versions",
        "referenced_artifacts_bound_to_embedded_result_bytes",
        "publisher_put_only_without_overwrite_or_delete",
        "aws_enforcement_verified_by_this_offline_validator",
    }, "publication ledger external enforcement")
    rows = record.get("entries")
    if (
        record.get("schema_version") != PUBLICATION_LEDGER_RECORD_SCHEMA_VERSION
        or record.get("publication_contract_schema_version")
        != PUBLICATION_CONTRACT_SCHEMA_VERSION
        or record.get("publication_contract_sha256")
        != PUBLICATION_CONTRACT_SHA256
        or record.get("entry_hash_semantics") != LEDGER_HASH_SEMANTICS
        or record.get("source_trust_model") != EXPORTED_WORM_TRUST_MODEL
        or enforcement != {
            "s3_versioning": True,
            "conditional_create_header": "If-None-Match: *",
            "s3_object_lock_mode": "COMPLIANCE",
            "minimum_retention_days": MINIMUM_RETENTION_DAYS,
            "complete_ledger_and_artifact_version_inventory_export": True,
            "zero_ledger_and_artifact_delete_markers_and_duplicate_versions": True,
            "referenced_artifacts_bound_to_embedded_result_bytes": True,
            "publisher_put_only_without_overwrite_or_delete": True,
            "aws_enforcement_verified_by_this_offline_validator": False,
        }
        or record.get("terminal_publications_only") is not True
        or record.get("zero_terminal_runs")
        != "not represented because they published no terminal result"
        or record.get("directory") != PUBLICATION_LEDGER_DIRECTORY
        or record.get("entry_count") != len(ordered)
        or record.get("campaign_publication_id")
        != expected_campaign_publication_id
        or not _full_sha256(record.get("protocol_sha256"))
        or not _full_sha256(record.get("manifest_sha256"))
        or record.get("storage_provider") != "aws_s3"
        or record.get("bucket") != expected_publication_bucket
        or not isinstance(rows, list)
        or len(rows) != len(ordered)
    ):
        raise CampaignPublicationError("publication ledger record contract changed")
    previous_sha: str | None = None
    previous_object: dict[str, str] | None = None
    active_segment: tuple[str, str, dict[str, Any]] | None = None
    seen_segments: set[tuple[str, str]] = set()
    observed_campaigns: set[str] = set()
    observed_protocols: set[str] = set()
    observed_manifests: set[str] = set()
    observed_objects: set[tuple[str, str, str]] = set()
    for index, (wing_id, row) in enumerate(zip(ordered, rows)):
        _exact_keys(row, {"export", "evidence"}, "publication ledger row")
        export = row["export"]
        prior_segment = active_segment
        result, result_raw, object_identity, active_segment = _decode_and_validate_export(
            export,
            sequence_index=index,
            ordered_manifest_wing_ids=ordered,
            previous_entry_sha256=previous_sha,
            previous_object=previous_object,
            active_segment=active_segment,
            expected_campaign_publication_id=expected_campaign_publication_id,
            expected_publication_bucket=expected_publication_bucket,
            allow_recovery=allow_recovery,
        )
        segment_key = active_segment[:2]
        if prior_segment is None or segment_key != prior_segment[:2]:
            if segment_key in seen_segments:
                raise CampaignPublicationError(
                    "a publication run/authorization segment reappears non-contiguously"
                )
            seen_segments.add(segment_key)
        evidence = _exact_keys(row["evidence"], {
            "path", "file_sha256", "canonical_sha256", "size_bytes",
        }, "publication ledger entry evidence")
        expected_path = (
            f"{PUBLICATION_LEDGER_DIRECTORY}/"
            f"{publication_entry_filename(index, wing_id)}"
        )
        if (
            evidence.get("path") != expected_path
            or not _full_sha256(evidence.get("file_sha256"))
            or evidence.get("canonical_sha256") != protocol_sha256(export)
            or isinstance(evidence.get("size_bytes"), bool)
            or not isinstance(evidence.get("size_bytes"), int)
            or evidence["size_bytes"] <= 0
        ):
            raise CampaignPublicationError("publication ledger entry evidence changed")
        if result_evidence_by_wing is not None:
            result_evidence = result_evidence_by_wing.get(wing_id)
            if not isinstance(result_evidence, Mapping) or (
                result_evidence.get("file_sha256")
                != hashlib.sha256(result_raw).hexdigest()
                or result_evidence.get("canonical_sha256") != protocol_sha256(result)
                or result_evidence.get("size_bytes") != len(result_raw)
            ):
                raise CampaignPublicationError(
                    f"publication ledger disagrees with extracted result {wing_id}"
                )
        entry = export["entry"]
        observed_campaigns.add(str(entry["campaign_publication_id"]))
        observed_protocols.add(str(entry["protocol_sha256"]))
        observed_manifests.add(str(entry["manifest_sha256"]))
        if object_identity["bucket"] != record.get("bucket"):
            raise CampaignPublicationError("publication ledger bucket identity changed")
        object_version = (
            object_identity["bucket"], object_identity["key"], object_identity["version_id"]
        )
        if object_version in observed_objects:
            raise CampaignPublicationError("publication ledger reuses a durable object version")
        observed_objects.add(object_version)
        previous_sha = str(export["entry_sha256"])
        previous_object = object_identity
    inventory_row = _exact_keys(
        record.get("inventory"), {"payload", "evidence"},
        "publication inventory record",
    )
    inventory = inventory_row.get("payload")
    if not isinstance(inventory, Mapping):
        raise CampaignPublicationError("publication inventory payload is missing")
    inventory_evidence = _exact_keys(
        inventory_row.get("evidence"),
        {"path", "file_sha256", "canonical_sha256", "size_bytes"},
        "publication inventory evidence",
    )
    if (
        inventory_evidence.get("path") != PUBLICATION_INVENTORY_FILENAME
        or not _full_sha256(inventory_evidence.get("file_sha256"))
        or inventory_evidence.get("canonical_sha256") != protocol_sha256(inventory)
        or isinstance(inventory_evidence.get("size_bytes"), bool)
        or not isinstance(inventory_evidence.get("size_bytes"), int)
        or inventory_evidence["size_bytes"] <= 0
    ):
        raise CampaignPublicationError("publication inventory evidence changed")
    _validate_publication_inventory(
        inventory,
        exports=[row["export"] for row in rows],
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=expected_publication_bucket,
    )
    if (
        observed_campaigns != {record.get("campaign_publication_id")}
        or observed_protocols != {record.get("protocol_sha256")}
        or observed_manifests != {record.get("manifest_sha256")}
        or previous_sha != record.get("terminal_entry_sha256")
    ):
        raise CampaignPublicationError("publication ledger shared identity changed")


def write_publication_export_file(path: Path, export: Mapping[str, Any]) -> None:
    """Test/exporter helper: create one ledger export file without overwriting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        raise FileExistsError(f"refusing to overwrite publication export: {path}")
    path.write_bytes(
        json.dumps(
            export,
            indent=2,
            sort_keys=True,
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        + b"\n"
    )
