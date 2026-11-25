#!/usr/bin/env python3
"""Rebuild the illustrative wing-surface figure from recorded campaign designs.

This renders geometries, not CFD meshes. The examples illustrate different
shape differences and are not a representative sample or a meshability test.

Layout: each wing is first drawn alone to find the tight box around its
visible pixels. Every cell is the size of the largest box and the cells
touch. The bottom row is then moved up into the empty corners until the
closest wings in any column are 3 pt apart. The figure is saved at its
printed size, so it goes into the paper without scaling.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.ndimage import binary_dilation

from common import design_table
from render_wing import build_wing


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "paper/fig_wing_mesh_outcomes.pdf"

# Shared paper style (see the palette block in paper/main.tex).
DELIVERED, UNMESHABLE = "#107A83", "#8F1F22"
plt.rcParams.update({"font.family": "serif",
                     "font.serif": ["Times New Roman", "Times"],
                     "pdf.fonttype": 42})

# Printed size of the 3-D panel each wing is drawn in, in inches. With the
# camera, zoom, and bounds in draw(), this fixes each wing's size on the page.
PANEL_W, PANEL_H = 2.9833, 1.2970
LINE_W = 0.505          # root and tip outlines, pt
DPI = 600               # used only to measure the wings' pixel boxes
MARGIN = 0.02           # white margin around the whole figure, in
CLEARANCE = 3 / 72      # closest approach between the two wings in a column, in

# Each column is (task, delivered geometry hash, unmeshable geometry hash). The
# task is not printed, since whether a wing meshes depends only on its shape.
PAIRS = (
    ("Low", "ab754cd6ddf1", "4beb6c0cc8eb"),
    ("Nominal A", "279a839814de", "bda7a3ae3986"),
    ("Nominal B", "df979c19db28", "9e5253939f41"),
    ("High", "a3a5a083f273", "55879a15a99a"),
)


def lookup(table: pd.DataFrame, prefix: str, expected: str):
    rows = table[table.geometry_sha256.str.startswith(prefix)]
    hashes = rows.geometry_sha256.unique()
    if len(hashes) != 1:
        raise ValueError(f"Expected one geometry matching {prefix}, got {len(hashes)}")
    if expected == "delivered":
        if not rows.delivered.all():
            raise ValueError(f"{prefix} is not consistently delivered")
    elif not rows.reason_codes.eq("not_run_unmeshable").all():
        raise ValueError(f"{prefix} is not consistently recorded as unmeshable")
    return build_wing(rows.iloc[0][[f"x{i:02d}" for i in range(38)]].to_numpy(float))


def limits(wings):
    def bounds(values):
        lo, hi = float(values.min()), float(values.max())
        margin = max((hi - lo) * .08, .015)
        return lo - margin, hi + margin
    y = np.concatenate([w["Y"].ravel() for w in wings])
    x = np.concatenate([v.ravel() for w in wings for v in (w["XU"], w["XL"])])
    z = np.concatenate([v.ravel() for w in wings for v in (w["ZU"], w["ZL"])])
    return bounds(y), bounds(x), bounds(z)


def draw(ax, wing, color: str, bounds):
    for x, z in ((wing["XU"], wing["ZU"]), (wing["XL"], wing["ZL"])):
        ax.plot_surface(wing["Y"], x, z, color=color, alpha=.96,
                        linewidth=0, antialiased=True, rstride=4, cstride=2,
                        shade=True)
    for k in (0, -1):
        for x, z in ((wing["XU"], wing["ZU"]), (wing["XL"], wing["ZL"])):
            ax.plot(wing["Y"][:, k], x[:, k], z[:, k], color="#192b39", lw=LINE_W)
    ax.set_xlim(*bounds[0])
    ax.set_ylim(*bounds[1])
    ax.set_zlim(*bounds[2])
    ax.set_box_aspect((np.ptp(bounds[0]), np.ptp(bounds[1]), 4 * np.ptp(bounds[2])),
                      zoom=1.23)
    ax.view_init(elev=23, azim=-61)
    ax.set_axis_off()
    # Panels overlap in the final figure, so their backgrounds must not paint.
    ax.patch.set_visible(False)


def measure(wing, color, bounds):
    """Draw one wing alone and return its visible pixels, cropped to their box,
    with the box's lower-left corner inside the panel (in inches)."""
    fig = plt.figure(figsize=(PANEL_W, PANEL_H), dpi=DPI)
    draw(fig.add_axes((0, 0, 1, 1), projection="3d"), wing, color, bounds)
    fig.canvas.draw()
    mask = (np.asarray(fig.canvas.buffer_rgba())[..., :3] < 245).any(axis=2)
    plt.close(fig)
    rows, cols = np.where(mask)
    crop = mask[rows.min():rows.max() + 1, cols.min():cols.max() + 1]
    return crop, cols.min() / DPI, (mask.shape[0] - rows.max() - 1) / DPI


def tuck(crops, cell_w, cell_h):
    """How far the bottom row can move up before any wing comes within
    CLEARANCE of the wing above it. Every column moves by the same amount."""
    rad = int(round(CLEARANCE * DPI))
    yy, xx = np.mgrid[-rad:rad + 1, -rad:rad + 1]
    disk = xx ** 2 + yy ** 2 <= rad ** 2
    w_px, h_px = int(round(cell_w * DPI)), int(round(cell_h * DPI))
    shift = np.inf
    for col in range(len(PAIRS)):
        # The two cells stacked with padding; image rows grow downward.
        top = np.zeros((2 * h_px + 2 * rad, w_px + 2 * rad), bool)
        bottom = np.zeros_like(top)
        for row, canvas in ((0, top), (1, bottom)):
            crop = crops[col, row]
            y = rad + row * h_px + (h_px - crop.shape[0]) // 2
            x = rad + (w_px - crop.shape[1]) // 2
            canvas[y:y + crop.shape[0], x:x + crop.shape[1]] |= crop
        top = binary_dilation(top, disk)
        gaps = [np.where(bottom[:, x])[0].min() - np.where(top[:, x])[0].max() - 1
                for x in range(top.shape[1])
                if top[:, x].any() and bottom[:, x].any()]
        shift = min(shift, min(gaps) / DPI)
    return shift


def main():
    table = design_table()
    items, crops, offsets = [], {}, {}
    for col, (task, good_hash, bad_hash) in enumerate(PAIRS):
        wings = [lookup(table, good_hash, "delivered"),
                 lookup(table, bad_hash, "unmeshable")]
        shared = limits(wings)
        for row, (wing, color) in enumerate(zip(wings, (DELIVERED, UNMESHABLE))):
            crop, x0, y0 = measure(wing, color, shared)
            crops[col, row], offsets[col, row] = crop, (x0, y0)
            items.append((col, row, task, wing, color, shared))

    cell_w = max(c.shape[1] for c in crops.values()) / DPI
    cell_h = max(c.shape[0] for c in crops.values()) / DPI
    shift = tuck(crops, cell_w, cell_h)
    width = 2 * MARGIN + len(PAIRS) * cell_w
    height = 2 * MARGIN + 2 * cell_h - shift

    fig = plt.figure(figsize=(width, height))
    for col, row, task, wing, color, shared in items:
        box_w = crops[col, row].shape[1] / DPI
        box_h = crops[col, row].shape[0] / DPI
        cell_x = MARGIN + col * cell_w
        cell_y = MARGIN + (cell_h - shift if row == 0 else 0)
        # Center the wing's box in its cell, then place its panel around it.
        x0 = cell_x + (cell_w - box_w) / 2 - offsets[col, row][0]
        y0 = cell_y + (cell_h - box_h) / 2 - offsets[col, row][1]
        ax = fig.add_axes((x0 / width, y0 / height, PANEL_W / width, PANEL_H / height),
                          projection="3d")
        draw(ax, wing, color, shared)
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUTPUT)
    plt.close(fig)
    print(f"{OUTPUT}  {width:.2f} x {height:.2f} in (bottom row moved up {shift:.3f} in)")


if __name__ == "__main__":
    main()
