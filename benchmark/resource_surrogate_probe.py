"""Real surrogate instrumentation probe; not a search episode."""
import argparse
import json
from pathlib import Path
from benchmark.common import load_tasks
from benchmark.surrogate_local import LocalSurrogate, CheapCounter
from benchmark.resource_logging import ResourceLog


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--flogen-root", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    a.out.mkdir(parents=True, exist_ok=False)
    log = ResourceLog(a.out / "surrogate.jsonl")
    case = json.loads(a.manifest.read_text())["cases"][0]
    counter = CheapCounter(4096)
    with log.span("model_initialization"):
        surrogate = LocalSurrogate(load_tasks()["nominal"],a.model_dir,flogen_root=a.flogen_root,device="cpu")
    # Two equal designs deliberately test baseline charging of repeated inputs.
    rows = [case["geometry"],case["geometry"]]
    counter.charge(len(rows))
    with log.span("surrogate", requested_candidates=len(rows), charged_candidates=len(rows), cache_hits=0):
        results = surrogate.predict(rows)
    assert counter.used == surrogate.evaluations == len(results) == 2
    assert str(surrogate.evaluator.device) == "cpu"
    result = {"evidence_kind":"real_surrogate_instrumentation_probe_not_search_episode",
              "charged_candidates":counter.used,"executed_candidates":surrogate.evaluations,
              "surrogate_wall_seconds":surrogate.seconds,"device":str(surrogate.evaluator.device),
              "model_sha_checks":"passed_in_LocalSurrogate_constructor",
              "statuses":[r.status for r in results],"scores":[r.score for r in results]}
    (a.out / "result.json").write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)


if __name__ == '__main__':
    main()
