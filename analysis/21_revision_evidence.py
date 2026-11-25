#!/usr/bin/env python3
"""Reproduce revision diagnostics from frozen extracts; no simulator/model calls.

The mesh checks cover selected source controls and the complete recoverable
failed-geometry census, not unconditional failure prevalence among arbitrary
wings. Agent transitions are descriptive and are not independent
replicates or a causal comparison of prompting methods.
"""
from pathlib import Path
import hashlib
import json

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
OUT = ROOT / "analysis/out/revision_evidence"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    provenance = json.loads((DATA / "revision_evidence_provenance.json").read_text())
    for name, expected in provenance["extract_sha256"].items():
        assert hashlib.sha256((DATA / name).read_bytes()).hexdigest() == expected, name

    handoffs = pd.read_csv(DATA / "agent_handoffs.csv")
    evaluations = pd.read_csv(DATA / "evaluations.csv")
    joined = handoffs.merge(evaluations[["evaluation", "wing", "delivered", "ld"]],
                            on="evaluation", suffixes=("", "_grade"), validate="one_to_one")
    assert (joined.wing == joined.wing_grade).all()
    assert (joined.delivered == joined.delivered_grade).all()
    assert ((joined.ld == joined.ld_grade) | (joined.ld.isna() & joined.ld_grade.isna())).all()
    common = handoffs[handoffs["query"] <= 4].sort_values(["episode", "query"])
    assert len(common) == 360 and len(common.episode.unique()) == 90
    assert (common.groupby("episode")["query"].apply(list).apply(lambda q: q == [1, 2, 3, 4])).all()
    transitions = []
    for episode, rows in common.groupby("episode", sort=True):
        records = rows.to_dict("records")
        for previous, following in zip(records, records[1:]):
            transitions.append(dict(episode=episode, method=previous["method"], task=previous["task"],
                                    from_query=previous["query"], to_query=following["query"],
                                    previous_failed=not previous["delivered"],
                                    changed_geometry=previous["wing"] != following["wing"],
                                    next_valid=following["delivered"]))
    transitions = pd.DataFrame(transitions)
    assert len(transitions) == 270
    transitions.to_csv(OUT / "agent_transitions.csv", index=False)
    failed = transitions[transitions.previous_failed]
    agent_summary = failed.groupby("method").agg(
        transitions=("episode", "size"), episodes=("episode", "nunique"),
        changed_geometry=("changed_geometry", "sum"), next_valid=("next_valid", "sum"))
    assert agent_summary.loc["Open-ended research"].tolist() == [27, 21, 27, 19]
    assert agent_summary.loc["Planning"].tolist() == [20, 14, 18, 10]
    assert agent_summary.loc["Reflection"].tolist() == [17, 15, 17, 15]
    agent_summary.to_csv(OUT / "agent_transition_summary.csv")
    planning = common[common.method == "Planning"]
    placeholders = planning[planning.handoff_rationale.str.contains("placeholder", case=False, regex=False)]
    assert len(placeholders) == 20 and placeholders.episode.nunique() == 7
    placeholders.to_csv(OUT / "explicit_placeholder_mentions.csv", index=False)
    recoveries = pd.read_csv(DATA / "agent_recoveries.csv")
    recovery_q4 = recoveries[recoveries["query"] <= 4]
    assert len(recovery_q4) == 10 and recovery_q4.episode.nunique() == 9

    flow = pd.read_csv(DATA / "accepted_mesh_flow_audit.csv")
    assert len(flow) == 11535 and flow.evaluation.nunique() == 3845
    assert not flow.duplicated(["evaluation", "mach"]).any()
    assert (flow.groupby("evaluation").size() == 3).all()
    flow["failed"] = flow.outcome != "valid"
    by_mach = flow.groupby("mach").agg(total=("failed", "size"), failed=("failed", "sum"))
    by_mach["failure_rate"] = by_mach.failed / by_mach.total
    assert by_mach.failed.tolist() == [742, 765, 896]
    by_mach.to_csv(OUT / "flow_by_mach.csv")
    failed_evaluations = int(flow.groupby("evaluation").failed.any().sum())
    assert failed_evaluations == 1226
    failure_reasons = flow[flow.failed].reasons.value_counts().to_dict()
    assert failure_reasons == {"inconclusive_timeout": 1222, "inconclusive_infrastructure": 1181}

    mesh = pd.read_csv(DATA / "mesh_validation_checks.csv")
    assert len(mesh) == 5826
    mesh_summary = mesh.groupby(["cohort", "profile"]).agg(
        cases=("case", "size"), accepted=("accepted", "sum"))
    mesh_summary.to_csv(OUT / "mesh_cohorts.csv")
    source = mesh[mesh.cohort.str.startswith("source-")]
    assert source.wing.nunique() == 1000
    assert source.groupby("profile").accepted.sum().to_dict() == {
        "benchmark-spacing": 999, "original-spacing": 1000}
    census = mesh[mesh.cohort == "previously-failed"]
    paired = census.pivot(index="case", columns="profile", values="accepted")
    assert len(paired) == 1913
    assert census.wing.nunique() == 1913
    declared_failed = set(evaluations.loc[
        evaluations.reasons.fillna("").str.contains("mesh"), "wing"].dropna())
    assert declared_failed.issubset(set(census.wing))
    assert len(provenance["failed_mesh_requests_missing_geometry"]) == 1
    assert int(paired["upstream-default"].sum()) == 1
    assert int(paired["upstream-layers129"].sum()) == 605
    recovered = int((~paired["upstream-default"] & paired["upstream-layers129"]).sum())
    assert recovered == 604
    summary = {
        "agent": {"episodes": 90, "common_handoffs": 360, "adjacent_pairs": 270,
                  "after_failure": agent_summary.reset_index().to_dict("records"),
                  "explicit_placeholder_handoffs": len(placeholders),
                  "explicit_placeholder_episodes": int(placeholders.episode.nunique()),
                  "successful_recovery_decisions_q4": len(recovery_q4),
                  "successful_recovery_episodes_q4": int(recovery_q4.episode.nunique()),
                  "recovery_episodes_q4_by_method": recovery_q4.groupby("method").episode.nunique().to_dict()},
        "flow": {"evaluations": 3845, "conditions": 11535, "failed_conditions": int(flow.failed.sum()),
                 "pooled_failure_rate": float(flow.failed.mean()), "failed_evaluations": failed_evaluations,
                 "joint_failure_rate": failed_evaluations / 3845,
                 "by_mach": by_mach.reset_index().to_dict("records"),
                 "failure_reasons": failure_reasons,
                 "failure_outcomes": flow[flow.failed].outcome.value_counts().to_dict()},
        "mesh": {"cohorts": mesh_summary.reset_index().to_dict("records"),
                 "paired_default_fail_to_129_pass": recovered},
        "limitations": ["Request-level flow records include reused outcomes.",
                        "The failed-geometry census covers all 1913 recoverable geometries; one additional failure request lacks an archived geometry. Source controls are selected cohorts.",
                        "Agent geometry changes do not by themselves demonstrate feedback-based reasoning.",
                        "Transitions are correlated within episodes and condition on different failures."]}
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print("Verified 360 common agent handoffs, 270 transitions, 11535 flow conditions and 5826 mesh checks.")
    print("Flow failures: 2403/11535 conditions; 1226/3845 evaluations. Mesh: 999/1000, 1000/1000, 1/1913, 605/1913.")


if __name__ == "__main__":
    main()
