#!/usr/bin/env python3
"""Compare a submission with the six reference methods.

    python3.11 benchmark/score.py submissions/mine --out submissions/mine/score

For each task and each checkpoint the table shows the recommendation delivery
rate and the mean L/D of the delivered recommendations, for the submission and
for the six reference methods of the released records. A second table shows
the online delivery rate, the share of online CFD queries that returned a
score. The submission's means carry 95 percent bootstrap intervals from
10,000 resamples of its episodes. The reference numbers are point estimates.

Every episode folder is first checked with ``benchmark/verify.py``. Episodes
that fail the check are left out and reported.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark.common import (  # noqa: E402
    CHECKPOINTS,
    DATA,
    REFERENCE_METHODS,
    TASK_NAMES,
    load_episode_protocol,
    load_tasks,
)
from benchmark.verify import find_episode_dirs, verify_episode  # noqa: E402

RESAMPLES = 10_000


def read_records(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, float_precision="round_trip", dtype={"wing": str, "method": str, "task": str})


def load_submission(folders: Sequence[Path]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    evaluations = [read_records(folder / "evaluations.csv") for folder in folders]
    recommendations = [read_records(folder / "recommendations.csv") for folder in folders]
    episodes = [read_records(folder / "episodes.csv") for folder in folders]
    return pd.concat(episodes), pd.concat(evaluations), pd.concat(recommendations)


def load_reference(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    evaluations = read_records(data_dir / "evaluations.csv")
    recommendations = read_records(data_dir / "recommendations.csv")
    keep = list(REFERENCE_METHODS)
    return evaluations[evaluations.method.isin(keep)], recommendations[recommendations.method.isin(keep)]


def summarize(rows: pd.DataFrame) -> dict[str, Any]:
    """Delivery rate and mean L/D of delivered rows. Mean is None when a delivered row has no L/D."""
    delivered = rows[rows.delivered.astype(bool)]
    values = delivered.ld.to_numpy(dtype=float)
    complete = len(delivered) == 0 or not np.isnan(values).any()
    return {
        "episodes": int(len(rows)),
        "delivered": int(len(delivered)),
        "delivery_rate": float(len(delivered)) / len(rows) if len(rows) else None,
        "mean_ld": float(np.mean(values)) if len(values) and complete else None,
        "median_ld": float(np.median(values)) if len(values) and complete else None,
        "ld_complete": bool(complete),
    }


def bootstrap_rate(flags: np.ndarray, rng: np.random.Generator, resamples: int) -> tuple[float, float]:
    if len(flags) == 0:
        return (float("nan"), float("nan"))
    draws = rng.choice(flags.astype(float), size=(resamples, len(flags)), replace=True).mean(axis=1)
    low, high = np.quantile(draws, [0.025, 0.975])
    return (float(low), float(high))


def bootstrap_conditional_mean(
    flags: np.ndarray, values: np.ndarray, rng: np.random.Generator, resamples: int
) -> tuple[float, float]:
    """Resample episodes, then average the delivered L/D in each resample."""
    if len(flags) == 0 or not flags.any():
        return (float("nan"), float("nan"))
    indices = rng.integers(0, len(flags), size=(resamples, len(flags)))
    picked_flags = flags[indices]
    picked_values = np.where(picked_flags, values[indices], 0.0)
    counts = picked_flags.sum(axis=1)
    keep = counts > 0
    means = picked_values[keep].sum(axis=1) / counts[keep]
    low, high = np.quantile(means, [0.025, 0.975])
    return (float(low), float(high))


def bootstrap_pooled_rate(
    delivered: np.ndarray, totals: np.ndarray, rng: np.random.Generator, resamples: int
) -> tuple[float, float]:
    """Resample episodes and recompute the pooled online delivery rate."""
    if len(totals) == 0:
        return (float("nan"), float("nan"))
    indices = rng.integers(0, len(totals), size=(resamples, len(totals)))
    rates = delivered[indices].sum(axis=1) / totals[indices].sum(axis=1)
    low, high = np.quantile(rates, [0.025, 0.975])
    return (float(low), float(high))


def recommendation_rows(
    submission: pd.DataFrame,
    reference: pd.DataFrame,
    submission_label: str,
    rng: np.random.Generator,
    resamples: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in TASK_NAMES:
        for checkpoint in CHECKPOINTS:
            here = submission[(submission.task == task) & (submission.checkpoint == checkpoint)]
            if len(here):
                stats = summarize(here)
                flags = here.delivered.astype(bool).to_numpy()
                values = here.ld.to_numpy(dtype=float)
                low, high = bootstrap_rate(flags, rng, resamples)
                stats.update({"delivery_ci_low": low, "delivery_ci_high": high})
                if stats["mean_ld"] is not None:
                    low, high = bootstrap_conditional_mean(flags, np.nan_to_num(values), rng, resamples)
                    stats.update({"mean_ld_ci_low": low, "mean_ld_ci_high": high})
                rows.append({"task": task, "checkpoint": checkpoint, "method": submission_label, "is_submission": True, **stats})
            for method in REFERENCE_METHODS:
                there = reference[(reference.task == task) & (reference.checkpoint == checkpoint) & (reference.method == method)]
                if len(there):
                    rows.append({"task": task, "checkpoint": checkpoint, "method": method, "is_submission": False, **summarize(there)})
    return rows


def online_rows(
    submission: pd.DataFrame,
    reference: pd.DataFrame,
    submission_label: str,
    rng: np.random.Generator,
    resamples: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for task in TASK_NAMES:
        here = submission[(submission.task == task) & (submission.kind == "online")]
        if len(here):
            per_episode = here.groupby("episode").delivered.agg(["sum", "size"])
            delivered = per_episode["sum"].to_numpy(dtype=float)
            totals = per_episode["size"].to_numpy(dtype=float)
            low, high = bootstrap_pooled_rate(delivered, totals, rng, resamples)
            rows.append({
                "task": task, "method": submission_label, "is_submission": True,
                "episodes": int(len(per_episode)), "online_queries": int(totals.sum()),
                "delivered": int(delivered.sum()), "delivery_rate": float(delivered.sum() / totals.sum()),
                "delivery_ci_low": low, "delivery_ci_high": high,
            })
        for method in REFERENCE_METHODS:
            there = reference[(reference.task == task) & (reference.kind == "online") & (reference.method == method)]
            if len(there):
                rows.append({
                    "task": task, "method": method, "is_submission": False,
                    "episodes": int(there.episode.nunique()), "online_queries": int(len(there)),
                    "delivered": int(there.delivered.astype(bool).sum()),
                    "delivery_rate": float(there.delivered.astype(bool).mean()),
                })
    return rows


def number(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "n/a"
    return f"{value:.{digits}f}"


def interval(row: dict[str, Any], prefix: str) -> str:
    low, high = row.get(f"{prefix}_ci_low"), row.get(f"{prefix}_ci_high")
    if low is None or high is None or np.isnan(low) or np.isnan(high):
        return ""
    return f"[{low:.2f}, {high:.2f}]"


def print_tables(recommendation: list[dict[str, Any]], online: list[dict[str, Any]], tasks: dict[str, Any]) -> None:
    width = max(len(row["method"]) for row in recommendation + online) if recommendation + online else 20
    width = max(width, 24)
    header = f"{'method':<{width}} {'n':>3} " + " ".join(f"{'ck' + str(ck):>13}" for ck in CHECKPOINTS)
    for task in TASK_NAMES:
        rows = [row for row in recommendation if row["task"] == task]
        if not rows:
            continue
        print()
        print(f"Task {task} (target C_L {tasks[task].target_cl}). Recommendation delivery rate by checkpoint.")
        print(header)
        methods = [row["method"] for row in rows if row["is_submission"]][:1] + list(REFERENCE_METHODS)
        for method in methods:
            cells = {row["checkpoint"]: row for row in rows if row["method"] == method}
            if not cells:
                continue
            n = max(row["episodes"] for row in cells.values())
            line = f"{method:<{width}} {n:>3} " + " ".join(
                f"{number(cells[ck]['delivery_rate']) if ck in cells else '':>13}" for ck in CHECKPOINTS
            )
            print(line)
            if any(row["is_submission"] for row in cells.values()):
                print(f"{'  95 percent interval':<{width}} {'':>3} " + " ".join(
                    f"{interval(cells[ck], 'delivery') if ck in cells else '':>13}" for ck in CHECKPOINTS
                ))
        print()
        print(f"Task {task}. Mean L/D of delivered recommendations by checkpoint.")
        print(header)
        for method in methods:
            cells = {row["checkpoint"]: row for row in rows if row["method"] == method}
            if not cells:
                continue
            n = max(row["episodes"] for row in cells.values())
            print(f"{method:<{width}} {n:>3} " + " ".join(
                f"{number(cells[ck]['mean_ld']) if ck in cells else '':>13}" for ck in CHECKPOINTS
            ))
            if any(row["is_submission"] for row in cells.values()):
                print(f"{'  95 percent interval':<{width}} {'':>3} " + " ".join(
                    f"{interval(cells[ck], 'mean_ld') if ck in cells else '':>13}" for ck in CHECKPOINTS
                ))
    print()
    print("Online delivery rate. The share of online CFD queries that returned a score.")
    print(f"{'task':<8} {'method':<{width}} {'episodes':>8} {'queries':>8} {'delivered':>9} {'rate':>6}  95 percent interval")
    for row in online:
        print(
            f"{row['task']:<8} {row['method']:<{width}} {row['episodes']:>8} {row['online_queries']:>8} "
            f"{row['delivered']:>9} {number(row['delivery_rate']):>6}  {interval(row, 'delivery')}"
        )
    print()
    print("n is the number of episodes. A mean L/D of n/a means no recommendation delivered, or the")
    print("released records lack the score. Blank cells are checkpoints the method did not reach.")
    print("Open-ended research was graded at checkpoints 0 to 4 only. Its online rate covers 32 queries.")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("submission", nargs="+", type=Path, help="one or more submission output folders")
    parser.add_argument("--out", type=Path, required=True, help="folder for the CSV tables and the verification report")
    parser.add_argument("--data", type=Path, default=DATA, help="folder with the released tables")
    parser.add_argument("--label", default=None, help="name for the submission in the tables. Default is its method name")
    parser.add_argument("--resamples", type=int, default=RESAMPLES)
    parser.add_argument("--seed", type=int, default=0, help="seed of the bootstrap")
    parser.add_argument("--no-verify", action="store_true", help="skip the replay check (not for a submission)")
    args = parser.parse_args(argv)

    folders = find_episode_dirs(args.submission)
    if not folders:
        print("no episode folders found", file=sys.stderr)
        return 2
    protocol = load_episode_protocol()
    reports = []
    accepted: list[Path] = []
    for folder in folders:
        if args.no_verify:
            if (folder / "trace.json").is_file():
                accepted.append(folder)
            continue
        report = verify_episode(folder, protocol)
        reports.append(report)
        if report["ok"]:
            accepted.append(folder)
        else:
            print(f"excluded {report['episode']} ({folder}):", file=sys.stderr)
            for problem in report["problems"]:
                print(f"    {problem}", file=sys.stderr)
    if not accepted:
        print("no episode passed the check", file=sys.stderr)
        return 1
    print(f"{len(accepted)} of {len(folders)} episodes accepted")

    episodes, evaluations, recommendations = load_submission(accepted)
    methods = sorted(set(episodes.method))
    if len(methods) > 1:
        print(f"warning: the submission mixes {len(methods)} method names, they are pooled: {methods}", file=sys.stderr)
    label = args.label or methods[0]
    reference_evaluations, reference_recommendations = load_reference(args.data)
    tasks = load_tasks()
    rng = np.random.default_rng(args.seed)
    recommendation = recommendation_rows(recommendations, reference_recommendations, label, rng, args.resamples)
    online = online_rows(evaluations, reference_evaluations, label, rng, args.resamples)
    print_tables(recommendation, online, tasks)

    args.out.mkdir(parents=True, exist_ok=True)
    columns = [
        "task", "checkpoint", "method", "is_submission", "episodes", "delivered", "delivery_rate",
        "delivery_ci_low", "delivery_ci_high", "mean_ld", "mean_ld_ci_low", "mean_ld_ci_high",
        "median_ld", "ld_complete",
    ]
    pd.DataFrame(recommendation).reindex(columns=columns).to_csv(args.out / "recommendation_comparison.csv", index=False)
    online_columns = [
        "task", "method", "is_submission", "episodes", "online_queries", "delivered", "delivery_rate",
        "delivery_ci_low", "delivery_ci_high",
    ]
    pd.DataFrame(online).reindex(columns=online_columns).to_csv(args.out / "online_comparison.csv", index=False)
    (args.out / "verification.json").write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (args.out / "score_settings.json").write_text(json.dumps({
        "submission_label": label,
        "episodes_accepted": [str(folder) for folder in accepted],
        "bootstrap_resamples": args.resamples,
        "bootstrap_seed": args.seed,
        "reference_methods": list(REFERENCE_METHODS),
    }, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {args.out / 'recommendation_comparison.csv'}, {args.out / 'online_comparison.csv'}")
    return 0 if len(accepted) == len(folders) else 1


if __name__ == "__main__":
    raise SystemExit(main())
