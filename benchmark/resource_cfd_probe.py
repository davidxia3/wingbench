"""Run a preselected real CFD case to qualify resource instrumentation only."""
import argparse
import json
from pathlib import Path

from benchmark.common import load_tasks, json_ready
from benchmark.evaluators import CfdEvaluator
from benchmark.resource_logging import ResourceLog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--index", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    case = json.loads(a.manifest.read_text())["cases"][a.index]
    root = a.out / case["case_id"]
    root.mkdir(parents=True, exist_ok=False)
    log = ResourceLog(root / "probe_resources.jsonl", case["case_id"])
    log.event("probe_start", evidence_kind="instrumentation_only", case=case,
              local_gpu="none; dedicated CPU-only worker", model_calls=0)
    with log.span("evaluator_initialization"):
        evaluator = CfdEvaluator(load_tasks()[case["task"]], root / "cfd", run_id=case["case_id"])
    with log.span("cfd_grade"):
        result = evaluator.grade(case["geometry"], case["case_id"])
    assert result.wing == case["wing"]
    record = {"evidence_kind": "live_CFD_instrumentation_probe_not_search_episode",
              "wing": result.wing, "delivered": result.delivered, "ld": result.ld,
              "outcome": result.outcome, "reason_codes": result.reason_codes,
              "detail": result.detail, "runtime": evaluator.runtime_status}
    (root / "result.json").write_text(json.dumps(json_ready(record), indent=2) + "\n")
    log.event("probe_end", delivered=result.delivered, outcome=result.outcome)
    print(json.dumps({"case": case["case_id"], "delivered": result.delivered, "outcome": result.outcome}), flush=True)


if __name__ == "__main__":
    main()
