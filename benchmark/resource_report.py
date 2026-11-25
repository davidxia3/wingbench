"""Audit resource sidecars. Diagnostic/replay evidence cannot qualify live runs."""
from __future__ import annotations
import argparse
import json
from pathlib import Path


def read_rows(paths):
    rows = []
    for path in paths:
        for number, line in enumerate(Path(path).read_text().splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except ValueError as exc:
                    raise ValueError(f"incomplete JSON event in {path.name}:{number}") from exc
    return rows


def model_summary(rows):
    starts = [r for r in rows if r.get("kind") == "model_request_start"]
    ends = [r for r in rows if r.get("kind") == "model_request_end"]
    start_ids, end_ids = [r["request_id"] for r in starts], [r["request_id"] for r in ends]
    issues = []
    if len(set(start_ids)) != len(start_ids) or len(set(end_ids)) != len(end_ids):
        issues.append("duplicate_model_request_events")
    if set(start_ids) != set(end_ids):
        issues.append("unclosed_or_orphaned_model_requests")
    fields = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens")
    usage = {}
    for field in fields:
        known = [(r.get("usage") or {}).get(field) for r in ends]
        available = [v for v in known if v is not None]
        usage[field] = {"known_total": sum(available) if available else None,
                        "requests_with_value": len(available), "requests_missing_value": len(ends)-len(available)}
    responses = [r for r in ends if r["status"] == "response"]
    missing_responses = sum(not r["usage_available"] for r in responses)
    if missing_responses:
        issues.append("successful_responses_missing_input_or_output_tokens")
    return {"request_attempts": len(starts), "responses": len(responses),
            "failed_attempts": sum(r["status"] == "error" for r in ends),
            "summed_request_seconds": sum(r["wall_seconds"] for r in ends),
            "tokens": usage, "issues": issues,
            "server_compute": None, "server_compute_status": "not_observable_from_client"}


def audit(episode, model_logs=(), cgroup_logs=(), require_model=False, cfd_logs=()):
    episode = Path(episode)
    summary = json.loads((episode / "summary.json").read_text())
    rows = read_rows([episode / "resources.jsonl"])
    issues = []
    starts = [r["span_id"] for r in rows if r["kind"] == "span_start"]
    ends = [r["span_id"] for r in rows if r["kind"] == "span_end"]
    if set(starts) != set(ends) or len(set(starts)) != len(starts) or len(set(ends)) != len(ends):
        issues.append("incomplete_or_duplicate_stage_spans")
    initial = next((r for r in rows if r["kind"] == "episode_setup_start"), None)
    nominations = [r for r in rows if r["kind"] == "checkpoint_nomination"]
    grades = [r for r in rows if r["kind"] == "checkpoint_grade_ready"]
    if not initial or not nominations or not grades:
        issues.append("missing_endpoint_timestamps")
    expected_checkpoints = [q for q in (0, 1, 2, 4, 8, 16, 32) if q <= summary["online_queries"]]
    if [r["checkpoint"] for r in nominations] != expected_checkpoints or [r["checkpoint"] for r in grades] != expected_checkpoints:
        issues.append("missing_or_duplicate_checkpoint_endpoints")
    cpu = [r["measurement"] for r in rows if r["kind"] == "optimizer_cpu"]
    decisions = [r for r in rows if r["kind"] == "span_end" and r["stage"] == "decision"]
    if not cpu or len(cpu) != len(decisions) or any(r is None for r in cpu):
        issues.append("missing_optimizer_cpu_measurement")
    surrogate = [r for r in rows if r["kind"] == "span_end" and r["stage"] == "surrogate"]
    charges = sum(r["charged_candidates"] for r in surrogate)
    if charges != summary["surrogate"]["evaluations"]:
        issues.append("surrogate_budget_reconciliation_failed")
    model = model_summary(read_rows(model_logs))
    issues.extend(model["issues"])
    if require_model and not model["request_attempts"]:
        issues.append("missing_live_model_requests")
    cgroups = read_rows(cgroup_logs)
    group_starts = [r for r in cgroups if r.get("kind") == "command_start"]
    group_ends = [r for r in cgroups if r.get("kind") == "command_end"]
    start_groups = [r["group_id"] for r in group_starts]
    end_groups = [r["group_id"] for r in group_ends]
    if set(start_groups) != set(end_groups) or len(set(start_groups)) != len(start_groups) or len(set(end_groups)) != len(end_groups):
        issues.append("incomplete_or_duplicate_cgroup_measurements")
    if any(r.get("episode") != summary["episode"] for r in cgroups):
        issues.append("cgroup_measurement_episode_identity_missing_or_mismatched")
    complete_cgroups = [r for r in cgroups if r.get("kind") == "command_end" and r.get("accounting_complete")]
    if not complete_cgroups or len(complete_cgroups) != len(group_ends):
        issues.append("exclusive_cgroup_process_tree_cpu_not_validated")
    if any(r.get("returncode") != 0 or r.get("timed_out") for r in complete_cgroups):
        issues.append("measured_command_failed_or_timed_out")
    if summary["evaluator"] != "cfd":
        issues.append("recorded_evaluator_is_not_live_CFD_validation")
    if summary["status"] not in ("complete", "checkpoint_complete"):
        issues.append("episode_did_not_reach_requested_endpoint")
    if initial is None or initial.get("surrogate_device") != "cpu" or initial.get("cuda_device_nodes_present"):
        issues.append("CPU_only_device_configuration_not_verified")
    if initial and initial.get("execution_mode") == "separate_pinned_CFD_container":
        expected_grades = sum(r["kind"] == "span_end" and r["stage"] == "evaluation" for r in rows)
        if sum(r["stage"] == "CFD-container" for r in group_starts) != expected_grades:
            issues.append("CFD_container_CPU_measurement_count_mismatch")
        if sum(r["stage"] == "baseline-controller" for r in group_starts) != 1:
            issues.append("baseline_controller_CPU_scope_missing_or_duplicated")
    cfd_paths = list(cfd_logs)
    default_cfd_log = episode / "cfd" / "cfd_resources.jsonl"
    if not cfd_paths and default_cfd_log.exists():
        cfd_paths.append(default_cfd_log)
    cfd_rows = read_rows(cfd_paths)
    cfd_starts = [r["span_id"] for r in cfd_rows if r["kind"] == "span_start"]
    cfd_ends = [r for r in cfd_rows if r["kind"] == "span_end"]
    if summary["evaluator"] == "cfd" and (not cfd_starts or set(cfd_starts) != {r["span_id"] for r in cfd_ends}):
        issues.append("missing_or_incomplete_CFD_attempt_measurements")
    stage_times = {}
    for row in rows:
        if row["kind"] == "span_end":
            stage_times[row["stage"]] = stage_times.get(row["stage"], 0) + row["wall_seconds"]
    elapsed = lambda row: (row["monotonic_ns"] - initial["monotonic_ns"]) / 1e9 if initial else None
    if initial and any(r.get("clock_id") != initial.get("clock_id") for r in nominations + grades):
        issues.append("endpoint_monotonic_clock_mismatch")
    return {"schema": "wingbench-resource-audit-v1", "episode": summary["episode"],
            "live_validation_passed": not issues, "issues": issues,
            "checkpoint": summary["online_queries"], "original_online_budget": summary["original_online_budget"],
            "endpoints": [{"checkpoint": r["checkpoint"], "elapsed_to_nomination_seconds": elapsed(r),
                           "elapsed_to_grade_seconds": next((elapsed(g) for g in grades if g["checkpoint"]==r["checkpoint"]), None)}
                          for r in nominations],
            "stage_wall_seconds": stage_times,
            "stage_time_note": "surrogate spans are nested inside decision spans; do not sum stages for elapsed time",
            "optimizer_cpu_seconds": sum(sum(r["cpu"].values()) for r in cpu if r is not None),
            "optimizer_cpu_scope": "self_and_reaped_children_only; consult exclusive cgroup for all descendants",
            "surrogate_charged_candidates": charges, "surrogate_tool_calls": len(surrogate),
            "local_device_record": {k: initial.get(k) for k in ("surrogate_device", "cuda_device_nodes_present")} if initial else None,
            "model": model, "exclusive_cgroup_records": complete_cgroups,
            "cfd_attempts": [{k: r.get(k) for k in ("stage", "wall_seconds", "cpu", "cpu_scope", "process_record")} for r in cfd_ends],
            "flow_execution_observed": any(r["stage"] == "flow" for r in cfd_ends),
            "recommendations": summary["checkpoints"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("episode", type=Path)
    p.add_argument("--model-log", type=Path, action="append", default=[])
    p.add_argument("--cgroup-log", type=Path, action="append", default=[])
    p.add_argument("--cfd-log", type=Path, action="append", default=[])
    p.add_argument("--require-model", action="store_true")
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    report = audit(a.episode, a.model_log, a.cgroup_log, a.require_model, a.cfd_log)
    a.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({k: report[k] for k in ("live_validation_passed", "issues")}))
    return 0 if report["live_validation_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
