#!/usr/bin/env python3
"""Convenience conversions for the 38-D design vector. Optional.

This is a small helper, not a method you have to use. It holds nothing but the
normalization arithmetic and a dataset loader.

    import geometry as G
    u = G.to_unit(phys); phys = G.to_physical(u)
    df_like = G.load_dataset()          # list of dicts, all columns as strings
    rows = G.geometries()               # (running_id, [38 floats]) for every row
"""
from __future__ import annotations
import csv, json, os
from pathlib import Path

SRC = Path(__file__).resolve().parent
_N = json.loads((SRC / "normalization.json").read_text())


def _arrs():
    import numpy as np
    return np.asarray(_N["mean_38d"], float), np.asarray(_N["sd_38d"], float)


def to_unit(phys):
    import numpy as np
    mean, sd = _arrs()
    return (np.asarray(phys, float) - (mean - 3.5 * sd)) / (7.0 * sd)


def to_physical(unit):
    import numpy as np
    mean, sd = _arrs()
    return (mean - 3.5 * sd) + np.asarray(unit, float) * (7.0 * sd)


def in_domain(phys, tol=1e-12) -> bool:
    u = to_unit(phys)
    return bool((u >= -tol).all() and (u <= 1 + tol).all())


WING_TABLE = SRC / "superwing_public_dataset_v1.csv"
CONDITIONS_TABLE = SRC / "conditions.csv"
CONDITIONS_RECORDS = SRC / "conditions.jsonl"
RAW_RECORDS = SRC / "running_info.jsonl"


def load_dataset(path=None):
    """One row per wing, every column as a string."""
    with open(path or WING_TABLE, newline="") as fh:
        return list(csv.DictReader(fh))


def load_conditions(path=None):
    """One row per (wing, condition slot), scalar fields only. See
    CONDITIONS_README.md for the full records and the field mapping."""
    with open(path or CONDITIONS_TABLE, newline="") as fh:
        return list(csv.DictReader(fh))


def geometries(rows=None):
    rows = rows if rows is not None else load_dataset()
    return [(r["running_id"], [float(r[f"x{i:02d}"]) for i in range(38)]) for r in rows]
