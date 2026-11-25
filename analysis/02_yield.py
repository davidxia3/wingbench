#!/usr/bin/env python3
"""02_yield.py — grade yield (delivery) and no-verdict structure.

The benchmark treats nondelivery as scientific signal: what fraction of graded
designs produced a complete score-eligible three-condition result, and why not,
by method x load config x stream, and along the online query index.

Outputs: figures/fig_yield_by_cell.pdf
         figures/fig_online_yield_vs_index.pdf
         out/tables/tab_yield.tex (+csv)
         out/yield_stats.json
"""
from __future__ import annotations

import json

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from common import (
    CONFIGS,
    CONFIG_SHORT,
    FIGURES,
    METHODS,
    METHOD_COLOR,
    METHOD_SHORT,
    OUT,
    ensure_dirs,
    load_grades,
    snapshot_stamp,
    stamp_axes,
    tentative_banner,
    write_table,
)

CATEGORY_OF_CODE = {
    "complete_score_eligible": "success",
    "not_run_unmeshable": "mesh failed (unmeshable)",
    "not_run_mesh_inconclusive": "mesh failed (inconclusive)",
    "inconclusive_timeout": "solver timeout",
    "inconclusive_infrastructure": "no solver result",
}


def categorize(row) -> str:
    codes = [c for c in str(row["reason_codes"]).split(";") if c]
    if row["outcome_kind"] == "pass":
        return "success"
    if "not_run_unmeshable" in codes:
        return "mesh failed (unmeshable)"
    if "not_run_mesh_inconclusive" in codes:
        return "mesh failed (inconclusive)"
    # timeout/infrastructure may co-occur; timeout takes precedence for display
    if "inconclusive_timeout" in codes:
        return "solver timeout"
    if "inconclusive_infrastructure" in codes:
        return "no solver result"
    return "other"


CATEGORIES = ["success", "mesh failed (inconclusive)", "mesh failed (unmeshable)",
              "solver timeout", "no solver result", "other"]
CAT_COLOR = {
    "success": "#2ca02c",
    "mesh failed (inconclusive)": "#d8b365",
    "mesh failed (unmeshable)": "#8c510a",
    "solver timeout": "#9467bd",
    "no solver result": "#c7c7c7",
    "other": "#000000",
}


def main() -> None:
    ensure_dirs()
    g = load_grades()
    g["category"] = g.apply(categorize, axis=1)
    g["delivered"] = g["outcome_kind"] == "pass"

    # ------------------------------------------------------------------ figure 1
    # stacked outcome composition per method, one panel per config (online grades)
    on = g[g.stream == "online"]
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.6), sharey=True)
    for ax, cfg in zip(axes, CONFIGS):
        sub = on[on.config == cfg]
        comp = (sub.groupby("method")["category"]
                .value_counts(normalize=True).unstack(fill_value=0)
                .reindex(METHODS).reindex(columns=CATEGORIES, fill_value=0))
        bottom = pd.Series(0.0, index=comp.index)
        for cat in CATEGORIES:
            ax.bar([METHOD_SHORT[m] for m in comp.index], comp[cat],
                   bottom=bottom, label=cat, color=CAT_COLOR[cat], width=0.7)
            bottom += comp[cat]
        n = sub.groupby("method").size().reindex(METHODS)
        for i, m in enumerate(METHODS):
            ax.text(i, 1.02, f"n={n[m]}", ha="center", fontsize=6.5)
        ax.set_title(CONFIG_SHORT[cfg], fontsize=9)
        ax.set_ylim(0, 1.08)
        ax.tick_params(axis="x", rotation=30, labelsize=7)
    axes[0].set_ylabel("fraction of requested CFD evaluations")
    axes[-1].legend(fontsize=6.5, loc="center left", bbox_to_anchor=(1.01, 0.5))
    fig.suptitle("Outcomes of the CFD evaluations requested by each optimizer", fontsize=10)
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    stamp_axes(fig)
    fig.savefig(FIGURES / "fig_yield_by_cell.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_yield_by_cell.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ------------------------------------------------------------------ figure 2
    # delivery rate vs online query index (rolling window of 4), per method,
    # one panel per config; denominator = grades that exist at that index.
    fig, axes = plt.subplots(1, 3, figsize=(11, 3.2), sharey=True)
    for ax, cfg in zip(axes, CONFIGS):
        for m in METHODS:
            sub = on[(on.config == cfg) & (on.method == m)]
            rate = sub.groupby("checkpoint")["delivered"].mean()
            nn = sub.groupby("checkpoint").size()
            rate = rate.rolling(4, min_periods=1, center=True).mean()
            ax.plot(rate.index, rate.values, color=METHOD_COLOR[m],
                    label=METHOD_SHORT[m], lw=1.6)
        ax.set_title(CONFIG_SHORT[cfg], fontsize=9)
        ax.set_xlabel("CFD evaluation index within the run")
        ax.set_ylim(0, 1)
    axes[0].set_ylabel("success rate (rolling window of 4)")
    axes[0].legend(fontsize=7)
    fig.suptitle("Success rate of requested evaluations along the run", fontsize=10)
    fig.tight_layout()
    stamp_axes(fig, "denominator: episodes that reached each index")
    fig.savefig(FIGURES / "fig_online_yield_vs_index.pdf", bbox_inches="tight")
    fig.savefig(FIGURES / "fig_online_yield_vs_index.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    # ------------------------------------------------------------------ table
    tab = (on.groupby(["config", "method"])
           .agg(n=("delivered", "size"), delivery=("delivered", "mean"))
           .reset_index())
    tab["method"] = tab["method"].map(METHOD_SHORT)
    tab["config"] = tab["config"].map(CONFIG_SHORT)
    piv = tab.pivot(index="method", columns="config", values="delivery").round(3)
    ns = tab.pivot(index="method", columns="config", values="n")
    order = [METHOD_SHORT[m] for m in METHODS]
    piv, ns = piv.reindex(order), ns.reindex(order)

    lines = [
        "% AUTO-GENERATED by analysis/02_yield.py — do not edit",
        f"% {tentative_banner()}",
        r"\begin{tabular}{lccc}", r"\toprule",
        "method & " + " & ".join(piv.columns) + r" \\",
        r"\midrule",
    ]
    for m in piv.index:
        cells = [f"{piv.loc[m, c]:.2f} ({int(ns.loc[m, c])})" for c in piv.columns]
        lines.append(f"{m} & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular}"]
    write_table("tab_yield", "\n".join(lines), piv)

    hid = g[g.stream == "hidden"]
    stats = {
        "stamp": snapshot_stamp(),
        "tentative": tentative_banner(),
        "overall_online_delivery": float(on["delivered"].mean()),
        "overall_hidden_fresh_delivery": float(hid["delivered"].mean()),
        "online_category_counts": on["category"].value_counts().to_dict(),
        "per_cell_online_delivery": {
            f"{cfg}|{m}": float(on[(on.config == cfg) & (on.method == m)]["delivered"].mean())
            for cfg in CONFIGS for m in METHODS
        },
    }
    (OUT / "yield_stats.json").write_text(json.dumps(stats, indent=1))
    print(json.dumps({k: v for k, v in stats.items()
                      if k in ("overall_online_delivery",
                               "overall_hidden_fresh_delivery")}, indent=1))
    print("wrote fig_yield_by_cell, fig_online_yield_vs_index, tab_yield")


if __name__ == "__main__":
    main()
