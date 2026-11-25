#!/usr/bin/env python3
"""09_failure_transfer.py — do source-trained failure classifiers work on optimizer designs?

Section 2.3 claims that classifiers trained on the public dataset's failure
records do only modestly on the extreme designs optimizers actually propose.
Appendix A tested that on extreme *dataset* designs, which is a statement about
the source distribution, not about optimizer output. This script makes the
claim direct: train on the public records, test on the designs our own
optimizers proposed, labelled by their real WingBench outcome.

Two shifts apply at once and the paper should say so. The designs move off the
source distribution, and the label changes meaning: the source label is "failed
any sampled Mach and AoA condition", while the WingBench label is "produced a
valid score at all three fixed-lift conditions".

Inputs:  data/superwing/superwing_records.csv   (training)
         data/evaluations.csv and data/wings.csv (test)
Output:  analysis/out/macros/transfer_macros.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import OUT, WORKSPACE, design_table

RNG = 20260827
VARS = [f"x{i:02d}" for i in range(38)]
NAMES = {"logit": "logistic regression", "knn": "15-nearest-neighbours",
         "gbdt": "gradient-boosted trees"}


def main() -> None:
    norm = json.loads((WORKSPACE / "configs" / "benchmark" /
                       "superwing_physical_normalization_v1.json").read_text())
    mean, sd = np.array(norm["mean_38d"]), np.array(norm["sd_38d"])

    src = pd.read_csv(WORKSPACE / "data" / "superwing" / "superwing_records.csv")
    Z_src = (src[VARS].to_numpy() - mean) / sd
    states = src[[f"cond{i}_state" for i in range(8)]]
    y_src = ((states.ne("Success") & states.ne("Not Simulated")).any(axis=1)).astype(int).to_numpy()

    tab = design_table()
    tab = tab[tab.stream == "online"].copy()          # one row per requested evaluation
    Z_opt = (tab[VARS].to_numpy() - mean) / sd
    y_opt = (~tab.delivered.astype(bool)).astype(int).to_numpy()   # 1 = no usable score

    print(f"train: {len(Z_src)} dataset designs, {100*y_src.mean():.0f}% labelled failing")
    print(f"test : {len(Z_opt)} optimizer designs, {100*y_opt.mean():.0f}% returned no score")
    print(f"       median largest standardized coordinate  dataset {np.median(np.abs(Z_src).max(1)):.2f}"
          f"  vs optimizer {np.median(np.abs(Z_opt).max(1)):.2f}")

    models = {"logit": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
              "knn": make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=15)),
              "gbdt": HistGradientBoostingClassifier(random_state=RNG)}
    macros, rows = {}, []
    for key, model in models.items():
        model.fit(Z_src, y_src)
        p = model.predict_proba(Z_opt)[:, 1]
        overall = float(roc_auc_score(y_opt, p))
        per = {}
        for t in ("low", "nominal", "high"):
            m = (tab.task == t).to_numpy()
            per[t] = (float(roc_auc_score(y_opt[m], p[m]))
                      if len(set(y_opt[m])) > 1 else float("nan"))
        rows.append((key, overall, per))
        macros[f"Transfer{key.capitalize()}Auc"] = f"{overall:.2f}"
        for t, v in per.items():
            macros[f"Transfer{key.capitalize()}Auc{t.capitalize()}"] = f"{v:.2f}"

    print(f"\n{'model':24s} {'AUC all':>8s} {'low':>7s} {'nominal':>8s} {'high':>7s}")
    for key, overall, per in rows:
        print(f"{NAMES[key]:24s} {overall:8.2f} {per['low']:7.2f} {per['nominal']:8.2f} {per['high']:7.2f}")

    # The pooled AUC is NOT the headline: the three tasks have very different
    # base rates (92% / 52% / 47% no-score), so pooling can score below every
    # subgroup. Per-task figures are the ones the paper quotes.
    lo = [r[2]["low"] for r in rows]
    rest = [r[2][t] for r in rows for t in ("nominal", "high")]
    macros["TransferLowAucMin"] = f"{min(lo):.2f}"
    macros["TransferLowAucMax"] = f"{max(lo):.2f}"
    macros["TransferRestAucMin"] = f"{min(rest):.2f}"
    macros["TransferRestAucMax"] = f"{max(rest):.2f}"
    macros["TransferPooledAucMin"] = f"{min(r[1] for r in rows):.2f}"
    macros["TransferPooledAucMax"] = f"{max(r[1] for r in rows):.2f}"
    macros["TransferNTest"] = f"{len(Z_opt):,}"
    macros["TransferPctNoScore"] = f"{100*y_opt.mean():.0f}"
    macros["TransferDatasetMedianMaxAbsZ"] = f"{np.median(np.abs(Z_src).max(1)):.2f}"
    macros["TransferOptimizerMedianMaxAbsZ"] = f"{np.median(np.abs(Z_opt).max(1)):.2f}"
    macros["TransferPctOptimizerAtEdge"] = f"{100*(np.abs(Z_opt).max(1) > 3.49).mean():.0f}"
    macros["TransferPctDatasetAtEdge"] = f"{100*(np.abs(Z_src).max(1) > 3.49).mean():.1f}"
    for t in ("low", "nominal", "high"):
        m = (tab.task == t).to_numpy()
        macros[f"TransferBaseRate{t.capitalize()}"] = f"{100*y_opt[m].mean():.0f}"
    (OUT / "macros" / "transfer_macros.json").write_text(json.dumps(macros, indent=1))
    print("\nwrote analysis/out/macros/transfer_macros.json")


if __name__ == "__main__":
    main()
