#!/usr/bin/env python3
"""04_discovery_vs_recommendation.py — what the optimizer saw vs what it honestly had.

Two views per the frozen protocol:
  discovery curve      — best complete ONLINE value observed so far (optimizer-visible)
  recommendation curve — hidden-policy grade of the checkpoint nominee (optimizer-blind)

The gap between them is the honest-measurement axis: an optimizer can look good
online while its nominated design fails hidden grading (or vice versa).

Also compares online delivery vs fresh-hidden delivery per method: fresh-hidden
grades evaluate designs the optimizer chose but never verified.

Outputs: figures/fig_discovery_vs_recommendation.pdf
         figures/fig_fresh_hidden_vs_online_yield.pdf
         out/gap_stats.json
"""
from __future__ import annotations

import json
import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from common import (
    CONFIGS,
    CONFIG_SHORT,
    FIGURES,
    HIDDEN_CHECKPOINTS,
    METHODS,
    METHOD_COLOR,
    METHOD_LS,
    METHOD_MARKER,
    METHOD_SHORT,
    OUT,
    ensure_dirs,
    load_grades,
    stamp_axes,
    snapshot_stamp,
)


def xpos(q: int) -> float:
    return math.log2(q + 1) / math.log2(33)


def main() -> None:
    ensure_dirs()
    curves = pd.read_csv(OUT / "curves.csv")
    disc = pd.read_csv(OUT / "discovery.csv")
    grades = load_grades()

    # ---------------------------------------------------------------- figure 1
    # mean discovery value (best-so-far, episodes with >=1 delivery) vs mean
    # recommendation value | delivered, per config; one panel per method would
    # be 12 panels — instead overlay per config with method colors, discovery
    # dashed / recommendation solid.
    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.6))
    for j, cfg in enumerate(CONFIGS):
        ax = axes[j]
        for m in METHODS:
            d = disc[(disc.config == cfg) & (disc.method == m)]
            mean_best = d.groupby("query")["best_conditional_value"].mean()
            ax.plot([xpos(q) for q in mean_best.index], mean_best.values,
                    color=METHOD_COLOR[m], lw=1.1, alpha=0.45)
            c = curves[(curves.config == cfg) & (curves.method == m)]
            xs, vals = [], []
            for ck in HIDDEN_CHECKPOINTS:
                pts = c[(c.checkpoint == ck) & (c.delivery == True)]  # noqa: E712
                if len(pts):
                    xs.append(xpos(ck))
                    vals.append(pts["conditional_value"].mean())
            ax.plot(xs, vals, color=METHOD_COLOR[m], lw=1.8,
                    marker=METHOD_MARKER[m], ms=3.5, label=METHOD_SHORT[m])
        ax.set_xticks([xpos(c) for c in HIDDEN_CHECKPOINTS])
        ax.set_xticklabels([str(c) for c in HIDDEN_CHECKPOINTS], fontsize=7)
        ax.set_title(CONFIG_SHORT[cfg], fontsize=9.5)
        ax.set_xlabel("CFD evaluations used, k (log scale)")
    axes[0].set_ylabel("L/D")
    axes[0].legend(fontsize=6.5, loc="lower right")
    fig.suptitle("Best observed score (thin) vs. score of the recommended design (thick)",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    stamp_axes(fig, "means over successful evaluations only")
    fig.savefig(FIGURES / "fig_discovery_vs_recommendation.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_discovery_vs_recommendation.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ---------------------------------------------------------------- figure 2
    # online delivery rate vs fresh-hidden delivery rate per method (pooled and
    # per config).  Fresh-hidden grades exist only for never-graded nominees.
    grades["delivered"] = grades["outcome_kind"] == "pass"
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.3), sharey=True)
    width = 0.35
    stats: dict = {"stamp": snapshot_stamp(), "cells": {}}
    for j, cfg in enumerate(CONFIGS):
        ax = axes[j]
        sub = grades[grades.config == cfg]
        on = sub[sub.stream == "online"].groupby("method")["delivered"].agg(["mean", "size"])
        hi = sub[sub.stream == "hidden"].groupby("method")["delivered"].agg(["mean", "size"])
        x = np.arange(len(METHODS))
        ov = [on.loc[m, "mean"] if m in on.index else np.nan for m in METHODS]
        hv = [hi.loc[m, "mean"] if m in hi.index else np.nan for m in METHODS]
        ax.bar(x - width / 2, ov, width, label="requested evaluations", color="#4c72b0")
        ax.bar(x + width / 2, hv, width, label="held-out evaluations of recommendations", color="#c44e52")
        for i, m in enumerate(METHODS):
            non = int(on.loc[m, "size"]) if m in on.index else 0
            nhi = int(hi.loc[m, "size"]) if m in hi.index else 0
            ax.text(i - width / 2, (0 if np.isnan(ov[i]) else ov[i]) + 0.015,
                    str(non), ha="center", fontsize=5.5)
            ax.text(i + width / 2, (0 if np.isnan(hv[i]) else hv[i]) + 0.015,
                    str(nhi), ha="center", fontsize=5.5)
            stats["cells"][f"{cfg}|{m}"] = {
                "online_delivery": None if np.isnan(ov[i]) else round(float(ov[i]), 4),
                "n_online": non,
                "fresh_hidden_delivery": None if np.isnan(hv[i]) else round(float(hv[i]), 4),
                "n_fresh_hidden": nhi,
            }
        ax.set_xticks(x)
        ax.set_xticklabels([METHOD_SHORT[m] for m in METHODS], rotation=30,
                           fontsize=7, ha="right")
        ax.set_title(CONFIG_SHORT[cfg], fontsize=9.5)
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("success rate")
    axes[0].legend(fontsize=7)
    fig.suptitle("Success rate: requested evaluations vs. held-out evaluations of recommended designs",
                 fontsize=10)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    stamp_axes(fig, "bar labels: number of evaluations")
    fig.savefig(FIGURES / "fig_fresh_hidden_vs_online_yield.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_fresh_hidden_vs_online_yield.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    (OUT / "gap_stats.json").write_text(json.dumps(stats, indent=1))
    print("wrote fig_discovery_vs_recommendation, fig_fresh_hidden_vs_online_yield")


if __name__ == "__main__":
    main()
