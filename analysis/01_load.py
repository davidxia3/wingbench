#!/usr/bin/env python3
"""01_load.py — build the working tables for the baseline analysis and check them.

Reads:   data/episodes.csv, data/evaluations.csv, data/recommendations.csv
Writes:  analysis/out/grades.csv        one row per evaluation of the four surrogate-assisted baselines
         analysis/out/episodes.csv      one row per episode
         analysis/out/curves.csv        the recommendation graded at each checkpoint
         analysis/out/discovery.csv     best delivered L/D found online after each query
         analysis/out/replay_meta.csv   per-episode private grades used and CFD cost
         analysis/out/verification.json counts and checks
         analysis/out/load_meta.json    data version stamped on every output

Problems are reported, never repaired.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from common import (
    DATA_VERSION,
    HIDDEN_CHECKPOINTS,
    METHOD_ID,
    ONLINE_MAX,
    OUT,
    TASK_ID,
    ensure_dirs,
    read_table,
    surrogate_evaluations,
)


def main() -> int:
    ensure_dirs()
    problems: dict[str, list] = {}

    def check(ok: bool, name: str, detail) -> None:
        if not ok:
            problems.setdefault(name, []).append(detail)

    ep = read_table("episodes")
    ep = ep[(ep.study == "baseline") & (ep.method != "SMAC3")].copy()
    ep["episode_index"] = ep.episode.str[1:].astype(int)
    ep["config"] = ep.task.map(TASK_ID)
    ep["method"] = ep.method.map(METHOD_ID)
    ids = ep.set_index("episode_index")[["config", "method", "seed"]]

    grades = surrogate_evaluations()
    check(len(ep) == 120, "episode_count", len(ep))
    for idx, g in grades.groupby("episode_index"):
        online = sorted(g[g.stream == "online"].checkpoint)
        hidden = list(g[g.stream == "hidden"].checkpoint)
        check(online == list(range(1, ONLINE_MAX + 1)), "online_sequence", f"B{idx:03d}")
        check(set(hidden) <= set(HIDDEN_CHECKPOINTS) and len(set(hidden)) == len(hidden),
              "private_checkpoints", f"B{idx:03d}")
    valued = grades.conditional_value.notna()
    check(bool((valued == grades.delivery).all()), "value_without_delivery", "")
    check(bool((grades.delivery == (grades.outcome_kind == "pass")).all()), "delivery_vs_outcome", "")

    rc = read_table("recommendations")
    rc = rc[(rc.study == "baseline") & (rc.method != "SMAC3")]
    curves = pd.DataFrame({
        "episode_index": rc.episode.str[1:].astype(int),
        "config": rc.task.map(TASK_ID),
        "method": rc.method.map(METHOD_ID),
        "seed": rc.seed,
        "checkpoint": rc.checkpoint,
        "conditional_value": rc.ld,
        "delivery": rc.delivered.astype(bool),
        "provenance": rc.graded_by.map({"private": "fresh_hidden", "online": "reused_online_visible"}),
        "geometry_sha256": rc.wing,
    }).sort_values(["episode_index", "checkpoint"]).reset_index(drop=True)
    check(len(curves) == 120 * len(HIDDEN_CHECKPOINTS), "curve_count", len(curves))
    fresh = curves[curves.provenance == "fresh_hidden"].set_index(["episode_index", "checkpoint"])
    hidden = grades[grades.stream == "hidden"].set_index(["episode_index", "checkpoint"])
    check(fresh.index.equals(hidden.index.sort_values()), "private_grades_vs_curve", "")

    # best delivered L/D found online after each query
    rows = []
    for idx, g in grades[grades.stream == "online"].groupby("episode_index"):
        best = None
        for r in g.sort_values("checkpoint").itertuples():
            if r.delivery and (best is None or r.conditional_value > best):
                best = r.conditional_value
            rows.append({"episode_index": idx, **ids.loc[idx].to_dict(), "query": r.checkpoint,
                         "best_conditional_value": best, "at_least_one_delivery": best is not None})
    discovery = pd.DataFrame(rows)

    n_private = grades[grades.stream == "hidden"].groupby("episode_index").size()
    replay_meta = pd.DataFrame({
        "episode_index": ep.episode_index, "config": ep.config, "method": ep.method, "seed": ep.seed,
        "fresh_checkpoint_grades_used": ep.episode_index.map(n_private).fillna(0).astype(int),
        "evaluator_cpu_seconds": ep.cfd_core_seconds,
    }).sort_values("episode_index").reset_index(drop=True)

    episodes = ep[["episode_index", "config", "method", "seed"]].assign(status_terminal=True) \
        .sort_values("episode_index").reset_index(drop=True)

    vals = grades.conditional_value.dropna()
    verification = {
        "data_version": DATA_VERSION,
        "n_episodes": int(len(episodes)),
        "n_grade_records": int(len(grades)),
        "n_online": int((grades.stream == "online").sum()),
        "n_hidden": int((grades.stream == "hidden").sum()),
        "n_null_value": int(grades.conditional_value.isna().sum()),
        "outcome_kinds": {str(k): int(v) for k, v in grades.outcome_kind.value_counts().sort_index().items()},
        "conditional_value_range": [float(vals.min()), float(vals.max())],
        "problems": problems,
    }

    grades.to_csv(OUT / "grades.csv", index=False)
    episodes.to_csv(OUT / "episodes.csv", index=False)
    curves.to_csv(OUT / "curves.csv", index=False)
    discovery.to_csv(OUT / "discovery.csv", index=False)
    replay_meta.to_csv(OUT / "replay_meta.csv", index=False)
    (OUT / "verification.json").write_text(json.dumps(verification, indent=1))
    (OUT / "load_meta.json").write_text(json.dumps({
        "serial": DATA_VERSION["serial"],
        "snapshot_serial": DATA_VERSION["serial"],
        "refreshed_at_utc": DATA_VERSION["refreshed_at_utc"],
        "campaign_complete": DATA_VERSION["campaign_complete"],
        "episodes_terminal": int(len(episodes)),
        "n_grade_records": int(len(grades)),
    }, indent=1))

    print(f"episodes: {len(episodes)}   grades: {len(grades)} "
          f"({verification['n_online']} online, {verification['n_hidden']} private, "
          f"{verification['n_null_value']} without a score)")
    print(f"curves: {len(curves)} recommendation points, {len(discovery)} discovery points")
    print(f"outcome kinds: {verification['outcome_kinds']}")
    if problems:
        print("PROBLEMS:", {k: len(v) for k, v in problems.items()})
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
