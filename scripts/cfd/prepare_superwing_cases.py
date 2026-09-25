"""Prepare per-winner CFD case directories for the SuperWing/ADflow pipeline.

Bridges our winner files (data/winners/*.json|.pkl, 38-D geometry vectors) to
the vendored AeroTransformer pipeline (scripts/cfd/aerotransformer/), which is
the EXACT mesher+solver chain that generated the SuperWing dataset. Each case
directory is self-contained: input.json (named wing coefficients + condition
lists) + the three pipeline scripts + the wingtip template.

38-D index -> named-coefficient mapping (docs/SUPERWING_PARAMETERIZATION.md and
the legacy export script; the MESHER's own semantics are dataset-authoritative,
so values pass through raw — no reinterpretation):

    [0] SA (LE sweep, deg)      [1] DA (tip dihedral, deg)  [2] DA_kink (deg)
    [3] AR                      [4] TR (taper)              [5] kink (fraction)
    [6] rootadj                 [7:10] rtcs (3)             [10:13] cambers (3)
    [13:17] twists (4)          [17:27] cst_u (10)          [27:37] cst_l (10)
    [37] tcroot

Patches applied to the copied scripts (mechanical, documented in the vendor
README): tip template path '../../original_tip.xyz' -> './original_tip.xyz'
(template is copied into each case dir), and 'mpirun -np 8' -> --np value.

Conditions default to each wing's own cruise points (what its surrogate claim
and kNN truth were computed over); v12 gets a 4-point subset of its 24-point
envelope. Override with --conds "M,A;M,A;...".

Usage:
    python scripts/cfd/prepare_superwing_cases.py \
        --winners data/winners/run11_winner.json data/winners/v12_winner.pkl \
                  data/winners/run6_winner.json \
        --out /workspace/cfd_cases --np 16
"""
from __future__ import annotations
import argparse
import json
import os
import pickle
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import numpy as np

VENDOR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aerotransformer")

# Default flight conditions per wing: its own cruise grid (surrogate claim and
# held-out kNN truth were computed on exactly these points). v12's full robust
# envelope is 24 points; the 4-point subset spans its Mach range and its
# claimed-safe L/D range (19.6 down to 12.6).
DEFAULT_CONDS = {
    "run11": [(0.78, 2.0), (0.82, 2.5), (0.85, 2.5), (0.85, 3.0), (0.88, 2.5)],
    "run6": [(0.78, 2.0), (0.80, 2.5)],
    "v12": [(0.78, 2.5), (0.82, 2.0), (0.85, 2.5), (0.88, 2.5)],
}


def load_geom(path: str) -> np.ndarray:
    if path.endswith(".json"):
        g = json.load(open(path))["geom_38d"]
    else:
        # NOTE: NumPy-2.x-pickled winners fail to unpickle under NumPy 1.x
        # ('No module named numpy._core'). Prefer the .json sibling if present
        # (v12_winner.json is the de-pickled v12). Convert with the snippet in
        # the commit that added v12_winner.json if you hit this on a new winner.
        sibling = path[:-4] + ".json"
        if os.path.exists(sibling):
            g = json.load(open(sibling))["geom_38d"]
        else:
            w = pickle.load(open(path, "rb"))["winner"]
            g = w.get("geom") if "geom" in w else w.get("winner_geom_38D")
    g = np.asarray(g, dtype=np.float64).ravel()
    assert g.shape == (38,), f"{path}: expected 38-D geometry, got {g.shape}"
    return g


def geom_to_wing_coefs(g: np.ndarray) -> dict:
    return {
        "SA": float(g[0]), "DA": float(g[1]), "DA_kink": float(g[2]),
        "AR": float(g[3]), "TR": float(g[4]), "kink": float(g[5]),
        "rootadj": float(g[6]),
        "rtcs": [float(x) for x in g[7:10]],
        "cambers": [float(x) for x in g[10:13]],
        "twists": [float(x) for x in g[13:17]],
        "cst_u": [float(x) for x in g[17:27]],
        "cst_l": [float(x) for x in g[27:37]],
        "tcroot": float(g[37]),
    }


def wing_key(path: str) -> str:
    base = os.path.basename(path)
    for k in DEFAULT_CONDS:
        if base.startswith(k + "_"):
            return k
    return os.path.splitext(base)[0].replace("_winner", "")


# pyHyp volume-march robustness for high-sweep wings whose default march tangles
# (v12 at 47.7 deg: segfault, then negative-volume cells). Two levers:
#  (1) MORE marching LAYERS (nFar 81->129) at the SAME marchDist=50 -> much
#      gentler per-layer growth, the primary cure for the tip overshoot/crossing
#      (and strictly more accurate farfield resolution, not less);
#  (2) much stronger smoothing (epsE/epsI/volBlend/volSmoothIter/volCoef) to keep
#      adjacent normals from converging at the swept tip & trailing edge.
# marchDist (farfield distance) is UNCHANGED, so the result stays comparable to
# the dataset; only layer count + smoothing differ.
_ROBUST_PYHYP = {
    "'nFar':     81":          "'nFar':     129",
    '"epsE": 1.0,':            '"epsE": 4.0,',
    '"epsI": 2.0,':            '"epsI": 8.0,',
    '"volCoef": 0.2,':         '"volCoef": 0.5,',
    '"volBlend": 0.0005,':     '"volBlend": 0.02,',
    '"volSmoothIter": 20,':    '"volSmoothIter": 200,',
}


def _copy_patched(src: str, dst: str, np_ranks: int, robust_mesh: bool = False,
                  screen_ncycles=None) -> None:
    txt = open(src).read()
    txt = txt.replace("tip_path = '../../original_tip.xyz'",
                      "tip_path = './original_tip.xyz'")
    txt = txt.replace("mpirun -np 8", f"mpirun -np {np_ranks}")
    # Convergence-SCREEN mode (cheap solvability labelling): cap ADflow iterations so a
    # non-convergent section fails fast (~min) instead of grinding to the full 3000-cycle
    # cap (~hours). The verdict comes from the residual TREND over these cycles, not from
    # hitting 1e-10. Only run-adflow.py has this option; a no-op for the mesh/orchestrator.
    if screen_ncycles is not None:
        txt = txt.replace('"nCycles": 3000', f'"nCycles": {int(screen_ncycles)}')
    # Fixed-CL (trim) grading for the frozen oracle: route the per-condition CL
    # target into run_ADflow's CL-targeting path (CFDSolver.solveCL). This call
    # line exists ONLY in run-adflow.py, so the replace is a no-op for the mesh /
    # single-point scripts. Backward-compatible: when conds.json has no 'CL_targ',
    # conds.get(...) is None and run_ADflow falls back to the fixed-AoA solve.
    txt = txt.replace(
        'run_ADflow(grid="../wing_vol.cgns", output=".", wingCoefs=wingCoefs, conds=conds)',
        'run_ADflow(grid="../wing_vol.cgns", output=".", wingCoefs=wingCoefs, conds=conds,\n'
        '                            CL_targ=conds.get("CL_targ"), alpha0=conds.get("AA"))')
    if robust_mesh:
        for old, new in _ROBUST_PYHYP.items():
            assert old in txt, f"robust-mesh patch target not found: {old}"
            txt = txt.replace(old, new)
    with open(dst, "w") as f:
        f.write(txt)


def prepare_case(winner_path: str, out_root: str, conds, np_ranks: int,
                 robust_mesh: bool = False, screen_ncycles=None) -> str:
    key = wing_key(winner_path)
    g = load_geom(winner_path)
    return prepare_geometry_case(
        key,
        g,
        out_root,
        conds,
        np_ranks,
        robust_mesh=robust_mesh,
        screen_ncycles=screen_ncycles,
    )


def prepare_geometry_case(key: str, geometry_38d, out_root: str, conds, np_ranks: int,
                          robust_mesh: bool = False, screen_ncycles=None) -> str:
    """Prepare a case from an already resolved, provenance-checked geometry."""
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", key):
        raise ValueError(f"unsafe case key: {key!r}")
    g = np.asarray(geometry_38d, dtype=np.float64).ravel()
    if g.shape != (38,) or not np.all(np.isfinite(g)):
        raise ValueError(f"{key}: expected a finite 38-D geometry, got shape {g.shape}")
    if isinstance(np_ranks, bool) or not isinstance(np_ranks, int) or np_ranks <= 0:
        raise ValueError("np_ranks must be a positive integer")
    coefs = geom_to_wing_coefs(g)
    coefs["M3"] = [float(m) for m, _ in conds]
    coefs["AA"] = [float(a) for _, a in conds]

    case = os.path.join(out_root, key)
    os.makedirs(case, exist_ok=True)
    with open(os.path.join(case, "input.json"), "w") as f:
        json.dump(coefs, f, indent=1)

    # the orchestrator invokes 'gen-mesh.py'; vendor name is gen-mesh.superwing.py
    _copy_patched(os.path.join(VENDOR, "gen-mesh.superwing.py"),
                  os.path.join(case, "gen-mesh.py"), np_ranks, robust_mesh)
    _copy_patched(os.path.join(VENDOR, "single-point.py"),
                  os.path.join(case, "single-point.py"), np_ranks)
    radflow = os.path.join(case, "run-adflow.py")
    _copy_patched(os.path.join(VENDOR, "run-adflow.py"), radflow, np_ranks,
                  screen_ncycles=screen_ncycles)
    radflow_txt = open(radflow).read()
    assert "CL_targ=conds.get" in radflow_txt, (
        "run-adflow.py CL-targeting patch did not apply — the vendored run_ADflow "
        "call signature changed; the frozen oracle needs CL-trim grading.")
    if screen_ncycles is not None:
        assert f'"nCycles": {int(screen_ncycles)}' in radflow_txt, (
            "screen nCycles patch did not apply — the vendored run-adflow nCycles option changed.")
    shutil.copy(os.path.join(VENDOR, "original_tip.xyz"),
                os.path.join(case, "original_tip.xyz"))

    print(f"[prep] {key}: case={case}")
    print(f"[prep]   SA={coefs['SA']:.2f} AR={coefs['AR']:.2f} TR={coefs['TR']:.3f} "
          f"kink={coefs['kink']:.3f} tcroot={coefs['tcroot']:.4f}")
    print(f"[prep]   conditions: {list(zip(coefs['M3'], coefs['AA']))}")
    return case


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--winners", nargs="+", required=True)
    ap.add_argument("--out", required=True, help="root dir for case folders")
    ap.add_argument("--np", type=int, default=16, dest="np_ranks",
                    help="MPI ranks for mesh + solve (replaces upstream's 8)")
    ap.add_argument("--conds", default=None,
                    help='override conditions for ALL wings: "M,A;M,A;..."')
    ap.add_argument("--robust-mesh", action="store_true",
                    help="stronger pyHyp smoothing for high-sweep wings whose "
                         "default volume march tangles/segfaults (e.g. v12).")
    args = ap.parse_args()

    override = None
    if args.conds:
        override = [tuple(float(v) for v in p.split(",")) for p in args.conds.split(";")]

    os.makedirs(args.out, exist_ok=True)
    cases = []
    for w in args.winners:
        key = wing_key(w)
        conds = override or DEFAULT_CONDS.get(key)
        if conds is None:
            print(f"[prep] ERROR: no default conditions for '{key}' — pass --conds",
                  file=sys.stderr)
            return 1
        cases.append(prepare_case(w, args.out, conds, args.np_ranks,
                                  robust_mesh=args.robust_mesh))

    print("[prep] run each case with:  cd <case> && python single-point.py")
    print(f"[prep] prepared {len(cases)} case(s) under {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
