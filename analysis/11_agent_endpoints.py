#!/usr/bin/env python3
"""Build paper results from delivery and continuous aerodynamic quality.

Reads the agent study's rows of data/episodes.csv, data/evaluations.csv and
data/recommendations.csv. The campaign's operational scoring configuration is
deliberately not used for any reported endpoint.
"""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
METHODS = {
    "S_OPEN_v2": "Open-ended research",
    "RAFA_BEST_OF_N_v1": "Planning",
    "S_OPEN_REFLEXION_v1": "Reflection",
}
TASKS = {
    "superwing-load-low-cl015": ("Low", "0.15"),
    "superwing-load-nominal-cl041": ("Nominal", "0.41"),
    "superwing-load-high-cl066": ("High", "0.66"),
}
OUT = ROOT / "analysis/out/agent_endpoints"
FIGURES = ROOT / "analysis/figures"
HORIZON = 32
VALUE_CEILING = 40.0  # Evaluator validity limit, not a performance goal.
# The bootstrap is seeded from the SHA-256 of the agent campaign's definition
# file, which was fixed before any result was seen.
BOOTSTRAP_SEED_SOURCE = "d4e25ae908016c56d28049e68e82f41197bdbbad11ce7113cf1f5df0a4912bc2"
IDENT = {name: ident for ident, name in METHODS.items()}
TASK_IDENT = {"low": "superwing-load-low-cl015", "nominal": "superwing-load-nominal-cl041",
              "high": "superwing-load-high-cl066"}


def load_agent_study():
    """The agent study's episodes and graded recommendations from data/."""
    def rows(name):
        with (ROOT / "data" / f"{name}.csv").open() as f:
            return [r for r in csv.DictReader(f) if r["study"] == "agent"]

    evaluations, recommendations = rows("evaluations"), rows("recommendations")
    episodes = []
    for e in rows("episodes"):
        online = sorted((r for r in evaluations if r["episode"] == e["episode"] and r["kind"] == "online"),
                        key=lambda r: int(r["query"]))
        episodes.append({
            "method": IDENT[e["method"]], "task": TASK_IDENT[e["task"]], "seed": e["seed"],
            "online": [{"query": int(r["query"]), "delivery": r["delivered"] == "True",
                        "conditional_value": float(r["ld"]) if r["ld"] else None} for r in online],
            "checkpoints": [{"checkpoint": int(r["checkpoint"]), "fresh_grade_consumed": r["graded_by"] == "private"}
                            for r in recommendations if r["episode"] == e["episode"]],
        })
    ledger = [{"method_id": IDENT[r["method"]], "task_id": TASK_IDENT[r["task"]], "seed_label": f"s{r['seed']}",
               "checkpoint": int(r["checkpoint"]), "status": "resolved", "delivered": r["delivered"] == "True",
               "ld": float(r["ld"]) if r["ld"] else None}
              for r in recommendations if int(r["checkpoint"]) > 0]
    return {"episodes": episodes}, {"rows": ledger}


def aggregate(rows, delivered_key="delivered", value_key="ld"):
    values = []
    for row in rows:
        if row[delivered_key]:
            value = row[value_key]
            if value is None or not 0 < float(value) <= VALUE_CEILING:
                raise ValueError("Delivered record has an invalid L/D")
            values.append(float(value))
        elif row[value_key] is not None:
            raise ValueError("Nondelivery must not acquire a numerical score")
    return {
        "n": len(rows), "delivered": len(values),
        "delivery_rate": len(values) / len(rows) if rows else None,
        "mean_ld": float(np.mean(values)) if values else None,
        "median_ld": float(np.median(values)) if values else None,
    }


def contrast(a, b, rng):
    diffs = np.asarray(a, dtype=float) - np.asarray(b, dtype=float)
    if not len(diffs):
        return {"n": 0, "estimate": None, "ci95": None}
    draws = rng.choice(diffs, size=(10000, len(diffs)), replace=True).mean(axis=1)
    return {"n": len(diffs), "estimate": float(diffs.mean()),
            "ci95": np.quantile(draws, [0.025, 0.975]).tolist()}


def extension_at(episodes, query):
    """Bound missing evaluations without fabricating successful designs.

    Every reported average of episode maxima conditions on an episode having
    at least one delivered online design. Missing evaluations can improve an
    existing maximum or add a newly delivering episode to that population.
    """
    rows = [r for e in episodes for r in e["online"] if r["query"] <= query]
    observed = aggregate(rows, "delivery", "conditional_value")
    missing = len(episodes) * query - len(rows)
    known_best, optimistic_best = [], []
    possibly_new = 0
    episodes_with_missing = 0
    for ep in episodes:
        rs = [r for r in ep["online"] if r["query"] <= query]
        unfinished = len(rs) < query
        episodes_with_missing += int(unfinished)
        values = [float(r["conditional_value"]) for r in rs if r["delivery"]]
        if values:
            best = max(values)
            known_best.append(best)
            optimistic_best.append(VALUE_CEILING if unfinished else best)
        elif unfinished:
            possibly_new += 1
            optimistic_best.append(VALUE_CEILING)
    largest_population = len(known_best) + possibly_new
    best_bounds = [
        sum(known_best) / largest_population if largest_population else None,
        float(np.mean(optimistic_best)) if optimistic_best else None,
    ]
    return {
        "query": query, "observed": observed, "missing": missing,
        "episodes_with_missing": episodes_with_missing,
        "episodes_with_delivery": len(known_best),
        "delivery_bounds": [observed["delivered"] / (len(episodes) * query),
                            (observed["delivered"] + missing) / (len(episodes) * query)],
        "mean_best_ld_bounds": best_bounds,
    }


def number(value):
    return "n/a" if value is None else f"{value:.2f}"


def interval(bounds, collapse=True):
    lo, hi = bounds
    if lo is None:
        return "n/a"
    return number(lo) if collapse and np.isclose(lo, hi) else f"[{lo:.2f}, {hi:.2f}]"


def table(path, spec, header, rows):
    lines = [f"\\begin{{tabular}}{{{spec}}}", r"\toprule", header + r" \\", r"\midrule"]
    lines += [" & ".join(row) + r" \\" for row in rows]
    lines += [r"\bottomrule", r"\end{tabular}"]
    path.write_text("\n".join(lines) + "\n")


def analyze():
    snapshot, ledger = load_agent_study()
    if len(snapshot["episodes"]) != 90 or len(ledger["rows"]) != 270:
        raise ValueError("Expected 90 episodes and 270 recommendation records")
    seeds = sorted({r["seed_label"] for r in ledger["rows"]})
    if len(seeds) != 10:
        raise ValueError("Expected ten seeds")
    index = {}
    for r in ledger["rows"]:
        key = (r["method_id"], r["task_id"], r["checkpoint"], r["seed_label"])
        if key in index or r["status"] != "resolved":
            raise ValueError("Duplicate or unresolved recommendation")
        index[key] = r
    for ep in snapshot["episodes"]:
        queries = sorted(r["query"] for r in ep["online"])
        if queries != list(range(1, len(queries) + 1)) or not 4 <= len(queries) <= HORIZON:
            raise ValueError("Online sequence must be unique and contiguous")
        aggregate(ep["online"], "delivery", "conditional_value")
    identities = {(e["method"], e["task"], e["seed"]) for e in snapshot["episodes"]}
    if len(identities) != 90:
        raise ValueError("Duplicate episode identity")

    cells = {}
    for method in METHODS:
        cells[method] = {}
        for task in TASKS:
            cells[method][task] = {
                q: aggregate([index[(method, task, q, s)] for s in seeds])
                for q in (1, 2, 4)
            }
    rng = np.random.default_rng(int(BOOTSTRAP_SEED_SOURCE[:16], 16))
    contrasts = {}
    for other in list(METHODS)[1:]:
        contrasts[other] = {}
        for task in TASKS:
            a = [index[("S_OPEN_v2", task, 4, s)] for s in seeds]
            b = [index[(other, task, 4, s)] for s in seeds]
            both = [(x["ld"], y["ld"]) for x, y in zip(a, b) if x["delivered"] and y["delivered"]]
            contrasts[other][task] = {
                "delivery": contrast([r["delivered"] for r in a], [r["delivered"] for r in b], rng),
                "paired_ld": contrast([p[0] for p in both], [p[1] for p in both], rng),
            }
    extension = {}
    for task in TASKS:
        eps = [e for e in snapshot["episodes"] if e["method"] == "S_OPEN_v2" and e["task"] == task]
        if len(eps) != 10:
            raise ValueError("Expected ten extension episodes per task")
        extension[task] = {q: extension_at(eps, q) for q in range(1, HORIZON + 1)}

    OUT.mkdir(parents=True, exist_ok=True)
    counts = {m: sum(cells[m][t][4]["delivered"] for t in TASKS) for m in METHODS}
    result = {
        "schema": "wingbench_delivery_quality_v1",
        "inputs": {f"data/{n}.csv": hashlib.sha256((ROOT / "data" / f"{n}.csv").read_bytes()).hexdigest()
                   for n in ("episodes", "evaluations", "recommendations")},
        "q4_delivered_totals": counts, "recommendations": cells,
        "q4_open_ended_minus_comparator": contrasts, "extension": extension,
    }
    (OUT / "summary.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    rows = []
    for method, name in METHODS.items():
        row = [name]
        for task in TASKS:
            c = cells[method][task][4]
            row += [f"{c['delivered']}/{c['n']}", number(c["mean_ld"])]
        rows.append(row)
    header = (r"method & \multicolumn{2}{c}{$C_L=0.15$} & \multicolumn{2}{c}{$C_L=0.41$} & \multicolumn{2}{c}{$C_L=0.66$} \\" + "\n" +
              r" & delivered & mean $L/D$ & delivered & mean $L/D$ & delivered & mean $L/D$")
    table(OUT / "recommendations.tex", "lrrrrrr", header, rows)
    rows = []
    for task, (name, cl) in TASKS.items():
        e = extension[task]
        rows.append([f"${cl}$", interval(e[32]["delivery_bounds"])] +
                    [interval(e[q]["mean_best_ld_bounds"]) for q in (4, 16, 32)])
    table(OUT / "extension.tex", "lrrrr", r"$C_L$ & delivery through query 32 & best $L/D$ at 4 & at 16 & at 32", rows)
    rows = []
    for other, by_task in contrasts.items():
        for task, record in by_task.items():
            d, v = record["delivery"], record["paired_ld"]
            rows.append([METHODS[other], f"${TASKS[task][1]}$", number(d["estimate"]),
                         interval(d["ci95"], False), str(v["n"]), number(v["estimate"]), interval(v["ci95"], False)])
    table(OUT / "contrasts.tex", "llrrrrr", r"comparator & $C_L$ & $\Delta$ delivery & 95\% CI & paired $n$ & $\Delta L/D$ & 95\% CI", rows)

    macros = {"AgentOnlineObserved": sum(len(e["online"]) for e in snapshot["episodes"]),
              "UnifiedCFDObserved": 5293 + sum(len(e["online"]) for e in snapshot["episodes"]) +
                                    sum(c["fresh_grade_consumed"] for e in snapshot["episodes"] for c in e["checkpoints"])}
    for task, (name, _) in TASKS.items():
        for q in (4, 16, 32):
            macros[f"Agent{name}BestAt{chr(65 + (4, 16, 32).index(q))}"] = interval(extension[task][q]["mean_best_ld_bounds"])
        macros[f"Agent{name}DeliveryFinal"] = interval(extension[task][32]["delivery_bounds"])
    for method, label in (("S_OPEN_v2", "Open"), ("RAFA_BEST_OF_N_v1", "Planning"), ("S_OPEN_REFLEXION_v1", "Reflection")):
        macros[f"{label}DeliveredAtFour"] = counts[method]
        for task, (name, _) in TASKS.items():
            macros[f"{label}{name}MeanAtFour"] = number(cells[method][task][4]["mean_ld"])
    (ROOT / "paper/agent_endpoint_numbers.tex").write_text(
        "% Generated by analysis/11_agent_endpoints.py\n" +
        "\n".join(f"\\newcommand{{\\{k}}}{{{v}}}" for k, v in macros.items()) + "\n")
    make_figures(cells, extension)
    print(json.dumps({"q4_delivered": counts, "contrasts": contrasts,
                      "extension": {t: {q: e[q] for q in (4, 16, 32)} for t, e in extension.items()}}, indent=2))


def make_figures(cells, extension):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#2563a6", "#c75c1c", "#7d4eab"]
    FIGURES.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(2, 3, figsize=(10, 5.3), sharex=True, constrained_layout=True)
    for j, (task, (name, cl)) in enumerate(TASKS.items()):
        for (method, label), color in zip(METHODS.items(), colors):
            data = [cells[method][task][q] for q in (1, 2, 4)]
            axes[0, j].plot([1, 2, 4], [d["delivery_rate"] for d in data], marker="o", color=color, label=label)
            axes[1, j].plot([1, 2, 4], [d["mean_ld"] for d in data], marker="o", color=color)
        axes[0, j].set_title(f"{name} load  $C_L={cl}$")
        axes[0, j].set_ylim(-0.04, 1.04)
        axes[1, j].set_xlabel("CFD queries")
        for ax in axes[:, j]:
            ax.set_xticks([1, 2, 4])
            ax.grid(alpha=.2)
    axes[0, 0].set_ylabel("Recommendation delivery")
    axes[1, 0].set_ylabel("Mean L/D among delivered")
    axes[0, 0].legend(frameon=False, fontsize=8, loc="lower right")
    for suffix in ("pdf", "png"):
        fig.savefig(FIGURES / f"fig_agent_endpoints.{suffix}", dpi=220)
    plt.close(fig)
    fig, axes = plt.subplots(1, 3, figsize=(10, 2.8), constrained_layout=True)
    for ax, (task, (name, cl)), color in zip(axes, TASKS.items(), colors):
        e = extension[task]
        x = np.arange(1, HORIZON + 1)
        lo, hi = np.array([e[int(q)]["mean_best_ld_bounds"] for q in x], dtype=float).T
        exact = np.array([e[int(q)]["missing"] == 0 for q in x])
        ax.plot(x, np.where(exact, lo, np.nan), color=color, lw=2)
        tail = np.where(exact)[0][-1] if exact.any() else 0
        ax.plot(x[tail:], lo[tail:], color=color, ls="--", lw=1.5)
        ax.plot(x[tail:], hi[tail:], color=color, ls="--", lw=1.5)
        ax.fill_between(x, lo, hi, color=color, alpha=.16)
        ax.set_title(f"{name} load  $C_L={cl}$")
        ax.set_xlabel("Online CFD queries")
        ax.set_xticks([1, 8, 16, 24, 32])
        ax.grid(alpha=.2)
    axes[0].set_ylabel("Mean episode-best L/D")
    for suffix in ("pdf", "png"):
        fig.savefig(FIGURES / f"fig_agent_search_quality.{suffix}", dpi=220)
    plt.close(fig)


if __name__ == "__main__":
    analyze()
