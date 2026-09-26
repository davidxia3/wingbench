#!/usr/bin/env python3
"""Draw and tabulate the best delivered wing of every method and task.

This builds the appendix on the best delivered wings: a grid of wings with one
row per task and one column per method, and a table of their planform values.

Selection. For each of the five baseline optimizers and each task, the
delivered recommendation at checkpoint 32 with the highest L/D over the ten
seeds. For open-ended research, which made no further requested checkpoint recommendation after query 4,
the delivered online evaluation with the highest L/D among queries 5 to 32 of
its extension episodes. A method with no delivered recommendation for a task
gets an empty cell, and the text says so.

Drawing. Every wing goes through the Figure 1 code path: build_wing lofts the
38 coordinates as the dataset's mesh generator does, and draw() from
14_wing_meshing_examples.py renders the surface. All wings share one set of
axis limits, so they are drawn at one scale. As in Figure 1, each wing is
first drawn alone on a panel with Figure 1's proportions to find the tight box
around its visible pixels. The panel is then scaled so that the widest box
fills a cell, and every panel is placed so that its box is centred in its
cell. The figure is saved at its printed size, 5.5 in wide.

Table. The four planform values are the design variables the released
parameterization guide (agents/workspace/source/PARAMETERIZATION.md) defines
without an interpolation rule: leading-edge sweep in degrees (column 0),
aspect ratio (3), taper ratio (4) and root thickness ratio (37). The guide
describes the twist and thickness station values in a way that differs from
how the mesh generator applies them (it compounds them from the root), so the
table leaves them out. The CSV keeps the four twist coordinates and the tip
twist and tip thickness ratio of the wing as built, for reference.

Outputs
    paper/fig_wing_gallery.pdf          the figure, at printed size
    paper/app_gallery.tex               the appendix fragment, text and table
    analysis/out/tables/tab_gallery.csv the 18 cells with wing ids and numbers

Run from the repository root:  python3 analysis/18_wing_gallery.py
The output is deterministic (the PDF carries no creation date).
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import TABLES, WORKSPACE, ensure_dirs, read_table  # noqa: E402
from render_wing import build_wing  # noqa: E402

# Figure 1's drawing code. draw() renders one wing surface, limits() gives the
# shared axis limits, PANEL_W/PANEL_H is its panel and DPI its measuring
# resolution. Importing it also sets the paper's matplotlib style.
fig1 = importlib.import_module("14_wing_meshing_examples")

FIG_OUT = WORKSPACE / "paper" / "fig_wing_gallery.pdf"
TEX_OUT = WORKSPACE / "paper" / "app_gallery.tex"
CSV_OUT = TABLES / "tab_gallery.csv"

plt.rcParams["mathtext.fontset"] = "stix"  # Times-like math for the labels

# (name in data/, display name, column title, colour from the paper's palette)
METHODS = [
    ("CMA-ES no feedback", "CMA-ES, no feedback", "CMA-ES,\nno feedback", "#7EA4C2"),
    ("CMA-ES selection only", "CMA-ES, selection only", "CMA-ES,\nselection only", "#004E85"),
    ("CMA-ES feedback-guided", "Feedback-guided CMA-ES", "Feedback-guided\nCMA-ES", "#8F1F22"),
    ("DE feedback-guided", "Feedback-guided DE", "Feedback-guided\nDE", "#107A83"),
    ("SMAC3", "SMAC3", "SMAC3", "#DF8932"),
    ("Open-ended research", "Open-ended research", "Open-ended\nresearch", "#56669E"),
]
TASKS = [("low", "0.15"), ("nominal", "0.41"), ("high", "0.66")]
# The display names as they read inside a sentence.
IN_SENTENCE = {
    "CMA-ES, no feedback": "CMA-ES with no feedback",
    "CMA-ES, selection only": "CMA-ES with selection only",
    "Feedback-guided CMA-ES": "feedback-guided CMA-ES",
    "Feedback-guided DE": "feedback-guided DE",
    "SMAC3": "SMAC3",
    "Open-ended research": "open-ended research",
}
AGENT = "Open-ended research"
CHECKPOINT = 32        # the baselines' final recommendation
ONLINE_QUERIES = 32    # queries per episode in the baseline study and the extension
EXTENSION_FROM = 5     # first online query of the agent's extension
EPISODES_PER_CELL = 10

# The planform values of the table: CSV name, column of wings.csv, table
# header, decimals in the table.
GEOMETRY = [
    ("sweep_deg", "x00", r"sweep ($^\circ$)", 1),
    ("aspect_ratio", "x03", "aspect ratio", 2),
    ("taper_ratio", "x04", "taper ratio", 3),
    ("root_thickness_ratio", "x37", r"root $t/c$", 3),
]

# Layout of the figure, in inches.
FIG_W = 5.5                 # the text width
LABEL_W = 0.22              # rotated row labels at the left
RIGHT = 0.02
COLS, ROWS = len(METHODS), len(TASKS)
CELL_W = (FIG_W - LABEL_W - RIGHT) / COLS
PAD_X = 0.035               # clearance between the widest wing and its cell's sides
PAD_Y = 0.02                # clearance above and below the tallest wing
TITLE_H = 0.30              # band for the column titles
LD_H = 0.15                 # band under every wing for its L/D
ROW_GAP = 0.02
BOTTOM = 0.02
TEXT = "#192b39"            # the outline colour of Figure 1
FADED = "#8a8a8a"


def select() -> pd.DataFrame:
    """One row per (method, task) with the chosen wing, or a note when the
    method delivered nothing for that task."""
    rc = read_table("recommendations")
    ev = read_table("evaluations")
    wings = read_table("wings").set_index("wing")
    rows = []
    for name, display, _, _ in METHODS:
        for task, cl in TASKS:
            if name == AGENT:
                pool = ev[(ev.study == "agent") & (ev.method == name) & (ev.task == task)
                          & (ev.kind == "online") & (ev["query"] >= EXTENSION_FROM)]
                expected = EPISODES_PER_CELL * (ONLINE_QUERIES - EXTENSION_FROM + 1)
                if pool.episode.nunique() != EPISODES_PER_CELL or len(pool) != expected:
                    raise ValueError(f"{name}/{task}: expected {expected} extension "
                                     f"queries in {EPISODES_PER_CELL} episodes, "
                                     f"got {len(pool)} in {pool.episode.nunique()}")
                pool = pool.assign(source="online query " + pool["query"].astype(int).astype(str))
            else:
                pool = rc[(rc.study == "baseline") & (rc.method == name) & (rc.task == task)
                          & (rc.checkpoint == CHECKPOINT)]
                if len(pool) != EPISODES_PER_CELL or pool.seed.nunique() != EPISODES_PER_CELL:
                    raise ValueError(f"{name}/{task}: expected one checkpoint-{CHECKPOINT} "
                                     f"recommendation per seed, got {len(pool)} rows")
                pool = pool.assign(source=f"recommendation at {CHECKPOINT} queries")
            delivered = pool[pool.delivered.astype(bool)]
            row = dict(method=display, task=task, target_cl=cl,
                       candidates=len(pool), delivered_candidates=len(delivered))
            if delivered.empty:
                row["note"] = "no delivered recommendation"
            else:
                # Highest L/D. The wing hash breaks a tie, so the choice is fixed.
                best = delivered.sort_values(["ld", "wing"], ascending=[False, True]).iloc[0]
                g = wings.loc[best.wing]
                row.update(episode=best.episode, seed=int(best.seed), source=best.source,
                           wing=best.wing, ld=float(best.ld), note="")
                for col, x, _, _ in GEOMETRY:
                    row[col] = float(g[x])
                for i in range(13, 17):
                    row[f"twist_x{i}_deg"] = float(g[f"x{i}"])
            rows.append(row)
    return pd.DataFrame(rows)


def measure(wing, colour, bounds, panel_w, panel_h):
    """Draw one wing alone on a panel and return its visible box: width and
    height, and the box's lower-left corner inside the panel, all in inches."""
    dpi = fig1.DPI
    fig = plt.figure(figsize=(panel_w, panel_h), dpi=dpi)
    fig1.draw(fig.add_axes((0, 0, 1, 1), projection="3d"), wing, colour, bounds)
    fig.canvas.draw()
    mask = (np.asarray(fig.canvas.buffer_rgba())[..., :3] < 245).any(axis=2)
    plt.close(fig)
    rows, cols = np.where(mask)
    return ((cols.max() - cols.min() + 1) / dpi, (rows.max() - rows.min() + 1) / dpi,
            cols.min() / dpi, (mask.shape[0] - rows.max() - 1) / dpi)


def render(sel: pd.DataFrame, wings: dict) -> float:
    """Draw the grid and return the figure height in inches."""
    colour = {display: c for _, display, _, c in METHODS}
    drawn = list(wings)
    bounds = fig1.limits([wings[i] for i in drawn])  # one scale for every wing

    # First pass on Figure 1's panel to find the scale, second pass at the
    # final panel size for the exact boxes.
    boxes = {i: measure(wings[i], colour[sel.at[i, "method"]], bounds,
                        fig1.PANEL_W, fig1.PANEL_H) for i in drawn}
    k = (CELL_W - 2 * PAD_X) / max(b[0] for b in boxes.values())
    panel_w, panel_h = k * fig1.PANEL_W, k * fig1.PANEL_H
    boxes = {i: measure(wings[i], colour[sel.at[i, "method"]], bounds, panel_w, panel_h)
             for i in drawn}

    band_h = max(b[1] for b in boxes.values()) + 2 * PAD_Y   # the wing band of a row
    cell_h = band_h + LD_H
    fig_h = TITLE_H + ROWS * cell_h + (ROWS - 1) * ROW_GAP + BOTTOM
    col_of = {display: j for j, (_, display, _, _) in enumerate(METHODS)}
    row_of = {task: i for i, (task, _) in enumerate(TASKS)}

    def origin(row, col):
        return LABEL_W + col * CELL_W, BOTTOM + (ROWS - 1 - row) * (cell_h + ROW_GAP)

    def text(x, y, s, color=TEXT, **kw):
        fig.text(x / FIG_W, y / fig_h, s, ha="center", va="center", color=color, **kw)

    fig = plt.figure(figsize=(FIG_W, fig_h))
    for j, (_, _, title, _) in enumerate(METHODS):
        text(LABEL_W + (j + .5) * CELL_W, fig_h - TITLE_H / 2, title,
             fontsize=7.5, linespacing=1.15)
    for i, (task, cl) in enumerate(TASKS):
        _, y = origin(i, 0)
        text(LABEL_W * .42, y + LD_H + band_h / 2, f"$C_L = {cl}$", rotation=90, fontsize=8)
    for idx, r in sel.iterrows():
        cx, cy = origin(row_of[r.task], col_of[r.method])
        if idx not in wings:
            text(cx + CELL_W / 2, cy + LD_H + band_h / 2, "no delivered\nrecommendation",
                 fontsize=6.5, color=FADED, linespacing=1.2)
            continue
        bw, bh, ox, oy = boxes[idx]
        # Centre the wing's box in the wing band, then place its panel around it.
        x0 = cx + (CELL_W - bw) / 2 - ox
        y0 = cy + LD_H + (band_h - bh) / 2 - oy
        ax = fig.add_axes((x0 / FIG_W, y0 / fig_h, panel_w / FIG_W, panel_h / fig_h),
                          projection="3d")
        fig1.draw(ax, wings[idx], colour[r.method], bounds)
        text(cx + CELL_W / 2, cy + LD_H / 2, f"$L/D$ {r.ld:.2f}", fontsize=7.5)
    FIG_OUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(FIG_OUT, metadata={"CreationDate": None})
    plt.close(fig)
    return fig_h


def join_words(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def empty_sentence(sel: pd.DataFrame) -> str:
    empty = sel[sel.wing.isna()]
    if empty.empty:
        return ""
    cl = {task: cl for task, cl in TASKS}
    same_task = empty.task.nunique() == 1
    if same_task:
        who = join_words(IN_SENTENCE[m] for m in empty.method)
        where = f" at $C_L={cl[empty.task.iloc[0]]}$"
    else:
        who = join_words(f"{IN_SENTENCE[r.method]} at $C_L={cl[r.task]}$"
                         for r in empty.itertuples())
        where = ""
    count = {1: "that cell is", 2: "those two cells are", 3: "those three cells are"}
    cells = count.get(len(empty), f"those {len(empty)} cells are")
    verb = "delivered" if len(empty) == 1 else "each delivered"
    sentence = f"{who} {verb} no recommendation after {CHECKPOINT} queries{where}, so {cells} empty."
    return sentence[0].upper() + sentence[1:]


def shared_sentence(sel: pd.DataFrame) -> str:
    """One sentence per wing that fills more than one cell."""
    cl = {task: cl for task, cl in TASKS}
    out = []
    for _, cells in sel[sel.wing.notna()].groupby("wing", sort=False):
        if len(cells) < 2:
            continue
        if cells.task.nunique() == 1:
            who = join_words(IN_SENTENCE[m] for m in cells.method)
            s = f"{who} delivered the same wing at $C_L={cl[cells.task.iloc[0]]}$."
        else:
            who = join_words(f"{IN_SENTENCE[r.method]} at $C_L={cl[r.task]}$"
                             for r in cells.itertuples())
            s = f"The same wing appears for {who}."
        out.append(s[0].upper() + s[1:])
    return " ".join(out)


def write_tex(sel: pd.DataFrame) -> None:
    got = sel[sel.wing.notna()]
    n_drawn = len(got)
    rng = {col: (got[col].min(), got[col].max()) for col, _, _, _ in GEOMETRY}
    tops = []
    for task, c in TASKS:
        t = got[got.task == task].sort_values("ld", ascending=False).iloc[0]
        tops.append(f"{t.ld:.2f} for {IN_SENTENCE[t.method]} at $C_L={c}$")
    empty = empty_sentence(sel)
    shared = shared_sentence(sel)
    lead = (
        "This appendix shows the best wing each method delivered. "
        f"For the five baseline optimizers, we took the recommendation each method made after {CHECKPOINT} queries "
        "in each of its ten episodes per task and kept the delivered one with the highest $L/D$. "
        f"The open-ended research agent made no further requested checkpoint recommendation after query {EXTENSION_FROM - 1}, "
        f"so for it we took the delivered online design with the highest $L/D$ among queries {EXTENSION_FROM} to {ONLINE_QUERIES} "
        f"of its {EPISODES_PER_CELL * len(TASKS)} extension episodes. "
        f"Figure~\\ref{{fig:wing-gallery}} draws the {n_drawn} wings at one scale, "
        "and Table~\\ref{tab:wing-gallery} lists their planform values. "
        + (empty + " " if empty else "")
        + (shared + " " if shared else "")
        + "The best wings of the different methods are all thin and swept, with a high aspect ratio. "
        f"Their leading-edge sweep ranges from {rng['sweep_deg'][0]:.0f} to {rng['sweep_deg'][1]:.0f} degrees, "
        f"their aspect ratio from {rng['aspect_ratio'][0]:.1f} to {rng['aspect_ratio'][1]:.1f}, "
        f"their taper ratio from {rng['taper_ratio'][0]:.2f} to {rng['taper_ratio'][1]:.2f}, "
        f"and their root thickness ratio from {rng['root_thickness_ratio'][0]:.3f} to {rng['root_thickness_ratio'][1]:.3f}. "
        f"The highest score at each load is {join_words(tops)}. "
        "Every wing shown was delivered by the CFD evaluator, so each score is a graded result rather than a surrogate prediction."
    )
    fig_caption = (
        "The best delivered wing of each method and task, drawn as in "
        "Figure~\\ref{fig:wing-mesh-outcomes} at one scale. "
        f"For the five baselines it is the delivered recommendation after {CHECKPOINT} queries "
        "with the highest $L/D$ over the ten seeds. For open-ended research it is the delivered "
        f"online design with the highest $L/D$ among queries {EXTENSION_FROM} to {ONLINE_QUERIES} "
        f"of its {EPISODES_PER_CELL * len(TASKS)} extension episodes. "
        "Each number is the wing's $L/D$. "
        + empty.replace("CMA-ES with no feedback and CMA-ES with selection only each delivered no recommendation",
                        "The two archive-only CMA-ES methods delivered nothing").replace("so those two cells are empty", "so those cells are empty")
    ).strip()
    tab_caption = (
        "The wings of Figure~\\ref{fig:wing-gallery}. Seed is the episode seed, "
        "and for open-ended research the episode label. "
        "Sweep is the leading-edge sweep in degrees, taper ratio is the tip chord over the root chord, "
        "and root $t/c$ is the thickness of the root section over its chord. "
        "The four are columns 0, 3, 4 and 37 of the 38 design variables, in the order of "
        "the released tables and the guide, and they are the planform values the "
        "guide defines without an interpolation rule. "
        "A dash marks a cell with no delivery."
    )
    lines = [
        "% Generated by analysis/18_wing_gallery.py. Do not edit by hand.",
        r"\section{The best delivered wings}",
        r"\label{app:gallery}",
        "",
        lead,
        "",
        r"\begin{figure}[H]",
        r"\centering",
        "% Natural size keeps every wing at its printed size; see",
        "% analysis/18_wing_gallery.py.",
        r"\includegraphics{fig_wing_gallery.pdf}",
        f"\\caption{{{fig_caption}}}",
        r"\label{fig:wing-gallery}",
        r"\end{figure}",
        "",
        r"\begin{table}[H]",
        f"\\caption{{{tab_caption}}}",
        r"\label{tab:wing-gallery}",
        r"\centering",
        r"\scriptsize",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular*}{\textwidth}{@{\extracolsep{\fill}}llrr" + "r" * len(GEOMETRY) + "}",
        r"\toprule",
        "method & $C_L$ & seed & $L/D$ & " + " & ".join(h for _, _, h, _ in GEOMETRY) + r" \\",
        r"\midrule",
    ]
    for m, (_, display, _, _) in enumerate(METHODS):
        for i, (task, c) in enumerate(TASKS):
            r = sel[(sel.method == display) & (sel.task == task)].iloc[0]
            name = display if i == 0 else ""
            if pd.isna(r.wing):
                cells = ["--"] * (2 + len(GEOMETRY))
            else:
                cells = [str(int(r.seed)), f"{r.ld:.2f}"] + [
                    f"{r[col]:.{nd}f}" for col, _, _, nd in GEOMETRY]
            lines.append(f"{name} & ${c}$ & " + " & ".join(cells) + r" \\")
    lines += [r"\bottomrule", r"\end{tabular*}", r"\end{table}", ""]
    TEX_OUT.write_text("\n".join(lines))


def main() -> None:
    ensure_dirs()
    sel = select()
    table = read_table("wings").set_index("wing")
    columns = [f"x{i:02d}" for i in range(38)]
    wings = {i: build_wing(table.loc[r.wing, columns].to_numpy(float))
             for i, r in sel.iterrows() if isinstance(r.wing, str)}
    # The tip twist and tip thickness ratio of the wing as the mesh generator
    # builds it, for reference (not in the table, see the module docstring).
    for i, w in wings.items():
        sel.at[i, "tip_twist_built_deg"] = float(w["tws"][-1])
        sel.at[i, "tip_thickness_ratio_built"] = float(w["tc_span"][-1])
    fig_h = render(sel, wings)

    order = (["method", "task", "target_cl", "episode", "seed", "source", "wing", "ld"]
             + [col for col, _, _, _ in GEOMETRY]
             + [f"twist_x{i}_deg" for i in range(13, 17)]
             + ["tip_twist_built_deg", "tip_thickness_ratio_built",
                "candidates", "delivered_candidates", "note"])
    sel = sel[order].copy()
    sel["seed"] = sel["seed"].astype("Int64")  # whole numbers next to empty cells
    CSV_OUT.parent.mkdir(parents=True, exist_ok=True)
    sel.to_csv(CSV_OUT, index=False)
    write_tex(sel)

    print(f"{FIG_OUT}  {FIG_W:.2f} x {fig_h:.2f} in")
    print(f"{TEX_OUT}\n{CSV_OUT}")
    for r in sel.itertuples():
        if isinstance(r.wing, str):
            print(f"  {r.method:24s} {r.task:8s} {r.episode} seed {r.seed} {r.source:32s} "
                  f"L/D {r.ld:6.2f}  {r.wing[:12]}")
        else:
            print(f"  {r.method:24s} {r.task:8s} EMPTY: {r.note} "
                  f"({r.delivered_candidates}/{r.candidates} delivered)")


if __name__ == "__main__":
    main()
