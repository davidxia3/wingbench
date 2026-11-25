#!/usr/bin/env python3
"""07_solver_error.py — are the non-zero-exit evaluations a property of the design?

The protocol requires the solver process to exit with return code zero
(`condition_gates.process_returncode_zero_required`), and records a non-zero exit
as `inconclusive_infrastructure`.  That name points at the computing environment,
but the protocol logs genuine environment events under separate codes (verified
spot reclaim, uninterrupted infrastructure failure), so the code means only that
the solver stopped with an error status.

This script tests whether those outcomes behave like random environment events or
like a property of the design, and identifies what kind of exit they were.

Output: analysis/out/macros/solver_error_macros.json
"""
from __future__ import annotations

import collections
import json

import pandas as pd

from common import OUT, WORKSPACE, cfd_attempts

df = pd.read_csv(OUT / "grades.csv")
df["serr"] = df.reason_codes.fillna("").str.contains("infrastructure")
df["task"] = df.config.str.extract(r"load-(\w+)-cl")[0]
df["day"] = pd.to_datetime(df.ingested_at_utc).dt.date

online = df[df.stream == "online"]
by_task = online.groupby("task").serr.mean() * 100

# repeated evaluations of the same shape within the same task
groups = df.groupby(["task", "geometry_sha256"]).filter(lambda x: len(x) > 1) \
           .groupby(["task", "geometry_sha256"])
with_err = [g for _, g in groups if g.serr.any()]
all_err = [g for g in with_err if g.serr.all()]
base = float(df.serr.mean())
expected = sum(base ** (len(g) - 1) for g in with_err)

# exposure: a solver error can only happen to a design that survived meshing
STEERED = ("fsd-cma-v1-port-v2-pilot", "checkpointed-de-feedback-v2-pilot")
nom = online[online.task == "nominal"].copy()
nom["past_mesh"] = ~nom.reason_codes.fillna("").str.contains("mesh|unmeshable")
nom["steered"] = nom.method.isin(STEERED)
past_mesh = nom.groupby("steered").past_mesh.mean() * 100
main = online[online.task.isin(["low", "nominal", "high"])].copy()
main["steered"] = main.method.isin(STEERED)
by_arm = main.groupby("steered").serr.sum()

# replicate disagreement measured on success/failure, not on the internal label
_rep = df.groupby(["task", "geometry_sha256"]).filter(lambda x: len(x) > 1)
_g = _rep.groupby(["task", "geometry_sha256"])
n_rep_groups = _g.ngroups
n_disagree = sum(1 for _, x in _g if x.delivery.astype(bool).nunique() > 1)

# sensitivity: headline and per-task success if the whole category were excluded
_keep = df[~df.serr]
pct_null_all = 100 * (~df.delivery.astype(bool)).mean()
pct_null_excl = 100 * (~_keep.delivery.astype(bool)).mean()
_onk = _keep[_keep.stream == "online"]
succ_excl = {t: 100 * _onk[_onk.task == t].delivery.astype(bool).mean() for t in ("low", "nominal", "high")}
succ_all = {t: 100 * online[online.task == t].delivery.astype(bool).mean() for t in ("low", "nominal", "high")}

ck = df[(df.stream == "hidden") & (df.checkpoint == 32)]
ck_main = ck[ck.task.isin(["low", "nominal", "high"])]
main_all = online[online.task.isin(["low", "nominal", "high"])]

# mechanism and timing, from every mesh and solve attempt (data/cfd_attempts.csv)
timing = cfd_attempts()
sealed_t = timing[timing.source == "sealed"]
rc_campaign_solve = collections.Counter(
    int(r) for r in sealed_t[(sealed_t.stage == "solve") & (sealed_t.termination_reason == "process_error")].returncode.dropna())
rc_any_mesh = collections.Counter(
    int(r) for r in sealed_t[(sealed_t.stage == "mesh") & (sealed_t.termination_reason == "process_error")].returncode.dropna())

# timing separation: how fast these fail vs a completed solve
import numpy as _np
_solve = sealed_t[(sealed_t.stage == "solve") & sealed_t.wall_seconds.notna() & (sealed_t.wall_seconds > 0)]
fast = _solve[_solve.termination_reason == "process_error"].wall_seconds.tolist()
done = _solve[(_solve.termination_reason == "completed") & (_solve.outcome == "valid")].wall_seconds.tolist()

fh = online[(online.task == "high") & (~online.delivery.astype(bool))]

macros = {
    "SolverErrPctLow": f"{by_task['low']:.1f}",
    "SolverErrPctNominal": f"{by_task['nominal']:.1f}",
    "SolverErrPctHigh": f"{by_task['high']:.1f}",
    "SolverErrNOnline": str(int(online.serr.sum())),
    "SolverErrRepGroups": str(len(with_err)),
    "SolverErrRepAllAgree": str(len(all_err)),
    "SolverErrRepExpected": f"{expected:.1f}",
    "SolverErrRepSpanMethods": str(sum(1 for g in all_err if g.method.nunique() > 1)),
    "SolverErrRepSpanDays": str(sum(1 for g in all_err if g.day.nunique() > 1)),
    "SolverErrNPartial": str(int((df.serr & (df.outcome_kind == "partial")).sum())),
    "SolverErrShareHighFailures": f"{100 * fh.serr.mean():.0f}",
    "SolverErrPastMeshSteered": f"{past_mesh[True]:.0f}",
    "SolverErrPastMeshNonSteered": f"{past_mesh[False]:.0f}",
    "SolverErrMainSteered": str(int(by_arm.get(True, 0))),
    "SolverErrMainNonSteered": str(int(by_arm.get(False, 0))),
    "SolverErrCkTotal": str(len(ck)),
    "SolverErrCkN": str(int(ck.serr.sum())),
    "SolverErrRcLocalN": str(sum(rc_campaign_solve.values())),
    "SolverErrRcLocalOne": str(rc_campaign_solve.get(1, 0)),
    "SolverErrSegfaultMesh": str(rc_any_mesh.get(139, 0)),
    "SolverErrMedianSeconds": f"{_np.median(fast):.0f}",
    "SolverErrMaxSeconds": f"{max(fast):.0f}",
    "SolveMinSeconds": f"{min(done):.0f}",
    "SolveMedianMinutes": f"{_np.median(done)/60:.0f}",
    "SolverErrMainPct": f"{100 * main_all.serr.mean():.1f}",
    "SolverErrMainN": str(int(main_all.serr.sum())),
    "SolverErrCkMainN": str(int(ck_main.serr.sum())),
    "SolverErrCkMainTotal": str(len(ck_main)),
    "RepGroups": str(n_rep_groups),
    "RepDisagree": str(n_disagree),
    "PctNullExcl": f"{pct_null_excl:.0f}",
    "SuccLowExcl": f"{succ_excl['low']:.1f}",
    "SuccNominalExcl": f"{succ_excl['nominal']:.1f}",
    "SuccNominalAll": f"{succ_all['nominal']:.1f}",
    "SuccHighExcl": f"{succ_excl['high']:.1f}",
    "SuccHighAll": f"{succ_all['high']:.1f}",
}
(WORKSPACE / "analysis" / "out" / "macros" / "solver_error_macros.json").write_text(json.dumps(macros, indent=1))
print(json.dumps(macros, indent=1))
print("\ncampaign SOLVE returncodes:", dict(rc_campaign_solve))
print("any-phase MESH returncodes:", dict(rc_any_mesh))
