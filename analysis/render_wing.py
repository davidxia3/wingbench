#!/usr/bin/env python3
"""render_wing.py — draw a SuperWing 38-D design vector.

The construction follows the dataset's own mesh generator (`gen-mesh.py`,
reproduced in the campaign case directories), so the picture is the wing the
CFD pipeline actually builds, not an approximation of it.

38-D layout (scripts/cfd/prepare_superwing_cases.py):

    [0]  SA       leading-edge sweep (deg)     [1]  DA        tip dihedral (deg)
    [2]  DA_kink  inner dihedral (deg)         [3]  AR        aspect ratio
    [4]  TR       taper ratio                  [5]  kink      kink location (fraction)
    [6]  rootadj  root chord adjustment        [7:10]  rtcs   thickness factors (3)
    [10:13] cambers (3)                        [13:17] twists (4)
    [17:27] cst_u  upper CST weights (10)      [27:37] cst_l  lower CST weights (10)
    [37] tcroot   root thickness ratio

Spanwise distributions follow the mesher: half-span is AR(1+TR)/4 with the root
chord at 1, thickness factors compound multiplicatively, twists compound
additively, and both are cubic splines on the mesher's own station grid.

Examples
--------
    python3 analysis/render_wing.py --dataset-row 0 -o wing.png
    python3 analysis/render_wing.py --sha 004c5d20 -o wing.png
    python3 analysis/render_wing.py --geom-file v.json -o wing.png
    python3 analysis/render_wing.py --sha 004c5d20 --compare-dataset-row 0 -o pair.png
"""
from __future__ import annotations

import argparse
import glob
import json
import pathlib
import sys
from functools import reduce

import numpy as np
from scipy.interpolate import CubicSpline

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from mpl_toolkits.mplot3d import Axes3D  # noqa: F401

from cst_modeling.section import cst_foil

ROOT = pathlib.Path(__file__).resolve().parents[1]
CABIN_RATIO = 0.1
TAIL = 0.008


def build_wing(g, n_foil=101, n_inner=18, n_outer=42):
    """Loft the 38-D vector exactly as the dataset mesher does."""
    g = np.asarray(g, dtype=float).reshape(-1)
    if g.size != 38:
        raise ValueError(f"expected 38 parameters, got {g.size}")

    SA, DA, DA_kink = g[0], g[1], g[2]
    AR, TR, kink_frac = g[3], g[4], g[5]
    rootadj = g[6]
    rtcs, cambers, twists = g[7:10], g[10:13], g[13:17]
    cst_u, cst_l, tcroot = g[17:27], g[27:37], float(g[37])

    half_span = AR * (1.0 + TR) / 4.0
    kink = half_span * kink_frac
    cabin = half_span * CABIN_RATIO
    tip = half_span + 1e-9
    mid_inner, mid_outer = 0.5 * kink, 0.5 * (kink + half_span)

    # root section: camber line and thickness distribution
    xa, yu1, yl1, _, _ = cst_foil(nn=n_foil, cst_u=cst_u, cst_l=cst_l, t=tcroot, tail=TAIL)
    cmb1, tc1 = 0.5 * (yu1 + yl1), (yu1 - yl1)

    y = np.concatenate([np.linspace(cabin, kink, n_inner),
                        np.linspace(kink, half_span, n_outer)[1:]])

    trs = CubicSpline([0, kink, mid_outer, tip],
                      [1.0] + [reduce(lambda a, b: a * b, rtcs[:i + 1]) for i in range(3)])(y)
    tws = CubicSpline([0, mid_inner, kink, mid_outer, tip],
                      [0.0] + [reduce(lambda a, b: a + b, twists[:i + 1]) for i in range(4)])(y)
    cmbs = CubicSpline([0, mid_inner, kink, mid_outer, tip],
                       [0.0, cambers[0] * cambers[1], cambers[1], 1.0, cambers[2]])(y)

    xLE = np.tan(np.deg2rad(SA)) * y
    zLE_kink = np.tan(np.deg2rad(DA_kink)) * kink
    zLE_tip = np.tan(np.deg2rad(DA)) * half_span
    zcs = CubicSpline([kink, tip], [zLE_kink, zLE_tip],
                      bc_type=((1, np.tan(np.deg2rad(DA_kink))), "not-a-knot"))
    zLE = np.where(y <= kink, np.tan(np.deg2rad(DA_kink)) * y, zcs(np.clip(y, kink, tip)))

    chord_tip = TR
    chord_kink = TR * kink_frac + (1.0 - kink_frac)
    root_adj_l = rootadj * (np.tan(np.deg2rad(SA)) * kink + chord_kink - 1.0)
    chord_cabin = (TR * CABIN_RATIO + (1.0 - CABIN_RATIO)
                   + root_adj_l * (kink_frac - CABIN_RATIO) / kink_frac)
    chords = np.where(
        y <= kink,
        chord_cabin + (chord_kink - chord_cabin) * (y - cabin) / max(kink - cabin, 1e-12),
        chord_kink + (chord_tip - chord_kink) * (y - kink) / max(half_span - kink, 1e-12))

    # section surfaces, then twist about the leading edge, scale by chord, place
    yus = cmb1[:, None] * cmbs[None, :] + 0.5 * tc1[:, None] * trs[None, :]
    yls = cmb1[:, None] * cmbs[None, :] - 0.5 * tc1[:, None] * trs[None, :]
    th = np.deg2rad(tws)[None, :]
    XU = (np.cos(th) * xa[:, None] + np.sin(th) * yus) * chords[None, :] + xLE[None, :]
    ZU = (-np.sin(th) * xa[:, None] + np.cos(th) * yus) * chords[None, :] + zLE[None, :]
    XL = (np.cos(th) * xa[:, None] + np.sin(th) * yls) * chords[None, :] + xLE[None, :]
    ZL = (-np.sin(th) * xa[:, None] + np.cos(th) * yls) * chords[None, :] + zLE[None, :]
    Y = np.repeat(y[None, :], xa.size, axis=0)

    tc_span = (yus - yls).max(axis=0)
    return dict(y=y, xa=xa, XU=XU, ZU=ZU, XL=XL, ZL=ZL, Y=Y, chords=chords, xLE=xLE, zLE=zLE,
                trs=trs, tws=tws, cmbs=cmbs, tc_span=tc_span, half_span=half_span, kink=kink,
                cabin=cabin, SA=SA, AR=AR, TR=TR, DA=DA, DA_kink=DA_kink, kink_frac=kink_frac,
                tcroot=tcroot, yus=yus, yls=yls)


def _surface(ax, W, color, alpha=0.92):
    for X, Z in ((W["XU"], W["ZU"]), (W["XL"], W["ZL"])):
        ax.plot_surface(W["Y"], X, Z, color=color, alpha=alpha, linewidth=0,
                        antialiased=True, shade=True, rstride=6, cstride=2)
    for k in (0, -1):
        ax.plot(W["Y"][:, k], W["XU"][:, k], W["ZU"][:, k], color="#12293f", lw=0.7)
        ax.plot(W["Y"][:, k], W["XL"][:, k], W["ZL"][:, k], color="#12293f", lw=0.7)


def _frame(ax, W):
    ax.set_box_aspect((W["half_span"], W["xLE"][-1] + W["chords"][-1],
                       max(W["tc_span"].max() * 6, 0.5)))
    ax.view_init(elev=20, azim=-62)
    ax.grid(False)
    ax.set_xlabel("span", fontsize=8)
    ax.set_ylabel("streamwise", fontsize=8)
    ax.set_zticks([])
    ax.tick_params(labelsize=7)


def figure(W, title, out):
    fig = plt.figure(figsize=(14, 8.2))
    gs = fig.add_gridspec(3, 3, width_ratios=[1.55, 1, 1], height_ratios=[1.3, 1, 1],
                          hspace=0.45, wspace=0.30)

    ax = fig.add_subplot(gs[:, 0], projection="3d")
    _surface(ax, W, "#3b6ea5")
    _frame(ax, W)
    ax.set_title("wing surface (half span, from the mesher's construction)", fontsize=9.5)

    axp = fig.add_subplot(gs[0, 1:])
    axp.fill_between(W["y"], W["xLE"], W["xLE"] + W["chords"], color="#3b6ea5", alpha=0.30)
    axp.plot(W["y"], W["xLE"], color="#16324f", lw=1.3)
    axp.plot(W["y"], W["xLE"] + W["chords"], color="#16324f", lw=1.3)
    axp.axvline(W["kink"], color="#b5651d", ls="--", lw=1.0)
    axp.text(W["kink"], axp.get_ylim()[0], " kink", color="#b5651d", fontsize=7, va="bottom")
    axp.invert_yaxis(); axp.set_aspect("equal"); axp.grid(alpha=0.25)
    axp.set_title(f"planform   sweep {W['SA']:.1f}$^\\circ$   AR {W['AR']:.2f}   "
                  f"taper {W['TR']:.3f}   kink at {W['kink_frac']:.2f}", fontsize=9)
    axp.set_xlabel("span (root chord = 1)", fontsize=8)
    axp.tick_params(labelsize=7)

    axs = fig.add_subplot(gs[1, 1])
    idx = [0, len(W["y"]) // 2, len(W["y"]) - 1]
    for j, c in zip(idx, ("#1b3a5c", "#3b6ea5", "#8fb8de")):
        axs.plot(W["xa"], W["yus"][:, j], color=c, lw=1.2,
                 label=f"y/s={W['y'][j]/W['half_span']:.2f}  t/c {W['tc_span'][j]*100:.1f}%")
        axs.plot(W["xa"], W["yls"][:, j], color=c, lw=1.2)
    axs.set_aspect("equal"); axs.legend(fontsize=6, frameon=False, loc="lower right")
    axs.set_title("sections (unrotated)", fontsize=9); axs.grid(alpha=0.25)
    axs.tick_params(labelsize=7)

    axt = fig.add_subplot(gs[1, 2])
    axt.plot(W["y"] / W["half_span"], W["tws"], color="#b5651d", lw=1.6)
    axt.set_title("twist (deg, cumulative)", fontsize=9); axt.grid(alpha=0.25)
    axt.set_xlabel("y / semi-span", fontsize=8); axt.tick_params(labelsize=7)

    axc = fig.add_subplot(gs[2, 1])
    axc.plot(W["y"] / W["half_span"], W["tc_span"] * 100, color="#2e7d5b", lw=1.6)
    axc.set_title("thickness t/c (%)", fontsize=9); axc.grid(alpha=0.25)
    axc.set_xlabel("y / semi-span", fontsize=8); axc.tick_params(labelsize=7)

    axi = fig.add_subplot(gs[2, 2]); axi.axis("off")
    axi.text(0.0, 1.0, (
        f"sweep         {W['SA']:8.2f} deg\n"
        f"aspect ratio  {W['AR']:8.2f}\n"
        f"taper ratio   {W['TR']:8.3f}\n"
        f"kink at       {W['kink_frac']:8.3f} of semi-span\n"
        f"dihedral      {W['DA_kink']:8.2f} inner / {W['DA']:.2f} tip\n"
        f"semi-span     {W['half_span']:8.2f} (root chord 1)\n"
        f"root t/c      {W['tc_span'][0]*100:8.1f} %\n"
        f"tip  t/c      {W['tc_span'][-1]*100:8.1f} %\n"
        f"twist at tip  {W['tws'][-1]:8.2f} deg\n"
        f"tcroot param  {W['tcroot']:8.3f}"),
        va="top", family="monospace", fontsize=8)

    fig.suptitle(title, fontsize=12)
    fig.savefig(out, dpi=170, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out}")


def from_dataset(row):
    import pandas as pd
    df = pd.read_csv(ROOT / "data" / "superwing" / "superwing_records.csv")
    return df.loc[row, [f"x{i:02d}" for i in range(38)]].to_numpy(float), f"dataset row {row}"


def from_sha(prefix):
    for f in glob.glob(str(ROOT / "output" / "benchmark_v2" / "**" / "result.json"), recursive=True):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        g = d.get("geometry") or {}
        if g.get("sha256", "").startswith(prefix) and g.get("geom_38d"):
            return np.array(g["geom_38d"], float), f"geometry {g['sha256'][:12]}"
    raise SystemExit(f"no geometry found with hash prefix {prefix!r}")


def main(argv=None):
    p = argparse.ArgumentParser(description="Draw a SuperWing 38-D wing.")
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--geom", help="38 comma-separated numbers")
    src.add_argument("--geom-file", help="JSON file holding 38 numbers")
    src.add_argument("--dataset-row", type=int)
    src.add_argument("--sha", help="campaign geometry hash (prefix ok)")
    p.add_argument("--compare-sha")
    p.add_argument("--compare-dataset-row", type=int)
    p.add_argument("-o", "--out", default="wing.png")
    a = p.parse_args(argv)

    if a.geom:
        g, name = np.array([float(v) for v in a.geom.replace(" ", "").split(",")]), "given vector"
    elif a.geom_file:
        g, name = np.array(json.load(open(a.geom_file)), float), a.geom_file
    elif a.dataset_row is not None:
        g, name = from_dataset(a.dataset_row)
    else:
        g, name = from_sha(a.sha)

    W = build_wing(g)
    second = from_sha(a.compare_sha) if a.compare_sha else (
        from_dataset(a.compare_dataset_row) if a.compare_dataset_row is not None else None)

    if second is None:
        figure(W, name, a.out)
        return 0

    W2 = build_wing(second[0])
    fig = plt.figure(figsize=(13, 5.8))
    for k, (Wx, nm, col) in enumerate(((W, name, "#3b6ea5"), (W2, second[1], "#b5651d"))):
        ax = fig.add_subplot(1, 2, k + 1, projection="3d")
        _surface(ax, Wx, col); _frame(ax, Wx)
        ax.set_title(f"{nm}\nsweep {Wx['SA']:.1f}$^\\circ$  AR {Wx['AR']:.2f}  taper {Wx['TR']:.3f}  "
                     f"t/c {Wx['tc_span'][0]*100:.1f}$\\to${Wx['tc_span'][-1]*100:.1f}%  "
                     f"tip twist {Wx['tws'][-1]:.1f}$^\\circ$", fontsize=8.5)
    fig.tight_layout(); fig.savefig(a.out, dpi=170, bbox_inches="tight"); plt.close(fig)
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
