"""Independent replay for the WingBench V2 candidate episode trace.

This module intentionally does not import the trace writer.  It independently
implements canonical hashing, structural checks, budget transitions, leakage
checks, and outcome reconstruction.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from typing import Any, Mapping, Sequence


TRACE_SCHEMA = "wingbench-v2-episode-trace-v1"
ZERO_SHA256 = "0" * 64
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


class V2ReplayError(ValueError):
    """A candidate trace cannot be independently reconstructed."""


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise V2ReplayError(message)


def _hash(value: Any, label: str) -> str:
    _require(
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value),
        f"{label} is not a lowercase SHA-256",
    )
    return value


def _zero_budget() -> dict[str, int]:
    return {field: 0 for field in BUDGET_FIELDS}


def _budget(value: Any, label: str) -> dict[str, int]:
    _require(isinstance(value, dict), f"{label} budget is not an object")
    _require(set(value) == set(BUDGET_FIELDS), f"{label} budget fields changed")
    result: dict[str, int] = {}
    for field in BUDGET_FIELDS:
        raw = value[field]
        _require(
            isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0,
            f"{label} budget field {field} is invalid",
        )
        result[field] = raw
    return result


def _resources(value: Any) -> dict[str, float]:
    _require(isinstance(value, dict), "resource accounting is not an object")
    _require(set(value) == set(RESOURCE_FIELDS), "resource fields changed")
    result: dict[str, float] = {}
    for field in RESOURCE_FIELDS:
        raw = value[field]
        _require(
            isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(float(raw))
            and float(raw) >= 0.0,
            f"resource field {field} is invalid",
        )
        result[field] = float(raw)
    return result


def _grade(
    value: Any,
    *,
    geometry_sha256: str,
    allowed_namespaces: set[str],
) -> dict[str, Any]:
    _require(isinstance(value, dict), "grade is not an object")
    grade = dict(value)
    expected = {
        "schema_version",
        "geometry_sha256",
        "delivery",
        "conditional_value",
        "reason_codes",
        "data_namespace",
        "artifact_id",
        "result_sha256",
    }
    _require(set(grade) == expected, "grade fields changed")
    _require(
        grade["schema_version"] == "wingbench-v2-development-grade-v1",
        "grade schema changed",
    )
    _require(
        grade["geometry_sha256"] == geometry_sha256,
        "grade geometry changed",
    )
    _require(isinstance(grade["delivery"], bool), "grade delivery is invalid")
    conditional = grade["conditional_value"]
    if grade["delivery"]:
        _require(
            isinstance(conditional, (int, float))
            and not isinstance(conditional, bool)
            and math.isfinite(float(conditional))
            and 0.0 < float(conditional) <= 40.0,
            "delivered grade value is invalid",
        )
        grade["conditional_value"] = float(conditional)
    else:
        _require(conditional is None, "nondelivery contains a numeric penalty")
    _require(
        isinstance(grade["reason_codes"], list)
        and all(
            isinstance(reason, str) and reason for reason in grade["reason_codes"]
        ),
        "grade reason codes are invalid",
    )
    _require(
        grade["data_namespace"] in allowed_namespaces,
        "sealed or undeclared grade namespace entered replay",
    )
    _require(
        isinstance(grade["artifact_id"], str) and grade["artifact_id"],
        "grade artifact ID is missing",
    )
    claimed = _hash(grade["result_sha256"], "grade result")
    core = dict(grade)
    core.pop("result_sha256")
    _require(_sha256(core) == claimed, "grade result hash changed")
    return grade


def _geometry(event: Mapping[str, Any]) -> str:
    geometry = event.get("geometry_identity")
    _require(isinstance(geometry, dict), "event geometry is missing")
    return _hash(geometry.get("sha256"), "event geometry")


def _check_artifacts(
    value: Any, allowed_namespaces: set[str]
) -> list[dict[str, Any]]:
    _require(isinstance(value, list), "artifact bindings are not a list")
    result: list[dict[str, Any]] = []
    for raw in value:
        _require(isinstance(raw, dict), "artifact binding is not an object")
        binding = dict(raw)
        _require(
            set(binding) == {"namespace", "artifact_id", "sha256"},
            "artifact binding fields changed",
        )
        _require(
            binding["namespace"] in allowed_namespaces,
            "sealed or undeclared artifact entered replay",
        )
        _require(
            isinstance(binding["artifact_id"], str) and binding["artifact_id"],
            "artifact ID is missing",
        )
        _hash(binding["sha256"], "artifact")
        result.append(binding)
    return result


def replay_trace(
    events: Sequence[Mapping[str, Any]],
    protocol: Mapping[str, Any],
) -> dict[str, Any]:
    """Independently verify and reconstruct one complete or partial episode."""

    _require(isinstance(events, Sequence) and len(events) > 0, "trace is empty")
    _require(
        protocol.get("schema_version") == "wingbench-v2-candidate-protocol-v1",
        "replay protocol schema changed",
    )
    tracks = protocol["tracks"]
    profile_name = events[0].get("event_payload", {}).get("profile")
    if profile_name == "main64":
        maximum_online = 64
        maximum_cheap = tracks["anytime_main"]["maximum_cheap_queries"]
        checkpoints = tracks["anytime_main"]["checkpoints"]
    elif profile_name == "pilot32":
        maximum_online = tracks["phase3_pilot_profile"][
            "maximum_online_cfd_queries"
        ]
        maximum_cheap = tracks["phase3_pilot_profile"]["maximum_cheap_queries"]
        checkpoints = tracks["phase3_pilot_profile"]["active_checkpoints"]
    elif profile_name == "bridge4":
        maximum_online = tracks["four_cfd_bridge"]["maximum_online_cfd_queries"]
        maximum_cheap = tracks["four_cfd_bridge"]["maximum_cheap_queries"]
        checkpoints = tracks["four_cfd_bridge"]["active_checkpoints"]
    else:
        raise V2ReplayError("trace profile is unknown")

    allowed_namespaces = set(
        protocol["data_boundaries"]["allowed_phase2_namespaces"]
    )
    expected_protocol_identity: dict[str, Any] | None = None
    stable_identity: dict[str, Any] | None = None
    episode_id: str | None = None
    previous = ZERO_SHA256
    state = _zero_budget()
    pending_cheap: dict[str, Any] | None = None
    pending_online_geometry: str | None = None
    online_grades: dict[str, dict[str, Any]] = {}
    pending_checkpoint: tuple[int, str] | None = None
    checkpoint_grades: dict[int, dict[str, Any]] = {}
    checkpoint_geometries: dict[int, str] = {}
    pending_terminal: str | None = None
    terminal_grade: dict[str, Any] | None = None
    stopped = False
    discovery_best: float | None = None
    discovery_curve: list[dict[str, Any]] = []
    recommendation_curve: list[dict[str, Any]] = []
    resource_totals = {field: 0.0 for field in RESOURCE_FIELDS}
    reason_counts: Counter[str] = Counter()

    for index, raw in enumerate(events):
        _require(isinstance(raw, Mapping), "trace event is not an object")
        event = dict(raw)
        _require(set(event) == EVENT_FIELDS, "trace event fields changed")
        _require(event["schema_version"] == TRACE_SCHEMA, "trace schema changed")
        _require(event["event_index"] == index, "trace event index changed")
        if episode_id is None:
            episode_id = event["episode_id"]
        _require(event["episode_id"] == episode_id, "cross-episode trace leak")
        _require(
            event["event_id"]
            == f"{episode_id}:event:{index:06d}:{event['event_type']}",
            "event ID changed",
        )
        _require(
            event["previous_event_sha256"] == previous,
            "trace hash-chain predecessor changed",
        )
        claimed = _hash(event["event_sha256"], "event")
        core = dict(event)
        core.pop("event_sha256")
        _require(_sha256(core) == claimed, "trace event hash changed")
        previous = claimed
        identity = {
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
        if stable_identity is None:
            stable_identity = identity
            expected_protocol_identity = event["protocol_identity"]
        _require(identity == stable_identity, "episode identity changed")
        _require(
            event["benchmark_identity"]["benchmark_id"]
            == protocol["benchmark_id"]
            and event["benchmark_identity"]["normative_release"] is False,
            "benchmark identity changed",
        )
        _require(
            event["protocol_identity"] == expected_protocol_identity
            and event["protocol_identity"]["protocol_id"]
            == protocol["protocol_id"],
            "protocol identity changed",
        )
        _require(
            event["evaluator_identity"]
            == {
                "evaluator_id": protocol["evaluator_id"],
                "official": False,
                "v1_equivalent": False,
            },
            "evaluator identity changed",
        )
        before = _budget(event["budget_before"], "before")
        after = _budget(event["budget_after"], "after")
        _require(before == state, "budget-before does not match replay state")
        resources = _resources(event["resource_accounting"])
        for field, amount in resources.items():
            resource_totals[field] += amount
        _check_artifacts(event["artifact_bindings"], allowed_namespaces)
        _require(
            isinstance(event["reason_codes"], list)
            and all(
                isinstance(reason, str) and reason
                for reason in event["reason_codes"]
            ),
            "event reason codes are invalid",
        )
        reason_counts.update(event["reason_codes"])
        payload = event["event_payload"]
        _require(isinstance(payload, dict), "event payload is not an object")
        event_type = event["event_type"]

        expected_after = dict(state)
        if event_type == "episode_reset":
            _require(index == 0 and state == _zero_budget(), "invalid reset")
            _require(event["observation_visibility"] == "none", "reset leaked data")
            _require(payload.get("profile") == profile_name, "profile changed")
        elif event_type == "cheap_query_requested":
            _require(pending_cheap is None, "cheap request already pending")
            count = payload.get("query_count")
            _require(
                isinstance(count, int) and not isinstance(count, bool) and count > 0,
                "cheap request count is invalid",
            )
            _require(
                event["observation_visibility"] == "none",
                "cheap request exposed an observation",
            )
            pending_cheap = {
                "count": count,
                "geometry_identity": event["geometry_identity"],
            }
        elif event_type == "cheap_query_observed":
            _require(pending_cheap is not None, "cheap observation is unrequested")
            count = payload.get("query_count")
            _require(count == pending_cheap["count"], "cheap batch count changed")
            _require(
                event["geometry_identity"]
                == pending_cheap["geometry_identity"],
                "cheap batch identity changed",
            )
            _require(
                isinstance(payload.get("cache_hit"), bool),
                "cheap cache visibility is not explicit",
            )
            _hash(payload.get("observation_sha256"), "cheap observation")
            _require(
                event["observation_visibility"] == "optimizer_visible",
                "cheap result was not optimizer-visible",
            )
            expected_after["cheap_queries_used"] += count
            _require(
                expected_after["cheap_queries_used"] <= maximum_cheap,
                "cheap budget exceeded",
            )
            pending_cheap = None
        elif event_type == "online_cfd_requested":
            _require(
                pending_online_geometry is None
                and state["online_cfd_in_flight"] == 0,
                "multiple online CFD actions overlap",
            )
            _require(
                state["online_cfd_queries_used"] < maximum_online,
                "online CFD budget exceeded",
            )
            geometry_sha = _geometry(event)
            _require(
                payload.get("cache_status")
                in {"episode_local_hit", "episode_local_miss"},
                "online cache visibility is not explicit",
            )
            _require(
                payload["cache_status"]
                == (
                    "episode_local_hit"
                    if geometry_sha in online_grades
                    else "episode_local_miss"
                ),
                "online cache status changed",
            )
            pending_online_geometry = geometry_sha
        elif event_type == "online_cfd_dispatched":
            geometry_sha = _geometry(event)
            _require(
                pending_online_geometry == geometry_sha
                and state["online_cfd_in_flight"] == 0,
                "online dispatch is unrequested",
            )
            _require(
                event["observation_visibility"] == "administrative_only",
                "dispatch visibility changed",
            )
            expected_after["online_cfd_in_flight"] = 1
        elif event_type == "online_cfd_observed":
            geometry_sha = _geometry(event)
            _require(
                pending_online_geometry == geometry_sha
                and state["online_cfd_in_flight"] == 1,
                "online observation is unrequested",
            )
            _require(
                event["observation_visibility"] == "optimizer_visible",
                "online result was not optimizer-visible",
            )
            _require(
                payload.get("cache_hit") is False,
                "online observation cache status changed",
            )
            grade = _grade(
                payload.get("grade"),
                geometry_sha256=geometry_sha,
                allowed_namespaces=allowed_namespaces,
            )
            expected_after["online_cfd_in_flight"] = 0
            expected_after["online_cfd_queries_used"] += 1
            _require(
                expected_after["online_cfd_queries_used"] <= maximum_online,
                "online CFD budget exceeded",
            )
            online_grades[geometry_sha] = grade
            if grade["delivery"]:
                value = float(grade["conditional_value"])
                discovery_best = (
                    value
                    if discovery_best is None
                    else max(discovery_best, value)
                )
            discovery_curve.append(
                {
                    "online_cfd_queries_used": expected_after[
                        "online_cfd_queries_used"
                    ],
                    "at_least_one_delivery": discovery_best is not None,
                    "best_conditional_value": discovery_best,
                }
            )
            pending_online_geometry = None
        elif event_type == "infrastructure_loss_recorded":
            geometry_sha = _geometry(event)
            _require(
                pending_online_geometry == geometry_sha
                and state["online_cfd_in_flight"] == 1,
                "infrastructure loss has no unresolved action",
            )
            _require(
                event["observation_visibility"] == "administrative_only"
                and payload.get("scientific_budget_charged") is False
                and payload.get("identical_action_reacquisition_allowed") is True,
                "infrastructure loss changed scientific semantics",
            )
            expected_after["online_cfd_in_flight"] = 0
            pending_online_geometry = None
        elif event_type == "checkpoint_nominated":
            geometry_sha = _geometry(event)
            checkpoint = payload.get("checkpoint")
            _require(
                checkpoint == state["online_cfd_queries_used"]
                and checkpoint in checkpoints
                and checkpoint not in checkpoint_grades
                and pending_checkpoint is None,
                "checkpoint nomination is invalid",
            )
            _require(
                state["online_cfd_in_flight"] == 0
                and event["observation_visibility"] == "none",
                "checkpoint nomination overlaps CFD or leaks data",
            )
            pending_checkpoint = (checkpoint, geometry_sha)
        elif event_type == "checkpoint_grade_bound":
            geometry_sha = _geometry(event)
            checkpoint = payload.get("checkpoint")
            _require(
                pending_checkpoint == (checkpoint, geometry_sha),
                "checkpoint grade is unbound",
            )
            _require(
                event["observation_visibility"] == "hidden_not_returned"
                and payload.get("returned_to_optimizer") is False,
                "checkpoint grade leaked to the optimizer",
            )
            grade = _grade(
                payload.get("grade"),
                geometry_sha256=geometry_sha,
                allowed_namespaces=allowed_namespaces,
            )
            provenance = payload.get("provenance")
            if provenance == "reused_online_visible":
                _require(
                    geometry_sha in online_grades
                    and online_grades[geometry_sha] == grade,
                    "checkpoint did not exactly reuse its online grade",
                )
            elif provenance == "fresh_hidden":
                _require(
                    geometry_sha not in online_grades,
                    "online-graded checkpoint was silently regraded",
                )
                expected_after["fresh_checkpoint_grades_used"] += 1
            else:
                raise V2ReplayError("checkpoint provenance is unknown")
            checkpoint_grades[int(checkpoint)] = grade
            checkpoint_geometries[int(checkpoint)] = geometry_sha
            recommendation_curve.append(
                {
                    "checkpoint": int(checkpoint),
                    "geometry_sha256": geometry_sha,
                    "provenance": provenance,
                    "delivery": grade["delivery"],
                    "conditional_value": grade["conditional_value"],
                }
            )
            pending_checkpoint = None
        elif event_type == "terminal_nominated":
            geometry_sha = _geometry(event)
            final = checkpoints[-1]
            _require(
                state["online_cfd_queries_used"] == final
                and final in checkpoint_grades
                and checkpoint_geometries[final] == geometry_sha
                and pending_terminal is None,
                "terminal nomination differs from the final checkpoint",
            )
            _require(
                event["observation_visibility"] == "none",
                "terminal nomination leaked data",
            )
            pending_terminal = geometry_sha
        elif event_type == "terminal_grade_bound":
            geometry_sha = _geometry(event)
            final = checkpoints[-1]
            _require(
                pending_terminal == geometry_sha,
                "terminal grade is unbound",
            )
            _require(
                event["observation_visibility"] == "hidden_not_returned"
                and payload.get("returned_to_optimizer") is False
                and payload.get("provenance")
                == "reused_final_checkpoint_grade",
                "terminal grade leakage or provenance changed",
            )
            grade = _grade(
                payload.get("grade"),
                geometry_sha256=geometry_sha,
                allowed_namespaces=allowed_namespaces,
            )
            _require(
                grade == checkpoint_grades[final],
                "terminal grade differs from final checkpoint grade",
            )
            expected_after["terminal_grades_used"] = 1
            terminal_grade = grade
            pending_terminal = None
        elif event_type == "episode_stopped":
            _require(
                terminal_grade is not None
                and not stopped
                and index == len(events) - 1,
                "episode stop is premature or not terminal",
            )
            _require(
                event["observation_visibility"] == "none",
                "stop event leaked data",
            )
            stopped = True
        else:
            raise V2ReplayError(f"unknown event type: {event_type}")

        _require(after == expected_after, f"{event_type} budget transition changed")
        state = after

    _require(pending_cheap is None, "trace ends with a pending cheap query")
    _require(
        state["online_cfd_in_flight"] in {0, 1},
        "invalid final online CFD state",
    )
    return {
        "schema_version": "wingbench-v2-independent-replay-v1",
        "episode_id": episode_id,
        "trace_head_sha256": previous,
        "event_count": len(events),
        "profile": profile_name,
        "identity": stable_identity,
        "final_budget": state,
        "online_grade_count": len(online_grades),
        "checkpoint_count": len(checkpoint_grades),
        "discovery_curve": discovery_curve,
        "recommendation_curve": recommendation_curve,
        "terminal": (
            None
            if terminal_grade is None
            else {
                "delivery": terminal_grade["delivery"],
                "conditional_value": terminal_grade["conditional_value"],
                "result_sha256": terminal_grade["result_sha256"],
            }
        ),
        "resource_totals": resource_totals,
        "reason_code_counts": dict(sorted(reason_counts.items())),
        "stopped": stopped,
    }
