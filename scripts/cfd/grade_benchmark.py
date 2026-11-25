#!/usr/bin/env python3
"""Whole-wing SuperWing CFD grader for the fingerprinted v5 result contract.

This replaces the legacy evaluator for future evidence.  It prepares one mesh,
runs the three protocol conditions serially, retries only infrastructure-
inconclusive attempts with identical settings, and writes one immutable result.
The checked-in protocol is a qualification candidate, so official mode is
mechanically unavailable until a separate reviewed freeze.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cfd.geometry_hash import (  # noqa: E402
    GEOMETRY_HASH_ALGORITHM,
    geometry_sha256,
    validated_geometry,
)
from scripts.cfd.campaign_publication import (  # noqa: E402
    AUTHORIZATION_BINDING_SCHEMA_VERSION,
    PUBLICATION_CONTRACT_SCHEMA_VERSION,
    PUBLICATION_CONTRACT_SHA256,
    CampaignPublicationError,
    publication_object_key,
    validate_authorization_publication_binding,
    validate_manifest_publication_contract,
)
from scripts.cfd.aws_campaign_publication import (  # noqa: E402
    SubprocessAwsCliRunner,
    preflight_campaign_publication,
    publish_terminal_result,
)
from scripts.cfd.grade_schema import (  # noqa: E402
    RESULT_SCHEMA_VERSION,
    aggregate_wing,
    classify_condition_attempt,
    classify_mesh_attempt,
    finalize_condition,
)
from scripts.cfd.grading_protocol import (  # noqa: E402
    DEFAULT_PROTOCOL_PATH,
    GradingProtocol,
    ProtocolError,
    SAFE_INHERITED_ENV,
    canonical_json_bytes,
    load_protocol,
    sha256_file,
)
from scripts.cfd.prepare_superwing_cases import (  # noqa: E402
    geom_to_wing_coefs,
    prepare_geometry_case,
)
from scripts.cfd.raw_evidence import (  # noqa: E402
    RawEvidenceError,
    parse_adflow_log_bytes,
    parse_volume_meshing_log_bytes,
)
from scripts.cfd.validate_mesh_artifact import (  # noqa: E402
    REPORT_SCHEMA_VERSION as MESH_VALIDATION_SCHEMA_VERSION,
    atomic_write_report as atomic_write_mesh_validation,
    validate_mesh_artifacts,
)


ProcessRunner = Callable[[Sequence[str], Path, Path, int, Mapping[str, str]], dict[str, Any]]
CasePreparer = Callable[..., str]
MeshValidator = Callable[[Path, Path], dict[str, Any]]

_EXECUTION_CONTEXT_TOKEN = object()
_HELD_WORKER_LOCKS: set[Path] = set()
_PUBLICATION_PREFLIGHT_SCHEMA_VERSION = (
    "wingbench-cfd-campaign-publication-preflight-v1"
)
_PUBLICATION_PREFLIGHT_TRUST_MODEL = (
    "live_aws_api_observation_not_cryptographic_attestation"
)
_PUBLICATION_PREFLIGHT_MAX_AGE_SECONDS = 300


@dataclass(frozen=True)
class _ValidatedExecutionContext:
    """Opaque proof that the CLI validated one exact, locked execution scope."""

    _token: object
    protocol_sha256: str
    manifest_sha256: str
    mode: str
    run_id: str
    wing_ids: tuple[str, ...]
    ordered_manifest_wing_ids: tuple[str, ...]
    campaign_publication_id: str
    publication_bucket: str
    publication_recovery_allowed: bool
    publication_sequence_index: int
    publication_preflight_json: bytes
    publication_preflight_sha256: str
    worker_lock_path: Path
    authorization_json: bytes
    authorization_sha256: str
    runtime_attestation_json: bytes
    runtime_attestation_sha256: str
    release_lock_json: bytes | None
    release_lock_sha256: str | None


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_payload_sha256(payload: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _safe_path_component(value: Any, label: str) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or re.fullmatch(r"[A-Za-z0-9_.-]+", value) is None
    ):
        raise ProtocolError(f"{label} must be a safe non-empty path component")
    return value


def read_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    return json.loads(
        path.read_text(),
        parse_constant=reject_constant,
        object_pairs_hook=reject_duplicate_keys,
    )


def atomic_write_json(path: Path, payload: Any) -> None:
    data = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite immutable JSON: {path}")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # A hard-link publish is atomic and, unlike replace(), fails if another
        # writer created the immutable destination during this operation.
        os.link(temporary, path)
        temporary.unlink()
    finally:
        if temporary.exists():
            temporary.unlink()


def artifact(path: Path) -> dict[str, Any]:
    path = Path(path).resolve()
    if not path.is_file():
        return {"path": str(path), "present": False, "size_bytes": None, "sha256": None}
    return {
        "path": str(path),
        "present": True,
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def _expected_case_input(
    geometry: Sequence[float], protocol: GradingProtocol
) -> dict[str, Any]:
    """Reconstruct the exact pre-mesh input from fingerprinted evidence."""
    payload = geom_to_wing_coefs(geometry)
    payload["M3"] = [float(row["mach"]) for row in protocol.conditions]
    payload["AA"] = [protocol.seed_alpha0 for _row in protocol.conditions]
    # Normalize through the same strict JSON value domain used by result hashes.
    return json.loads(canonical_json_bytes(payload))


def _validate_case_input(
    observed: Any,
    expected: Mapping[str, Any],
    *,
    exact: bool,
    label: str,
) -> None:
    if not isinstance(observed, dict):
        raise ProtocolError(f"{label} must be a JSON object")
    if exact:
        if canonical_json_bytes(observed) != canonical_json_bytes(expected):
            raise ProtocolError(f"{label} is not the geometry/protocol-derived case input")
        return
    changed = [
        key for key, value in expected.items()
        if key not in observed
        or canonical_json_bytes(observed[key]) != canonical_json_bytes(value)
    ]
    if changed:
        raise ProtocolError(
            f"{label} changed immutable geometry/condition fields: " + ", ".join(changed)
        )


def _validate_post_mesh_derived_geometry(
    observed: Mapping[str, Any], expected_base: Mapping[str, Any]
) -> None:
    """Recompute the load-bearing mesher geometry values from the 38-D input."""
    try:
        aspect_ratio = float(expected_base["AR"])
        taper = float(expected_base["TR"])
        kink_fraction = float(expected_base["kink"])
        sweep_deg = float(expected_base["SA"])
        root_adjustment = float(expected_base["rootadj"])
        half_span = aspect_ratio * (1.0 + taper) * 0.25
        ref_area = (1.0 + taper) * half_span * 0.5
        kink_span = half_span * kink_fraction
        cabin_ratio = 0.1
        cabin_span = half_span * cabin_ratio
        chord_tip = taper
        chord_kink = taper * kink_fraction + (1.0 - kink_fraction)
        x_le_kink = math.tan(sweep_deg / 180.0 * math.pi) * kink_span
        root_adjustment_length = root_adjustment * (
            x_le_kink + chord_kink - 1.0
        )
        chord_cabin = (
            taper * cabin_ratio
            + (1.0 - cabin_ratio)
            + root_adjustment_length
            * (kink_fraction - cabin_ratio)
            / kink_fraction
        )
        surface_area = (
            (chord_cabin + chord_kink) * (kink_span - cabin_span) * 0.5
            + (chord_kink + chord_tip) * (half_span - kink_span) * 0.5
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError, OverflowError) as exc:
        raise ProtocolError("cannot reconstruct post-mesh derived geometry") from exc
    expected = {
        "half_span": half_span,
        "ref_area": ref_area,
        "surface_area": surface_area,
    }
    changed = []
    for name, value in expected.items():
        actual = observed.get(name)
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or not math.isfinite(float(actual))
            or not math.isclose(float(actual), value, rel_tol=1e-12, abs_tol=1e-12)
        ):
            changed.append(name)
    if changed:
        raise ProtocolError(
            "post-mesh input changed geometry-derived solver references: "
            + ", ".join(changed)
        )


def _artifact_materialized_path(
    record: Mapping[str, Any], artifact_store: Path | None
) -> Path:
    """Resolve either the producing path or a portable content-addressed copy."""
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        raise ProtocolError("malformed artifact record")
    if artifact_store is None:
        return Path(record["path"])
    digest = record.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        raise ProtocolError("present portable artifact lacks a full SHA-256")
    store = Path(artifact_store).resolve()
    path = (store / digest).resolve()
    if path.parent != store:
        raise ProtocolError("portable artifact path escapes its content-addressed store")
    return path


def _verified_artifact_bytes(
    record: Mapping[str, Any], artifact_store: Path | None = None
) -> bytes | None:
    """Read and hash one artifact in a single pass, returning its verified bytes."""
    if not isinstance(record, Mapping) or not isinstance(record.get("path"), str):
        raise ProtocolError("malformed artifact record")
    if type(record.get("present")) is not bool:
        raise ProtocolError("artifact presence is not boolean")
    if record.get("present") is False:
        if record.get("size_bytes") is not None or record.get("sha256") is not None:
            raise ProtocolError("absent artifact has a size or hash")
        if artifact_store is None and Path(record["path"]).is_file():
            raise ProtocolError(f"artifact presence changed: {record['path']}")
        return None
    path = _artifact_materialized_path(record, artifact_store)
    present = path.is_file()
    if not present:
        raise ProtocolError(f"artifact presence changed: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProtocolError(f"cannot read artifact: {path}: {exc}") from exc
    if len(raw) != record.get("size_bytes"):
        raise ProtocolError(f"artifact size changed: {path}")
    if hashlib.sha256(raw).hexdigest() != record.get("sha256"):
        raise ProtocolError(f"artifact hash changed: {path}")
    return raw


def verify_artifact(
    record: Mapping[str, Any], artifact_store: Path | None = None
) -> None:
    """Verify an artifact at its source or in a portable SHA-256 store."""
    _verified_artifact_bytes(record, artifact_store)


def _read_json_artifact(
    record: Mapping[str, Any], artifact_store: Path | None = None
) -> Any:
    raw = _verified_artifact_bytes(record, artifact_store)
    if raw is None:
        raise ProtocolError("cannot read an absent JSON artifact")

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant {value}")

    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON object key {key!r}")
            result[key] = value
        return result

    try:
        return json.loads(
            raw,
            parse_constant=reject_constant,
            object_pairs_hook=reject_duplicate_keys,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"artifact is not strict JSON: {record['path']}: {exc}") from exc


def _matching_numeric_fields(
    observed: Any,
    recomputed: Mapping[str, Any],
    names: Sequence[str],
) -> list[str]:
    if not isinstance(observed, Mapping):
        return list(names)
    mismatches: list[str] = []
    for name in names:
        actual = observed.get(name)
        expected = recomputed.get(name)
        if (
            isinstance(actual, bool)
            or not isinstance(actual, (int, float))
            or isinstance(expected, bool)
            or not isinstance(expected, (int, float))
            or not math.isfinite(float(actual))
            or not math.isfinite(float(expected))
            or not math.isclose(
                float(actual), float(expected), rel_tol=1e-12, abs_tol=1e-15
            )
        ):
            mismatches.append(name)
    return mismatches


def _mesh_log_crosscheck(
    mesh_info: Any,
    log_record: Mapping[str, Any],
    artifact_store: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    raw = _verified_artifact_bytes(log_record, artifact_store)
    if raw is None:
        return None, "volume_meshing_log_missing"
    try:
        evidence = parse_volume_meshing_log_bytes(raw)
    except RawEvidenceError as exc:
        return None, f"volume_meshing_log_invalid:{exc}"
    mismatches = _matching_numeric_fields(
        mesh_info, evidence, ("min_Quality", "min_Vol", "actual_ds")
    )
    if mismatches:
        return evidence, "mesh_info_log_mismatch:" + ",".join(mismatches)
    return evidence, None


def _adflow_log_crosscheck(
    output: Any,
    log_record: Mapping[str, Any],
    artifact_store: Path | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    raw = _verified_artifact_bytes(log_record, artifact_store)
    if raw is None:
        return None, "adflow_log_missing"
    try:
        evidence = parse_adflow_log_bytes(raw)
    except RawEvidenceError as exc:
        return None, f"adflow_log_invalid:{exc}"
    results = output.get("results") if isinstance(output, Mapping) else None
    mismatches = _matching_numeric_fields(
        results,
        evidence,
        (
            "err_cl",
            "err_cd",
            "err_cmz",
            "n_step",
            "min_ResRho",
            "min_totalRes",
            "min_relative_ResRho",
            "min_relative_totalRes",
        ),
    )
    if mismatches:
        return evidence, "adflow_output_log_mismatch:" + ",".join(mismatches)
    return evidence, None


def _validate_artifact_record(
    record: Mapping[str, Any],
    expected_path: Path,
    *,
    required: bool,
    expected_sha256: str | None = None,
) -> None:
    if not isinstance(record, Mapping):
        raise ProtocolError(f"missing artifact record for {expected_path}")
    recorded_path = record.get("path")
    if not isinstance(recorded_path, str) or Path(recorded_path).resolve() != expected_path.resolve():
        raise ProtocolError(f"artifact record points at the wrong path for {expected_path.name}")
    present = record.get("present")
    if type(present) is not bool:
        raise ProtocolError(f"artifact presence is not boolean for {expected_path.name}")
    if required and present is not True:
        raise ProtocolError(f"required artifact is absent: {expected_path.name}")
    if present:
        size = record.get("size_bytes")
        digest = record.get("sha256")
        if (isinstance(size, bool) or not isinstance(size, int) or size < 0
                or not isinstance(digest, str) or len(digest) != 64):
            raise ProtocolError(f"malformed present artifact record for {expected_path.name}")
        if expected_sha256 is not None and digest != expected_sha256:
            raise ProtocolError(f"pinned artifact hash changed for {expected_path.name}")
    elif record.get("size_bytes") is not None or record.get("sha256") is not None:
        raise ProtocolError(f"absent artifact carries a size or hash: {expected_path.name}")


def run_process(
    command: Sequence[str],
    cwd: Path,
    log_path: Path,
    timeout_seconds: int,
    environment: Mapping[str, str],
) -> dict[str, Any]:
    """Run one process group and preserve timeout as an execution outcome."""
    if not command or not all(isinstance(part, str) and part for part in command):
        raise ValueError("command must be a non-empty string sequence")
    if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive integer")
    cwd = Path(cwd)
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started_at = utc_now()
    start = time.monotonic()
    returncode: int | None = None
    termination = "process_error"
    error: str | None = None
    with log_path.open("xb") as log:
        try:
            process = subprocess.Popen(
                list(command),
                cwd=str(cwd),
                env=dict(environment),
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
        except OSError as exc:
            error = f"{type(exc).__name__}: {exc}"
        else:
            try:
                returncode = process.wait(timeout=timeout_seconds)
                termination = "completed" if returncode == 0 else "process_error"
            except subprocess.TimeoutExpired:
                termination = "timeout"
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except (ProcessLookupError, PermissionError):
                    pass
                try:
                    returncode = process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except (ProcessLookupError, PermissionError):
                        pass
                    returncode = process.wait()
    return {
        "command": list(command),
        "cwd": str(cwd),
        "timeout_seconds": timeout_seconds,
        "termination_reason": termination,
        "returncode": returncode,
        "wall_seconds": time.monotonic() - start,
        "started_at": started_at,
        "finished_at": utc_now(),
        "error": error,
        "log": artifact(log_path),
        "effective_child_environment": dict(sorted(environment.items())),
        "effective_child_environment_sha256": canonical_payload_sha256(dict(sorted(environment.items()))),
    }


def bind_process_environment(process: Mapping[str, Any], environment: Mapping[str, str]) -> dict[str, Any]:
    """Ensure injected/test runners carry the same effective-environment evidence."""
    bound = dict(process)
    frozen_environment = dict(sorted(environment.items()))
    bound["effective_child_environment"] = frozen_environment
    bound["effective_child_environment_sha256"] = canonical_payload_sha256(frozen_environment)
    return bound


def _validate_process_contract(
    process: Mapping[str, Any],
    expected_command: Sequence[str],
    expected_cwd: Path,
    expected_timeout_seconds: int,
    protocol: GradingProtocol,
    runtime_versions: Mapping[str, Any] | None = None,
) -> None:
    """Reject execution evidence that does not match the frozen launch contract."""
    if not isinstance(process, Mapping):
        raise ProtocolError("process evidence must be an object")
    if process.get("command") != list(expected_command):
        raise ProtocolError("process command differs from the frozen MPI command")
    recorded_cwd = process.get("cwd")
    if not isinstance(recorded_cwd, str):
        raise ProtocolError("process working directory is missing")
    if Path(recorded_cwd).resolve() != Path(expected_cwd).resolve():
        raise ProtocolError("process working directory differs from its attempt directory")
    if process.get("timeout_seconds") != expected_timeout_seconds:
        raise ProtocolError("process timeout differs from the frozen timeout")

    termination = process.get("termination_reason")
    if termination not in {"completed", "timeout", "process_error"}:
        raise ProtocolError("process termination reason is unsupported or unauthenticated")
    returncode = process.get("returncode")
    if returncode is not None and (isinstance(returncode, bool) or not isinstance(returncode, int)):
        raise ProtocolError("process return code must be an integer or null")
    error = process.get("error")
    if error is not None and not isinstance(error, str):
        raise ProtocolError("process error must be a string or null")
    if termination == "completed" and (returncode != 0 or error is not None):
        raise ProtocolError("completed process evidence must have return code zero and no error")
    if termination == "timeout" and error is not None:
        raise ProtocolError("timeout process evidence must not claim a launch error")
    if termination == "process_error" and returncode in (None, 0) and not error:
        raise ProtocolError("process_error requires a nonzero return code or launch error")

    wall = process.get("wall_seconds")
    if (isinstance(wall, bool) or not isinstance(wall, (int, float))
            or not math.isfinite(float(wall)) or float(wall) < 0.0):
        raise ProtocolError("process wall time must be finite and non-negative")
    started = _parse_utc_timestamp(process.get("started_at"), "process started_at")
    finished = _parse_utc_timestamp(process.get("finished_at"), "process finished_at")
    elapsed = (finished - started).total_seconds()
    if elapsed < 0.0:
        raise ProtocolError("process finished before it started")
    if abs(elapsed - float(wall)) > 30.0:
        raise ProtocolError("process wall time is inconsistent with its timestamps")
    if float(wall) > expected_timeout_seconds + 35.0:
        raise ProtocolError("process wall time exceeds the frozen watchdog allowance")
    if termination == "timeout" and float(wall) + 1.0 < expected_timeout_seconds:
        raise ProtocolError("timeout was reported before the frozen watchdog elapsed")

    child_environment = process.get("effective_child_environment")
    if not isinstance(child_environment, dict):
        raise ProtocolError("process lacks its effective child environment")
    if any(not isinstance(key, str) or not isinstance(value, str)
           for key, value in child_environment.items()):
        raise ProtocolError("effective child environment must contain only string pairs")
    required = protocol.payload["environment"]["required_child_environment"]
    if any(child_environment.get(key) != value for key, value in required.items()):
        raise ProtocolError("effective child environment changed a required solver setting")
    allowed_names = set(SAFE_INHERITED_ENV) | set(required)
    unexpected = sorted(set(child_environment) - allowed_names)
    if unexpected:
        raise ProtocolError("effective child environment contains unapproved names: " + ", ".join(unexpected))
    observed_environment_sha256 = canonical_payload_sha256(dict(sorted(child_environment.items())))
    if process.get("effective_child_environment_sha256") != observed_environment_sha256:
        raise ProtocolError("effective child environment hash is inconsistent")
    if runtime_versions is not None:
        fingerprint_environment = runtime_versions.get("effective_probe_environment")
        if (
            not isinstance(fingerprint_environment, dict)
            or child_environment != fingerprint_environment
        ):
            raise ProtocolError(
                "process child environment differs from the runtime fingerprint"
            )


def mpi_python_command(
    script_name: str,
    protocol: GradingProtocol,
    runtime_versions: Mapping[str, Any] | None = None,
) -> list[str]:
    execution = protocol.payload["execution"]
    exported = protocol.payload["environment"]["required_child_environment"]
    if runtime_versions is None:
        mpirun_path = "mpirun"
        python_path = sys.executable
    else:
        mpirun_path = (runtime_versions.get("mpirun") or {}).get("path")
        python_path = (runtime_versions.get("python_executable") or {}).get("path")
        if (
            not isinstance(mpirun_path, str)
            or not Path(mpirun_path).is_absolute()
            or not isinstance(python_path, str)
            or not Path(python_path).is_absolute()
        ):
            raise ProtocolError("runtime fingerprint lacks absolute MPI/Python executables")
    command = [mpirun_path, "-np", str(execution["mpi_ranks"])]
    for name in sorted(exported):
        command.extend(["-x", name])
    command.extend([python_path, script_name])
    return command


def load_manifest(path: str | os.PathLike[str], protocol: GradingProtocol) -> tuple[dict[str, Any], str]:
    manifest_path = Path(path).resolve()
    payload = read_json(manifest_path)
    supported_schemas = {
        "benchmark-redevelopment-manifest-v1",
        "cfd-protocol-qualification-manifest-v1",
    }
    if not isinstance(payload, dict) or payload.get("schema_version") not in supported_schemas:
        raise ProtocolError("unsupported benchmark manifest schema")
    reference = payload.get("protocol_reference")
    if not isinstance(reference, dict):
        raise ProtocolError("benchmark manifest lacks protocol_reference")
    if reference.get("path") != str(protocol.path.relative_to(ROOT)):
        raise ProtocolError("benchmark manifest points at a different protocol path")
    if reference.get("sha256") != protocol.sha256:
        raise ProtocolError("benchmark manifest protocol fingerprint is stale")
    reference_status = reference.get("status", reference.get("protocol_status"))
    if reference_status != protocol.payload["protocol_status"]:
        raise ProtocolError("benchmark manifest protocol status is stale")
    reference_official = reference.get(
        "official", reference.get("official_grading_enabled")
    )
    if reference_official is not protocol.payload["official_grading_enabled"]:
        raise ProtocolError("benchmark manifest official flag disagrees with its protocol")
    if payload.get("official_benchmark") is True and protocol.payload["protocol_status"] != "frozen":
        raise ProtocolError("a non-frozen protocol cannot back an official benchmark manifest")
    if payload.get("official_benchmark") is True:
        # Local import avoids a cycle: promotion validation reuses this grader
        # through the qualification evaluator.
        from scripts.cfd.promote_benchmark_manifest import (  # noqa: PLC0415
            PromotionError,
            validate_official_manifest,
        )

        try:
            validate_official_manifest(payload, repo_root=ROOT, verify_files=True)
        except PromotionError as exc:
            raise ProtocolError(f"official benchmark promotion is invalid: {exc}") from exc
    geometry_contract = payload.get("geometry_hash") or {}
    if geometry_contract.get("algorithm") != GEOMETRY_HASH_ALGORITHM:
        raise ProtocolError("benchmark manifest geometry hash algorithm is unsupported")
    row_key = "wings" if payload["schema_version"] == "benchmark-redevelopment-manifest-v1" else "controls"
    id_key = "wing_id" if row_key == "wings" else "control_id"
    wings = payload.get(row_key)
    if not isinstance(wings, list) or not wings:
        raise ProtocolError(f"manifest has no {row_key}")
    seen_ids: set[str] = set()
    seen_hashes: set[str] = set()
    for row in wings:
        if not isinstance(row, dict):
            raise ProtocolError("every manifest wing must be an object")
        wing_id = _safe_path_component(row.get(id_key), f"manifest {id_key}")
        if wing_id in seen_ids:
            raise ProtocolError(f"invalid or duplicate wing_id: {wing_id!r}")
        seen_ids.add(wing_id)
        geometry = validated_geometry(row.get("geom_38d"))
        observed = geometry_sha256(geometry)
        if row.get("geometry_sha256") != observed:
            raise ProtocolError(f"geometry hash mismatch for {wing_id}")
        if observed in seen_hashes:
            raise ProtocolError(f"duplicate geometry in manifest: {wing_id}")
        seen_hashes.add(observed)
    return payload, canonical_payload_sha256(payload)


def _parse_utc_timestamp(value: Any, label: str) -> datetime:
    if not isinstance(value, str):
        raise ProtocolError(f"{label} must be an ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProtocolError(f"{label} is not a valid ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise ProtocolError(f"{label} must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _publication_preflight_record(
    observation: Mapping[str, Any],
    *,
    protocol_sha256_value: str,
    manifest_sha256: str,
    authorization_sha256: str,
    ordered_manifest_wing_ids: Sequence[str],
) -> dict[str, Any]:
    return {
        "schema_version": _PUBLICATION_PREFLIGHT_SCHEMA_VERSION,
        "trust_model": _PUBLICATION_PREFLIGHT_TRUST_MODEL,
        "protocol_sha256": protocol_sha256_value,
        "manifest_sha256": manifest_sha256,
        "execution_authorization_sha256": authorization_sha256,
        "ordered_wing_ids": list(ordered_manifest_wing_ids),
        "ordered_wing_ids_sha256": canonical_payload_sha256(
            list(ordered_manifest_wing_ids)
        ),
        "observation": dict(observation),
    }


def _validate_publication_preflight_record(
    record: Mapping[str, Any],
    *,
    protocol_sha256_value: str,
    manifest_sha256: str,
    authorization_sha256: str,
    ordered_manifest_wing_ids: Sequence[str],
    publication_binding: Mapping[str, Any],
    reference_time: datetime,
    expected_sequence_index: int | None = None,
) -> dict[str, Any]:
    record_keys = {
        "schema_version", "trust_model", "protocol_sha256", "manifest_sha256",
        "execution_authorization_sha256", "ordered_wing_ids",
        "ordered_wing_ids_sha256",
        "observation",
    }
    observation_keys = {
        "preflight_status", "campaign_publication_id", "publication_bucket",
        "start_sequence_index", "verified_prefix_entry_count",
        "remaining_suffix_entry_count", "previous_entry_sha256",
        "previous_entry_object", "bucket_versioning_status",
        "bucket_object_lock_status", "initial_list_pages", "final_list_pages",
        "observed_at",
    }
    observation = record.get("observation")
    ordered = list(ordered_manifest_wing_ids)
    if not ordered or len(set(ordered)) != len(ordered):
        raise ProtocolError("campaign publication preflight roster is invalid")
    for wing_id in ordered:
        _safe_path_component(wing_id, "publication preflight wing_id")
    authorization_start = publication_binding.get("start_sequence_index")
    observed_start = observation.get("start_sequence_index") if isinstance(
        observation, Mapping
    ) else None
    if (
        not isinstance(record, Mapping)
        or set(record) != record_keys
        or record.get("schema_version") != _PUBLICATION_PREFLIGHT_SCHEMA_VERSION
        or record.get("trust_model") != _PUBLICATION_PREFLIGHT_TRUST_MODEL
        or record.get("protocol_sha256") != protocol_sha256_value
        or record.get("manifest_sha256") != manifest_sha256
        or record.get("execution_authorization_sha256") != authorization_sha256
        or record.get("ordered_wing_ids") != ordered
        or record.get("ordered_wing_ids_sha256")
        != canonical_payload_sha256(ordered)
        or not isinstance(observation, Mapping)
        or set(observation) != observation_keys
        or observation.get("preflight_status") != "passed"
        or observation.get("campaign_publication_id")
        != publication_binding.get("campaign_publication_id")
        or observation.get("publication_bucket")
        != publication_binding.get("publication_bucket")
        or isinstance(authorization_start, bool)
        or not isinstance(authorization_start, int)
        or isinstance(observed_start, bool)
        or not isinstance(observed_start, int)
        or observed_start < authorization_start
        or observed_start >= len(ordered)
        or (
            expected_sequence_index is not None
            and observed_start != expected_sequence_index
        )
        or observation.get("verified_prefix_entry_count") != observed_start
        or observation.get("remaining_suffix_entry_count")
        != len(ordered) - observed_start
        or observation.get("bucket_versioning_status") != "Enabled"
        or observation.get("bucket_object_lock_status") != "Enabled"
        or isinstance(observation.get("initial_list_pages"), bool)
        or not isinstance(observation.get("initial_list_pages"), int)
        or observation["initial_list_pages"] < 1
        or isinstance(observation.get("final_list_pages"), bool)
        or not isinstance(observation.get("final_list_pages"), int)
        or observation["final_list_pages"] < 1
    ):
        raise ProtocolError("campaign publication preflight record is invalid")
    previous_sha = observation.get("previous_entry_sha256")
    previous_object = observation.get("previous_entry_object")
    if observed_start == authorization_start:
        if (
            previous_sha != publication_binding.get("previous_entry_sha256")
            or canonical_json_bytes(previous_object)
            != canonical_json_bytes(publication_binding.get("previous_entry_object"))
        ):
            raise ProtocolError(
                "campaign publication preflight does not start at its authorized head"
            )
    else:
        expected_previous_key = publication_object_key(
            str(publication_binding.get("campaign_publication_id")),
            observed_start - 1,
            ordered[observed_start - 1],
        )
        if (
            not isinstance(previous_sha, str)
            or re.fullmatch(r"[0-9a-f]{64}", previous_sha) is None
            or not isinstance(previous_object, Mapping)
            or set(previous_object)
            != {"storage_provider", "bucket", "key", "version_id"}
            or previous_object.get("storage_provider") != "aws_s3"
            or previous_object.get("bucket")
            != publication_binding.get("publication_bucket")
            or previous_object.get("key") != expected_previous_key
            or not isinstance(previous_object.get("version_id"), str)
            or not previous_object["version_id"]
            or previous_object["version_id"] == "null"
        ):
            raise ProtocolError(
                "campaign publication preflight continuation head is invalid"
            )
    observed_at = _parse_utc_timestamp(
        observation.get("observed_at"), "publication preflight observed_at"
    )
    if reference_time.tzinfo is None:
        raise ProtocolError("campaign publication preflight reference time is naive")
    reference = reference_time.astimezone(timezone.utc)
    age_seconds = (reference - observed_at).total_seconds()
    if age_seconds < 0 or age_seconds > _PUBLICATION_PREFLIGHT_MAX_AGE_SECONDS:
        raise ProtocolError("campaign publication preflight is stale or future-dated")
    return dict(record)


def _validate_runtime_attestation_payload(
    payload: Mapping[str, Any],
    protocol: GradingProtocol,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, dict) or payload.get("schema_version") != "wingbench-cfd-runtime-attestation-v2":
        raise ProtocolError("unsupported runtime attestation schema")
    observed_sha256 = canonical_payload_sha256(payload)
    if observed_sha256 != expected_sha256:
        raise ProtocolError("runtime attestation is not the artifact bound by authorization")
    environment = protocol.payload["environment"]
    expected_manifest_digest = environment.get("final_child_image_manifest_digest")
    expected_linux_digest = environment.get("final_child_image_linux_amd64_digest")
    if not isinstance(expected_manifest_digest, str) or not isinstance(expected_linux_digest, str):
        raise ProtocolError(
            "protocol is not executable until the final child image digests are pinned"
        )
    runtime_sha = payload.get("runtime_fingerprint_sha256")
    checks = {
        "trust_model": payload.get("trust_model")
        == "operator_generated_observation_record_not_hardware_attestation",
        "attestation_status": payload.get("attestation_status") == "verified_by_host",
        "protocol_sha256": payload.get("protocol_sha256") == protocol.sha256,
        "child_manifest_digest": (
            payload.get("final_child_image_manifest_digest") == expected_manifest_digest
        ),
        "child_linux_digest": (
            payload.get("final_child_image_linux_amd64_digest") == expected_linux_digest
        ),
        "runtime_schema": (
            payload.get("runtime_fingerprint_schema_version")
            == "superwing-runtime-fingerprint-v2"
        ),
        "runtime_fingerprint": isinstance(runtime_sha, str) and len(runtime_sha) == 64,
        "probe_inside_child": payload.get("runtime_probe_executed_inside_final_child") is True,
        "mpi_smoke": payload.get("mpi_smoke_test_passed") is True,
        "mpi_ranks": payload.get("mpi_ranks_verified") == protocol.payload["execution"]["mpi_ranks"],
        "image_inspected": payload.get("host_image_inspection_passed") is True,
        "read_only": payload.get("mounted_read_only") is True,
        "architecture": payload.get("architecture") == "x86_64",
        "container_id": isinstance(payload.get("container_id"), str) and bool(payload["container_id"]),
        "host_image_id": isinstance(payload.get("host_image_id"), str) and bool(payload["host_image_id"]),
        "exclusive_node": payload.get("exclusive_grader_scheduling_verified") is True,
        "host_lock_mount": payload.get("host_backed_lock_mount_verified") is True,
        "host_lock_path": (
            payload.get("host_lock_path")
            == protocol.payload["execution"]["host_lock_path"]
        ),
    }
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ProtocolError("runtime attestation failed: " + ", ".join(failed))
    return dict(payload), observed_sha256


def load_runtime_attestation(
    path: str | os.PathLike[str],
    protocol: GradingProtocol,
    expected_sha256: str,
) -> tuple[dict[str, Any], str]:
    payload = read_json(Path(path).resolve())
    return _validate_runtime_attestation_payload(payload, protocol, expected_sha256)


def load_qualification_envelope_for_release(
    path: str | os.PathLike[str],
    protocol: GradingProtocol,
) -> dict[str, Any]:
    """Load the actual passing envelope and prove scientific promotion identity."""
    # Local import avoids a module cycle: the evaluator reuses validate_result.
    from scripts.cfd.evaluate_protocol_qualification import (  # noqa: PLC0415
        QualificationError,
        validate_qualification_envelope,
    )

    envelope_path = Path(path).resolve()
    envelope = read_json(envelope_path)
    if not isinstance(envelope, dict):
        raise ProtocolError("qualification envelope must be a JSON object")
    try:
        validate_qualification_envelope(
            envelope,
            verify_evidence_files=True,
            evidence_bundle_root=envelope_path.parent,
        )
    except QualificationError as exc:
        raise ProtocolError(f"qualification envelope failed validation: {exc}") from exc
    record = envelope.get("qualification_record") or {}
    decision = record.get("campaign_decision") or {}
    promotion = record.get("promotion") or {}
    if not (
        record.get("qualification_status") == "passed"
        and record.get("artifact_files_cryptographically_verified") is True
        and decision.get("all_gate_controls_passed") is True
        and promotion.get("eligible_for_separate_manual_freeze_review") is True
        and promotion.get("automatic_promotion_allowed") is False
        and promotion.get("promotion_performed") is False
        and promotion.get("official_authorization_granted") is False
    ):
        raise ProtocolError("qualification envelope is not eligible for manual freeze review")

    qualification_row = record.get("qualification_manifest") or {}
    qualification_path_value = qualification_row.get("path")
    if not isinstance(qualification_path_value, str):
        raise ProtocolError("qualification record lacks its manifest path")
    qualification_path = (ROOT / qualification_path_value).resolve()
    try:
        qualification_path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ProtocolError("qualification manifest path escapes the repository") from exc
    qualification_manifest = read_json(qualification_path)
    if not isinstance(qualification_manifest, dict):
        raise ProtocolError("qualification manifest must be a JSON object")
    if (
        qualification_row.get("manifest_id") != qualification_manifest.get("manifest_id")
        or qualification_row.get("canonical_sha256")
        != canonical_payload_sha256(qualification_manifest)
        or qualification_row.get("file_sha256") != sha256_file(qualification_path)
    ):
        raise ProtocolError("qualification record does not bind the current qualification manifest")
    candidate_snapshot = qualification_manifest.get("protocol_snapshot")
    candidate_row = record.get("candidate_protocol") or {}
    if (
        not isinstance(candidate_snapshot, dict)
        or canonical_payload_sha256(candidate_snapshot) != candidate_row.get("canonical_sha256")
        or candidate_row.get("canonical_sha256")
        != (qualification_manifest.get("protocol_reference") or {}).get("sha256")
    ):
        raise ProtocolError("qualification record does not bind its candidate protocol snapshot")

    frozen_promotion = protocol.payload.get("promotion") or {}
    record_sha256 = envelope.get("qualification_record_sha256")
    if not (
        frozen_promotion.get("source_candidate_protocol_sha256")
        == candidate_row.get("canonical_sha256")
        and frozen_promotion.get("qualification_record_sha256") == record_sha256
        and frozen_promotion.get("qualification_manifest_sha256")
        == qualification_row.get("canonical_sha256")
    ):
        raise ProtocolError("frozen protocol promotion metadata is not qualification-bound")

    allowed_promotion_changes = {
        "protocol_id",
        "protocol_revision",
        "protocol_status",
        "official_grading_enabled",
        "benchmark_release_id",
        "promotion",
    }
    candidate_scientific = {
        key: value for key, value in candidate_snapshot.items()
        if key not in allowed_promotion_changes
    }
    frozen_scientific = {
        key: value for key, value in protocol.payload.items()
        if key not in allowed_promotion_changes
    }
    if candidate_scientific != frozen_scientific:
        raise ProtocolError("frozen protocol is not scientifically identical to the qualified candidate")
    return {
        "envelope": envelope,
        "qualification_envelope_sha256": canonical_payload_sha256(envelope),
        "evidence_bundle_contract_sha256": canonical_payload_sha256(
            record.get("evidence_bundle")
        ),
        "qualification_record_sha256": record_sha256,
        "candidate_protocol_sha256": candidate_row.get("canonical_sha256"),
        "candidate_protocol_id": candidate_row.get("protocol_id"),
        "qualification_id": record.get("qualification_id"),
        "qualification_manifest_id": qualification_row.get("manifest_id"),
        "qualification_manifest_sha256": qualification_row.get("canonical_sha256"),
    }


def load_release_lock(
    path: str | os.PathLike[str],
    protocol: GradingProtocol,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    qualification: Mapping[str, Any],
) -> tuple[dict[str, Any], str]:
    payload = read_json(Path(path).resolve())
    from scripts.cfd.build_release_lock import (  # noqa: PLC0415
        ReleaseLockError,
        validate_release_lock_payload,
    )

    try:
        validate_release_lock_payload(payload)
    except ReleaseLockError as exc:
        raise ProtocolError(f"invalid benchmark release lock: {exc}") from exc
    manifest_release = manifest.get("release") or {}
    manifest_review = manifest_release.get("release_review") or {}
    lock_review = payload.get("release_review") or {}
    checks = {
        "release_status": payload.get("release_status") == "frozen",
        "release_id": payload.get("benchmark_release_id") == protocol.payload.get("benchmark_release_id"),
        "protocol_id": payload.get("protocol_id") == protocol.protocol_id,
        "protocol_sha256": payload.get("protocol_sha256") == protocol.sha256,
        "manifest_id": payload.get("manifest_id") == manifest.get("manifest_id"),
        "manifest_sha256": payload.get("manifest_sha256") == manifest_sha256,
        "source_candidate_manifest": (
            payload.get("source_candidate_manifest_sha256")
            == ((manifest.get("release") or {}).get("source_candidate_manifest") or {}).get(
                "canonical_sha256"
            )
        ),
        "candidate_protocol": (
            payload.get("qualified_candidate_protocol_sha256")
            == qualification.get("candidate_protocol_sha256")
        ),
        "candidate_protocol_id": (
            payload.get("qualified_candidate_protocol_id")
            == qualification.get("candidate_protocol_id")
        ),
        "qualification_id": (
            payload.get("qualification_id") == qualification.get("qualification_id")
        ),
        "qualification_manifest_id": (
            payload.get("qualification_manifest_id")
            == qualification.get("qualification_manifest_id")
        ),
        "qualification_manifest_sha256": (
            payload.get("qualification_manifest_sha256")
            == qualification.get("qualification_manifest_sha256")
        ),
        "qualification_envelope": (
            payload.get("qualification_envelope_sha256")
            == qualification.get("qualification_envelope_sha256")
            == (((manifest.get("release") or {}).get("qualification") or {}).get(
                "envelope_canonical_sha256"
            ))
        ),
        "qualification_evidence_bundle": (
            payload.get("qualification_evidence_bundle_contract_sha256")
            == qualification.get("evidence_bundle_contract_sha256")
            == (((manifest.get("release") or {}).get("qualification") or {}).get(
                "evidence_bundle_contract_sha256"
            ))
        ),
        "result_schema": payload.get("result_schema_version") == RESULT_SCHEMA_VERSION,
        "qualified": payload.get("qualification_passed") is True,
        "official": payload.get("official_benchmark") is True,
        "release_review_identity": all(
            lock_review.get(name) == manifest_review.get(name)
            for name in ("path", "schema_version", "canonical_sha256", "file_sha256")
        ),
        "release_review_decision": (
            lock_review.get("review_status") == manifest_release.get("review_status")
            and lock_review.get("reviewed_by") == manifest_release.get("reviewed_by")
            and lock_review.get("reviewed_at") == manifest_release.get("reviewed_at")
            and lock_review.get("review_note") == manifest_release.get("review_note")
            and payload.get("reviewed_at") == manifest_release.get("reviewed_at")
        ),
    }
    qualification_sha = payload.get("qualification_record_sha256")
    checks["qualification_record"] = (
        qualification_sha == qualification.get("qualification_record_sha256")
    )
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ProtocolError("benchmark release lock failed: " + ", ".join(failed))
    return payload, canonical_payload_sha256(payload)


def _validate_execution_authorization_core(
    payload: Mapping[str, Any],
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    release_lock_sha256: str | None,
    mode: str,
    run_id: str,
    wing_ids: Sequence[str],
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    expected_keys = {
        "schema_version", "trust_model", "authorization_id",
        "authorization_status", "authorized_for_billable_compute",
        "explicit_user_approval_recorded", "authorized_mode",
        "protocol_sha256", "manifest_sha256", "release_lock_sha256",
        "runtime_attestation_sha256", "run_id", "wing_ids",
        "campaign_publication", "max_concurrent_workers", "mpi_ranks",
        "worker_memory_gib", "official_labels_authorized", "expires_at",
        "approval_scope_note",
    }
    if (
        not isinstance(payload, dict)
        or set(payload) != expected_keys
        or payload.get("schema_version")
        != "wingbench-cfd-execution-authorization-v1"
    ):
        raise ProtocolError("unsupported execution-authorization schema")
    expected_wings = list(wing_ids)
    _safe_path_component(payload.get("authorization_id"), "authorization_id")
    _safe_path_component(run_id, "authorization run_id")
    for wing_id in expected_wings:
        _safe_path_component(wing_id, "authorized wing_id")
    checks = {
        "trust_model": payload.get("trust_model")
        == "procedural_scope_record_not_cryptographic_signature",
        "authorization_status": payload.get("authorization_status") == "approved",
        "billable_compute": payload.get("authorized_for_billable_compute") is True,
        "user_approval": payload.get("explicit_user_approval_recorded") is True,
        "mode": payload.get("authorized_mode") == mode,
        "protocol": payload.get("protocol_sha256") == protocol.sha256,
        "manifest": payload.get("manifest_sha256") == manifest_sha256,
        "release_lock": payload.get("release_lock_sha256") == release_lock_sha256,
        "run_id": payload.get("run_id") == run_id,
        "wing_ids": (
            bool(expected_wings)
            and payload.get("wing_ids") == expected_wings
            and len(set(expected_wings)) == len(expected_wings)
        ),
        "workers": payload.get("max_concurrent_workers") == 1,
        "mpi_ranks": payload.get("mpi_ranks") == protocol.payload["execution"]["mpi_ranks"],
        "memory": payload.get("worker_memory_gib") == 64,
        "official_labels": payload.get("official_labels_authorized") is (mode == "official"),
        "approval_scope_note": (
            isinstance(payload.get("approval_scope_note"), str)
            and bool(payload["approval_scope_note"].strip())
        ),
    }
    attestation_sha = payload.get("runtime_attestation_sha256")
    checks["runtime_attestation"] = (
        isinstance(attestation_sha, str)
        and re.fullmatch(r"[0-9a-f]{64}", attestation_sha) is not None
    )
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    expires = _parse_utc_timestamp(payload.get("expires_at"), "authorization expires_at")
    checks["not_expired"] = current < expires
    failed = [name for name, passed in checks.items() if not passed]
    if failed:
        raise ProtocolError("execution authorization failed: " + ", ".join(failed))
    return dict(payload), canonical_payload_sha256(payload)


def _validate_execution_authorization_payload(
    payload: Mapping[str, Any],
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    release_lock_sha256: str | None,
    mode: str,
    run_id: str,
    wing_ids: Sequence[str],
    ordered_manifest_wing_ids: Sequence[str],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_publication_recovery: bool,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    validated, digest = _validate_execution_authorization_core(
        payload,
        protocol=protocol,
        manifest_sha256=manifest_sha256,
        release_lock_sha256=release_lock_sha256,
        mode=mode,
        run_id=run_id,
        wing_ids=wing_ids,
        now=now,
    )
    try:
        validate_authorization_publication_binding(
            validated,
            ordered_manifest_wing_ids=ordered_manifest_wing_ids,
            requested_wing_ids=list(wing_ids),
            expected_campaign_publication_id=expected_campaign_publication_id,
            expected_publication_bucket=expected_publication_bucket,
            allow_recovery=allow_publication_recovery,
        )
    except CampaignPublicationError as exc:
        raise ProtocolError(
            f"execution authorization publication binding failed: {exc}"
        ) from exc
    return validated, digest


def load_execution_authorization(
    path: str | os.PathLike[str],
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    release_lock_sha256: str | None,
    mode: str,
    run_id: str,
    wing_ids: Sequence[str],
    ordered_manifest_wing_ids: Sequence[str],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_publication_recovery: bool,
    now: datetime | None = None,
) -> tuple[dict[str, Any], str]:
    payload = read_json(Path(path).resolve())
    return _validate_execution_authorization_payload(
        payload,
        protocol=protocol,
        manifest_sha256=manifest_sha256,
        release_lock_sha256=release_lock_sha256,
        mode=mode,
        run_id=run_id,
        wing_ids=wing_ids,
        ordered_manifest_wing_ids=ordered_manifest_wing_ids,
        expected_campaign_publication_id=expected_campaign_publication_id,
        expected_publication_bucket=expected_publication_bucket,
        allow_publication_recovery=allow_publication_recovery,
        now=now,
    )


def _decode_context_payload(data: bytes, label: str) -> dict[str, Any]:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"validated {label} context is malformed") from exc
    if not isinstance(payload, dict) or canonical_json_bytes(payload) != data:
        raise ProtocolError(f"validated {label} context is not canonical")
    return payload


def _validate_release_context_payload(
    payload: Mapping[str, Any],
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    expected_sha256: str,
) -> dict[str, Any]:
    from scripts.cfd.build_release_lock import (  # noqa: PLC0415
        ReleaseLockError,
        validate_release_lock_payload,
    )

    try:
        validate_release_lock_payload(payload)
    except ReleaseLockError as exc:
        raise ProtocolError(f"validated release-lock context is malformed: {exc}") from exc
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != "wingbench-cfd-release-lock-v1"
        or canonical_payload_sha256(payload) != expected_sha256
        or payload.get("release_status") != "frozen"
        or payload.get("protocol_sha256") != protocol.sha256
        or payload.get("manifest_sha256") != manifest_sha256
        or payload.get("official_benchmark") is not True
        or payload.get("qualification_passed") is not True
        or payload.get("qualification_record_sha256")
        != (protocol.payload.get("promotion") or {}).get("qualification_record_sha256")
    ):
        raise ProtocolError("validated release-lock context no longer matches the frozen scope")
    return dict(payload)


def _make_validated_execution_context(
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    mode: str,
    run_id: str,
    wing_ids: Sequence[str],
    ordered_manifest_wing_ids: Sequence[str],
    expected_campaign_publication_id: str,
    expected_publication_bucket: str,
    allow_publication_recovery: bool,
    publication_sequence_index: int,
    authorization: Mapping[str, Any],
    authorization_sha256: str,
    publication_preflight: Mapping[str, Any],
    publication_preflight_sha256: str,
    runtime_attestation: Mapping[str, Any],
    runtime_attestation_sha256: str,
    runtime_versions: Mapping[str, Any],
    release_lock: Mapping[str, Any] | None,
    release_lock_sha256: str | None,
) -> _ValidatedExecutionContext:
    """Mint a non-serializable launch capability while the host lock is held."""
    lock_path = Path(protocol.payload["execution"]["host_lock_path"]).resolve()
    if lock_path not in _HELD_WORKER_LOCKS:
        raise ProtocolError("validated execution context requires the active worker lock")
    selected_wings = tuple(wing_ids)
    validated_authorization, observed_authorization_sha256 = (
        _validate_execution_authorization_payload(
            authorization,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            release_lock_sha256=release_lock_sha256,
            mode=mode,
            run_id=run_id,
            wing_ids=selected_wings,
            ordered_manifest_wing_ids=ordered_manifest_wing_ids,
            expected_campaign_publication_id=expected_campaign_publication_id,
            expected_publication_bucket=expected_publication_bucket,
            allow_publication_recovery=allow_publication_recovery,
        )
    )
    if observed_authorization_sha256 != authorization_sha256:
        raise ProtocolError("execution authorization context hash changed after validation")
    validated_preflight = _validate_publication_preflight_record(
        publication_preflight,
        protocol_sha256_value=protocol.sha256,
        manifest_sha256=manifest_sha256,
        authorization_sha256=authorization_sha256,
        ordered_manifest_wing_ids=ordered_manifest_wing_ids,
        publication_binding=validated_authorization["campaign_publication"],
        reference_time=datetime.now(timezone.utc),
        expected_sequence_index=publication_sequence_index,
    )
    if canonical_payload_sha256(validated_preflight) != publication_preflight_sha256:
        raise ProtocolError("publication preflight context hash changed after validation")
    validated_attestation, observed_attestation_sha256 = (
        _validate_runtime_attestation_payload(
            runtime_attestation,
            protocol,
            validated_authorization["runtime_attestation_sha256"],
        )
    )
    if observed_attestation_sha256 != runtime_attestation_sha256:
        raise ProtocolError("runtime attestation context hash changed after validation")
    runtime_sha256 = validate_runtime_fingerprint(
        runtime_versions, protocol, official=mode == "official", active_runtime=True
    )
    if validated_attestation.get("runtime_fingerprint_sha256") != runtime_sha256:
        raise ProtocolError("runtime probe fingerprint is not bound by the host attestation")

    validated_release: dict[str, Any] | None = None
    if mode == "official":
        if release_lock is None or not isinstance(release_lock_sha256, str):
            raise ProtocolError("official execution context requires a validated release lock")
        validated_release = _validate_release_context_payload(
            release_lock,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            expected_sha256=release_lock_sha256,
        )
    elif release_lock is not None or release_lock_sha256 is not None:
        raise ProtocolError("diagnostic execution context cannot carry a release lock")

    return _ValidatedExecutionContext(
        _token=_EXECUTION_CONTEXT_TOKEN,
        protocol_sha256=protocol.sha256,
        manifest_sha256=manifest_sha256,
        mode=mode,
        run_id=run_id,
        wing_ids=selected_wings,
        ordered_manifest_wing_ids=tuple(ordered_manifest_wing_ids),
        campaign_publication_id=expected_campaign_publication_id,
        publication_bucket=expected_publication_bucket,
        publication_recovery_allowed=allow_publication_recovery,
        publication_sequence_index=publication_sequence_index,
        publication_preflight_json=canonical_json_bytes(validated_preflight),
        publication_preflight_sha256=publication_preflight_sha256,
        worker_lock_path=lock_path,
        authorization_json=canonical_json_bytes(validated_authorization),
        authorization_sha256=authorization_sha256,
        runtime_attestation_json=canonical_json_bytes(validated_attestation),
        runtime_attestation_sha256=runtime_attestation_sha256,
        release_lock_json=(
            canonical_json_bytes(validated_release) if validated_release is not None else None
        ),
        release_lock_sha256=release_lock_sha256,
    )


def _execution_context_payloads(
    context: _ValidatedExecutionContext,
    *,
    protocol: GradingProtocol,
    manifest_sha256: str,
    mode: str,
    run_id: str,
    wing_id: str,
    runtime_versions: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None, dict[str, Any]]:
    if (
        type(context) is not _ValidatedExecutionContext
        or context._token is not _EXECUTION_CONTEXT_TOKEN
        or context.protocol_sha256 != protocol.sha256
        or context.manifest_sha256 != manifest_sha256
        or context.mode != mode
        or context.run_id != run_id
        or wing_id not in context.wing_ids
        or context.publication_sequence_index < 0
        or context.publication_sequence_index >= len(context.ordered_manifest_wing_ids)
        or context.ordered_manifest_wing_ids[context.publication_sequence_index]
        != wing_id
        or context.worker_lock_path not in _HELD_WORKER_LOCKS
    ):
        raise ProtocolError("grading requires an active validated execution context")
    authorization = _decode_context_payload(context.authorization_json, "authorization")
    attestation = _decode_context_payload(
        context.runtime_attestation_json, "runtime attestation"
    )
    validated_authorization, authorization_sha256 = (
        _validate_execution_authorization_payload(
            authorization,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            release_lock_sha256=context.release_lock_sha256,
            mode=mode,
            run_id=run_id,
            wing_ids=context.wing_ids,
            ordered_manifest_wing_ids=context.ordered_manifest_wing_ids,
            expected_campaign_publication_id=context.campaign_publication_id,
            expected_publication_bucket=context.publication_bucket,
            allow_publication_recovery=context.publication_recovery_allowed,
        )
    )
    if authorization_sha256 != context.authorization_sha256:
        raise ProtocolError("validated authorization context fingerprint changed")
    publication_preflight = _decode_context_payload(
        context.publication_preflight_json, "campaign publication preflight"
    )
    validated_preflight = _validate_publication_preflight_record(
        publication_preflight,
        protocol_sha256_value=protocol.sha256,
        manifest_sha256=manifest_sha256,
        authorization_sha256=context.authorization_sha256,
        ordered_manifest_wing_ids=context.ordered_manifest_wing_ids,
        publication_binding=validated_authorization["campaign_publication"],
        reference_time=datetime.now(timezone.utc),
        expected_sequence_index=context.publication_sequence_index,
    )
    if canonical_payload_sha256(validated_preflight) != context.publication_preflight_sha256:
        raise ProtocolError("validated publication preflight context fingerprint changed")
    validated_attestation, attestation_sha256 = _validate_runtime_attestation_payload(
        attestation, protocol, context.runtime_attestation_sha256
    )
    if attestation_sha256 != context.runtime_attestation_sha256:
        raise ProtocolError("validated attestation context fingerprint changed")
    runtime_sha256 = validate_runtime_fingerprint(
        runtime_versions, protocol, official=mode == "official", active_runtime=True
    )
    if validated_attestation.get("runtime_fingerprint_sha256") != runtime_sha256:
        raise ProtocolError("active runtime differs from the validated attestation")

    validated_release = None
    if mode == "official":
        if context.release_lock_json is None or not isinstance(
            context.release_lock_sha256, str
        ):
            raise ProtocolError("official grading lacks its validated release context")
        release = _decode_context_payload(context.release_lock_json, "release lock")
        validated_release = _validate_release_context_payload(
            release,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            expected_sha256=context.release_lock_sha256,
        )
    elif context.release_lock_json is not None or context.release_lock_sha256 is not None:
        raise ProtocolError("diagnostic grading carries an official release context")
    return (
        validated_authorization,
        validated_attestation,
        validated_release,
        validated_preflight,
    )


def _fresh_directory(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"attempt path already exists; refusing stale reuse: {path}")
    path.mkdir(parents=True)


@contextmanager
def exclusive_worker_lock(path: Path):
    """Prevent concurrent np16 solves on the same 64-GiB worker."""
    path = Path(path).resolve()
    if path in _HELD_WORKER_LOCKS:
        raise ProtocolError(f"another whole-wing CFD process holds {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ProtocolError(f"another whole-wing CFD process holds {path}") from exc
        os.ftruncate(descriptor, 0)
        os.write(descriptor, f"pid={os.getpid()} started={utc_now()}\n".encode("utf-8"))
        os.fsync(descriptor)
        _HELD_WORKER_LOCKS.add(path)
        yield
    finally:
        _HELD_WORKER_LOCKS.discard(path)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _read_optional_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, "missing_output"
    try:
        payload = read_json(path)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"malformed_output: {type(exc).__name__}: {exc}"
    if not isinstance(payload, dict):
        return None, "malformed_output: root is not an object"
    return payload, None


def _select_final_mesh(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    if not attempts:
        raise ProtocolError("mesh has no attempts")
    completed = [attempt for attempt in attempts if attempt.get("outcome") != "inconclusive"]
    final = dict(completed[-1] if completed else attempts[-1])
    final["attempts"] = attempts
    return final


def _not_run_condition(spec: Mapping[str, Any], reason: str) -> dict[str, Any]:
    return {
        "condition_id": spec["condition_id"],
        "mach": float(spec["mach"]),
        "cl_target": float(spec["cl_target"]),
        "outcome_class": "not_run",
        "outcome": reason,
        "reason_codes": [reason],
        "measurements": {
            "mach": float(spec["mach"]),
            "cl_target": float(spec["cl_target"]),
            "seed_alpha0_deg": None,
            "realized_aoa_deg": None,
            "cl": None,
            "cd": None,
            "ld": None,
            "err_cl": None,
            "solvecl_iterations": None,
            "solvecl_l2convergence": None,
            "solvecl_time_seconds": None,
        },
        "attempts": [],
    }


def _git_commit() -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def probe_runtime(environment: Mapping[str, str], output_path: Path) -> dict[str, Any]:
    """Run imports in a clean subprocess before any mesh or solver is launched."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    required_environment = {
        name: value
        for name, value in environment.items()
        if name.startswith("ADFLOW_")
        or name in {"OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"}
    }
    command = [
        sys.executable,
        str(ROOT / "scripts/cfd/runtime_probe.py"),
        "--output",
        str(output_path),
        "--mpi-ranks",
        "16",
        "--required-child-environment-json",
        canonical_json_bytes(required_environment).decode("utf-8"),
    ]
    try:
        result = subprocess.run(
            command,
            cwd=ROOT,
            env=dict(environment),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=120,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProtocolError(f"runtime preflight could not run: {type(exc).__name__}: {exc}") from exc
    if result.returncode != 0 or not output_path.is_file():
        raise ProtocolError(
            f"runtime preflight failed with return code {result.returncode}: {result.stdout[-2000:]}"
        )
    payload = read_json(output_path)
    if not isinstance(payload, dict) or payload.get("status") != "complete":
        raise ProtocolError("runtime preflight did not produce a complete fingerprint")
    return payload


def validate_runtime_fingerprint(
    payload: Mapping[str, Any],
    protocol: GradingProtocol,
    *,
    official: bool,
    active_runtime: bool = False,
) -> str:
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != "superwing-runtime-fingerprint-v2"
        or payload.get("status") != "complete"
    ):
        raise ProtocolError("solver runtime fingerprint is incomplete")
    observed_commit = payload.get("cst_modeling3d_commit_observed")
    expected_commit = protocol.payload["environment"]["cst_modeling3d_commit"]
    if observed_commit != expected_commit:
        raise ProtocolError(
            f"CST runtime commit mismatch: expected {expected_commit}, observed {observed_commit}"
        )
    identity = payload.get("process_identity") or {}
    if protocol.payload["environment"]["require_non_root"] and identity.get("euid") == 0:
        raise ProtocolError("the frozen runtime must not execute MPI as root")
    mpi_smoke = payload.get("mpi_smoke_test") or {}
    filtered_import_smoke = payload.get("filtered_scientific_import_smoke") or {}
    mpirun = payload.get("mpirun") or {}
    python_executable = payload.get("python_executable") or {}
    mpirun_path = mpirun.get("path")
    python_path = python_executable.get("path")
    effective_probe_environment = payload.get("effective_probe_environment")
    allowed_environment_names = set(SAFE_INHERITED_ENV) | set(
        protocol.payload["environment"]["required_child_environment"]
    )
    expected_environment = dict(sorted(
        protocol.payload["environment"]["required_child_environment"].items()
    ))
    if not (
        mpi_smoke.get("schema_version") == "superwing-mpi-smoke-v1"
        and mpi_smoke.get("passed") is True
        and mpi_smoke.get("requested_ranks") == protocol.payload["execution"]["mpi_ranks"] == 16
        and mpi_smoke.get("required_child_environment") == expected_environment
        and mpi_smoke.get("probe_process_load_bearing_environment") == expected_environment
        and isinstance(mpirun_path, str)
        and Path(mpirun_path).is_absolute()
        and mpi_smoke.get("launcher_path") == mpirun_path
        and isinstance(mpirun.get("sha256"), str)
        and len(mpirun["sha256"]) == 64
        and isinstance(python_path, str)
        and Path(python_path).is_absolute()
        and (payload.get("python") or {}).get("executable") == python_path
        and mpi_smoke.get("python_executable") == python_path
        and isinstance(python_executable.get("sha256"), str)
        and len(python_executable["sha256"]) == 64
        and isinstance(effective_probe_environment, dict)
        and all(
            isinstance(name, str) and isinstance(value, str)
            for name, value in effective_probe_environment.items()
        )
        and set(effective_probe_environment) <= allowed_environment_names
        and all(
            effective_probe_environment.get(name) == value
            for name, value in expected_environment.items()
        )
        and filtered_import_smoke.get("passed") is True
        and filtered_import_smoke.get("returncode") == 0
        and filtered_import_smoke.get("effective_environment")
        == effective_probe_environment
    ):
        raise ProtocolError("runtime fingerprint lacks a valid exact-environment MPI-16 smoke test")
    if active_runtime:
        current_mpirun = shutil.which("mpirun")
        current_environment = dict(sorted(protocol.child_environment().items()))
        if (
            not Path(mpirun_path).is_file()
            or sha256_file(Path(mpirun_path)) != mpirun["sha256"]
            or not isinstance(current_mpirun, str)
            or Path(current_mpirun).resolve() != Path(mpirun_path).resolve()
            or not Path(python_path).is_file()
            or sha256_file(Path(python_path)) != python_executable["sha256"]
            or Path(sys.executable).resolve() != Path(python_path).resolve()
            or effective_probe_environment != current_environment
        ):
            raise ProtocolError("active runtime differs from its pinned probe fingerprint")
    fingerprint = canonical_payload_sha256(payload)
    expected_runtime = protocol.payload["environment"].get("expected_runtime_fingerprint_sha256")
    if expected_runtime is None:
        raise ProtocolError(
            "protocol is not executable until its qualified runtime fingerprint is pinned"
        )
    if fingerprint != expected_runtime:
        raise ProtocolError(
            f"runtime fingerprint mismatch: expected {expected_runtime}, observed {fingerprint}"
        )
    return fingerprint


def grade_wing(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    wing: Mapping[str, Any],
    protocol: GradingProtocol,
    mode: str,
    run_id: str,
    work_root: Path,
    runtime_versions: Mapping[str, Any],
    execution_context: _ValidatedExecutionContext,
) -> dict[str, Any]:
    """Production entry point; callers must hold the opaque, locked CLI context."""
    return _grade_wing_impl(
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        wing=wing,
        protocol=protocol,
        mode=mode,
        run_id=run_id,
        work_root=work_root,
        process_runner=run_process,
        case_preparer=prepare_geometry_case,
        mesh_validator=validate_mesh_artifacts,
        runtime_versions=runtime_versions,
        execution_context=execution_context,
        test_only=False,
    )


def grade_wing_test_only(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    wing: Mapping[str, Any],
    protocol: GradingProtocol,
    mode: str,
    run_id: str,
    work_root: Path,
    process_runner: ProcessRunner,
    case_preparer: CasePreparer = prepare_geometry_case,
    mesh_validator: MeshValidator = validate_mesh_artifacts,
) -> dict[str, Any]:
    """No-subprocess state-machine harness; never an execution authorization path."""
    protocol.require_mode(mode)
    if (
        mode != "diagnostic"
        or not os.environ.get("PYTEST_CURRENT_TEST")
        or process_runner is run_process
        or getattr(process_runner, "__wingbench_test_double__", False) is not True
    ):
        raise ProtocolError("test-only grading requires a marked pytest diagnostic double")
    return _grade_wing_impl(
        manifest_path=manifest_path,
        manifest=manifest,
        manifest_sha256=manifest_sha256,
        wing=wing,
        protocol=protocol,
        mode=mode,
        run_id=run_id,
        work_root=work_root,
        process_runner=process_runner,
        case_preparer=case_preparer,
        mesh_validator=mesh_validator,
        runtime_versions=None,
        execution_context=None,
        test_only=True,
    )


def _grade_wing_impl(
    *,
    manifest_path: Path,
    manifest: Mapping[str, Any],
    manifest_sha256: str,
    wing: Mapping[str, Any],
    protocol: GradingProtocol,
    mode: str,
    run_id: str,
    work_root: Path,
    process_runner: ProcessRunner,
    case_preparer: CasePreparer,
    mesh_validator: MeshValidator,
    runtime_versions: Mapping[str, Any] | None,
    execution_context: _ValidatedExecutionContext | None,
    test_only: bool,
) -> dict[str, Any]:
    """Internal engine shared with the explicitly non-production test harness."""
    protocol.require_mode(mode)
    protocol.reject_parent_overrides()
    if (
        not test_only
        and protocol.payload["environment"]["require_non_root"]
        and hasattr(os, "geteuid")
        and os.geteuid() == 0
    ):
        raise ProtocolError("the np16 grading protocol refuses root execution")
    wing_id = _safe_path_component(wing.get("wing_id"), "wing_id")
    _safe_path_component(run_id, "run_id")
    execution_authorization: dict[str, Any] | None
    publication_preflight: dict[str, Any] | None
    runtime_attestation: dict[str, Any] | None
    release_lock: dict[str, Any] | None
    execution_authorization_sha256: str | None
    publication_preflight_sha256: str | None
    runtime_attestation_sha256: str | None
    release_lock_sha256: str | None
    if test_only:
        if execution_context is not None or runtime_versions is not None or mode != "diagnostic":
            raise ProtocolError("test-only grading cannot carry executable runtime claims")
        execution_authorization = None
        publication_preflight = None
        runtime_attestation = None
        release_lock = None
        execution_authorization_sha256 = None
        publication_preflight_sha256 = None
        runtime_attestation_sha256 = None
        release_lock_sha256 = None
    else:
        if execution_context is None or runtime_versions is None:
            raise ProtocolError("grading requires an active validated execution context")
        (
            execution_authorization,
            runtime_attestation,
            release_lock,
            publication_preflight,
        ) = (
            _execution_context_payloads(
                execution_context,
                protocol=protocol,
                manifest_sha256=manifest_sha256,
                mode=mode,
                run_id=run_id,
                wing_id=wing_id,
                runtime_versions=runtime_versions,
            )
        )
        execution_authorization_sha256 = execution_context.authorization_sha256
        publication_preflight_sha256 = execution_context.publication_preflight_sha256
        runtime_attestation_sha256 = execution_context.runtime_attestation_sha256
        release_lock_sha256 = execution_context.release_lock_sha256
    geometry = validated_geometry(wing["geom_38d"])
    geometry_digest = geometry_sha256(geometry)
    if geometry_digest != wing.get("geometry_sha256"):
        raise ProtocolError(f"geometry hash mismatch for {wing_id}")
    expected_case_input = _expected_case_input(geometry, protocol)
    wing_work = Path(work_root) / run_id / wing_id
    _fresh_directory(wing_work)
    started_at = utc_now()
    execution = protocol.payload["execution"]
    child_env = protocol.child_environment()
    seed = protocol.seed_alpha0
    seed_conditions = [(float(row["mach"]), seed) for row in protocol.conditions]
    max_attempts = 1 + int(execution["infrastructure_retries"])

    mesh_attempts: list[dict[str, Any]] = []
    usable_case: Path | None = None
    for attempt_index in range(max_attempts):
        attempt_root = wing_work / f"mesh_attempt_{attempt_index:02d}"
        _fresh_directory(attempt_root)
        case = Path(case_preparer(
            wing_id,
            geometry,
            str(attempt_root),
            seed_conditions,
            int(execution["mpi_ranks"]),
            robust_mesh=False,
            screen_ncycles=None,
        ))
        if case.resolve() != (attempt_root / wing_id).resolve():
            raise ProtocolError("case preparer returned a directory outside the fresh attempt")
        shutil.copy2(case / "input.json", case / "input.before_mesh.json")
        raw_input_before_mesh = read_json(case / "input.before_mesh.json")
        _validate_case_input(
            raw_input_before_mesh,
            expected_case_input,
            exact=True,
            label="pre-mesh input",
        )
        generated = {
            name: artifact(case / name)
            for name in ("input.before_mesh.json", "gen-mesh.py", "run-adflow.py", "single-point.py", "original_tip.xyz")
        }
        for name, expected_sha256 in protocol.payload["generated_case_file_sha256"].items():
            observed_sha256 = generated[name]["sha256"]
            if observed_sha256 != expected_sha256:
                raise ProtocolError(
                    f"generated scientific script drift for {name}: "
                    f"expected {expected_sha256}, observed {observed_sha256}"
                )
        process = process_runner(
            mpi_python_command("gen-mesh.py", protocol, runtime_versions),
            case,
            case / "grade_mesh.log",
            int(execution["mesh_timeout_seconds"]),
            child_env,
        )
        process = bind_process_environment(process, child_env)
        mesh_info_path = case / "mesh_info.json"
        mesh_info, output_error = _read_optional_json(mesh_info_path)
        volume_meshing_log_artifact = artifact(case / "volume_meshing.log")
        raw_volume_meshing_log_evidence, volume_meshing_log_evidence_error = (
            _mesh_log_crosscheck(mesh_info, volume_meshing_log_artifact)
        )
        raw_input_after_mesh, input_after_mesh_error = _read_optional_json(case / "input.json")
        if raw_input_after_mesh is not None:
            if input_after_mesh_error is not None:
                raise ProtocolError("parseable post-mesh input carries a parse error")
            _validate_case_input(
                raw_input_after_mesh,
                expected_case_input,
                exact=False,
                label="post-mesh input",
            )
        volume_grid = artifact(case / "wing_vol.cgns")
        mesh_validation = mesh_validator(case / "wing_vol.cgns", case / "input.json")
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
            mesh_info if volume_meshing_log_evidence_error is None else None,
            protocol,
            volume_grid_valid=mesh_validation["valid"] is True,
        )
        if classified["outcome"] in {"valid", "marginal"} and raw_input_after_mesh is None:
            raise ProtocolError("usable mesh lacks a parseable geometry-bound post-mesh input")
        if classified["outcome"] in {"valid", "marginal"}:
            _validate_post_mesh_derived_geometry(
                raw_input_after_mesh, expected_case_input
            )
        classified.update({
            "attempt_index": attempt_index,
            "case_directory": str(case),
            "generated_artifacts": generated,
            "raw_input_before_mesh": raw_input_before_mesh,
            "raw_input_after_mesh": raw_input_after_mesh,
            "input_after_mesh_error": input_after_mesh_error,
            "raw_mesh_info": mesh_info,
            "raw_volume_meshing_log_evidence": raw_volume_meshing_log_evidence,
            "volume_meshing_log_evidence_error": volume_meshing_log_evidence_error,
            "raw_mesh_validation": mesh_validation,
            "mesh_info_artifact": artifact(mesh_info_path),
            "volume_meshing_log_artifact": volume_meshing_log_artifact,
            "mesh_validation_artifact": artifact(mesh_validation_path),
            "volume_grid_artifact": volume_grid,
            "input_after_mesh_artifact": artifact(case / "input.json"),
            "output_error": output_error,
        })
        mesh_attempts.append(classified)
        if classified["outcome"] in {"valid", "marginal"}:
            usable_case = case
            break
        if classified["outcome"] == "unmeshable":
            break
    mesh = _select_final_mesh(mesh_attempts)

    by_index: dict[int, dict[str, Any]] = {}
    if usable_case is None:
        reason = "not_run_unmeshable" if mesh["outcome"] == "unmeshable" else "not_run_mesh_inconclusive"
        for index, spec in enumerate(protocol.conditions):
            by_index[index] = _not_run_condition(spec, reason)
    else:
        for condition_index in protocol.payload["condition_execution_order"]:
            spec = protocol.conditions[condition_index]
            attempts: list[dict[str, Any]] = []
            for attempt_index in range(max_attempts):
                attempt_dir = usable_case / f"{spec['condition_id']}_attempt_{attempt_index:02d}"
                _fresh_directory(attempt_dir)
                conds = {
                    "M3": float(spec["mach"]),
                    "AA": seed,
                    "CL_targ": float(spec["cl_target"]),
                    "re": float(protocol.payload["flow"]["reynolds"]),
                    "temp": float(protocol.payload["flow"]["temperature_k"]),
                }
                atomic_write_json(attempt_dir / "conds.json", conds)
                shutil.copy2(usable_case / "run-adflow.py", attempt_dir / "run-adflow.py")
                process = process_runner(
                    mpi_python_command("run-adflow.py", protocol, runtime_versions),
                    attempt_dir,
                    attempt_dir / "grade_adflow.log",
                    int(execution["solve_timeout_seconds_per_attempt"]),
                    child_env,
                )
                process = bind_process_environment(process, child_env)
                output_path = attempt_dir / "output.json"
                output, output_error = _read_optional_json(output_path)
                adflow_log_artifact = artifact(attempt_dir / "adflow.log")
                raw_adflow_log_evidence, adflow_log_evidence_error = (
                    _adflow_log_crosscheck(output, adflow_log_artifact)
                )
                classified = classify_condition_attempt(
                    process,
                    output if adflow_log_evidence_error is None else None,
                    spec,
                    seed,
                    protocol,
                )
                classified.update({
                    "attempt_index": attempt_index,
                    "attempt_directory": str(attempt_dir),
                    "output_error": output_error,
                    "raw_output": output,
                    "raw_adflow_log_evidence": raw_adflow_log_evidence,
                    "adflow_log_evidence_error": adflow_log_evidence_error,
                    "artifacts": {
                        "conditions": artifact(attempt_dir / "conds.json"),
                        "solver_script": artifact(attempt_dir / "run-adflow.py"),
                        "output": artifact(output_path),
                        "adflow_log": adflow_log_artifact,
                    },
                })
                attempts.append(classified)
                if classified["outcome_class"] != "inconclusive":
                    break
            by_index[condition_index] = finalize_condition(spec, attempts)
    conditions = [by_index[index] for index in protocol.payload["condition_output_order"]]
    aggregate = aggregate_wing(mesh, conditions, protocol)
    result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "official": mode == "official",
        "run_id": run_id,
        "wing_id": wing_id,
        "protocol": {
            "protocol_id": protocol.protocol_id,
            "protocol_status": protocol.payload["protocol_status"],
            "canonical_sha256": protocol.sha256,
            "snapshot": protocol.snapshot(),
        },
        "benchmark_manifest": {
            "manifest_id": manifest.get("manifest_id"),
            "path": str(Path(manifest_path).resolve()),
            "canonical_sha256": manifest_sha256,
            "official_benchmark": manifest.get("official_benchmark") is True,
        },
        "release_lock": (
            {
                "benchmark_release_id": release_lock.get("benchmark_release_id"),
                "canonical_sha256": release_lock_sha256,
                "qualification_record_sha256": release_lock.get("qualification_record_sha256"),
                "snapshot": dict(release_lock),
            }
            if not test_only and release_lock is not None else None
        ),
        "execution_authorization": (
            {
                "authorization_id": execution_authorization.get("authorization_id"),
                "canonical_sha256": execution_authorization_sha256,
                "runtime_attestation_sha256": runtime_attestation_sha256,
                "snapshot": dict(execution_authorization),
            }
            if not test_only and execution_authorization is not None
            else {"test_only_fake_runner": True}
        ),
        "campaign_publication_preflight": (
            {
                "canonical_sha256": publication_preflight_sha256,
                "snapshot": dict(publication_preflight),
            }
            if not test_only and publication_preflight is not None
            else {"test_only_fake_runner": True}
        ),
        "geometry": {
            "schema": "superwing-38d-v1",
            "hash_algorithm": GEOMETRY_HASH_ALGORITHM,
            "sha256": geometry_digest,
            "geom_38d": list(geometry),
            "manifest_provenance": wing.get("provenance"),
            "canonical_source": wing.get("canonical_source"),
        },
        "runtime": {
            "started_at": started_at,
            "finished_at": utc_now(),
            "git_commit": _git_commit(),
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "final_child_image_linux_amd64_digest_observed": (
                runtime_attestation.get("final_child_image_linux_amd64_digest")
                if not test_only and runtime_attestation is not None else None
            ),
            "runtime_attestation": (
                dict(runtime_attestation)
                if not test_only and runtime_attestation is not None
                else {"status": "not_provided_test_only"}
            ),
            "runtime_attestation_sha256": runtime_attestation_sha256,
            "aws_instance_type_observed": os.environ.get("AWS_INSTANCE_TYPE"),
            "aws_lifecycle_observed": os.environ.get("AWS_EC2_LIFECYCLE"),
            "solver_environment": dict(runtime_versions or {"status": "not_probed_test_only"}),
            "solver_environment_sha256": (
                canonical_payload_sha256(runtime_versions) if runtime_versions else None
            ),
        },
        "mesh": mesh,
        "conditions": conditions,
        "aggregate": aggregate,
        "legacy_evidence_used_for_verdict": False,
    }
    validate_result(result, protocol, wing, manifest_sha256, verify_artifacts=True)
    return result


def validate_result(
    result: Mapping[str, Any],
    protocol: GradingProtocol,
    wing: Mapping[str, Any],
    manifest_sha256: str,
    *,
    verify_artifacts: bool = True,
    artifact_store: Path | None = None,
) -> None:
    if verify_artifacts is not True:
        raise ProtocolError("result validation requires cryptographic artifact verification")
    if result.get("schema_version") != RESULT_SCHEMA_VERSION:
        raise ProtocolError("result schema_version mismatch")
    if type(result.get("official")) is not bool:
        raise ProtocolError("result official flag must be boolean")
    if result.get("wing_id") != wing.get("wing_id"):
        raise ProtocolError("result wing_id mismatch")
    _safe_path_component(result.get("run_id"), "result run_id")
    if result.get("legacy_evidence_used_for_verdict") is not False:
        raise ProtocolError("v5 verdicts must not use legacy evidence")
    protocol_row = result.get("protocol") or {}
    if protocol_row.get("canonical_sha256") != protocol.sha256:
        raise ProtocolError("result protocol fingerprint mismatch")
    if protocol_sha := protocol_row.get("snapshot"):
        if canonical_payload_sha256(protocol_sha) != protocol.sha256:
            raise ProtocolError("result protocol snapshot does not match its fingerprint")
    else:
        raise ProtocolError("result lacks protocol snapshot")
    geometry = result.get("geometry") or {}
    if geometry.get("sha256") != wing.get("geometry_sha256"):
        raise ProtocolError("result geometry fingerprint mismatch")
    if geometry_sha256(geometry.get("geom_38d")) != geometry.get("sha256"):
        raise ProtocolError("result embedded geometry does not match its fingerprint")
    if canonical_json_bytes(geometry.get("manifest_provenance")) != canonical_json_bytes(
        wing.get("provenance")
    ):
        raise ProtocolError("result geometry provenance differs from its manifest row")
    if canonical_json_bytes(geometry.get("canonical_source")) != canonical_json_bytes(
        wing.get("canonical_source")
    ):
        raise ProtocolError("result canonical geometry source differs from its manifest row")
    expected_case_input = _expected_case_input(geometry.get("geom_38d"), protocol)
    if (result.get("benchmark_manifest") or {}).get("canonical_sha256") != manifest_sha256:
        raise ProtocolError("result benchmark manifest fingerprint mismatch")
    conditions = result.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != len(protocol.conditions):
        raise ProtocolError("result must contain exactly three ordered conditions")
    runtime = result.get("runtime") or {}
    solver_environment = runtime.get("solver_environment") or {}
    test_only_result = (
        (result.get("execution_authorization") or {}).get("test_only_fake_runner") is True
    )
    command_runtime = (
        solver_environment if solver_environment.get("status") == "complete" else None
    )
    mesh = result.get("mesh") or {}
    mesh_attempts = mesh.get("attempts")
    if not isinstance(mesh_attempts, list) or not mesh_attempts:
        raise ProtocolError("result mesh must preserve every attempt")
    max_attempts = 1 + int(protocol.payload["execution"]["infrastructure_retries"])
    if len(mesh_attempts) > max_attempts:
        raise ProtocolError("mesh exceeds the frozen infrastructure retry count")
    rebuilt_mesh_attempts = []
    seen_attempt_directories: set[Path] = set()
    previous_finished = None
    first_process_started: datetime | None = None
    for attempt_index, stored in enumerate(mesh_attempts):
        if stored.get("attempt_index") != attempt_index:
            raise ProtocolError("mesh attempt indices are not sequential")
        if attempt_index and mesh_attempts[attempt_index - 1].get("outcome") != "inconclusive":
            raise ProtocolError("mesh retry followed a conclusive attempt")
        case_directory = Path(str(stored.get("case_directory")))
        resolved_case = case_directory.resolve()
        if (
            case_directory.name != str(result.get("wing_id"))
            or case_directory.parent.name != f"mesh_attempt_{attempt_index:02d}"
            or resolved_case in seen_attempt_directories
        ):
            raise ProtocolError("mesh attempt directory does not match the fresh-attempt layout")
        seen_attempt_directories.add(resolved_case)
        _validate_process_contract(
            stored.get("execution") or {},
            mpi_python_command("gen-mesh.py", protocol, command_runtime),
            case_directory,
            int(protocol.payload["execution"]["mesh_timeout_seconds"]),
            protocol,
            command_runtime,
        )
        started = _parse_utc_timestamp((stored.get("execution") or {}).get("started_at"), "mesh started_at")
        finished = _parse_utc_timestamp((stored.get("execution") or {}).get("finished_at"), "mesh finished_at")
        if first_process_started is None:
            first_process_started = started
        if previous_finished is not None and started < previous_finished:
            raise ProtocolError("mesh retries overlap or are out of order")
        previous_finished = finished
        generated = stored.get("generated_artifacts") or {}
        if set(generated) != {
            "input.before_mesh.json", "gen-mesh.py", "run-adflow.py",
            "single-point.py", "original_tip.xyz",
        }:
            raise ProtocolError("mesh attempt generated-artifact set is incomplete")
        for name, record in generated.items():
            _validate_artifact_record(
                record,
                case_directory / name,
                required=True,
                expected_sha256=protocol.payload["generated_case_file_sha256"].get(name),
            )
        raw_input_before_mesh = stored.get("raw_input_before_mesh")
        raw_input_after_mesh = stored.get("raw_input_after_mesh")
        _validate_case_input(
            raw_input_before_mesh,
            expected_case_input,
            exact=True,
            label="stored pre-mesh input",
        )
        if raw_input_after_mesh is not None:
            if stored.get("input_after_mesh_error") is not None:
                raise ProtocolError("parseable post-mesh input carries a parse error")
            _validate_case_input(
                raw_input_after_mesh,
                expected_case_input,
                exact=False,
                label="stored post-mesh input",
            )
        elif stored.get("outcome") in {"valid", "marginal"}:
            raise ProtocolError("stored usable mesh lacks a geometry-bound post-mesh input")
        elif not isinstance(stored.get("input_after_mesh_error"), str):
            raise ProtocolError("missing post-mesh input lacks a parse-error record")
        if stored.get("outcome") in {"valid", "marginal"}:
            _validate_post_mesh_derived_geometry(
                raw_input_after_mesh, expected_case_input
            )
        _validate_artifact_record(
            (stored.get("execution") or {}).get("log") or {},
            case_directory / "grade_mesh.log",
            required=True,
        )
        _validate_artifact_record(
            stored.get("mesh_info_artifact") or {},
            case_directory / "mesh_info.json",
            required=False,
        )
        _validate_artifact_record(
            stored.get("volume_meshing_log_artifact") or {},
            case_directory / "volume_meshing.log",
            required=False,
        )
        _validate_artifact_record(
            stored.get("mesh_validation_artifact") or {},
            case_directory / "mesh_validation.json",
            required=True,
        )
        _validate_artifact_record(
            stored.get("volume_grid_artifact") or {},
            case_directory / "wing_vol.cgns",
            required=False,
        )
        _validate_artifact_record(
            stored.get("input_after_mesh_artifact") or {},
            case_directory / "input.json",
            required=False,
        )
        volume = stored.get("volume_grid_artifact") or {}
        mesh_validation = stored.get("raw_mesh_validation")
        if (
            not isinstance(mesh_validation, dict)
            or mesh_validation.get("schema_version") != MESH_VALIDATION_SCHEMA_VERSION
            or type(mesh_validation.get("valid")) is not bool
            or not isinstance(mesh_validation.get("reason_codes"), list)
        ):
            raise ProtocolError("stored mesh validation evidence is malformed")
        raw_log_evidence, raw_log_error = _mesh_log_crosscheck(
            stored.get("raw_mesh_info"),
            stored.get("volume_meshing_log_artifact") or {},
            artifact_store,
        )
        if canonical_json_bytes(raw_log_evidence) != canonical_json_bytes(
            stored.get("raw_volume_meshing_log_evidence")
        ):
            raise ProtocolError("stored pyHyp log evidence differs from its raw artifact")
        if stored.get("volume_meshing_log_evidence_error") != raw_log_error:
            raise ProtocolError("stored pyHyp log cross-check outcome changed")
        rebuilt = classify_mesh_attempt(
            stored.get("execution") or {},
            stored.get("raw_mesh_info") if raw_log_error is None else None,
            protocol,
            volume_grid_valid=mesh_validation["valid"] is True,
        )
        for key in ("outcome", "reason_codes", "min_volume", "min_quality"):
            if rebuilt.get(key) != stored.get(key):
                raise ProtocolError(f"stored mesh attempt classification drifted at {key}")
        rebuilt_mesh_attempts.append(rebuilt)
        if verify_artifacts:
            verify_artifact(
                (stored.get("execution") or {}).get("log") or {}, artifact_store
            )
            for record in (stored.get("generated_artifacts") or {}).values():
                verify_artifact(record, artifact_store)
            for name in (
                "mesh_info_artifact", "volume_meshing_log_artifact",
                "mesh_validation_artifact",
                "volume_grid_artifact", "input_after_mesh_artifact",
            ):
                verify_artifact(stored.get(name) or {}, artifact_store)
            mesh_info_record = stored.get("mesh_info_artifact") or {}
            embedded_mesh_info = stored.get("raw_mesh_info")
            if mesh_info_record.get("present") is True:
                if canonical_json_bytes(
                    _read_json_artifact(mesh_info_record, artifact_store)
                ) != canonical_json_bytes(
                    embedded_mesh_info
                ):
                    raise ProtocolError("embedded mesh_info is not the hashed mesh_info artifact")
            elif embedded_mesh_info is not None:
                raise ProtocolError("embedded mesh_info exists without an artifact")
            validation_record = stored.get("mesh_validation_artifact") or {}
            if canonical_json_bytes(
                _read_json_artifact(validation_record, artifact_store)
            ) != canonical_json_bytes(
                mesh_validation
            ):
                raise ProtocolError("embedded mesh validation is not its hashed artifact")
            if canonical_json_bytes(
                _read_json_artifact(
                    generated["input.before_mesh.json"], artifact_store
                )
            ) != canonical_json_bytes(raw_input_before_mesh):
                raise ProtocolError("embedded pre-mesh input is not its hashed artifact")
            input_after_record = stored.get("input_after_mesh_artifact") or {}
            if input_after_record.get("present") is True:
                if canonical_json_bytes(
                    _read_json_artifact(input_after_record, artifact_store)
                ) != canonical_json_bytes(raw_input_after_mesh):
                    raise ProtocolError("embedded post-mesh input is not its hashed artifact")
            elif raw_input_after_mesh is not None:
                raise ProtocolError("embedded post-mesh input exists without an artifact")
        validation_volume = (mesh_validation.get("volume_grid") or {}).get("artifact") or {}
        validation_input = (mesh_validation.get("post_mesh_input") or {}).get("artifact") or {}
        for label, nested, outer in (
            ("volume grid", validation_volume, volume),
            ("post-mesh input", validation_input, stored.get("input_after_mesh_artifact") or {}),
        ):
            if any(nested.get(key) != outer.get(key) for key in (
                "path", "present", "size_bytes", "sha256",
            )):
                raise ProtocolError(f"mesh validation {label} is not bound to its artifact record")
    rebuilt_mesh = _select_final_mesh(rebuilt_mesh_attempts)
    for key in ("outcome", "reason_codes", "min_volume", "min_quality"):
        if rebuilt_mesh.get(key) != mesh.get(key):
            raise ProtocolError(f"stored final mesh classification drifted at {key}")

    condition_windows: dict[str, tuple[datetime, datetime]] = {}
    usable_mesh_cases = [
        Path(str(row.get("case_directory"))).resolve()
        for row in mesh_attempts
        if row.get("outcome") in {"valid", "marginal"}
    ]
    usable_mesh_case = usable_mesh_cases[-1] if usable_mesh_cases else None
    for spec, condition in zip(protocol.conditions, conditions):
        attempts = condition.get("attempts")
        if condition.get("outcome_class") == "not_run":
            if attempts != [] or mesh.get("outcome") not in {"unmeshable", "inconclusive"}:
                raise ProtocolError("condition may be not_run only after terminal mesh evidence")
            expected_reason = (
                "not_run_unmeshable"
                if mesh.get("outcome") == "unmeshable"
                else "not_run_mesh_inconclusive"
            )
            if condition != _not_run_condition(spec, expected_reason):
                raise ProtocolError(
                    f"stored {spec['condition_id']} not-run condition differs from its exact contract"
                )
            continue
        if not isinstance(attempts, list) or not attempts:
            raise ProtocolError("executed condition must preserve every attempt")
        if len(attempts) > max_attempts:
            raise ProtocolError(f"{spec['condition_id']} exceeds the frozen retry count")
        rebuilt_attempts = []
        previous_finished = None
        condition_started: datetime | None = None
        for attempt_index, stored in enumerate(attempts):
            if stored.get("attempt_index") != attempt_index:
                raise ProtocolError(f"{spec['condition_id']} attempt indices are not sequential")
            if attempt_index and attempts[attempt_index - 1].get("outcome_class") != "inconclusive":
                raise ProtocolError(f"{spec['condition_id']} retry followed a conclusive attempt")
            attempt_directory = Path(str(stored.get("attempt_directory")))
            resolved_attempt = attempt_directory.resolve()
            if (
                usable_mesh_case is None
                or resolved_attempt.parent != usable_mesh_case
                or attempt_directory.name != f"{spec['condition_id']}_attempt_{attempt_index:02d}"
                or resolved_attempt in seen_attempt_directories
            ):
                raise ProtocolError(
                    f"{spec['condition_id']} attempt directory does not match the fresh-attempt layout"
                )
            seen_attempt_directories.add(resolved_attempt)
            _validate_process_contract(
                stored.get("execution") or {},
                mpi_python_command("run-adflow.py", protocol, command_runtime),
                attempt_directory,
                int(protocol.payload["execution"]["solve_timeout_seconds_per_attempt"]),
                protocol,
                command_runtime,
            )
            started = _parse_utc_timestamp((stored.get("execution") or {}).get("started_at"), "solve started_at")
            finished = _parse_utc_timestamp((stored.get("execution") or {}).get("finished_at"), "solve finished_at")
            if condition_started is None:
                condition_started = started
            if previous_finished is not None and started < previous_finished:
                raise ProtocolError(f"{spec['condition_id']} retries overlap or are out of order")
            previous_finished = finished
            artifacts = stored.get("artifacts") or {}
            if set(artifacts) != {"conditions", "solver_script", "output", "adflow_log"}:
                raise ProtocolError(f"{spec['condition_id']} artifact set is incomplete")
            _validate_artifact_record(
                (stored.get("execution") or {}).get("log") or {},
                attempt_directory / "grade_adflow.log",
                required=True,
            )
            _validate_artifact_record(
                artifacts["conditions"], attempt_directory / "conds.json", required=True
            )
            _validate_artifact_record(
                artifacts["solver_script"],
                attempt_directory / "run-adflow.py",
                required=True,
                expected_sha256=protocol.payload["generated_case_file_sha256"]["run-adflow.py"],
            )
            _validate_artifact_record(
                artifacts["output"], attempt_directory / "output.json", required=False
            )
            _validate_artifact_record(
                artifacts["adflow_log"], attempt_directory / "adflow.log", required=False
            )
            raw_log_evidence, raw_log_error = _adflow_log_crosscheck(
                stored.get("raw_output"), artifacts["adflow_log"], artifact_store
            )
            if canonical_json_bytes(raw_log_evidence) != canonical_json_bytes(
                stored.get("raw_adflow_log_evidence")
            ):
                raise ProtocolError(
                    f"stored {spec['condition_id']} ADflow log evidence differs from its artifact"
                )
            if stored.get("adflow_log_evidence_error") != raw_log_error:
                raise ProtocolError(
                    f"stored {spec['condition_id']} ADflow log cross-check outcome changed"
                )
            rebuilt = classify_condition_attempt(
                stored.get("execution") or {},
                stored.get("raw_output") if raw_log_error is None else None,
                spec,
                protocol.seed_alpha0,
                protocol,
            )
            for key in ("outcome_class", "outcome", "reason_codes", "measurements", "gate_decisions"):
                if rebuilt.get(key) != stored.get(key):
                    raise ProtocolError(
                        f"stored {spec['condition_id']} attempt classification drifted at {key}"
                    )
            rebuilt_attempts.append(rebuilt)
            if verify_artifacts:
                verify_artifact(
                    (stored.get("execution") or {}).get("log") or {}, artifact_store
                )
                for record in (stored.get("artifacts") or {}).values():
                    verify_artifact(record, artifact_store)
                artifacts = stored.get("artifacts") or {}
                output_record = artifacts.get("output") or {}
                embedded_output = stored.get("raw_output")
                if output_record.get("present") is True:
                    artifact_output = _read_json_artifact(output_record, artifact_store)
                    if canonical_json_bytes(artifact_output) != canonical_json_bytes(embedded_output):
                        raise ProtocolError(
                            f"embedded {spec['condition_id']} output is not the hashed output artifact"
                        )
                elif embedded_output is not None:
                    raise ProtocolError(f"embedded {spec['condition_id']} output exists without an artifact")
                conditions_payload = _read_json_artifact(
                    artifacts["conditions"], artifact_store
                )
                raw_working = (embedded_output or {}).get("working_conditions")
                if embedded_output is not None and canonical_json_bytes(
                    conditions_payload
                ) != canonical_json_bytes(raw_working):
                    raise ProtocolError(f"{spec['condition_id']} conds.json is not bound to raw output")
        if condition_started is None or previous_finished is None:
            raise ProtocolError(f"{spec['condition_id']} has no execution time window")
        condition_windows[str(spec["condition_id"])] = (condition_started, previous_finished)
        rebuilt_condition = finalize_condition(spec, rebuilt_attempts)
        for key in ("condition_id", "mach", "cl_target", "outcome_class", "outcome", "reason_codes", "measurements"):
            if rebuilt_condition.get(key) != condition.get(key):
                raise ProtocolError(f"stored final condition classification drifted at {key}")

    # `previous_finished` above is condition-local, so derive the mesh boundary
    # directly from the last recorded mesh attempt.
    mesh_finished = _parse_utc_timestamp(
        (mesh_attempts[-1].get("execution") or {}).get("finished_at"), "last mesh finished_at"
    )
    prior_finished = mesh_finished
    for condition_index in protocol.payload["condition_execution_order"]:
        condition_id = str(protocol.conditions[condition_index]["condition_id"])
        if condition_id not in condition_windows:
            continue
        current_started, current_finished = condition_windows[condition_id]
        if current_started < prior_finished:
            raise ProtocolError("condition executions overlap or violate the frozen serial order")
        prior_finished = current_finished

    result_started = _parse_utc_timestamp((result.get("runtime") or {}).get("started_at"), "result started_at")
    result_finished = _parse_utc_timestamp((result.get("runtime") or {}).get("finished_at"), "result finished_at")
    if first_process_started is None or result_finished < result_started:
        raise ProtocolError("result runtime window does not contain every process execution")
    if not test_only_result and (
        result_started > first_process_started or result_finished < prior_finished
    ):
        raise ProtocolError("result runtime window does not contain every process execution")

    recomputed = aggregate_wing(mesh, conditions, protocol)
    if recomputed != result.get("aggregate"):
        raise ProtocolError("result aggregate is inconsistent with raw condition outcomes")
    execution_authorization = result.get("execution_authorization") or {}
    test_only_fake_runner = execution_authorization.get("test_only_fake_runner") is True
    publication_preflight_evidence = result.get("campaign_publication_preflight")
    if solver_environment.get("status") == "complete":
        observed_runtime_sha = validate_runtime_fingerprint(
            solver_environment, protocol, official=result.get("official") is True
        )
        if runtime.get("solver_environment_sha256") != observed_runtime_sha:
            raise ProtocolError("stored runtime fingerprint hash is inconsistent")
    if test_only_fake_runner:
        if result.get("official") is True or (result.get("release_lock") is not None):
            raise ProtocolError("fake-runner results cannot carry official or release claims")
        if solver_environment.get("status") != "not_probed_test_only":
            raise ProtocolError("fake-runner result carries non-test runtime evidence")
        if publication_preflight_evidence != {"test_only_fake_runner": True}:
            raise ProtocolError("fake-runner result carries a live publication preflight claim")
    else:
        authorization_sha = execution_authorization.get("canonical_sha256")
        authorization_snapshot = execution_authorization.get("snapshot")
        attestation_sha = runtime.get("runtime_attestation_sha256")
        attestation = runtime.get("runtime_attestation") or {}
        if (
            not isinstance(authorization_sha, str)
            or len(authorization_sha) != 64
            or not isinstance(attestation_sha, str)
            or len(attestation_sha) != 64
            or execution_authorization.get("runtime_attestation_sha256") != attestation_sha
            or not isinstance(authorization_snapshot, dict)
            or canonical_payload_sha256(authorization_snapshot) != authorization_sha
        ):
            raise ProtocolError("result execution authorization and attestation are not bound")
        authorization_wings = authorization_snapshot.get("wing_ids")
        if (
            not isinstance(authorization_wings, list)
            or not all(isinstance(value, str) for value in authorization_wings)
            or result.get("wing_id") not in authorization_wings
            or len(set(authorization_wings)) != len(authorization_wings)
        ):
            raise ProtocolError("result is outside its embedded authorization wing scope")
        _validate_execution_authorization_core(
            authorization_snapshot,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            release_lock_sha256=(result.get("release_lock") or {}).get("canonical_sha256"),
            mode="official" if result.get("official") is True else "diagnostic",
            run_id=str(result.get("run_id")),
            wing_ids=authorization_wings,
            now=result_started,
        )
        publication_binding = authorization_snapshot.get("campaign_publication")
        publication_keys = {
            "schema_version", "campaign_publication_id", "publication_bucket",
            "publication_contract_schema_version", "publication_contract_sha256",
            "start_sequence_index", "previous_entry_sha256",
            "previous_entry_object",
        }
        if (
            not isinstance(publication_binding, Mapping)
            or set(publication_binding) != publication_keys
            or publication_binding.get("schema_version")
            != AUTHORIZATION_BINDING_SCHEMA_VERSION
            or publication_binding.get("publication_contract_schema_version")
            != PUBLICATION_CONTRACT_SCHEMA_VERSION
            or publication_binding.get("publication_contract_sha256")
            != PUBLICATION_CONTRACT_SHA256
            or not isinstance(publication_binding.get("campaign_publication_id"), str)
            or not publication_binding["campaign_publication_id"]
            or not isinstance(publication_binding.get("publication_bucket"), str)
            or not publication_binding["publication_bucket"]
            or isinstance(publication_binding.get("start_sequence_index"), bool)
            or not isinstance(publication_binding.get("start_sequence_index"), int)
            or publication_binding["start_sequence_index"] < 0
        ):
            raise ProtocolError(
                "result embeds an invalid campaign publication authorization"
            )
        if (
            not isinstance(publication_preflight_evidence, Mapping)
            or set(publication_preflight_evidence)
            != {"canonical_sha256", "snapshot"}
            or not isinstance(
                publication_preflight_evidence.get("canonical_sha256"), str
            )
            or not isinstance(publication_preflight_evidence.get("snapshot"), Mapping)
            or canonical_payload_sha256(publication_preflight_evidence["snapshot"])
            != publication_preflight_evidence["canonical_sha256"]
        ):
            raise ProtocolError(
                "result campaign publication preflight is not cryptographically bound"
            )
        embedded_preflight = publication_preflight_evidence["snapshot"]
        embedded_ordered_wings = embedded_preflight.get("ordered_wing_ids")
        authorization_start = publication_binding["start_sequence_index"]
        if (
            not isinstance(embedded_ordered_wings, list)
            or not all(isinstance(value, str) for value in embedded_ordered_wings)
            or len(set(embedded_ordered_wings)) != len(embedded_ordered_wings)
            or authorization_start >= len(embedded_ordered_wings)
            or embedded_ordered_wings[authorization_start:] != authorization_wings
            or result.get("wing_id") not in embedded_ordered_wings
        ):
            raise ProtocolError(
                "result publication preflight roster does not match authorization"
            )
        _validate_publication_preflight_record(
            embedded_preflight,
            protocol_sha256_value=protocol.sha256,
            manifest_sha256=manifest_sha256,
            authorization_sha256=str(authorization_sha),
            ordered_manifest_wing_ids=embedded_ordered_wings,
            publication_binding=publication_binding,
            reference_time=result_started,
            expected_sequence_index=embedded_ordered_wings.index(
                str(result.get("wing_id"))
            ),
        )
        if (
            not isinstance(attestation, dict)
            or attestation.get("schema_version") != "wingbench-cfd-runtime-attestation-v2"
            or canonical_payload_sha256(attestation) != attestation_sha
        ):
            raise ProtocolError("result embeds an invalid runtime attestation")
        expected_environment = protocol.payload["environment"]
        if (
            attestation.get("protocol_sha256") != protocol.sha256
            or attestation.get("attestation_status") != "verified_by_host"
            or attestation.get("final_child_image_manifest_digest")
            != expected_environment.get("final_child_image_manifest_digest")
            or attestation.get("final_child_image_linux_amd64_digest")
            != expected_environment.get("final_child_image_linux_amd64_digest")
            or runtime.get("final_child_image_linux_amd64_digest_observed")
            != expected_environment.get("final_child_image_linux_amd64_digest")
            or attestation.get("runtime_fingerprint_sha256")
            != runtime.get("solver_environment_sha256")
            or attestation.get("runtime_probe_executed_inside_final_child") is not True
            or attestation.get("mpi_smoke_test_passed") is not True
            or attestation.get("mpi_ranks_verified")
            != protocol.payload["execution"]["mpi_ranks"]
            or attestation.get("host_image_inspection_passed") is not True
            or attestation.get("mounted_read_only") is not True
            or attestation.get("architecture") != "x86_64"
            or not isinstance(attestation.get("container_id"), str)
            or not attestation.get("container_id")
            or not isinstance(attestation.get("host_image_id"), str)
            or not attestation.get("host_image_id")
            or attestation.get("exclusive_grader_scheduling_verified") is not True
            or attestation.get("host_backed_lock_mount_verified") is not True
            or attestation.get("host_lock_path")
            != protocol.payload["execution"]["host_lock_path"]
        ):
            raise ProtocolError("result runtime identity differs from its protocol or attestation")
        if solver_environment.get("status") != "complete":
            raise ProtocolError("an executed result requires a complete solver runtime fingerprint")
        if result.get("official") is False and result.get("release_lock") is not None:
            raise ProtocolError("diagnostic result cannot carry an official release lock")
    if result.get("official") is True:
        protocol.require_mode("official")
        if (result.get("benchmark_manifest") or {}).get("official_benchmark") is not True:
            raise ProtocolError("official result requires an official benchmark manifest")
        release = result.get("release_lock") or {}
        release_snapshot = release.get("snapshot")
        if (
            release.get("benchmark_release_id") != protocol.payload.get("benchmark_release_id")
            or not isinstance(release.get("canonical_sha256"), str)
            or len(release["canonical_sha256"]) != 64
            or release.get("qualification_record_sha256")
            != (protocol.payload.get("promotion") or {}).get("qualification_record_sha256")
            or not isinstance(release_snapshot, dict)
        ):
            raise ProtocolError("official result requires its exact frozen release lock")
        _validate_release_context_payload(
            release_snapshot,
            protocol=protocol,
            manifest_sha256=manifest_sha256,
            expected_sha256=release["canonical_sha256"],
        )
        authorization = execution_authorization
        if (
            not isinstance(authorization.get("canonical_sha256"), str)
            or len(authorization["canonical_sha256"]) != 64
        ):
            raise ProtocolError("official result requires a bound execution authorization")
        if (runtime.get("solver_environment") or {}).get("status") != "complete":
            raise ProtocolError("official result requires a complete solver runtime fingerprint")
        expected_digest = protocol.payload["environment"]["final_child_image_linux_amd64_digest"]
        if runtime.get("final_child_image_linux_amd64_digest_observed") != expected_digest:
            raise ProtocolError("official result container digest does not match the protocol")
        attestation_sha = runtime.get("runtime_attestation_sha256")
        if (
            not isinstance(attestation_sha, str)
            or len(attestation_sha) != 64
            or authorization.get("runtime_attestation_sha256") != attestation_sha
        ):
            raise ProtocolError("official result runtime attestation is not authorization-bound")


def qualification_control_as_wing(control: Mapping[str, Any]) -> dict[str, Any]:
    """Canonical control adapter shared by execution and qualification review."""
    return {
        "wing_id": control["control_id"],
        "geom_38d": list(control["geom_38d"]),
        "geometry_sha256": control["geometry_sha256"],
        "provenance": {
            "qualification_control_id": control["control_id"],
            "provenance_sha256": control.get("provenance_sha256"),
        },
        "canonical_source": json.loads(
            canonical_json_bytes(control.get("canonical_source"))
        ),
    }


def _manifest_wings_by_id(manifest: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    if manifest.get("schema_version") == "benchmark-redevelopment-manifest-v1":
        return {str(row["wing_id"]): dict(row) for row in manifest["wings"]}
    result: dict[str, dict[str, Any]] = {}
    for control in manifest["controls"]:
        row = qualification_control_as_wing(control)
        result[str(row["wing_id"])] = row
    return result


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--protocol", default=str(DEFAULT_PROTOCOL_PATH))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--results", required=True)
    parser.add_argument(
        "--publication-journal",
        required=True,
        help=(
            "append-only local mirror of synchronously published S3 terminal entries"
        ),
    )
    parser.add_argument(
        "--aws-region",
        default="us-east-1",
        help="S3 publication Region (default: us-east-1)",
    )
    parser.add_argument(
        "--aws-writer-profile",
        default=None,
        help="optional local AWS profile; omit on EC2 to use the instance role",
    )
    parser.add_argument(
        "--aws-preflight-profile",
        default=None,
        help=(
            "optional AWS reader profile for live publication preflight; "
            "defaults to --aws-writer-profile, then the instance role"
        ),
    )
    parser.add_argument("--mode", choices=("diagnostic", "official"), default="diagnostic")
    parser.add_argument("--wing-id", action="append", required=True,
                        help="exact manifest wing_id; repeat explicitly for multiple wings")
    parser.add_argument("--authorization", required=True,
                        help="approved execution authorization bound to this exact run")
    parser.add_argument("--runtime-attestation", required=True,
                        help="read-only host image/container attestation bound by authorization")
    parser.add_argument("--release-lock", default=None,
                        help="required in official mode; immutable protocol/manifest/qualification binding")
    parser.add_argument("--qualification-record", default=None,
                        help="required in official mode; actual passing qualification envelope")
    args = parser.parse_args(argv)
    try:
        _safe_path_component(args.run_id, "run_id")
        protocol = load_protocol(args.protocol)
        protocol.require_mode(args.mode)
        protocol.reject_parent_overrides()
        manifest, manifest_hash = load_manifest(args.manifest, protocol)
        by_id = _manifest_wings_by_id(manifest)
        if manifest.get("schema_version") == "cfd-protocol-qualification-manifest-v1":
            ordered_manifest_wing_ids = [
                str(control["control_id"])
                for control in manifest.get("controls") or []
            ]
            publication_recovery_allowed = False
        else:
            ordered_manifest_wing_ids = [
                str(wing["wing_id"]) for wing in manifest.get("wings") or []
            ]
            publication_recovery_allowed = True
        if manifest.get("execution_readiness") != {
            "ready": True,
            "status": "ready",
        }:
            raise ProtocolError(
                "manifest execution is blocked until publication storage is pinned"
            )
        publication_contract = validate_manifest_publication_contract(
            manifest,
            ordered_manifest_wing_ids=ordered_manifest_wing_ids,
            expected_recovery_allowed=publication_recovery_allowed,
        )
        missing = [wing_id for wing_id in args.wing_id if wing_id not in by_id]
        if missing:
            raise ProtocolError("unknown manifest wing_id(s): " + ", ".join(missing))
        release_lock = None
        release_lock_hash = None
        if args.mode == "official":
            if not args.release_lock or not args.qualification_record:
                raise ProtocolError("official mode requires --qualification-record and --release-lock")
            if not (
                manifest.get("manifest_status") == "frozen"
                and manifest.get("official_benchmark") is True
                and manifest.get("execution_authorized") is False
                and (manifest.get("protocol_reference") or {}).get("qualified") is True
                and (manifest.get("protocol_reference") or {}).get("official") is True
            ):
                raise ProtocolError(
                    "official mode requires the frozen qualified manifest; "
                    "billable execution must remain separately authorized"
                )
            qualification = load_qualification_envelope_for_release(
                args.qualification_record, protocol
            )
            release_lock, release_lock_hash = load_release_lock(
                args.release_lock, protocol, manifest, manifest_hash, qualification
            )
        elif args.release_lock or args.qualification_record:
            raise ProtocolError(
                "diagnostic mode must not claim an official qualification record or release lock"
            )
        authorization, authorization_hash = load_execution_authorization(
            args.authorization,
            protocol=protocol,
            manifest_sha256=manifest_hash,
            release_lock_sha256=release_lock_hash,
            mode=args.mode,
            run_id=args.run_id,
            wing_ids=args.wing_id,
            ordered_manifest_wing_ids=ordered_manifest_wing_ids,
            expected_campaign_publication_id=publication_contract[
                "campaign_publication_id"
            ],
            expected_publication_bucket=publication_contract[
                "publication_bucket"
            ],
            allow_publication_recovery=publication_recovery_allowed,
        )
        publication_binding = authorization["campaign_publication"]
        runtime_attestation, runtime_attestation_hash = load_runtime_attestation(
            args.runtime_attestation,
            protocol,
            authorization["runtime_attestation_sha256"],
        )
        with exclusive_worker_lock(Path(protocol.payload["execution"]["host_lock_path"])):
            preflight_runner = SubprocessAwsCliRunner(
                profile=(
                    args.aws_preflight_profile
                    if args.aws_preflight_profile is not None
                    else args.aws_writer_profile
                ),
                region=args.aws_region,
            )
            authorized_start = publication_binding["start_sequence_index"]
            initial_preflight_observation = preflight_campaign_publication(
                ordered_manifest_wing_ids=ordered_manifest_wing_ids,
                campaign_publication_id=publication_contract[
                    "campaign_publication_id"
                ],
                publication_bucket=publication_contract["publication_bucket"],
                start_sequence_index=authorized_start,
                previous_entry_sha256=publication_binding[
                    "previous_entry_sha256"
                ],
                previous_entry_object=publication_binding[
                    "previous_entry_object"
                ],
                reader_runner=preflight_runner,
            )
            initial_preflight = _publication_preflight_record(
                initial_preflight_observation,
                protocol_sha256_value=protocol.sha256,
                manifest_sha256=manifest_hash,
                authorization_sha256=authorization_hash,
                ordered_manifest_wing_ids=ordered_manifest_wing_ids,
            )
            initial_preflight_sha256 = canonical_payload_sha256(initial_preflight)
            result_root = Path(args.results).resolve() / args.run_id
            result_root.mkdir(parents=True, exist_ok=True)
            runtime_versions = probe_runtime(
                protocol.child_environment(),
                result_root / "runtime_fingerprint.json",
            )
            validate_runtime_fingerprint(
                runtime_versions,
                protocol,
                official=args.mode == "official",
                active_runtime=True,
            )
            if (
                runtime_attestation.get("runtime_fingerprint_sha256")
                != canonical_payload_sha256(runtime_versions)
            ):
                raise ProtocolError(
                    "runtime probe fingerprint is not the one bound by the host attestation"
                )
            previous_entry_sha256 = publication_binding["previous_entry_sha256"]
            previous_entry_object = publication_binding["previous_entry_object"]
            writer_runner = SubprocessAwsCliRunner(
                profile=args.aws_writer_profile,
                region=args.aws_region,
            )
            for wing_id in args.wing_id:
                sequence_index = ordered_manifest_wing_ids.index(wing_id)
                if sequence_index == authorized_start:
                    publication_preflight = initial_preflight
                    publication_preflight_sha256 = initial_preflight_sha256
                else:
                    observation = preflight_campaign_publication(
                        ordered_manifest_wing_ids=ordered_manifest_wing_ids,
                        campaign_publication_id=publication_contract[
                            "campaign_publication_id"
                        ],
                        publication_bucket=publication_contract[
                            "publication_bucket"
                        ],
                        start_sequence_index=sequence_index,
                        previous_entry_sha256=previous_entry_sha256,
                        previous_entry_object=previous_entry_object,
                        reader_runner=preflight_runner,
                    )
                    publication_preflight = _publication_preflight_record(
                        observation,
                        protocol_sha256_value=protocol.sha256,
                        manifest_sha256=manifest_hash,
                        authorization_sha256=authorization_hash,
                        ordered_manifest_wing_ids=ordered_manifest_wing_ids,
                    )
                    publication_preflight_sha256 = canonical_payload_sha256(
                        publication_preflight
                    )
                execution_context = _make_validated_execution_context(
                    protocol=protocol,
                    manifest_sha256=manifest_hash,
                    mode=args.mode,
                    run_id=args.run_id,
                    wing_ids=args.wing_id,
                    ordered_manifest_wing_ids=ordered_manifest_wing_ids,
                    expected_campaign_publication_id=publication_contract[
                        "campaign_publication_id"
                    ],
                    expected_publication_bucket=publication_contract[
                        "publication_bucket"
                    ],
                    allow_publication_recovery=publication_recovery_allowed,
                    publication_sequence_index=sequence_index,
                    authorization=authorization,
                    authorization_sha256=authorization_hash,
                    publication_preflight=publication_preflight,
                    publication_preflight_sha256=publication_preflight_sha256,
                    runtime_attestation=runtime_attestation,
                    runtime_attestation_sha256=runtime_attestation_hash,
                    runtime_versions=runtime_versions,
                    release_lock=release_lock,
                    release_lock_sha256=release_lock_hash,
                )
                output = result_root / f"{wing_id}.json"
                if output.exists():
                    raise FileExistsError(f"result already exists; choose a new run-id: {output}")
                result = grade_wing(
                    manifest_path=Path(args.manifest),
                    manifest=manifest,
                    manifest_sha256=manifest_hash,
                    wing=by_id[wing_id],
                    protocol=protocol,
                    mode=args.mode,
                    run_id=args.run_id,
                    work_root=Path(args.workdir).resolve(),
                    runtime_versions=runtime_versions,
                    execution_context=execution_context,
                )
                atomic_write_json(output, result)
                publication_receipt = publish_terminal_result(
                    result_json_path=output,
                    sequence_index=sequence_index,
                    ordered_manifest_wing_ids=ordered_manifest_wing_ids,
                    previous_entry_sha256=previous_entry_sha256,
                    previous_entry_object=previous_entry_object,
                    campaign_publication_id=publication_contract[
                        "campaign_publication_id"
                    ],
                    publication_bucket=publication_contract[
                        "publication_bucket"
                    ],
                    allow_recovery=publication_recovery_allowed,
                    journal_root=Path(args.publication_journal).resolve(),
                    writer_runner=writer_runner,
                )
                previous_entry_sha256 = publication_receipt["entry_sha256"]
                previous_entry_object = publication_receipt["object"]
                print(
                    f"[grade] {wing_id}: {result['aggregate']['admissibility']} "
                    f"published as {previous_entry_sha256}",
                    flush=True,
                )
    except (OSError, ValueError, ProtocolError) as exc:
        print(f"[grade] ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
