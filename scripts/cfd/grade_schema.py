"""Pure state machine for the v5 whole-wing CFD result contract.

This module deliberately knows nothing about MPI or ADflow imports.  It turns
structured process evidence and solver output into scientific outcomes while
keeping infrastructure censoring orthogonal to aerodynamic invalidity.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from scripts.cfd.grading_protocol import GradingProtocol, ProtocolError


RESULT_SCHEMA_VERSION = "wingbench-cfd-whole-wing-result-v1"
PROCESS_TERMINATIONS = {
    "completed",
    "timeout",
    "process_error",
}


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _inclusive_le(value: float, bound: float) -> bool:
    """Mathematical <= with a tiny binary64 boundary allowance."""
    return value <= bound or math.isclose(value, bound, rel_tol=0.0, abs_tol=1e-12)


def _execution_inconclusive_reason(process: Mapping[str, Any]) -> str | None:
    termination = process.get("termination_reason")
    returncode = process.get("returncode")
    if termination not in PROCESS_TERMINATIONS:
        return "inconclusive_infrastructure"
    if termination == "timeout":
        return "inconclusive_timeout"
    if termination == "process_error" or returncode != 0:
        return "inconclusive_infrastructure"
    return None


def classify_mesh_attempt(
    process: Mapping[str, Any],
    mesh_info: Mapping[str, Any] | None,
    protocol: GradingProtocol,
    *,
    volume_grid_valid: bool = True,
) -> dict[str, Any]:
    """Classify mesh evidence; absence is never proof of unmeshability."""
    reason = _execution_inconclusive_reason(process)
    base = {
        "execution": dict(process),
        "outcome": "inconclusive",
        "reason_codes": [],
        "min_volume": None,
        "min_quality": None,
    }
    if reason:
        base["reason_codes"] = [reason]
        return base
    if not isinstance(mesh_info, Mapping):
        base["reason_codes"] = ["inconclusive_output_failure"]
        return base
    min_volume = mesh_info.get("min_Vol")
    min_quality = mesh_info.get("min_Quality")
    base["min_volume"] = float(min_volume) if _finite(min_volume) else None
    base["min_quality"] = float(min_quality) if _finite(min_quality) else None
    if not _finite(min_volume) or not _finite(min_quality):
        base["reason_codes"] = ["inconclusive_output_failure"]
        return base
    if float(min_volume) <= 0.0 or float(min_quality) <= 0.0:
        base["outcome"] = "unmeshable"
        base["reason_codes"] = ["invalid_mesh_nonpositive_metric"]
        return base
    if volume_grid_valid is not True:
        base["reason_codes"] = ["inconclusive_mesh_artifact_validation"]
        return base
    marginal_threshold = 0.05
    if protocol.payload["mesh"].get("marginal_quality_if") != "0<q<0.05":
        raise ProtocolError("unsupported marginal mesh expression")
    base["outcome"] = "marginal" if float(min_quality) < marginal_threshold else "valid"
    return base


def _empty_measurements(expected: Mapping[str, Any], seed_alpha0: float) -> dict[str, Any]:
    return {
        "mach": float(expected["mach"]),
        "cl_target": float(expected["cl_target"]),
        "seed_alpha0_deg": float(seed_alpha0),
        "realized_aoa_deg": None,
        "cl": None,
        "cd": None,
        "ld": None,
        "err_cl": None,
        "solvecl_iterations": None,
        "solvecl_l2convergence": None,
        "solvecl_time_seconds": None,
    }


def classify_condition_attempt(
    process: Mapping[str, Any],
    output: Mapping[str, Any] | None,
    expected: Mapping[str, Any],
    seed_alpha0: float,
    protocol: GradingProtocol,
) -> dict[str, Any]:
    """Classify one fresh solve attempt under the frozen candidate gates."""
    measurements = _empty_measurements(expected, seed_alpha0)
    attempt = {
        "execution": dict(process),
        "outcome_class": "inconclusive",
        "outcome": "inconclusive_infrastructure",
        "reason_codes": [],
        "measurements": measurements,
        "gate_decisions": {},
    }
    execution_reason = _execution_inconclusive_reason(process)
    if execution_reason:
        attempt["outcome"] = execution_reason
        attempt["reason_codes"] = [execution_reason]
        return attempt
    if not isinstance(output, Mapping):
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_output_failure"]
        return attempt
    results = output.get("results")
    working = output.get("working_conditions")
    if not isinstance(results, Mapping) or not isinstance(working, Mapping):
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_output_failure"]
        return attempt

    expected_mach = float(expected["mach"])
    expected_cl = float(expected["cl_target"])
    mach_matches = _finite(working.get("M3")) and math.isclose(
        float(working["M3"]), expected_mach, rel_tol=0.0, abs_tol=1e-12
    )
    target_matches = _finite(working.get("CL_targ")) and math.isclose(
        float(working["CL_targ"]), expected_cl, rel_tol=0.0, abs_tol=1e-12
    )
    seed_matches = _finite(working.get("AA")) and math.isclose(
        float(working["AA"]), float(seed_alpha0), rel_tol=0.0, abs_tol=1e-12
    )
    reynolds_matches = _finite(working.get("re")) and math.isclose(
        float(working["re"]), float(protocol.payload["flow"]["reynolds"]),
        rel_tol=0.0, abs_tol=1e-6,
    )
    temperature_matches = _finite(working.get("temp")) and math.isclose(
        float(working["temp"]), float(protocol.payload["flow"]["temperature_k"]),
        rel_tol=0.0, abs_tol=1e-12,
    )
    if not (mach_matches and target_matches and seed_matches and reynolds_matches and temperature_matches):
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_condition_identity_mismatch"]
        return attempt

    grading = results.get("_grading")
    if not isinstance(grading, Mapping) or grading.get("contract_version") != "superwing-adflow-evidence-v1":
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_missing_convergence_evidence"]
        return attempt
    solvecl = grading.get("solvecl")
    aero = grading.get("aeroproblem")
    if (grading.get("solvecl_requested") is not True or not isinstance(solvecl, Mapping)
            or solvecl.get("available") is not True or not isinstance(aero, Mapping)):
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_missing_convergence_evidence"]
        return attempt
    if type(solvecl.get("converged")) is not bool:
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_missing_convergence_evidence"]
        return attempt
    if type(aero.get("solveFailed")) is not bool or type(aero.get("fatalFail")) is not bool:
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_missing_convergence_evidence"]
        return attempt

    cl = results.get("wing_cl")
    cd = results.get("wing_cd")
    err_cl = results.get("err_cl")
    aoa = solvecl.get("alpha")
    if aoa is None:
        aoa = results.get("AA")
    measurements.update({
        "realized_aoa_deg": float(aoa) if _finite(aoa) else None,
        "cl": float(cl) if _finite(cl) else None,
        "cd": float(cd) if _finite(cd) else None,
        "err_cl": float(err_cl) if _finite(err_cl) else None,
        "solvecl_iterations": int(solvecl["iterations"])
        if isinstance(solvecl.get("iterations"), int) and not isinstance(solvecl.get("iterations"), bool) else None,
        "solvecl_l2convergence": float(solvecl["l2convergence"])
        if _finite(solvecl.get("l2convergence")) else None,
        "solvecl_time_seconds": float(solvecl["time"]) if _finite(solvecl.get("time")) else None,
    })
    if _finite(cl) and _finite(cd) and float(cd) != 0.0:
        measurements["ld"] = float(cl) / float(cd)

    # Both success and failure verdicts require a structurally complete solveCL
    # record.  A truncated `{converged: false}` packet is epistemically missing,
    # not conclusive nonconvergence.
    iterations = solvecl.get("iterations")
    l2convergence = solvecl.get("l2convergence")
    solvecl_time = solvecl.get("time")
    solvecl_alpha = solvecl.get("alpha")
    solvecl_cl = solvecl.get("cl")
    solvecl_target = solvecl.get("clstar")
    solvecl_error = solvecl.get("error")
    history_entries = solvecl.get("history_entries")
    max_iterations = int(protocol.payload["solver"]["solvecl"]["max_iter"])
    common_evidence_complete = (
        isinstance(iterations, int)
        and not isinstance(iterations, bool)
        and 1 <= iterations <= max_iterations
        and isinstance(history_entries, int)
        and not isinstance(history_entries, bool)
        and history_entries == iterations
        and all(_finite(value) for value in (
            l2convergence, solvecl_time, solvecl_alpha, solvecl_cl,
            solvecl_target, solvecl_error,
        ))
        and float(solvecl_time) >= 0.0
        and math.isclose(float(solvecl_target), expected_cl, rel_tol=0.0, abs_tol=1e-12)
        and math.isclose(
            float(solvecl_error), float(solvecl_cl) - expected_cl,
            rel_tol=0.0, abs_tol=1e-10,
        )
        and _finite(process.get("wall_seconds"))
        and float(solvecl_time) <= float(process["wall_seconds"]) + 1.0
    )
    if not common_evidence_complete:
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_inconsistent_solvecl_evidence"]
        return attempt

    solver_converged = solvecl["converged"] is True
    # Do not trust the solver's boolean in isolation.  ADflow's solveCL result
    # exposes the two quantities that define convergence under this protocol:
    # the lift-target error must be strictly inside solveCL's tolerance and the
    # flow residual must meet the configured L2 target.  A packet whose boolean
    # disagrees with those quantities is contradictory evidence, not proof of
    # either convergence or scientific nonconvergence.
    solvecl_tolerance = float(protocol.payload["solver"]["solvecl"]["tol"])
    l2_target = float(protocol.payload["solver"]["l2_convergence"])
    threshold_converged = (
        abs(float(solvecl_error)) < solvecl_tolerance
        and float(l2convergence) <= l2_target
    )
    if solver_converged != threshold_converged:
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_inconsistent_solvecl_evidence"]
        return attempt

    solver_flags_clear = aero["solveFailed"] is False and aero["fatalFail"] is False
    if not solver_converged or not solver_flags_clear:
        reasons = []
        if not solver_converged:
            reasons.append("invalid_solver_nonconvergence")
        if aero["solveFailed"] is True:
            reasons.append("invalid_solver_failure")
        if aero["fatalFail"] is True:
            reasons.append("invalid_solver_fatal_failure")
        attempt.update({
            "outcome_class": "invalid",
            "outcome": reasons[0],
            "reason_codes": reasons,
            "gate_decisions": {
                "solvecl_converged": solver_converged,
                "solvecl_threshold_converged": threshold_converged,
                "solver_flags_clear": solver_flags_clear,
                "solvecl_evidence_complete": True,
            },
        })
        return attempt

    success_evidence_consistent = (
        _finite(cl)
        and _finite(aoa)
        and float(l2convergence) <= l2_target
        and math.isclose(float(solvecl_cl), float(cl), rel_tol=0.0, abs_tol=1e-10)
        and math.isclose(float(solvecl_alpha), float(aoa), rel_tol=0.0, abs_tol=1e-10)
    )
    if not success_evidence_consistent:
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_inconsistent_solvecl_evidence"]
        return attempt

    # Missing stationarity evidence is epistemic, not a failed buffet gate.
    if not _finite(err_cl):
        attempt["outcome"] = "inconclusive_output_failure"
        attempt["reason_codes"] = ["inconclusive_missing_stationarity_evidence"]
        return attempt

    gates = protocol.payload["condition_gates"]
    numeric = _finite(cl) and _finite(cd) and _finite(aoa) and _finite(measurements["ld"])
    positive_cl = numeric and float(cl) > 0.0
    positive_cd = numeric and float(cd) > 0.0
    positive_ld = numeric and float(measurements["ld"]) > 0.0
    buffet_ok = float(err_cl) < float(gates["err_cl_strictly_less_than"])
    on_target = numeric and _inclusive_le(
        abs(float(cl) - expected_cl), float(gates["cl_match_abs_less_than_or_equal"])
    )
    aoa_min, aoa_max = (float(x) for x in gates["aoa_deg_inclusive"])
    aoa_ok = numeric and aoa_min <= float(aoa) <= aoa_max
    gate_decisions = {
        "solvecl_converged": True,
        "solvecl_threshold_converged": True,
        "solver_flags_clear": True,
        "numeric": bool(numeric),
        "positive_cl": bool(positive_cl),
        "positive_cd": bool(positive_cd),
        "positive_ld": bool(positive_ld),
        "stationary_err_cl_strict": bool(buffet_ok),
        "cl_on_target_inclusive": bool(on_target),
        "aoa_inclusive": bool(aoa_ok),
    }
    reasons = []
    if not numeric or not (positive_cl and positive_cd and positive_ld):
        reasons.append("invalid_numeric")
    if not buffet_ok:
        reasons.append("invalid_buffet")
    if not on_target:
        reasons.append("invalid_off_target")
    if not aoa_ok:
        reasons.append("invalid_aoa")
    if reasons:
        attempt.update({
            "outcome_class": "invalid",
            "outcome": reasons[0],
            "reason_codes": reasons,
            "gate_decisions": gate_decisions,
        })
    else:
        attempt.update({
            "outcome_class": "valid",
            "outcome": "valid",
            "reason_codes": [],
            "gate_decisions": gate_decisions,
        })
    return attempt


def finalize_condition(
    expected: Mapping[str, Any],
    attempts: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Roll retry attempts into one condition without discarding their history."""
    if not attempts:
        raise ProtocolError("a condition must contain at least one attempt")
    completed = [a for a in attempts if a.get("outcome_class") in {"valid", "invalid"}]
    if completed:
        final = completed[-1]
        outcome_class = final["outcome_class"]
        outcome = final["outcome"]
        reasons = list(final.get("reason_codes") or [])
        measurements = dict(final.get("measurements") or {})
    else:
        final = attempts[-1]
        outcome_class = "inconclusive"
        outcome = final.get("outcome", "inconclusive_infrastructure")
        reasons = sorted({reason for a in attempts for reason in (a.get("reason_codes") or [])})
        measurements = dict(final.get("measurements") or {})
    return {
        "condition_id": expected["condition_id"],
        "mach": float(expected["mach"]),
        "cl_target": float(expected["cl_target"]),
        "outcome_class": outcome_class,
        "outcome": outcome,
        "reason_codes": reasons,
        "measurements": measurements,
        "attempts": [dict(a) for a in attempts],
    }


def aggregate_wing(
    mesh: Mapping[str, Any],
    conditions: Sequence[Mapping[str, Any]],
    protocol: GradingProtocol,
) -> dict[str, Any]:
    """Compute the only official roll-up: complete 3/3 validity or no score."""
    expected = protocol.conditions
    if len(conditions) != len(expected):
        raise ProtocolError("whole-wing result must contain every protocol condition")
    for index, (condition, spec) in enumerate(zip(conditions, expected)):
        if condition.get("condition_id") != spec["condition_id"]:
            raise ProtocolError(f"condition {index} is not in canonical output order")
        if not math.isclose(float(condition.get("mach")), float(spec["mach"]), rel_tol=0.0, abs_tol=1e-12):
            raise ProtocolError(f"condition {index} Mach does not match the protocol")

    mesh_outcome = mesh.get("outcome")
    condition_classes = [condition.get("outcome_class") for condition in conditions]
    if mesh_outcome == "unmeshable":
        admissibility, profile_status = "fail", "complete"
    elif mesh_outcome == "inconclusive":
        admissibility, profile_status = "undetermined", "incomplete"
    elif any(value == "invalid" for value in condition_classes):
        admissibility = "fail"
        profile_status = (
            "complete"
            if all(value in {"valid", "invalid"} for value in condition_classes)
            else "incomplete"
        )
    elif any(value != "valid" for value in condition_classes):
        admissibility, profile_status = "undetermined", "incomplete"
    else:
        admissibility, profile_status = "pass", "complete"

    score_eligible = admissibility == "pass" and profile_status == "complete"
    valid_lds = [condition.get("measurements", {}).get("ld")
                 for condition in conditions if condition.get("outcome_class") == "valid"]
    diagnostic_lds = [float(value) for value in valid_lds if _finite(value) and float(value) > 0.0]
    partial_geomean = (
        math.exp(sum(math.log(value) for value in diagnostic_lds) / len(diagnostic_lds))
        if diagnostic_lds else None
    )
    official_geomean = partial_geomean if score_eligible and len(diagnostic_lds) == len(expected) else None
    official_worst = min(diagnostic_lds) if official_geomean is not None else None
    return {
        "admissibility": admissibility,
        "profile_status": profile_status,
        "score_eligible": score_eligible,
        "cfd_ld_geomean": official_geomean,
        "cfd_ld_worst": official_worst,
        "n_conditions": len(conditions),
        "n_valid": sum(value == "valid" for value in condition_classes),
        "n_invalid": sum(value == "invalid" for value in condition_classes),
        "n_inconclusive": sum(value == "inconclusive" for value in condition_classes),
        "diagnostic_partial_metrics": {
            "n_valid": len(diagnostic_lds),
            "cfd_ld_geomean": partial_geomean,
            "cfd_ld_worst": min(diagnostic_lds) if diagnostic_lds else None,
        },
    }
