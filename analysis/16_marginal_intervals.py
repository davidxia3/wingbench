#!/usr/bin/env python3
"""16_marginal_intervals.py — unpaired intervals for the agent study and the baselines.

The agent methods ran under seed labels, but for an agent a seed label is only
a name and does not pair episodes across methods. This script therefore treats
each method's ten episodes per task as an independent sample.

Two outputs.

1. For every method and task, an interval for the recommendation delivery rate
   and for the mean L/D of the delivered recommendations. The delivery interval
   is the exact (Clopper-Pearson) binomial interval for k of 10, so ten of ten
   gives [0.69, 1.00] rather than a degenerate [1, 1]. The mean-L/D interval is a
   95% percentile bootstrap from 10,000 resamples of the ten episodes (PCG64,
   seed 73001). Agents are taken at query 4, where all three were compared, and
   baselines at query 32.
2. For each task, open-ended research minus planning and minus reflection at
   query 4: the difference in delivery rate, with a Newcombe score interval
   (a bootstrap of two arms that both deliver ten of ten has no spread), and
   the difference in conditional mean L/D, with a 95% percentile interval
   from 10,000 independent resamples of the two arms. A quality difference
   uses the delivered recommendations of each resample, so it is the
   difference between two conditional means, not a paired difference.

Input:  data/recommendations.csv
Output: analysis/out/tables/tab_marginal_intervals.csv
        analysis/out/tables/tab_marginal_intervals.tex        (a tabular body for the appendix)
        analysis/out/tables/tab_agent_contrasts_unpaired.csv
        analysis/out/tables/tab_agent_contrasts_unpaired.tex  (a tabular body for the appendix)
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy.stats import beta

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
REFERENCE = "Open-ended research"
COMPARATORS = ["Planning", "Reflection"]


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple[float, float]:
    low = 0.0 if k == 0 else float(beta.ppf(alpha / 2, k, n - k + 1))
    high = 1.0 if k == n else float(beta.ppf(1 - alpha / 2, k + 1, n - k))
    return low, high


def percentile(values: np.ndarray) -> tuple[float, float]:
    return float(np.percentile(values, 2.5)), float(np.percentile(values, 97.5))


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def newcombe(k1: int, n1: int, k2: int, n2: int) -> tuple[float, float]:
    """Newcombe's score interval (method 10) for the difference p1 - p2."""
    p1, p2 = k1 / n1, k2 / n2
    l1, u1 = wilson(k1, n1)
    l2, u2 = wilson(k2, n2)
    d = p1 - p2
    return d - math.sqrt((p1 - l1) ** 2 + (u2 - p2) ** 2), d + math.sqrt((u1 - p1) ** 2 + (p2 - l2) ** 2)


def mean_interval(delivered: np.ndarray, ld: np.ndarray) -> tuple[float, float] | None:
    rng = np.random.Generator(np.random.PCG64(SEED))
    n = len(delivered)
    index = rng.integers(0, n, size=(REPLICATES, n))
    means = []
    for row in index:
        d = delivered[row]
        if d.any():
            means.append(ld[row][d].mean())
    if len(means) < REPLICATES // 2:
        return None
    return percentile(np.array(means))


def unpaired_contrast(da: np.ndarray, la: np.ndarray, db: np.ndarray, lb: np.ndarray) -> dict:
    """Arm A minus arm B, resampling the two arms independently."""
    rng = np.random.Generator(np.random.PCG64(SEED))
    n = len(da)
    ia = rng.integers(0, n, size=(REPLICATES, n))
    ib = rng.integers(0, n, size=(REPLICATES, n))
    quality = []
    for ra, rb in zip(ia, ib):
        sa, sb = da[ra], db[rb]
        if sa.any() and sb.any():
            quality.append(la[ra][sa].mean() - lb[rb][sb].mean())
    out = dict(delivery_diff=float(da.mean() - db.mean()), delivery_ci=newcombe(int(da.sum()), n, int(db.sum()), n),
               n_a=int(da.sum()), n_b=int(db.sum()), quality_diff=None, quality_ci=None)
    if da.any() and db.any() and len(quality) >= REPLICATES // 2:
        out["quality_diff"] = float(la[da].mean() - lb[db].mean())
        out["quality_ci"] = percentile(np.array(quality))
    return out


def cell(rec: pd.DataFrame, study: str, checkpoint: int, method: str, task: str) -> tuple[np.ndarray, np.ndarray]:
    rows = rec[(rec.study == study) & (rec.checkpoint == checkpoint) & (rec.method == method) & (rec.task == task)]
    assert len(rows) == 10, (method, task, len(rows))
    return rows.delivered.to_numpy(), rows.ld.to_numpy(dtype=float)


def main() -> None:
    ensure_dirs()
    rec = pd.read_csv(DATA / "recommendations.csv", float_precision="round_trip")
    rec["delivered"] = rec.delivered.astype(str).str.lower().eq("true")

    # 1. one interval per reported mean
    rows = []
    for study, checkpoint, methods in CELLS:
        for method in methods:
            for task in TASKS:
                delivered, ld = cell(rec, study, checkpoint, method, task)
                n_del = int(delivered.sum())
                rate_ci = clopper_pearson(n_del, len(delivered))
                mean_ci = mean_interval(delivered, ld)
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

    # 2. open-ended research minus each comparator at query 4, arms resampled independently
    contrasts = []
    for comparator in COMPARATORS:
        for task in TASKS:
            da, la = cell(rec, "agent", 4, REFERENCE, task)
            db, lb = cell(rec, "agent", 4, comparator, task)
            c = unpaired_contrast(da, la, db, lb)
            contrasts.append(dict(comparator=comparator, task=task,
                                  delivery_diff=round(c["delivery_diff"], 2),
                                  delivery_ci_low=round(c["delivery_ci"][0], 2), delivery_ci_high=round(c["delivery_ci"][1], 2),
                                  delivered_reference=c["n_a"], delivered_comparator=c["n_b"],
                                  quality_diff=None if c["quality_diff"] is None else round(c["quality_diff"], 2),
                                  quality_ci_low=None if c["quality_ci"] is None else round(c["quality_ci"][0], 2),
                                  quality_ci_high=None if c["quality_ci"] is None else round(c["quality_ci"][1], 2)))
    ctable = pd.DataFrame(contrasts)
    ctable.to_csv(TABLES / "tab_agent_contrasts_unpaired.csv", index=False)
    lines = [r"\begin{tabular}{llrrrrr}", r"\toprule",
             r"comparator & $C_L$ & $\Delta$ delivery & 95\% CI & delivered & $\Delta L/D$ & 95\% CI \\", r"\midrule"]
    for r in ctable.itertuples():
        dci = f"[{r.delivery_ci_low:.2f}, {r.delivery_ci_high:.2f}]"
        if r.quality_diff is None or pd.isna(r.quality_diff):
            q, qci = "--", "--"
        else:
            q, qci = f"{r.quality_diff:.2f}", f"[{r.quality_ci_low:.2f}, {r.quality_ci_high:.2f}]"
        lines.append(f"{r.comparator} & {TASK_LABEL[r.task]} & {r.delivery_diff:.2f} & {dci} & "
                     f"{r.delivered_reference}/{r.delivered_comparator} & {q} & {qci} \\\\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    (TABLES / "tab_agent_contrasts_unpaired.tex").write_text("\n".join(lines) + "\n")
    print(ctable.to_string(index=False))


if __name__ == "__main__":
    main()
