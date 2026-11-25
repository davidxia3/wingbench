"""Candidate WingBench V2 anytime protocol and append-only episode trace.

This module is Phase 2 protocol engineering.  It contains no Slurm, CFD,
network, paid-provider, or sealed-data execution path.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from scripts.cfd.geometry_hash import (
    GEOMETRY_HASH_ALGORITHM,
    geometry_sha256,
    validated_geometry,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROTOCOL = (
    ROOT / "configs/benchmark/wingbench_v2_candidate_protocol_v1.json"
)
TRACE_SCHEMA = "wingbench-v2-episode-trace-v1"
PROTOCOL_SCHEMA = "wingbench-v2-candidate-protocol-v1"
ZERO_SHA256 = "0" * 64
EVENT_TYPES = (
    "episode_reset",
    "cheap_query_requested",
    "cheap_query_observed",
    "online_cfd_requested",
    "online_cfd_dispatched",
    "online_cfd_observed",
    "infrastructure_loss_recorded",
    "checkpoint_nominated",
    "checkpoint_grade_bound",
    "terminal_nominated",
    "terminal_grade_bound",
    "episode_stopped",
)
BUDGET_FIELDS = (
    "cheap_queries_used",
    "online_cfd_queries_used",
    "online_cfd_in_flight",
    "fresh_checkpoint_grades_used",
    "terminal_grades_used",
)
RESOURCE_FIELDS = (
    "evaluator_cpu_seconds",
    "optimizer_cpu_seconds",
    "optimizer_gpu_seconds",
    "scheduler_wait_seconds",
    "administrative_wall_seconds",
    "storage_bytes",
)
EVENT_FIELDS = {
    "schema_version",
    "episode_id",
    "event_index",
    "event_type",
    "event_id",
    "previous_event_sha256",
    "event_sha256",
    "benchmark_identity",
    "protocol_identity",
    "evaluator_identity",
    "optimizer_identity",
    "instance_id",
    "matched_seed",
    "geometry_identity",
    "budget_before",
    "budget_after",
    "observation_visibility",
    "artifact_bindings",
    "resource_accounting",
    "reason_codes",
    "event_payload",
}
VISIBILITY_VALUES = {
    "none",
    "optimizer_visible",
    "hidden_not_returned",
    "administrative_only",
}


class V2ProtocolError(ValueError):
    """The candidate protocol or an episode action violates Phase 2 rules."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise V2ProtocolError(message)


def _sha256_text(value: Any, label: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        f"{label} must be a lowercase SHA-256",
    )
    return value


def _load_object(path: Path) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), f"expected a JSON object: {path}")
    return payload


@dataclass(frozen=True)
class V2Protocol:
    payload: dict[str, Any]
    path: Path
    file_sha256: str

    @property
    def protocol_id(self) -> str:
        return str(self.payload["protocol_id"])

    @property
    def benchmark_id(self) -> str:
        return str(self.payload["benchmark_id"])

    @property
    def evaluator_id(self) -> str:
        return str(self.payload["evaluator_id"])

    def profile(self, name: str) -> dict[str, Any]:
        if name == "main64":
            track = self.payload["tracks"]["anytime_main"]
            return {
                "name": name,
                "maximum_online_cfd_queries": 64,
                "maximum_cheap_queries": track["maximum_cheap_queries"],
                "maximum_cheap_batch_size": track["maximum_cheap_batch_size"],
                "active_checkpoints": list(track["checkpoints"]),
            }
        if name == "pilot32":
            pilot = self.payload["tracks"]["phase3_pilot_profile"]
            main = self.payload["tracks"]["anytime_main"]
            return {
                "name": name,
                "maximum_online_cfd_queries": pilot[
                    "maximum_online_cfd_queries"
                ],
                "maximum_cheap_queries": pilot["maximum_cheap_queries"],
                "maximum_cheap_batch_size": main["maximum_cheap_batch_size"],
                "active_checkpoints": list(pilot["active_checkpoints"]),
            }
        if name == "bridge4":
            bridge = self.payload["tracks"]["four_cfd_bridge"]
            main = self.payload["tracks"]["anytime_main"]
            return {
                "name": name,
                "maximum_online_cfd_queries": bridge[
                    "maximum_online_cfd_queries"
                ],
                "maximum_cheap_queries": bridge["maximum_cheap_queries"],
                "maximum_cheap_batch_size": main["maximum_cheap_batch_size"],
                "active_checkpoints": list(bridge["active_checkpoints"]),
            }
        raise V2ProtocolError(f"unknown V2 profile: {name}")


def validate_protocol(payload: Mapping[str, Any], root: Path = ROOT) -> None:
    protocol = dict(payload)
    _require(
        protocol.get("schema_version") == PROTOCOL_SCHEMA,
        "V2 protocol schema changed",
    )
    _require(
        protocol.get("status")
        == "phase2_candidate_frozen_for_phase3_development_pilot",
        "V2 candidate protocol status changed",
    )
    _require(protocol.get("normative_release") is False, "candidate became normative")
    _require(
        protocol.get("official_results_enabled") is False,
        "candidate enabled official results",
    )
    _require(
        protocol.get("sealed_execution_enabled") is False,
        "candidate enabled sealed execution",
    )
    lineage = protocol.get("lineage") or {}
    _require(
        lineage.get("v1_evaluator_identity_inherited") is False,
        "Candidate cannot inherit the V1 evaluator identity",
    )
    for name in ("v1_release", "phase1_terminal"):
        binding = lineage.get(name) or {}
        path = root / str(binding.get("path", ""))
        _require(path.is_file(), f"{name} lineage file is missing")
        _require(
            file_sha256(path) == binding.get("file_sha256"),
            f"{name} lineage binding changed",
        )
    design = protocol.get("design_action") or {}
    _require(
        design.get("geometry_hash_algorithm") == GEOMETRY_HASH_ALGORITHM,
        "geometry identity changed",
    )
    normalization = design.get("normalization_binding") or {}
    normalization_path = root / str(normalization.get("path", ""))
    _require(
        normalization_path.is_file()
        and file_sha256(normalization_path) == normalization.get("file_sha256"),
        "normalization binding changed",
    )
    _require(
        design.get("standardized_bounds_inclusive") == [-3.5, 3.5]
        and design.get("geometry_gate") is None
        and design.get("manifold_restriction") is None,
        "candidate physical domain changed",
    )
    objective = protocol.get("physical_objective") or {}
    _require(
        objective.get("cl_target") == 0.41
        and objective.get("mach_conditions") == [0.7853, 0.8195, 0.853]
        and objective.get("required_valid_conditions") == 3,
        "candidate physical objective changed",
    )
    tracks = protocol.get("tracks") or {}
    main = tracks.get("anytime_main") or {}
    pilot = tracks.get("phase3_pilot_profile") or {}
    bridge = tracks.get("four_cfd_bridge") or {}
    _require(
        main.get("maximum_online_cfd_queries") == 64
        and main.get("maximum_cheap_queries") == 4096
        and main.get("maximum_cheap_batch_size") == 16
        and main.get("checkpoints") == [0, 1, 2, 4, 8, 16, 32, 64]
        and main.get("one_unresolved_online_cfd_query_per_episode") is True
        and main.get("parallelism_across_episodes_only") is True,
        "main anytime contract changed",
    )
    _require(
        pilot.get("maximum_online_cfd_queries") == 32
        and pilot.get("active_checkpoints") == [0, 1, 2, 4, 8, 16, 32]
        and pilot.get("data_namespace") == "development"
        and pilot.get("official") is False
        and pilot.get("sealed") is False,
        "Phase 3 pilot profile changed",
    )
    _require(
        bridge.get("separate_track") is True
        and bridge.get("maximum_online_cfd_queries") == 4
        and bridge.get("active_checkpoints") == [0, 1, 2, 4]
        and bridge.get("long_horizon_prefix_is_equivalent") is False,
        "four-CFD bridge changed",
    )
    checkpoint = protocol.get("checkpoint_policy") or {}
    _require(
        checkpoint.get("previously_online_graded_geometry", {}).get(
            "provenance_label"
        )
        == "reused_online_visible"
        and checkpoint.get("previously_ungraded_geometry", {}).get(
            "provenance_label"
        )
        == "fresh_hidden"
        and checkpoint.get("previously_ungraded_geometry", {}).get(
            "returned_to_optimizer"
        )
        is False
        and checkpoint.get("provenance_classes_reported_separately") is True,
        "checkpoint provenance contract changed",
    )
    outcomes = protocol.get("outcomes") or {}
    _require(
        outcomes.get("weighted_scalar_grade") is False
        and outcomes.get("scientific_nondelivery_is_not_numeric_penalty") is True
        and outcomes.get("infrastructure_loss_is_censored_not_scientific_failure")
        is True,
        "outcome separation changed",
    )
    _require(
        protocol.get("budget_state_fields") == list(BUDGET_FIELDS),
        "budget field roster changed",
    )
    _require(
        protocol.get("resource_accounting_fields") == list(RESOURCE_FIELDS),
        "resource field roster changed",
    )
    boundaries = protocol.get("data_boundaries") or {}
    _require(
        boundaries.get("allowed_phase2_namespaces")
        == ["development", "retained_development_fixture"]
        and boundaries.get("cross_episode_observation_sharing") is False
        and boundaries.get("restart_state_must_bind_trace_head") is True
        and boundaries.get("cache_visibility_must_be_explicit") is True,
        "data-boundary contract changed",
    )
    next_gate = protocol.get("next_gate") or {}
    _require(
        next_gate.get("phase") == 3
        and next_gate.get("separate_authorization_required") is True
        and next_gate.get("phase3_started") is False,
        "candidate protocol entered Phase 3",
    )
    canonical_json_bytes(protocol)


def load_protocol(
    path: Path = DEFAULT_PROTOCOL, root: Path = ROOT
) -> V2Protocol:
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = root / resolved
    payload = _load_object(resolved)
    validate_protocol(payload, root)
    return V2Protocol(
        payload=payload,
        path=resolved,
        file_sha256=file_sha256(resolved),
    )


def _normalization(protocol: V2Protocol) -> tuple[tuple[float, ...], tuple[float, ...]]:
    binding = protocol.payload["design_action"]["normalization_binding"]
    payload = _load_object(ROOT / binding["path"])
    mean = tuple(float(value) for value in payload["mean_38d"])
    sd = tuple(float(value) for value in payload["sd_38d"])
    _require(
        len(mean) == len(sd) == 38 and all(value > 0.0 for value in sd),
        "normalization is malformed",
    )
    return mean, sd


def validate_geometry(
    values: Sequence[float], protocol: V2Protocol
) -> tuple[float, ...]:
    geometry = validated_geometry(values)
    mean, sd = _normalization(protocol)
    z_lower, z_upper = protocol.payload["design_action"][
        "standardized_bounds_inclusive"
    ]
    for index, (value, mu, scale) in enumerate(zip(geometry, mean, sd)):
        standardized = (value - mu) / scale
        _require(
            z_lower <= standardized <= z_upper,
            f"geometry[{index}] lies outside the V2 candidate domain",
        )
    return geometry


def geometry_identity(
    values: Sequence[float], protocol: V2Protocol
) -> dict[str, Any]:
    geometry = validate_geometry(values, protocol)
    return {
        "schema": "superwing-38d-v1",
        "hash_algorithm": GEOMETRY_HASH_ALGORITHM,
        "sha256": geometry_sha256(geometry),
    }


def zero_budget_state() -> dict[str, int]:
    return {field: 0 for field in BUDGET_FIELDS}


def resource_accounting(
    values: Mapping[str, float | int] | None = None,
) -> dict[str, float | int]:
    supplied = dict(values or {})
    _require(
        set(supplied).issubset(RESOURCE_FIELDS),
        "unknown resource-accounting field",
    )
    result: dict[str, float | int] = {}
    for field in RESOURCE_FIELDS:
        value = supplied.get(field, 0)
        _require(
            not isinstance(value, bool)
            and isinstance(value, (int, float))
            and math.isfinite(float(value))
            and float(value) >= 0.0,
            f"resource field {field} must be finite and nonnegative",
        )
        result[field] = int(value) if field == "storage_bytes" else float(value)
    return result


def validate_artifact_bindings(
    bindings: Sequence[Mapping[str, Any]] | None,
    protocol: V2Protocol,
) -> list[dict[str, str]]:
    allowed = set(
        protocol.payload["data_boundaries"]["allowed_phase2_namespaces"]
    )
    result: list[dict[str, str]] = []
    for raw in list(bindings or []):
        binding = dict(raw)
        _require(
            set(binding) == {"namespace", "artifact_id", "sha256"},
            "artifact binding fields changed",
        )
        _require(
            binding["namespace"] in allowed,
            "sealed or undeclared artifact namespace is forbidden",
        )
        _require(
            isinstance(binding["artifact_id"], str) and binding["artifact_id"],
            "artifact ID is missing",
        )
        _sha256_text(binding["sha256"], "artifact binding")
        result.append(binding)
    return result


def make_grade(
    *,
    geometry: Sequence[float],
    protocol: V2Protocol,
    delivery: bool,
    conditional_value: float | None,
    reason_codes: Sequence[str],
    artifact_id: str,
    data_namespace: str = "development",
) -> dict[str, Any]:
    identity = geometry_identity(geometry, protocol)
    _require(isinstance(delivery, bool), "grade delivery must be Boolean")
    if delivery:
        _require(
            conditional_value is not None
            and isinstance(conditional_value, (int, float))
            and not isinstance(conditional_value, bool)
            and math.isfinite(float(conditional_value))
            and 0.0 < float(conditional_value) <= 40.0,
            "delivered grade requires a finite eligible conditional value",
        )
        value: float | None = float(conditional_value)
    else:
        _require(
            conditional_value is None,
            "nondelivery cannot carry a numerical conditional value",
        )
        value = None
    core = {
        "schema_version": "wingbench-v2-development-grade-v1",
        "geometry_sha256": identity["sha256"],
        "delivery": delivery,
        "conditional_value": value,
        "reason_codes": list(reason_codes),
        "data_namespace": data_namespace,
        "artifact_id": artifact_id,
    }
    grade = dict(core)
    grade["result_sha256"] = canonical_sha256(core)
    return validate_grade(grade, protocol)


def validate_grade(
    raw: Mapping[str, Any], protocol: V2Protocol
) -> dict[str, Any]:
    grade = dict(raw)
    _require(
        set(grade)
        == {
            "schema_version",
            "geometry_sha256",
            "delivery",
            "conditional_value",
            "reason_codes",
            "data_namespace",
            "artifact_id",
            "result_sha256",
        },
        "development grade fields changed",
    )
    _require(
        grade.get("schema_version") == "wingbench-v2-development-grade-v1",
        "development grade schema changed",
    )
    _sha256_text(grade.get("geometry_sha256"), "grade geometry")
    _require(isinstance(grade.get("delivery"), bool), "grade delivery is invalid")
    value = grade.get("conditional_value")
    if grade["delivery"]:
        _require(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and 0.0 < float(value) <= 40.0,
            "delivered grade value is invalid",
        )
        grade["conditional_value"] = float(value)
    else:
        _require(value is None, "nondelivery grade must have null value")
    _require(
        isinstance(grade.get("reason_codes"), list)
        and all(
            isinstance(reason, str) and reason for reason in grade["reason_codes"]
        ),
        "grade reason codes are invalid",
    )
    _require(
        grade.get("data_namespace")
        in protocol.payload["data_boundaries"]["allowed_phase2_namespaces"],
        "sealed or undeclared grade namespace is forbidden",
    )
    _require(
        isinstance(grade.get("artifact_id"), str) and grade["artifact_id"],
        "grade artifact ID is missing",
    )
    claimed = _sha256_text(grade.get("result_sha256"), "grade result")
    core = dict(grade)
    core.pop("result_sha256")
    _require(canonical_sha256(core) == claimed, "grade result hash mismatch")
    return grade


class TraceWriter:
    """In-memory append-only trace writer with deterministic canonical hashes."""

    def __init__(
        self,
        *,
        protocol: V2Protocol,
        episode_id: str,
        optimizer_identity: Mapping[str, Any],
        instance_id: str,
        matched_seed: int,
    ) -> None:
        _require(isinstance(episode_id, str) and episode_id, "episode ID missing")
        _require(
            isinstance(instance_id, str) and instance_id,
            "instance ID missing",
        )
        _require(
            isinstance(matched_seed, int) and not isinstance(matched_seed, bool),
            "matched seed must be an integer",
        )
        optimizer = dict(optimizer_identity)
        _require(
            set(optimizer) == {"method_id", "implementation_sha256"},
            "optimizer identity fields changed",
        )
        _require(
            isinstance(optimizer["method_id"], str) and optimizer["method_id"],
            "optimizer method ID missing",
        )
        _sha256_text(
            optimizer["implementation_sha256"], "optimizer implementation"
        )
        self.protocol = protocol
        self.episode_id = episode_id
        self.optimizer_identity = optimizer
        self.instance_id = instance_id
        self.matched_seed = matched_seed
        self.events: list[dict[str, Any]] = []

    @property
    def head_sha256(self) -> str:
        return (
            self.events[-1]["event_sha256"] if self.events else ZERO_SHA256
        )

    def append(
        self,
        event_type: str,
        *,
        geometry: Mapping[str, Any] | None,
        budget_before: Mapping[str, int],
        budget_after: Mapping[str, int],
        visibility: str,
        artifacts: Sequence[Mapping[str, Any]] | None = None,
        resources: Mapping[str, float | int] | None = None,
        reason_codes: Sequence[str] = (),
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        _require(event_type in EVENT_TYPES, f"unknown trace event: {event_type}")
        _require(
            not self.events or self.events[-1]["event_type"] != "episode_stopped",
            "cannot append after episode stop",
        )
        before = {field: int(budget_before[field]) for field in BUDGET_FIELDS}
        after = {field: int(budget_after[field]) for field in BUDGET_FIELDS}
        _require(
            all(value >= 0 for value in before.values())
            and all(value >= 0 for value in after.values()),
            "budget values must be nonnegative",
        )
        _require(visibility in VISIBILITY_VALUES, "invalid observation visibility")
        _require(
            all(isinstance(reason, str) and reason for reason in reason_codes),
            "invalid reason code",
        )
        index = len(self.events)
        event_id = (
            f"{self.episode_id}:event:{index:06d}:{event_type}"
        )
        draft = {
            "schema_version": TRACE_SCHEMA,
            "episode_id": self.episode_id,
            "event_index": index,
            "event_type": event_type,
            "event_id": event_id,
            "previous_event_sha256": self.head_sha256,
            "benchmark_identity": {
                "benchmark_id": self.protocol.benchmark_id,
                "normative_release": False,
            },
            "protocol_identity": {
                "protocol_id": self.protocol.protocol_id,
                "protocol_file_sha256": self.protocol.file_sha256,
            },
            "evaluator_identity": {
                "evaluator_id": self.protocol.evaluator_id,
                "official": False,
                "v1_equivalent": False,
            },
            "optimizer_identity": deepcopy(self.optimizer_identity),
            "instance_id": self.instance_id,
            "matched_seed": self.matched_seed,
            "geometry_identity": deepcopy(dict(geometry)) if geometry else None,
            "budget_before": before,
            "budget_after": after,
            "observation_visibility": visibility,
            "artifact_bindings": validate_artifact_bindings(
                artifacts, self.protocol
            ),
            "resource_accounting": resource_accounting(resources),
            "reason_codes": list(reason_codes),
            "event_payload": deepcopy(dict(payload or {})),
        }
        event = dict(draft)
        event["event_sha256"] = canonical_sha256(draft)
        canonical_json_bytes(event)
        self.events.append(event)
        return deepcopy(event)

    def write(self, path: Path) -> None:
        verify_trace(self.events, self.protocol)
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        data = canonical_json_bytes(self.events) + b"\n"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        descriptor = os.open(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            temporary.replace(destination)
        finally:
            if temporary.exists():
                temporary.unlink()


def verify_trace(
    events: Sequence[Mapping[str, Any]], protocol: V2Protocol
) -> str:
    _require(isinstance(events, Sequence), "trace must be a sequence")
    previous = ZERO_SHA256
    episode_id: str | None = None
    identity_fields: dict[str, Any] | None = None
    for index, raw in enumerate(events):
        event = dict(raw)
        _require(set(event) == EVENT_FIELDS, "trace event fields changed")
        _require(event.get("schema_version") == TRACE_SCHEMA, "trace schema changed")
        _require(event.get("event_index") == index, "trace event index changed")
        _require(
            event.get("event_type") in EVENT_TYPES,
            "trace contains an unknown event type",
        )
        if episode_id is None:
            episode_id = event.get("episode_id")
        _require(event.get("episode_id") == episode_id, "cross-episode trace leak")
        _require(
            event.get("event_id")
            == f"{episode_id}:event:{index:06d}:{event['event_type']}",
            "trace event ID changed",
        )
        _require(
            event.get("previous_event_sha256") == previous,
            "trace predecessor hash changed",
        )
        claimed = _sha256_text(event.get("event_sha256"), "event")
        core = dict(event)
        core.pop("event_sha256")
        _require(canonical_sha256(core) == claimed, "trace event hash mismatch")
        current_identity = {
            key: event[key]
            for key in (
                "benchmark_identity",
                "protocol_identity",
                "evaluator_identity",
                "optimizer_identity",
                "instance_id",
                "matched_seed",
            )
        }
        if identity_fields is None:
            identity_fields = current_identity
        _require(
            current_identity == identity_fields,
            "trace identity changed within an episode",
        )
        _require(
            event["protocol_identity"]
            == {
                "protocol_id": protocol.protocol_id,
                "protocol_file_sha256": protocol.file_sha256,
            },
            "trace protocol identity mismatch",
        )
        _require(
            event["evaluator_identity"]["evaluator_id"]
            == protocol.evaluator_id
            and event["evaluator_identity"]["official"] is False
            and event["evaluator_identity"]["v1_equivalent"] is False,
            "trace evaluator identity mismatch",
        )
        _require(
            set(event["budget_before"]) == set(BUDGET_FIELDS)
            and set(event["budget_after"]) == set(BUDGET_FIELDS),
            "trace budget fields changed",
        )
        _require(
            event["observation_visibility"] in VISIBILITY_VALUES,
            "trace visibility changed",
        )
        validate_artifact_bindings(event["artifact_bindings"], protocol)
        resource_accounting(event["resource_accounting"])
        previous = claimed
    return previous


class EpisodeMachine:
    """Causal Phase 2 state machine for local, mock, or retained-fixture runs."""

    def __init__(
        self,
        *,
        protocol: V2Protocol,
        profile: str,
        episode_id: str,
        optimizer_identity: Mapping[str, Any],
        instance_id: str,
        matched_seed: int,
    ) -> None:
        self.protocol = protocol
        self.profile = protocol.profile(profile)
        self.writer = TraceWriter(
            protocol=protocol,
            episode_id=episode_id,
            optimizer_identity=optimizer_identity,
            instance_id=instance_id,
            matched_seed=matched_seed,
        )
        self.budget = zero_budget_state()
        self._reset = False
        self._stopped = False
        self._pending_geometry: dict[str, Any] | None = None
        self._online_grades: dict[str, dict[str, Any]] = {}
        self._checkpoint_grades: dict[int, dict[str, Any]] = {}
        self._checkpoint_geometries: dict[int, str] = {}
        self._terminal_grade: dict[str, Any] | None = None

    @property
    def events(self) -> list[dict[str, Any]]:
        return deepcopy(self.writer.events)

    @property
    def trace_head_sha256(self) -> str:
        return self.writer.head_sha256

    def _ready(self) -> None:
        _require(self._reset, "episode has not been reset")
        _require(not self._stopped, "episode has stopped")

    def reset(self) -> None:
        _require(not self.writer.events, "episode reset may occur only once")
        state = zero_budget_state()
        self.writer.append(
            "episode_reset",
            geometry=None,
            budget_before=state,
            budget_after=state,
            visibility="none",
            payload={"profile": self.profile["name"]},
        )
        self._reset = True

    def cheap_query(
        self,
        geometries: Sequence[Sequence[float]],
        observations: Sequence[Mapping[str, Any]],
        *,
        cache_hit: bool,
        resources: Mapping[str, float | int] | None = None,
    ) -> None:
        self._ready()
        rows = list(geometries)
        values = [dict(value) for value in observations]
        count = len(rows)
        _require(count > 0 and count == len(values), "cheap batch is malformed")
        _require(
            count <= self.profile["maximum_cheap_batch_size"],
            "cheap batch exceeds its cap",
        )
        _require(
            self.budget["cheap_queries_used"] + count
            <= self.profile["maximum_cheap_queries"],
            "cheap-query budget is exhausted",
        )
        identities = [geometry_identity(row, self.protocol) for row in rows]
        batch_identity = {
            "schema": "ordered-superwing-batch-v1",
            "count": count,
            "ordered_geometry_sha256": [
                identity["sha256"] for identity in identities
            ],
            "batch_sha256": canonical_sha256(
                [identity["sha256"] for identity in identities]
            ),
        }
        before = dict(self.budget)
        self.writer.append(
            "cheap_query_requested",
            geometry=batch_identity,
            budget_before=before,
            budget_after=before,
            visibility="none",
            payload={"query_count": count, "cache_status": "not_yet_observed"},
        )
        after = dict(before)
        after["cheap_queries_used"] += count
        observation_sha = canonical_sha256(values)
        self.writer.append(
            "cheap_query_observed",
            geometry=batch_identity,
            budget_before=before,
            budget_after=after,
            visibility="optimizer_visible",
            resources=resources,
            payload={
                "query_count": count,
                "observation_sha256": observation_sha,
                "cache_hit": bool(cache_hit),
                "observations": values,
            },
        )
        self.budget = after

    def request_online(self, geometry: Sequence[float]) -> str:
        self._ready()
        _require(
            self.budget["online_cfd_in_flight"] == 0,
            "one online CFD query is already unresolved",
        )
        _require(
            self.budget["online_cfd_queries_used"]
            < self.profile["maximum_online_cfd_queries"],
            "online CFD budget is exhausted",
        )
        identity = geometry_identity(geometry, self.protocol)
        before = dict(self.budget)
        self.writer.append(
            "online_cfd_requested",
            geometry=identity,
            budget_before=before,
            budget_after=before,
            visibility="none",
            payload={
                "query_index": before["online_cfd_queries_used"],
                "cache_status": (
                    "episode_local_hit"
                    if identity["sha256"] in self._online_grades
                    else "episode_local_miss"
                ),
            },
        )
        after = dict(before)
        after["online_cfd_in_flight"] = 1
        self.writer.append(
            "online_cfd_dispatched",
            geometry=identity,
            budget_before=before,
            budget_after=after,
            visibility="administrative_only",
            payload={
                "query_index": before["online_cfd_queries_used"],
                "scientific_retry_ordinal": 0,
            },
        )
        self.budget = after
        self._pending_geometry = identity
        return identity["sha256"]

    def observe_online(
        self,
        grade: Mapping[str, Any],
        *,
        resources: Mapping[str, float | int] | None = None,
    ) -> None:
        self._ready()
        _require(
            self.budget["online_cfd_in_flight"] == 1
            and self._pending_geometry is not None,
            "no online CFD query is unresolved",
        )
        validated = validate_grade(grade, self.protocol)
        _require(
            validated["geometry_sha256"] == self._pending_geometry["sha256"],
            "online grade geometry differs from the request",
        )
        before = dict(self.budget)
        after = dict(before)
        after["online_cfd_in_flight"] = 0
        after["online_cfd_queries_used"] += 1
        artifact = {
            "namespace": validated["data_namespace"],
            "artifact_id": validated["artifact_id"],
            "sha256": validated["result_sha256"],
        }
        self.writer.append(
            "online_cfd_observed",
            geometry=self._pending_geometry,
            budget_before=before,
            budget_after=after,
            visibility="optimizer_visible",
            artifacts=[artifact],
            resources=resources,
            reason_codes=validated["reason_codes"],
            payload={
                "grade": validated,
                "cache_hit": False,
            },
        )
        self._online_grades[validated["geometry_sha256"]] = validated
        self.budget = after
        self._pending_geometry = None

    def record_infrastructure_loss(
        self,
        reason_code: str,
        *,
        resources: Mapping[str, float | int] | None = None,
    ) -> None:
        self._ready()
        _require(
            self.budget["online_cfd_in_flight"] == 1
            and self._pending_geometry is not None,
            "no online CFD query is unresolved",
        )
        _require(
            reason_code
            in self.protocol.payload["reason_code_categories"]["infrastructure"],
            "infrastructure reason code is not declared",
        )
        before = dict(self.budget)
        after = dict(before)
        after["online_cfd_in_flight"] = 0
        self.writer.append(
            "infrastructure_loss_recorded",
            geometry=self._pending_geometry,
            budget_before=before,
            budget_after=after,
            visibility="administrative_only",
            resources=resources,
            reason_codes=[reason_code],
            payload={
                "scientific_budget_charged": False,
                "identical_action_reacquisition_allowed": True,
            },
        )
        self.budget = after
        self._pending_geometry = None

    def online_cfd(
        self,
        geometry: Sequence[float],
        grade: Mapping[str, Any],
        *,
        resources: Mapping[str, float | int] | None = None,
    ) -> None:
        self.request_online(geometry)
        self.observe_online(grade, resources=resources)

    def checkpoint(
        self,
        geometry: Sequence[float],
        *,
        fresh_hidden_grade: Mapping[str, Any] | None = None,
        resources: Mapping[str, float | int] | None = None,
    ) -> dict[str, Any]:
        self._ready()
        checkpoint = self.budget["online_cfd_queries_used"]
        _require(
            checkpoint in self.profile["active_checkpoints"],
            "current budget is not an active checkpoint",
        )
        _require(
            checkpoint not in self._checkpoint_grades,
            "checkpoint was already recorded",
        )
        _require(
            self.budget["online_cfd_in_flight"] == 0,
            "cannot nominate while online CFD is unresolved",
        )
        identity = geometry_identity(geometry, self.protocol)
        before = dict(self.budget)
        self.writer.append(
            "checkpoint_nominated",
            geometry=identity,
            budget_before=before,
            budget_after=before,
            visibility="none",
            payload={"checkpoint": checkpoint},
        )
        online = self._online_grades.get(identity["sha256"])
        if online is not None:
            _require(
                fresh_hidden_grade is None,
                "online-visible checkpoint grade must be reused exactly",
            )
            grade = online
            provenance = "reused_online_visible"
            after = dict(before)
            grade_resources = None
        else:
            _require(
                fresh_hidden_grade is not None,
                "unseen checkpoint nominee requires a fresh hidden grade",
            )
            grade = validate_grade(fresh_hidden_grade, self.protocol)
            _require(
                grade["geometry_sha256"] == identity["sha256"],
                "hidden checkpoint grade geometry mismatch",
            )
            provenance = "fresh_hidden"
            after = dict(before)
            after["fresh_checkpoint_grades_used"] += 1
            grade_resources = resources
        artifact = {
            "namespace": grade["data_namespace"],
            "artifact_id": grade["artifact_id"],
            "sha256": grade["result_sha256"],
        }
        self.writer.append(
            "checkpoint_grade_bound",
            geometry=identity,
            budget_before=before,
            budget_after=after,
            visibility="hidden_not_returned",
            artifacts=[artifact],
            resources=grade_resources,
            reason_codes=grade["reason_codes"],
            payload={
                "checkpoint": checkpoint,
                "provenance": provenance,
                "grade": grade,
                "returned_to_optimizer": False,
            },
        )
        self._checkpoint_grades[checkpoint] = grade
        self._checkpoint_geometries[checkpoint] = identity["sha256"]
        self.budget = after
        return deepcopy(grade)

    def terminal(self, geometry: Sequence[float]) -> dict[str, Any]:
        self._ready()
        final_checkpoint = self.profile["active_checkpoints"][-1]
        _require(
            self.budget["online_cfd_queries_used"] == final_checkpoint,
            "terminal nomination requires the final active checkpoint",
        )
        _require(
            final_checkpoint in self._checkpoint_grades,
            "final checkpoint must be graded before terminal binding",
        )
        identity = geometry_identity(geometry, self.protocol)
        _require(
            self._checkpoint_geometries[final_checkpoint] == identity["sha256"],
            "terminal nominee must equal the final checkpoint nominee",
        )
        before = dict(self.budget)
        self.writer.append(
            "terminal_nominated",
            geometry=identity,
            budget_before=before,
            budget_after=before,
            visibility="none",
            payload={"final_checkpoint": final_checkpoint},
        )
        after = dict(before)
        after["terminal_grades_used"] = 1
        grade = self._checkpoint_grades[final_checkpoint]
        artifact = {
            "namespace": grade["data_namespace"],
            "artifact_id": grade["artifact_id"],
            "sha256": grade["result_sha256"],
        }
        self.writer.append(
            "terminal_grade_bound",
            geometry=identity,
            budget_before=before,
            budget_after=after,
            visibility="hidden_not_returned",
            artifacts=[artifact],
            reason_codes=grade["reason_codes"],
            payload={
                "provenance": "reused_final_checkpoint_grade",
                "grade": grade,
                "returned_to_optimizer": False,
            },
        )
        self.budget = after
        self._terminal_grade = grade
        return deepcopy(grade)

    def stop(self, reason_code: str = "budget_exhausted") -> None:
        self._ready()
        _require(self._terminal_grade is not None, "terminal grade is missing")
        before = dict(self.budget)
        self.writer.append(
            "episode_stopped",
            geometry=None,
            budget_before=before,
            budget_after=before,
            visibility="none",
            reason_codes=[reason_code],
            payload={
                "unused_cheap_queries": (
                    self.profile["maximum_cheap_queries"]
                    - before["cheap_queries_used"]
                ),
                "unused_online_cfd_queries": (
                    self.profile["maximum_online_cfd_queries"]
                    - before["online_cfd_queries_used"]
                ),
            },
        )
        self._stopped = True

    @classmethod
    def resume(
        cls,
        *,
        protocol: V2Protocol,
        profile: str,
        events: Sequence[Mapping[str, Any]],
        expected_head_sha256: str,
    ) -> "EpisodeMachine":
        from wingbench.benchmark.replay import replay_trace

        head = verify_trace(events, protocol)
        _require(head == expected_head_sha256, "restart trace-head binding changed")
        _require(bool(events), "cannot resume an empty trace")
        first = dict(events[0])
        summary = replay_trace(events, protocol.payload)
        machine = cls(
            protocol=protocol,
            profile=profile,
            episode_id=first["episode_id"],
            optimizer_identity=first["optimizer_identity"],
            instance_id=first["instance_id"],
            matched_seed=first["matched_seed"],
        )
        machine.writer.events = [deepcopy(dict(event)) for event in events]
        machine.budget = dict(summary["final_budget"])
        machine._reset = True
        machine._stopped = summary["stopped"]
        for event in events:
            event = dict(event)
            payload = event["event_payload"]
            geometry = event["geometry_identity"]
            if event["event_type"] == "online_cfd_dispatched":
                machine._pending_geometry = deepcopy(geometry)
            elif event["event_type"] in {
                "online_cfd_observed",
                "infrastructure_loss_recorded",
            }:
                machine._pending_geometry = None
            if event["event_type"] == "online_cfd_observed":
                grade = deepcopy(payload["grade"])
                machine._online_grades[grade["geometry_sha256"]] = grade
            elif event["event_type"] == "checkpoint_grade_bound":
                checkpoint = int(payload["checkpoint"])
                grade = deepcopy(payload["grade"])
                machine._checkpoint_grades[checkpoint] = grade
                machine._checkpoint_geometries[checkpoint] = geometry["sha256"]
            elif event["event_type"] == "terminal_grade_bound":
                machine._terminal_grade = deepcopy(payload["grade"])
        return machine
