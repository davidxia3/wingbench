#!/usr/bin/env python3
"""Freeze local rebuttal audits and agent handoffs as portable evidence tables.

Reads existing archives only. No CFD, surrogate or model calls are made.
The original archives are not distributed; analysis/21_revision_evidence.py
reproduces the reported summaries from these extracted tables.
"""
from __future__ import annotations
import argparse
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def dump_csv(name, rows):
    with (ROOT / "data" / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def main(archive):
    sources = {}

    def read(path, base=ROOT):
        blob = path.read_bytes()
        sources[str(path.relative_to(base))] = hashlib.sha256(blob).hexdigest()
        return json.loads(blob)

    eps = list(csv.DictReader((ROOT / "data/episodes.csv").open()))
    ev = {(r["episode"], int(float(r["query"]))): r
          for r in csv.DictReader((ROOT / "data/evaluations.csv").open()) if r["kind"] == "online"}
    private = archive / "data/host_pull_2026-09-25/extracted_private/private"
    handoffs = []
    for ep in eps:
        if ep["study"] != "agent":
            continue
        horizon = 8 if ep["episode"] in {"A076", "A080"} else 4
        for q in range(1, horizon + 1):
            path = private / ep["original_id"] / "handoffs" / f"q{q:02d}.json"
            h = read(path, archive)
            grade = ev[(ep["episode"], q)]
            assert grade["wing"] == h["proposed"]["geometry_sha256"]
            reason = h.get("reason", "")
            # Only the public decision rationale is exported, not full reasoning streams.
            assert "/Users/" not in reason and "api_key" not in reason.lower()
            handoffs.append(dict(episode=ep["episode"], method=ep["method"], task=ep["task"], query=q,
                                 evaluation=grade["evaluation"], wing=grade["wing"], delivered=grade["delivered"],
                                 ld=grade["ld"], reasons=grade["reasons"], handoff_rationale=reason,
                                 source_sha256=sources[str(path.relative_to(archive))]))
    assert len(handoffs) == 368
    dump_csv("agent_handoffs.csv", handoffs)

    flow = []
    inventory_path = ROOT / "rebuttal/mesh_forensics_v1/inventory.jsonl"
    blob = inventory_path.read_bytes()
    sources[str(inventory_path.relative_to(ROOT))] = hashlib.sha256(blob).hexdigest()
    for line in blob.splitlines():
        entry = json.loads(line)
        if entry.get("mesh_outcome") not in {"valid", "marginal"}:
            continue
        raw = Path(entry["result_source"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry["result_sha256"]
        result = json.loads(raw)
        conditions = sorted(result["conditions"], key=lambda c: c["mach"])
        assert [c["mach"] for c in conditions] == [0.7853, 0.8195, 0.853]
        for c in conditions:
            flow.append(dict(evaluation=entry["evaluation"], episode=entry["episode"], kind=entry["kind"],
                             wing=entry["wing"], mach=c["mach"], outcome=c["outcome_class"],
                             reasons=";".join(c.get("reason_codes", [])), result_sha256=entry["result_sha256"]))
    assert len(flow) == 3845 * 3
    dump_csv("accepted_mesh_flow_audit.csv", flow)
    flow_summary = read(ROOT / "rebuttal/three_condition_failure_audit.json")

    mesh = []
    for cohort in (1, 2):
        summary = read(ROOT / f"rebuttal/source_control_expansion_v{cohort}/summary.json")
        for c in summary["cases"]:
            if c["analysis_group"] != "primary":
                continue
            for profile in ["benchmark-spacing", "original-spacing"]:
                m = c[profile]
                mesh.append(dict(cohort=f"source-{cohort}", case=c["case_id"], wing=c["wing"],
                                 source_running_id=c["source_running_id"], random_rank="", profile=profile,
                                 outcome=m["classification"], accepted=m["accepted"],
                                 evidence_sha256=m["evidence_sha256"]))
    combined = read(ROOT / "rebuttal/source_control_expansion_v2/combined_summary.json")
    census = read(ROOT / "rebuttal/mesh_forensics_v1/randomized_census_20261107T190602Z/random_sample_status.json")
    order = read(ROOT / "rebuttal/mesh_forensics_v1/randomized_census_20261107T190602Z/random_order.json")
    manifest = read(ROOT / "rebuttal/mesh_forensics_v1/reproduction_manifest.json")
    # Freeze the completed census, including every recoverable failed geometry.
    selected = census["cases"]
    assert census["population"] == order["population"] == len(selected) == 1913
    assert all(c["upstream"] and c["layers129"] for c in selected)
    expected_wings = {c["wing"] for c in manifest["cases"]
                      if c["group"] == "historical_mesh_failure"}
    assert {c["wing"] for c in selected} == expected_wings
    assert {c["random_rank"] for c in selected} == set(range(1, 1914))
    for c in selected:
        for profile, field in [("upstream-default", "upstream"), ("upstream-layers129", "layers129")]:
            mesh.append(dict(cohort="previously-failed", case=c["case_id"], wing=c["wing"],
                             source_running_id="", random_rank=c["random_rank"], profile=profile,
                             outcome=c[field], accepted=c[field] in {"valid", "marginal"}, evidence_sha256=""))
    assert len(mesh) == 5826
    dump_csv("mesh_validation_checks.csv", mesh)
    recovery = read(ROOT / "rebuttal/audit.json")
    public_recoveries = []
    by_original = {e["original_id"]: e for e in eps}
    for r in recovery["recovery_success_records"]:
        ep = by_original[r["original_id"]]
        public_recoveries.append(dict(episode=ep["episode"], method=ep["method"], task=ep["task"],
                                     query=int(r["decision"].split("q")[-1]), mode=r["mode"], version=r["version"]))
    dump_csv("agent_recoveries.csv", public_recoveries)
    metadata = {
        "date": "2026-11-10", "sources_sha256": sources,
        "flow_scope": flow_summary["scope"], "flow_expected_counts": flow_summary["condition_outcomes"],
        "source_mesh_scope": combined["scope"],
        "failed_mesh_scope": "Complete census of all 1913 distinct failed geometries recoverable from released request categories or raw evaluation records (all 30 prespecified batches). One additional mesh-failure request lacks an archived geometry and cannot be rerun. This is not an unconditional failure-rate sample of arbitrary wings.",
        "failed_mesh_requests_missing_geometry": manifest["requests_missing_geometry"],
        "upstream_meshing_script_sha256": manifest["upstream_script_sha256"],
        "failed_mesh_snapshot_utc": census["updated_at_utc"],
        "failed_mesh_order_algorithm": order["algorithm"],
        "failed_mesh_reporting_rule": census["reporting_rule"],
        "agent_handoff_scope": "All 360 first-four-query handoffs in 90 episodes, plus q5-q8 in two selected open-ended examples; rationales are agent statements, not independently verified diagnoses.",
        "recovery_scope": "Successful bounded-recovery outcomes from the archived audit; retains every episode in the main results.",
        "extract_sha256": {name: hashlib.sha256((ROOT / "data" / name).read_bytes()).hexdigest()
                           for name in ["agent_handoffs.csv", "accepted_mesh_flow_audit.csv", "mesh_validation_checks.csv", "agent_recoveries.csv"]},
    }
    (ROOT / "data/revision_evidence_provenance.json").write_text(json.dumps(metadata, indent=2) + "\n")
    print(f"Exported {len(handoffs)} handoffs, {len(flow)} condition records, {len(mesh)} mesh checks and {len(public_recoveries)} recoveries.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", type=Path, required=True)
    main(parser.parse_args().archive_root)
