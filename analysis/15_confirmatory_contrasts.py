#!/usr/bin/env python3
"""15_confirmatory_contrasts.py — the six planned baseline contrasts in Section 6.1.

Compares the checkpoint-32 recommendations of feedback-guided CMA-ES with both
archive-only CMA-ES methods, and feedback-guided DE with selection-only CMA-ES,
on delivery and on conditional L/D. The unit is the seed block: for delivery,
each seed contributes the difference in delivery averaged over the three tasks;
for conditional L/D, each seed contributes the difference in L/D averaged over
the tasks where both recommendations deliver, and a seed with no such task is
left out. Each contrast gets an exact two-sided sign-flip test over its seed
blocks and a 95% percentile interval from 10,000 seed-block bootstrap
resamples, drawn from a fresh PCG64 generator with seed 73001 (frozen in the
analysis plan before any result was known). Holm correction covers the six
tests.

Input:  data/recommendations.csv
Output: analysis/out/confirmatory_contrasts.json
        analysis/out/tables/tab_confirmatory.csv
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

from common import DATA, OUT, TABLES, ensure_dirs

CONTRASTS = [
    ("feedback-guided CMA-ES vs selection-only CMA-ES", "CMA-ES feedback-guided", "CMA-ES selection only"),
    ("feedback-guided CMA-ES vs no-feedback CMA-ES", "CMA-ES feedback-guided", "CMA-ES no feedback"),
    ("feedback-guided DE vs selection-only CMA-ES", "DE feedback-guided", "CMA-ES selection only"),
]
TASKS = ["low", "nominal", "high"]
BOOTSTRAP_REPLICATES = 10_000
BOOTSTRAP_SEED = 73001
MIN_BLOCKS_CONDITIONAL = 5
FAMILYWISE_ALPHA = 0.05


def seed_block_effects(rec: pd.DataFrame, seeds, method_a, method_b, endpoint):
    """One effect per seed, or None when the seed has no usable task."""
    effects = []
    for seed in seeds:
        per_task = []
        for task in TASKS:
            a = rec.loc[(task, seed, method_a)]
            b = rec.loc[(task, seed, method_b)]
            if endpoint == "delivery":
                per_task.append(float(a.delivered) - float(b.delivered))
            elif a.delivered and b.delivered:
                per_task.append(float(a.ld) - float(b.ld))
        effects.append(float(np.mean(per_task)) if per_task else None)
    return effects


def sign_flip_p(effects: np.ndarray) -> float:
    """Exact two-sided sign-flip test over the seed-block effects."""
    observed = abs(effects.mean())
    count = 0
    for signs in itertools.product((1.0, -1.0), repeat=len(effects)):
        if abs((effects * np.array(signs)).mean()) >= observed - 1e-12:
            count += 1
    return count / 2 ** len(effects)


def bootstrap_ci(effects: np.ndarray) -> tuple[float, float]:
    rng = np.random.Generator(np.random.PCG64(BOOTSTRAP_SEED))
    n = len(effects)
    index = rng.integers(0, n, size=(BOOTSTRAP_REPLICATES, n))
    means = effects[index].mean(axis=1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def holm(pvals: dict[str, float]) -> dict[str, float]:
    """Holm step-down adjusted p-values over the family."""
    items = sorted(pvals.items(), key=lambda kv: kv[1])
    adjusted, running = {}, 0.0
    for rank, (key, p) in enumerate(items):
        running = max(running, min(1.0, (len(items) - rank) * p))
        adjusted[key] = running
    return adjusted


def main() -> None:
    ensure_dirs()
    rec = pd.read_csv(DATA / "recommendations.csv", float_precision="round_trip")
    rec = rec[(rec.study == "baseline") & (rec.checkpoint == 32)].copy()
    rec["delivered"] = rec.delivered.astype(str).str.lower().eq("true")
    rec = rec.set_index(["task", "seed", "method"]).sort_index()
    seeds = sorted(rec.index.get_level_values("seed").unique())
    assert len(seeds) == 10, seeds

    rows, pvals = [], {}
    for name, method_a, method_b in CONTRASTS:
        for endpoint in ("delivery", "conditional_ld"):
            effects = seed_block_effects(rec, seeds, method_a, method_b, endpoint)
            present = np.array([e for e in effects if e is not None])
            row = {"contrast": name, "endpoint": endpoint, "method_a": method_a,
                   "method_b": method_b, "seed_blocks": int(len(present))}
            if endpoint == "conditional_ld" and len(present) < MIN_BLOCKS_CONDITIONAL:
                row["status"] = "not estimable"
            else:
                low, high = bootstrap_ci(present)
                row.update({"estimate": round(float(present.mean()), 4),
                            "ci95": [round(low, 4), round(high, 4)],
                            "p_sign_flip": round(sign_flip_p(present), 5)})
                pvals[f"{name}|{endpoint}"] = row["p_sign_flip"]
            rows.append(row)
    adjusted = holm(pvals)
    for row in rows:
        key = f"{row['contrast']}|{row['endpoint']}"
        if key in adjusted:
            row["p_holm"] = round(adjusted[key], 5)
            row["significant"] = bool(adjusted[key] <= FAMILYWISE_ALPHA)

    (OUT / "confirmatory_contrasts.json").write_text(json.dumps(
        {"holm_family_size": len(pvals), "familywise_alpha": FAMILYWISE_ALPHA,
         "bootstrap": {"replicates": BOOTSTRAP_REPLICATES, "seed": BOOTSTRAP_SEED},
         "contrasts": rows}, indent=1))
    pd.DataFrame(rows).to_csv(TABLES / "tab_confirmatory.csv", index=False)
    for row in rows:
        if "estimate" in row:
            print(f"{row['contrast']:52s} {row['endpoint']:15s} n={row['seed_blocks']:2d} "
                  f"{row['estimate']:+.3f} [{row['ci95'][0]:+.3f}, {row['ci95'][1]:+.3f}] "
                  f"p={row['p_sign_flip']:.4f} Holm p={row['p_holm']:.4f}")
        else:
            print(f"{row['contrast']:52s} {row['endpoint']:15s} n={row['seed_blocks']:2d} not estimable")


if __name__ == "__main__":
    main()
