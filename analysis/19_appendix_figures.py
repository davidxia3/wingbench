#!/usr/bin/env python3
"""19_appendix_figures.py — the four supplementary figures, drawn in the paper's style.

Redraws, without changing them, the figures that 02_yield.py and
04_discovery_vs_recommendation.py write into analysis/figures/:

  fig_yield_by_cell               -> paper/app_fig_yield_by_cell.pdf
  fig_online_yield_vs_index       -> paper/app_fig_online_yield_vs_index.pdf
  fig_discovery_vs_recommendation -> paper/app_fig_discovery_vs_recommendation.pdf
  fig_fresh_hidden_vs_online_yield-> paper/app_fig_fresh_hidden_vs_online_yield.pdf

The data preparation is imported from those two scripts (the outcome grouping
`categorize` of 02_yield.py and the checkpoint axis `xpos` of
04_discovery_vs_recommendation.py) and the same working tables are read
(analysis/out/grades.csv, curves.csv and discovery.csv, which 01_load.py
builds from data/evaluations.csv and data/recommendations.csv). Only the
drawing differs: the paper's palette, Times at 8 pt (ticks, legends) and 9 pt
(axis labels, panel titles), 5.5 inches wide so that \\includegraphics at
\\textwidth shows the text at its true size, no title and no data stamp.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import math

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D
from matplotlib.ticker import FormatStrFormatter, MultipleLocator
from matplotlib.patches import Patch

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from common import (  # noqa: E402
    CONFIGS,
    HIDDEN_CHECKPOINTS,
    METHODS,
    OUT,
    SMAC_ID,
    WORKSPACE,
    load_grades,
)

PAPER = WORKSPACE / "paper"
WIDTH = 5.5  # inches, the text width of the ICLR style

# The data preparation of the two figure scripts, imported so that it cannot
# drift from them. Their main() is not run.
_yield = importlib.import_module("02_yield")
_disc = importlib.import_module("04_discovery_vs_recommendation")
categorize = _yield.categorize
xpos = _disc.xpos

# ---------------------------------------------------------------- paper style
# Colors and line styles of the paper's TikZ figures (see the \tikzset in
# paper/main.tex): navy-light dashed squares, navy circles, maroon diamonds,
# teal dash-dotted triangles; SMAC3 and the agent methods are listed for
# completeness but do not occur in these four figures.
METHOD_STYLE = {
    "static-precommitted-cmaes-v2-pilot": dict(color="#7EA4C2", ls=(0, (4, 2)), marker="s"),
    "conventional-adaptive-cmaes-v2-pilot": dict(color="#004E85", ls="-", marker="o"),
    "fsd-cma-v1-port-v2-pilot": dict(color="#8F1F22", ls="-", marker="D"),
    "checkpointed-de-feedback-v2-pilot": dict(color="#107A83", ls=(0, (4, 1.5, 1, 1.5)), marker="^"),
    SMAC_ID: dict(color="#DF8932", ls=(0, (1, 1.5)), marker="+"),
    "S_OPEN_v2": dict(color="#56669E", ls="-", marker="o"),
}
METHOD_LABEL = {
    "static-precommitted-cmaes-v2-pilot": "CMA-ES, no feedback",
    "conventional-adaptive-cmaes-v2-pilot": "CMA-ES, selection only",
    "fsd-cma-v1-port-v2-pilot": "Feedback-guided CMA-ES",
    "checkpointed-de-feedback-v2-pilot": "Feedback-guided DE",
    SMAC_ID: "SMAC3",
    "S_OPEN_v2": "Open-ended research",
}
TASK_TITLE = {
    "superwing-load-low-cl015": r"$C_L=0.15$ (low)",
    "superwing-load-nominal-cl041": r"$C_L=0.41$ (nominal)",
    "superwing-load-high-cl066": r"$C_L=0.66$ (high)",
}
# The outcome groups of 02_yield.categorize, in the order of the paper's
# outcome table (first failing stage), with the paper's short names.
OUTCOME_ORDER = ["success", "mesh failed (unmeshable)", "mesh failed (inconclusive)",
                 "solver timeout", "no solver result", "other"]
OUTCOME_LABEL = {
    "success": "delivered",
    "mesh failed (unmeshable)": "geometry rejected",
    "mesh failed (inconclusive)": "mesh stop",
    "solver timeout": "flow time limit",
    "no solver result": "solver error",
    "other": "other",
}
OUTCOME_COLOR = {
    "success": "#107A83",
    "mesh failed (unmeshable)": "#8F1F22",
    "mesh failed (inconclusive)": "#BF888E",
    "solver timeout": "#DF8932",
    "no solver result": "#E6C08C",
    "other": "#9A9AB9",
}
FRAME = "#666666"   # the paper's wbframe, black!60
GRID = "#E0E0E0"    # the paper's wbgrid, black!12
NEUTRAL = "#777777"

plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "STIX Two Text", "STIXGeneral", "DejaVu Serif"],
    "mathtext.fontset": "stix",
    "font.size": 8,
    "axes.titlesize": 9,
    "axes.labelsize": 9,
    "xtick.labelsize": 8,
    "ytick.labelsize": 8,
    "legend.fontsize": 8,
    "legend.frameon": False,
    "legend.handlelength": 2.0,
    "legend.handletextpad": 0.5,
    "legend.columnspacing": 1.2,
    "legend.borderaxespad": 0.0,
    "axes.edgecolor": FRAME,
    "axes.linewidth": 0.5,
    "xtick.color": FRAME,
    "ytick.color": FRAME,
    "xtick.labelcolor": "black",
    "ytick.labelcolor": "black",
    "xtick.major.width": 0.5,
    "ytick.major.width": 0.5,
    "xtick.major.size": 2.5,
    "ytick.major.size": 2.5,
    "grid.color": GRID,
    "grid.linewidth": 0.35,
    "lines.linewidth": 0.8,
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "savefig.facecolor": "white",
    "pdf.fonttype": 42,
})


def method_line(m: str, lw: float = 1.2, marker: bool = True) -> Line2D:
    """A legend key in the method's style."""
    st = METHOD_STYLE[m]
    kw = dict(color=st["color"], ls=st["ls"], lw=lw)
    if marker:
        kw.update(marker=st["marker"], ms=3.2, mfc="white", mec=st["color"], mew=0.8)
    return Line2D([], [], **kw)


def key_rows(fig, rows, gap: float = 0.004) -> float:
    """Rows of legend keys across the top of the figure, as in the paper.

    Each row is (handles, labels, ncol). The rows are stacked from the top
    edge down and measured after drawing, so a row that is wider than the
    figure fails loudly instead of being clipped. Returns the bottom of the
    block as a figure fraction, for the rect of tight_layout.
    """
    y = 1.0
    for handles, labels, ncol in rows:
        leg = fig.legend(handles, labels, loc="upper center", bbox_to_anchor=(0.5, y),
                         ncol=ncol, frameon=False)
        fig.canvas.draw()
        bb = leg.get_window_extent().transformed(fig.transFigure.inverted())
        assert bb.x0 > 0.005 and bb.x1 < 0.995, f"legend row wider than the figure: {labels}"
        y = bb.y0 - gap
    return y


def grid(ax, axis: str) -> None:
    ax.grid(True, axis=axis, color=GRID, linewidth=0.35)
    ax.set_axisbelow(True)


def save(fig, name: str) -> Path:
    path = PAPER / f"app_fig_{name}.pdf"
    fig.savefig(path, metadata={"CreationDate": None})
    plt.close(fig)
    return path


# ---------------------------------------------------------------------- data
def working_tables():
    """grades, curves and discovery as 02_yield.py and 04_... read them."""
    if not (OUT / "grades.csv").exists():
        importlib.import_module("01_load").main()
    grades = load_grades()
    grades["category"] = grades.apply(categorize, axis=1)
    grades["delivered"] = grades["outcome_kind"] == "pass"
    curves = pd.read_csv(OUT / "curves.csv")
    disc = pd.read_csv(OUT / "discovery.csv")
    return grades, curves, disc


# ------------------------------------------------------------------- figures
def fig_yield_by_cell(grades) -> dict:
    """Outcome composition of the online CFD evaluations, per method and task."""
    on = grades[grades.stream == "online"]
    present = [c for c in OUTCOME_ORDER if (on.category == c).any()]
    unknown = set(on.category) - set(OUTCOME_ORDER)
    assert not unknown, f"unexpected outcome groups {unknown}"

    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.8), sharey=True)
    y = np.arange(len(METHODS))
    counts = {}
    for ax, cfg in zip(axes, CONFIGS):
        sub = on[on.config == cfg]
        comp = (sub.groupby("method")["category"]
                .value_counts(normalize=True).unstack(fill_value=0)
                .reindex(METHODS).reindex(columns=OUTCOME_ORDER, fill_value=0))
        left = np.zeros(len(METHODS))
        for cat in present:
            ax.barh(y, comp[cat].values, left=left, height=0.68,
                    color=OUTCOME_COLOR[cat], edgecolor="white", linewidth=0.4)
            left += comp[cat].values
        n = sub.groupby("method").size().reindex(METHODS)
        counts[cfg] = {m: int(n[m]) for m in METHODS}
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_xticklabels(["0", "0.25", "0.5", "0.75", "1"])
        ax.set_title(TASK_TITLE[cfg])
        grid(ax, "x")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([METHOD_LABEL[m] for m in METHODS])
    axes[0].invert_yaxis()
    axes[1].set_xlabel("share of the online CFD evaluations")
    top = key_rows(fig, [([Patch(facecolor=OUTCOME_COLOR[c], edgecolor="white", linewidth=0.4)
                           for c in present], [OUTCOME_LABEL[c] for c in present], len(present))])
    fig.tight_layout(rect=(0, 0, 1, top - 0.01), w_pad=0.6)
    return {"path": save(fig, "yield_by_cell"), "n_online": counts,
            "outcome_groups_drawn": [OUTCOME_LABEL[c] for c in present]}


def fig_online_yield_vs_index(grades) -> dict:
    """Delivery rate along the online query index, window of four, per method."""
    on = grades[grades.stream == "online"]
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 2.8), sharey=True)
    for ax, cfg in zip(axes, CONFIGS):
        for m in METHODS:
            sub = on[(on.config == cfg) & (on.method == m)]
            rate = sub.groupby("checkpoint")["delivered"].mean()
            rate = rate.rolling(4, min_periods=1, center=True).mean()
            st = METHOD_STYLE[m]
            ax.plot(rate.index, rate.values, color=st["color"], ls=st["ls"], lw=0.9)
        ax.set_xlim(1, 32)
        ax.set_xticks([1, 8, 16, 24, 32])
        ax.set_ylim(0, 1)
        ax.set_yticks([0, 0.2, 0.4, 0.6, 0.8, 1.0])
        ax.set_title(TASK_TITLE[cfg])
        ax.set_xlabel("online CFD query")
        grid(ax, "both")
    axes[0].set_ylabel("delivery rate, window of 4")
    top = key_rows(fig, [([method_line(m, lw=0.9, marker=False) for m in METHODS],
                          [METHOD_LABEL[m] for m in METHODS], 2)])
    fig.tight_layout(rect=(0, 0, 1, top - 0.01), w_pad=0.6)
    return {"path": save(fig, "online_yield_vs_index"),
            "runs_per_cell": int(on.groupby(["config", "method"]).seed.nunique().min()),
            "queries_per_run": int(on.checkpoint.max())}


def fig_discovery_vs_recommendation(curves, disc) -> dict:
    """Best delivered online score so far (thin) against the graded checkpoint
    recommendation (thick), means over runs, per method and task."""
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 3.2))
    absent = []
    for ax, cfg in zip(axes, CONFIGS):
        for m in METHODS:
            st = METHOD_STYLE[m]
            d = disc[(disc.config == cfg) & (disc.method == m)]
            mean_best = d.groupby("query")["best_conditional_value"].mean().dropna()
            c = curves[(curves.config == cfg) & (curves.method == m)]
            xs, vals = [], []
            for ck in HIDDEN_CHECKPOINTS:
                pts = c[(c.checkpoint == ck) & (c.delivery == True)]  # noqa: E712
                if len(pts):
                    xs.append(xpos(ck))
                    vals.append(pts["conditional_value"].mean())
            if not len(mean_best) and not xs:
                absent.append((cfg, m))
                continue
            ax.plot([xpos(q) for q in mean_best.index], mean_best.values,
                    color=st["color"], ls=st["ls"], lw=0.6, alpha=0.55, zorder=2)
            ax.plot(xs, vals, color=st["color"], ls=st["ls"], lw=1.3,
                    marker=st["marker"], ms=3.2, mfc="white", mec=st["color"], mew=0.8,
                    zorder=3)
        ax.set_xticks([xpos(c) for c in HIDDEN_CHECKPOINTS])
        ax.set_xticklabels([str(c) for c in HIDDEN_CHECKPOINTS])
        ax.set_xlim(-0.03, 1.03)
        ax.set_title(TASK_TITLE[cfg])
        ax.set_xlabel("online CFD queries used")
        grid(ax, "both")
    # one y-axis convention for the three panels: ticks every 2 L/D, integer labels,
    # limits snapped to even values, so the panels differ only in their range
    for ax in axes:
        lo, hi = ax.get_ylim()
        ax.set_ylim(2 * math.floor(lo / 2), 2 * math.ceil(hi / 2))
        ax.yaxis.set_major_locator(MultipleLocator(2))
        ax.yaxis.set_major_formatter(FormatStrFormatter("%d"))
    axes[0].set_ylabel("$L/D$")
    top = key_rows(fig, [
        ([method_line(m) for m in METHODS], [METHOD_LABEL[m] for m in METHODS], 2),
        ([Line2D([], [], color=NEUTRAL, lw=0.6, alpha=0.7),
          Line2D([], [], color=NEUTRAL, lw=1.3, marker="o", ms=3.2,
                 mfc="white", mec=NEUTRAL, mew=0.8)],
         ["thin: best delivered online score so far",
          "thick: graded checkpoint recommendation"], 2),
    ])
    fig.tight_layout(rect=(0, 0, 1, top - 0.01), w_pad=0.8)
    return {"path": save(fig, "discovery_vs_recommendation"),
            "absent_cells": [(cfg, METHOD_LABEL[m]) for cfg, m in absent]}


def fig_fresh_hidden_vs_online_yield(grades) -> dict:
    """Online delivery rate against the delivery rate of the private grades of
    recommendations the optimizer had not tested, per method and task."""
    fig, axes = plt.subplots(1, 3, figsize=(WIDTH, 3.0), sharey=True)
    h = 0.36
    y = np.arange(len(METHODS))
    cells = {}
    for ax, cfg in zip(axes, CONFIGS):
        sub = grades[grades.config == cfg]
        on = (sub[sub.stream == "online"].groupby("method")["delivered"]
              .agg(["mean", "size"]).reindex(METHODS))
        hi = (sub[sub.stream == "hidden"].groupby("method")["delivered"]
              .agg(["mean", "size"]).reindex(METHODS))
        for i, m in enumerate(METHODS):
            col = METHOD_STYLE[m]["color"]
            ov = 0.0 if np.isnan(on.loc[m, "mean"]) else float(on.loc[m, "mean"])
            hv = 0.0 if np.isnan(hi.loc[m, "mean"]) else float(hi.loc[m, "mean"])
            ax.barh(i - h / 2, ov, h, color=col, edgecolor=col, linewidth=0.5)
            ax.barh(i + h / 2, hv, h, color="white", edgecolor=col, linewidth=0.6, hatch="////")
            n_hi = 0 if np.isnan(hi.loc[m, "size"]) else int(hi.loc[m, "size"])
            ax.text(hv + 0.015, i + h / 2, str(n_hi), va="center", ha="left", fontsize=8)
            cells[(cfg, METHOD_LABEL[m])] = {
                "online_delivery": round(ov, 4), "n_online": int(on.loc[m, "size"]),
                "private_delivery": round(hv, 4), "n_private": n_hi}
        ax.set_xlim(0, 1)
        ax.set_xticks([0, 0.25, 0.5, 0.75, 1])
        ax.set_xticklabels(["0", "0.25", "0.5", "0.75", "1"])
        ax.set_title(TASK_TITLE[cfg])
        grid(ax, "x")
    axes[0].set_yticks(y)
    axes[0].set_yticklabels([METHOD_LABEL[m] for m in METHODS])
    axes[0].invert_yaxis()
    axes[1].set_xlabel("delivery rate")
    top = key_rows(fig, [([Patch(facecolor=NEUTRAL, edgecolor=NEUTRAL, linewidth=0.5),
                           Patch(facecolor="white", edgecolor=NEUTRAL, linewidth=0.6, hatch="////")],
                          ["online CFD queries", "private grades of recommendations not tested online"], 2)])
    fig.tight_layout(rect=(0, 0, 1, top - 0.01), w_pad=0.6)
    return {"path": save(fig, "fresh_hidden_vs_online_yield"), "cells": cells}


def main() -> None:
    grades, curves, disc = working_tables()
    r1 = fig_yield_by_cell(grades)
    r2 = fig_online_yield_vs_index(grades)
    r3 = fig_discovery_vs_recommendation(curves, disc)
    r4 = fig_fresh_hidden_vs_online_yield(grades)
    for r in (r1, r2, r3, r4):
        print("wrote", r["path"].relative_to(WORKSPACE))
    print("outcome groups drawn:", r1["outcome_groups_drawn"])
    print("online evaluations per bar:", sorted({n for d in r1["n_online"].values() for n in d.values()}))
    print("runs per cell:", r2["runs_per_cell"], " queries per run:", r2["queries_per_run"])
    print("cells without any delivered point:", r3["absent_cells"])
    for k, v in r4["cells"].items():
        print(f"  {k[0]:30s} {k[1]:24s} online {v['online_delivery']:.2f} (n={v['n_online']})"
              f"  private {v['private_delivery']:.2f} (n={v['n_private']})")


if __name__ == "__main__":
    main()
