#!/usr/bin/env python3
"""06_failure_predictability.py — can failure be predicted from the design parameters?

Question (paper Section 2.3): a natural response to failure-blind surrogates is to
train a classifier on (design, failed?) labels. The public dataset's run states
give such labels. This script trains standard classifiers on them and measures:

  (a) 5-fold cross-validated ROC-AUC on the dataset distribution;
  (b) the same models tested on the most extreme 15% of designs (largest
      standardized coordinate), i.e. toward where optimizers actually search.

Also verifies two facts used in the paper: the largest standardized coordinate
of any dataset design (box-bounds claim) and the total evaluator compute.

Inputs:  data/superwing/superwing_records.csv
         configs/benchmark/superwing_physical_normalization_v1.json
         analysis/out/replay_meta.csv (CFD processor-core seconds per episode)
Output:  paper/generated/failure_macros.json
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import StratifiedKFold
from sklearn.neighbors import KNeighborsClassifier
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from common import WORKSPACE

RNG = 20260827

df = pd.read_csv(WORKSPACE / "data" / "superwing" / "superwing_records.csv")
norm = json.loads((WORKSPACE / "configs" / "benchmark" /
                   "superwing_physical_normalization_v1.json").read_text())
mean = np.array(norm["mean_38d"])
sd = np.array(norm["sd_38d"])

X_phys = df[[f"x{i:02d}" for i in range(38)]].to_numpy()
Z = (X_phys - mean) / sd
states = df[[f"cond{i}_state" for i in range(8)]]
y = ((states.ne("Success") & states.ne("Not Simulated")).any(axis=1)).astype(int).to_numpy()

maxabs = np.abs(Z).max(axis=1)
box_edge = float(np.abs(Z).max())
n_outside = int((maxabs > 3.5).sum())

MODELS = {
    "logit": make_pipeline(StandardScaler(), LogisticRegression(max_iter=2000)),
    "knn": make_pipeline(StandardScaler(), KNeighborsClassifier(n_neighbors=15)),
    "gbdt": HistGradientBoostingClassifier(random_state=RNG),
}

cv_auc = {}
skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=RNG)
for name, model in MODELS.items():
    aucs = []
    for tr, te in skf.split(Z, y):
        model.fit(Z[tr], y[tr])
        aucs.append(roc_auc_score(y[te], model.predict_proba(Z[te])[:, 1]))
    cv_auc[name] = (float(np.mean(aucs)), float(np.std(aucs)))

# extrapolation split: train on the central 85%, test on the most extreme 15%
order = np.argsort(maxabs)
cut = int(0.85 * len(order))
tr_idx, te_idx = order[:cut], order[cut:]
ex_auc = {}
for name, model in MODELS.items():
    model.fit(Z[tr_idx], y[tr_idx])
    ex_auc[name] = float(roc_auc_score(y[te_idx], model.predict_proba(Z[te_idx])[:, 1]))

att_total = int((states != "Not Simulated").sum().sum())
fail_total = int(((states != "Success") & (states != "Not Simulated")).sum().sum())
ld_all = []
for i in range(8):
    ok = df[f"cond{i}_state"] == "Success"
    ld_all.append((df.loc[ok, f"cond{i}_cl"] / df.loc[ok, f"cond{i}_cd"]))
max_ld_dataset = float(pd.concat(ld_all).max())

anysucc = df["any_success"].astype(bool).to_numpy()
pct_inside = 100.0 * (maxabs[anysucc] <= 3.5).mean()
median_maxabs = float(np.median(maxabs[anysucc]))

grades = pd.read_csv(WORKSPACE / "analysis" / "out" / "grades.csv")
max_ld = float(grades.loc[grades.delivery == True, "conditional_value"].max())

ref = json.loads((WORKSPACE / "data" / "superwing" / "cruise_ld_percentiles.json").read_text())
ref_ld_max = float(ref["ld_range"][1])
ref_ld_median = float(ref["percentile_ld"][50])

core_seconds = 0.0
for s in pd.read_csv(WORKSPACE / "analysis" / "out" / "replay_meta.csv",
                     float_precision="round_trip").evaluator_cpu_seconds:
    core_seconds += float(s)

_on = grades[grades.stream == "online"]
_f = _on[~_on.delivery.astype(bool)]
n_fail = len(_f)
n_mesh_stage = int(_f.reason_codes.fillna("").str.contains("mesh|unmeshable").sum())

best_cv = max(cv_auc, key=lambda k: cv_auc[k][0])
best_ex = max(ex_auc, key=lambda k: ex_auc[k])
macros = {
    "FailPredNDesigns": f"{len(df):,}",
    "FailPredPctFragile": f"{100 * y.mean():.0f}",
    "FailPredCvAucBest": f"{cv_auc[best_cv][0]:.2f}",
    "FailPredCvAucBestModel": {"logit": "logistic regression", "knn": "nearest neighbours",
                               "gbdt": "gradient-boosted trees"}[best_cv],
    "FailPredCvAucLogit": f"{cv_auc['logit'][0]:.2f}",
    "FailPredCvAucKnn": f"{cv_auc['knn'][0]:.2f}",
    "FailPredCvAucGbdt": f"{cv_auc['gbdt'][0]:.2f}",
    "FailPredExtrapAucBest": f"{max(ex_auc.values()):.2f}",
    "FailPredExtrapAucLogit": f"{ex_auc['logit']:.2f}",
    "FailPredExtrapAucKnn": f"{ex_auc['knn']:.2f}",
    "FailPredExtrapAucGbdt": f"{ex_auc['gbdt']:.2f}",
    "FailPredExtrapAucWorst": f"{min(ex_auc.values()):.2f}",
    "DatasetMaxAbsZ": f"{box_edge:.2f}",
    "DatasetNOutsideBox": str(n_outside),
    "CampaignCoreHours": f"{core_seconds / 3600:,.0f}",
    "DatasetPctInsideBox": f"{pct_inside:.0f}",
    "DatasetMedianMaxAbsZ": f"{median_maxabs:.1f}",
    "MaxObservedLd": f"{max_ld:.1f}",
    "AvgCoreHoursPerEval": f"{core_seconds / 3600 / len(grades):.0f}",
    "DatasetRefLdMedian": f"{ref_ld_median:.1f}",
    "DatasetPctCondFail": f"{100 * fail_total / att_total:.0f}",
    "DatasetMaxLdOverall": f"{max_ld_dataset:.1f}",
    "DatasetRefLdMax": f"{ref_ld_max:.1f}",
    "NOnlineFailures": str(int(n_fail)),
    "NOnlineMeshStage": str(int(n_mesh_stage)),
    "PctFailuresMeshStage": f"{100 * n_mesh_stage / n_fail:.0f}",
}
out = WORKSPACE / "analysis" / "out" / "macros" / "failure_macros.json"
out.write_text(json.dumps(macros, indent=1))
print(json.dumps(macros, indent=1))
print("cv detail:", {k: f"{v[0]:.3f}±{v[1]:.3f}" for k, v in cv_auc.items()})
print("extrap detail:", {k: f"{v:.3f}" for k, v in ex_auc.items()})
