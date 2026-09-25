"""Shared constants and helpers for the analysis.

All scripts read the tables in data/ and write only into analysis/out/,
analysis/figures/, and the number files in paper/. The data tables use the
paper's names for methods and tasks; the analysis code keeps the identifiers
the runs had, and the maps below translate between the two. Every output is
stamped with DATA_VERSION, the version of the baseline-study records.
"""
from __future__ import annotations

import json
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parent.parent
DATA = WORKSPACE / "data"
OUT = WORKSPACE / "analysis" / "out"
FIGURES = WORKSPACE / "analysis" / "figures"
TABLES = OUT / "tables"

# ---------------------------------------------------------------------------
# Frozen design facts (from the hash-bound pre-campaign configs in
# the frozen configs under configs/benchmark/; see paper/notes for citations).
# ---------------------------------------------------------------------------
CONFIGS = [
    "superwing-load-low-cl015",
    "superwing-load-nominal-cl041",
    "superwing-load-high-cl066",
]
METHODS = [
    "static-precommitted-cmaes-v2-pilot",
    "conventional-adaptive-cmaes-v2-pilot",
    "fsd-cma-v1-port-v2-pilot",
    "checkpointed-de-feedback-v2-pilot",
]

# Short display names for figures/tables (descriptive, derived from the sealed
# method ids; the authoritative definitions are the frozen configs).
METHOD_SHORT = {
    "static-precommitted-cmaes-v2-pilot": "CMA-ES (no feedback)",
    "conventional-adaptive-cmaes-v2-pilot": "CMA-ES (selection only)",
    "fsd-cma-v1-port-v2-pilot": "CMA-ES (feedback-guided)",
    "checkpointed-de-feedback-v2-pilot": "DE (feedback-guided)",
}
CONFIG_SHORT = {
    "superwing-load-low-cl015": "CL = 0.15 (low)",
    "superwing-load-nominal-cl041": "CL = 0.41 (nominal)",
    "superwing-load-high-cl066": "CL = 0.66 (high)",
}
METHOD_COLOR = {
    "static-precommitted-cmaes-v2-pilot": "#888888",
    "conventional-adaptive-cmaes-v2-pilot": "#1f77b4",
    "fsd-cma-v1-port-v2-pilot": "#d62728",
    "checkpointed-de-feedback-v2-pilot": "#2ca02c",
}
# distinct dash patterns so exactly-overlapping curves (e.g. two methods at
# delivery = 0) remain individually visible
METHOD_LS = {
    "static-precommitted-cmaes-v2-pilot": (0, (2, 2)),
    "conventional-adaptive-cmaes-v2-pilot": "-",
    "fsd-cma-v1-port-v2-pilot": "-",
    "checkpointed-de-feedback-v2-pilot": "-",
}
METHOD_MARKER = {
    "static-precommitted-cmaes-v2-pilot": "s",
    "conventional-adaptive-cmaes-v2-pilot": "o",
    "fsd-cma-v1-port-v2-pilot": "D",
    "checkpointed-de-feedback-v2-pilot": "^",
}

SEEDS = list(range(6200, 6210))
HIDDEN_CHECKPOINTS = [0, 1, 2, 4, 8, 16, 32]
ONLINE_MAX = 32

# Version of the baseline-study records the analysis was built from. It is
# stamped on every figure and table.
DATA_VERSION = {"serial": 1, "refreshed_at_utc": "2026-08-27T00:39:45Z",
                "campaign_complete": True}

# Names used in data/ for the identifiers used in the analysis code.
SMAC_ID = "smac3_blackbox_cfd_only_penalty_v1"
METHOD_NAME = {
    "static-precommitted-cmaes-v2-pilot": "CMA-ES no feedback",
    "conventional-adaptive-cmaes-v2-pilot": "CMA-ES selection only",
    "fsd-cma-v1-port-v2-pilot": "CMA-ES feedback-guided",
    "checkpointed-de-feedback-v2-pilot": "DE feedback-guided",
    SMAC_ID: "SMAC3",
    "S_OPEN_v2": "Open-ended research",
    "S_OPEN_REFLEXION_v1": "Reflection",
    "RAFA_BEST_OF_N_v1": "Planning",
}
TASK_NAME = {
    "superwing-load-low-cl015": "low",
    "superwing-load-nominal-cl041": "nominal",
    "superwing-load-high-cl066": "high",
}
METHOD_ID = {v: k for k, v in METHOD_NAME.items()}
TASK_ID = {v: k for k, v in TASK_NAME.items()}


def read_table(name: str):
    """One of the tables in data/, for example read_table("evaluations")."""
    import pandas as pd

    return pd.read_csv(DATA / f"{name}.csv", float_precision="round_trip")


def surrogate_evaluations():
    """The 4,325 evaluations of the four surrogate-assisted baselines, laid out
    as the analysis scripts use them: an online query's number is stored as its
    checkpoint, and private grades are the "hidden" stream."""
    import pandas as pd

    ev = read_table("evaluations")
    ev = ev[(ev.study == "baseline") & (ev.method != "SMAC3")]
    online = ev.kind == "online"
    df = pd.DataFrame({
        "episode_index": ev.episode.str[1:].astype(int),
        "config": ev.task.map(TASK_ID),
        "method": ev.method.map(METHOD_ID),
        "seed": ev.seed,
        "stream": online.map({True: "online", False: "hidden"}),
        "checkpoint": ev["query"].where(online, ev["checkpoint"]).astype(int),
        "action_id": ev.original_id,
        "conditional_value": ev.ld,
        "delivery": ev.delivered.astype(bool),
        "reason_codes": ev.reasons,
        "outcome_kind": ev.outcome,
        "geometry_sha256": ev.wing,
        "ingested_at_utc": ev.recorded_at,
    })
    return df.sort_values(["episode_index", "stream", "checkpoint"]).reset_index(drop=True)


def design_table():
    """surrogate_evaluations() with the 38 design variables of each wing."""
    ev = surrogate_evaluations()
    wings = read_table("wings").set_index("wing")
    tab = ev[["episode_index", "method", "stream", "checkpoint", "geometry_sha256",
              "delivery", "conditional_value", "reason_codes"]].copy()
    tab.insert(2, "task", ev.config.map(TASK_NAME))
    tab = tab.rename(columns={"delivery": "delivered", "conditional_value": "cfd_ld_geomean"})
    return tab.join(wings, on="geometry_sha256")


def smac3_recommendations():
    """SMAC3's checkpoint recommendations, with the task under its identifier."""
    rc = read_table("recommendations")
    rc = rc[rc.method == "SMAC3"].copy()
    rc["config"] = rc.task.map(TASK_ID)
    rc["delivery"] = rc.delivered.astype(bool)
    return rc


def cfd_attempts():
    """Every mesh and solve attempt of the baseline study. `source` is "sealed"
    for the four surrogate-assisted baselines and "smac3" for SMAC3."""
    ca = read_table("cfd_attempts")
    ca["source"] = ca.episode.str[1:].astype(int).map(lambda i: "sealed" if i < 120 else "smac3")
    return ca


def load_meta() -> dict:
    """Stamp of the data the working tables were built from (01_load.py)."""
    p = OUT / "load_meta.json"
    if p.exists():
        return json.loads(p.read_text())
    return dict(DATA_VERSION)  # before first load


def snapshot_stamp() -> str:
    m = load_meta()
    return f"snapshot serial {m['serial']} @ {m['refreshed_at_utc']}"


def tentative_banner() -> str:
    m = load_meta()
    if m.get("campaign_complete"):
        return f"Final (data snapshot {m['serial']})"
    return (
        f"Preliminary: {m.get('episodes_terminal', '?')}/120 episodes complete, "
        f"{m.get('n_grade_records', '?')} CFD evaluations (data snapshot {m['serial']}, "
        f"{str(m['refreshed_at_utc'])[:10]})"
    )


def ensure_dirs() -> None:
    for d in (OUT, FIGURES, TABLES):
        d.mkdir(parents=True, exist_ok=True)


def load_grades():
    """The working grades table written by 01_load.py."""
    import pandas as pd

    df = pd.read_csv(OUT / "grades.csv")
    # reason_codes serialized as ';'-joined; NaN -> empty
    df["reason_codes"] = df["reason_codes"].fillna("")
    return df


def load_episodes():
    import pandas as pd

    return pd.read_csv(OUT / "episodes.csv")


def stamp_axes(fig, extra: str = ""):
    """Put the snapshot stamp + TENTATIVE banner on a matplotlib figure."""
    txt = tentative_banner()
    if extra:
        txt += " — " + extra
    fig.text(0.005, 0.005, txt, fontsize=5.5, color="#aa3333", ha="left", va="bottom")


def write_table(name: str, tex: str, csv_df=None) -> None:
    """Write a LaTeX table fragment (and optional CSV twin) into out/tables."""
    ensure_dirs()
    (TABLES / f"{name}.tex").write_text(tex)
    if csv_df is not None:
        csv_df.to_csv(TABLES / f"{name}.csv", index=True)
