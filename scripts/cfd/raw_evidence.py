#!/usr/bin/env python3
"""Independently recompute load-bearing metrics from raw CFD text logs."""
from __future__ import annotations

import math
from typing import Any


class RawEvidenceError(ValueError):
    """A raw solver/mesher log cannot support a scientific classification."""


def _lines(raw: bytes, label: str) -> list[str]:
    try:
        return raw.decode("utf-8").splitlines()
    except UnicodeDecodeError as exc:
        raise RawEvidenceError(f"{label} is not UTF-8 text") from exc


def _finite_float(token: str, label: str) -> float:
    try:
        value = float(token)
    except ValueError as exc:
        raise RawEvidenceError(f"{label} is not numeric") from exc
    if not math.isfinite(value):
        raise RawEvidenceError(f"{label} is not finite")
    return value


def parse_volume_meshing_log_bytes(raw: bytes) -> dict[str, Any]:
    """Reproduce ``read_volumn_meshing_log`` without trusting mesh_info.json."""
    lines = _lines(raw, "volume_meshing.log")
    header_index = None
    for index, line in enumerate(lines[:-1]):
        if line.split()[:2] == ["#", "Grid"] and lines[index + 1].split()[:2] == [
            "#",
            "Lvl",
        ]:
            header_index = index
            break
    if header_index is None:
        raise RawEvidenceError("volume_meshing.log lacks the pyHyp history header")

    rows: list[list[float]] = []
    for line_number, line in enumerate(lines[header_index + 2 :], header_index + 3):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        tokens = stripped.split()
        if len(tokens) != 14:
            continue
        try:
            row = [float(token) for token in tokens]
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in row):
            raise RawEvidenceError(
                f"volume_meshing.log row {line_number} contains a non-finite value"
            )
        rows.append(row)
    if not rows or rows[0][0] != 2.0:
        raise RawEvidenceError("volume_meshing.log lacks a complete level-2 history")
    return {
        "schema_version": "wingbench-pyhyp-log-evidence-v1",
        "history_rows": len(rows),
        "first_grid_level": rows[0][0],
        "last_grid_level": rows[-1][0],
        "min_Quality": min(row[8] for row in rows),
        "min_Vol": min(row[9] for row in rows),
        "actual_ds": rows[0][10],
    }


def parse_adflow_log_bytes(raw: bytes, *, tail_rows: int = 10) -> dict[str, Any]:
    """Reproduce the stationarity metrics derived by ``read_adflow_log``."""
    if isinstance(tail_rows, bool) or not isinstance(tail_rows, int) or tail_rows <= 0:
        raise RawEvidenceError("tail_rows must be a positive integer")
    lines = _lines(raw, "adflow.log")
    header_index = None
    for index, line in enumerate(lines[:-1]):
        if line.split()[:2] == ["#", "Grid"] and lines[index + 1].split()[:2] == [
            "#",
            "level",
        ]:
            header_index = index
            break
    if header_index is None:
        raise RawEvidenceError("adflow.log lacks the solver-history header")

    rows: list[dict[str, float]] = []
    for line_number, line in enumerate(lines[header_index + 2 :], header_index + 3):
        tokens = line.split()
        if len(tokens) != 13:
            continue
        try:
            row = {
                "grid_level": float(tokens[0]),
                "iteration": float(tokens[2]),
                "res_rho": float(tokens[7]),
                "cl": float(tokens[9]),
                "cd": float(tokens[10]),
                "cmz": float(tokens[11]),
                "total_res": float(tokens[12]),
            }
        except ValueError:
            continue
        if not all(math.isfinite(value) for value in row.values()):
            raise RawEvidenceError(
                f"adflow.log row {line_number} contains a non-finite load-bearing value"
            )
        rows.append(row)
    if not rows:
        raise RawEvidenceError("adflow.log contains no parseable solver-history rows")
    window = rows[-tail_rows:]
    first_res_rho = rows[0]["res_rho"]
    first_total_res = rows[0]["total_res"]
    if first_res_rho == 0.0 or first_total_res == 0.0:
        raise RawEvidenceError("adflow.log has a zero initial residual")
    min_res_rho = min(row["res_rho"] for row in window)
    min_total_res = min(row["total_res"] for row in window)
    return {
        "schema_version": "wingbench-adflow-log-evidence-v1",
        "history_rows": len(rows),
        "tail_rows": min(tail_rows, len(rows)),
        "err_cl": max(row["cl"] for row in window) - min(row["cl"] for row in window),
        "err_cd": max(row["cd"] for row in window) - min(row["cd"] for row in window),
        "err_cmz": max(row["cmz"] for row in window) - min(row["cmz"] for row in window),
        "n_step": rows[-1]["iteration"],
        "min_ResRho": min_res_rho,
        "min_totalRes": min_total_res,
        "min_relative_ResRho": min_res_rho / first_res_rho,
        "min_relative_totalRes": min_total_res / first_total_res,
    }
