"""Run a bounded, real baseline episode prefix with separately measured CFD."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import traceback

from benchmark.common import load_tasks, load_box
from benchmark.container_evaluator import ContainerEvaluator
from benchmark.resource_logging import ResourceLog
from benchmark.run_episode import EpisodeRunner
from benchmark.surrogate_local import LocalSurrogate
from wingbench.optimizers.surrogate_assisted import FSD_METHOD, CONVENTIONAL_METHOD


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--method", choices=(FSD_METHOD, CONVENTIONAL_METHOD), required=True)
    p.add_argument("--task", choices=("low", "nominal", "high"), required=True)
    p.add_argument("--seed", type=int, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--episode-id", required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--entry-script", type=Path, required=True)
    p.add_argument("--model-dir", type=Path, required=True)
    p.add_argument("--flogen-root", type=Path, required=True)
    p.add_argument("--checkpoint", type=int, default=4, choices=(0, 1, 2, 4))
    p.add_argument("--evidence-kind", choices=("instrumentation_pilot", "nonagent_campaign"),
                   default="instrumentation_pilot")
    a = p.parse_args()
    episode = a.out.resolve() / a.episode_id
    episode.mkdir(parents=True, exist_ok=False)
    log = ResourceLog(episode / "resources.jsonl", a.episode_id)
    log.event("episode_setup_start", evaluator_kind="cfd", surrogate_device="cpu",
              cuda_device_nodes_present=bool(list(Path("/dev").glob("nvidia[0-9]*"))),
              execution_mode="separate_pinned_CFD_container", evidence_kind=a.evidence_kind)
    task = load_tasks()[a.task]
    with log.span("surrogate_initialization"):
        surrogate = LocalSurrogate(task, a.model_dir, flogen_root=a.flogen_root, device="cpu")
    log.event("surrogate_device_verified", device=str(surrogate.evaluator.device))
    assert str(surrogate.evaluator.device) == "cpu"
    evaluator = ContainerEvaluator(task, episode / "cfd", image=a.image,
                                   entry_script=a.entry_script, run_id=a.episode_id)
    log.event("episode_setup_end")
    runner = EpisodeRunner(task=task, box=load_box(), seed=a.seed,
                           episode_id=a.episode_id, episode_dir=episode,
                           evaluator=evaluator, evaluator_kind="cfd", surrogate=surrogate,
                           optimizer_spec="benchmark.examples.frozen_baseline:FrozenBaseline",
                           optimizer_options={"method_id": a.method}, method_name=None,
                           stop_after_checkpoint=a.checkpoint)
    try:
        summary = runner.run()
        print(json.dumps({k: summary[k] for k in ("status", "online_queries", "private_grade_count")}), flush=True)
    except Exception as exc:
        traceback.print_exc()
        log.event("pilot_error", exception_type=type(exc).__name__)
        if runner.machine is not None:
            runner.finish(status="error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if runner.client is not None:
            runner.client.close()


if __name__ == "__main__":
    main()
