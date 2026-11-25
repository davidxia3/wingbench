#!/usr/bin/env python3
"""08_recommendation_curves_all.py — recommendation delivery for all five baselines.

Combines the sealed campaign's recommendation curves (out/curves.csv) with the
SMAC3 baseline's recommendations (data/recommendations.csv)
on the same checkpoint axis and the same delivery definition. Display names are
taken from common.METHOD_SHORT so the figure, the tables and the paper agree.

Outputs: figures/fig_recommendation_curves_all.pdf (+png)
         ../paper/fig_recommendation_curves.png (the copy the paper includes)
"""
from __future__ import annotations

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import (CONFIGS, CONFIG_SHORT, FIGURES, HIDDEN_CHECKPOINTS, METHODS,
                    METHOD_COLOR, METHOD_LS, METHOD_MARKER, METHOD_SHORT, OUT,
                    SMAC_ID, WORKSPACE, ensure_dirs, smac3_recommendations)

SMAC_SHORT = "SMAC3"
SMAC_STYLE = {"color": "#9467bd", "ls": "-", "marker": "P"}
PAPER_COPY = WORKSPACE / "paper" / "fig_recommendation_curves.png"


def xpos(q: int) -> float:
    return math.log2(q + 1) / math.log2(33)


def main() -> None:
    ensure_dirs()
    sealed = pd.read_csv(OUT / "curves.csv")
    smac = smac3_recommendations()
    smac["method"] = SMAC_ID
    curves = pd.concat([sealed[["config", "method", "checkpoint", "delivery"]],
                        smac[["config", "method", "checkpoint", "delivery"]]])

    methods = list(METHODS) + [SMAC_ID]
    short = dict(METHOD_SHORT, **{SMAC_ID: SMAC_SHORT})
    color = dict(METHOD_COLOR, **{SMAC_ID: SMAC_STYLE["color"]})
    ls = dict(METHOD_LS, **{SMAC_ID: SMAC_STYLE["ls"]})
    marker = dict(METHOD_MARKER, **{SMAC_ID: SMAC_STYLE["marker"]})

    fig, axes = plt.subplots(1, 3, figsize=(11.5, 3.4), sharey=True)
    for ax, cfg in zip(axes, CONFIGS):
        for m in methods:
            sub = curves[(curves.config == cfg) & (curves.method == m)]
            xs, ys = [], []
            for ck in HIDDEN_CHECKPOINTS:
                pts = sub[sub.checkpoint == ck]
                if len(pts):
                    xs.append(xpos(ck))
                    ys.append(pts["delivery"].mean())
            ax.plot(xs, ys, color=color[m], lw=1.6, ls=ls[m], marker=marker[m],
                    ms=4, label=short[m])
        ax.set_xticks([xpos(c) for c in HIDDEN_CHECKPOINTS])
        ax.set_xticklabels([str(c) for c in HIDDEN_CHECKPOINTS], fontsize=8)
        ax.set_title(CONFIG_SHORT[cfg], fontsize=10)
        ax.set_ylim(-0.03, 1.03)
        ax.set_xlabel("CFD evaluations used, $k$", fontsize=9)
        ax.grid(True, alpha=0.25)
    axes[0].set_ylabel("recommendation delivery", fontsize=9)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, loc="upper center", ncol=5, fontsize=8.5,
               frameon=False, bbox_to_anchor=(0.5, 1.02))
    fig.tight_layout(rect=(0, 0, 1, 0.9))
    for path in (FIGURES / "fig_recommendation_curves_all.pdf",
                 FIGURES / "fig_recommendation_curves_all.png", PAPER_COPY):
        fig.savefig(path, dpi=200 if path.suffix == ".png" else None, bbox_inches="tight")
    plt.close(fig)

    # print the plotted values so the figure can be checked against the tables
    for cfg in CONFIGS:
        for m in methods:
            sub = curves[(curves.config == cfg) & (curves.method == m)]
            vals = [f"{sub[sub.checkpoint == ck]['delivery'].mean():.2f}" for ck in HIDDEN_CHECKPOINTS]
            print(f"{CONFIG_SHORT[cfg]:20s} {short[m]:26s} " + " ".join(vals))
    print(f"wrote {PAPER_COPY.relative_to(WORKSPACE)}")


if __name__ == "__main__":
    main()
