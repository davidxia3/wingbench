#!/usr/bin/env python3
"""16_marginal_intervals.py — unpaired intervals for the means the paper reports.

The paired comparisons in Appendix A match agent episodes by seed label, but for
an agent a seed label is only a name, so the pairing has no experimental
meaning. This script gives each reported mean its own interval without any
pairing. For every method and task it resamples the ten episodes with
replacement 10,000 times (PCG64, seed 73001) and reports 95% percentile
intervals for the recommendation delivery rate and for the mean L/D of the
delivered recommendations. Agents are taken at query 4, where all three were
compared, and baselines at query 32.

Input:  data/recommendations.csv
Output: analysis/out/tables/tab_marginal_intervals.csv
        analysis/out/tables/tab_marginal_intervals.tex   (a tabular body for the appendix)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from common import DATA, TABLES, ensure_dirs

REPLICATES = 10_000
SEED = 73001
CELLS = [  # (study, checkpoint, method order)
    ("agent", 4, ["Open-ended research", "Planning", "Reflection"]),
    ("baseline", 32, ["CMA-ES no feedback", "CMA-ES selection only", "CMA-ES feedback-guided",
                      "DE feedback-guided", "SMAC3"]),
]
TASKS = ["low", "nominal", "high"]
TASK_LABEL = {"low": "$0.15$", "nominal": "$0.41$", "high": "$0.66$"}
DISPLAY = {"CMA-ES no feedback": "CMA-ES, no feedback", "CMA-ES selection only": "CMA-ES, selection only",
           "CMA-ES feedback-guided": "Feedback-guided CMA-ES", "DE feedback-guided": "Feedback-guided DE"}


def intervals(delivered: np.ndarray, ld: np.ndarray) -> tuple[tuple[float, float], tuple[float, float] | None]:
    rng = np.random.Generator(np.random.PCG64(SEED))
    n = len(delivered)
    index = rng.integers(0, n, size=(REPLICATES, n))
    rate = delivered[index].mean(axis=1)
    rate_ci = (float(np.percentile(rate, 2.5)), float(np.percentile(rate, 97.5)))
    means = []
    for row in index:
        d = delivered[row]
        if d.any():
            means.append(ld[row][d].mean())
    if len(means) < REPLICATES // 2:
        return rate_ci, None
    means = np.array(means)
    return rate_ci, (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))


def main() -> None:
    ensure_dirs()
    rec = pd.read_csv(DATA / "recommendations.csv", float_precision="round_trip")
    rec["delivered"] = rec.delivered.astype(str).str.lower().eq("true")
    rows = []
    for study, checkpoint, methods in CELLS:
        for method in methods:
            for task in TASKS:
                cell = rec[(rec.study == study) & (rec.checkpoint == checkpoint) & (rec.method == method) & (rec.task == task)]
                assert len(cell) == 10, (method, task, len(cell))
                delivered = cell.delivered.to_numpy()
                ld = cell.ld.to_numpy(dtype=float)
                rate_ci, mean_ci = intervals(delivered, ld)
                n_del = int(delivered.sum())
                rows.append(dict(study=study, checkpoint=checkpoint, method=method, task=task,
                                 delivered=n_del, delivery_rate=n_del / 10,
                                 delivery_ci_low=round(rate_ci[0], 3), delivery_ci_high=round(rate_ci[1], 3),
                                 mean_ld=round(float(ld[delivered].mean()), 2) if n_del else None,
                                 mean_ld_ci_low=round(mean_ci[0], 2) if mean_ci else None,
                                 mean_ld_ci_high=round(mean_ci[1], 2) if mean_ci else None))
    table = pd.DataFrame(rows)
    table.to_csv(TABLES / "tab_marginal_intervals.csv", index=False)

    lines = [r"\begin{tabular}{llrlrl}", r"\toprule",
             r"method & $C_L$ & delivered & 95\% CI & mean $L/D$ & 95\% CI \\", r"\midrule"]
    for study, checkpoint, methods in CELLS:
        lines.append(r"\multicolumn{6}{l}{\emph{" + ("Agents after four queries" if study == "agent" else "Baselines after 32 queries") + r"}} \\")
        for method in methods:
            for task in TASKS:
                r = table[(table.method == method) & (table.task == task)].iloc[0]
                ci = f"[{r.delivery_ci_low:.2f}, {r.delivery_ci_high:.2f}]"
                if r.mean_ld is None or pd.isna(r.mean_ld):
                    mean, mci = "--", "--"
                elif pd.isna(r.mean_ld_ci_low):
                    mean, mci = f"{r.mean_ld:.2f}", "--"
                else:
                    mean, mci = f"{r.mean_ld:.2f}", f"[{r.mean_ld_ci_low:.2f}, {r.mean_ld_ci_high:.2f}]"
                name = DISPLAY.get(method, method) if task == "low" else ""
                lines.append(f"{name} & {TASK_LABEL[task]} & {int(r.delivered)}/10 & {ci} & {mean} & {mci} \\\\")
        if study == "agent":
            lines.append(r"\midrule")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TABLES / "tab_marginal_intervals.tex").write_text("\n".join(lines) + "\n")
    print(table.to_string(index=False))


if __name__ == "__main__":
    main()
