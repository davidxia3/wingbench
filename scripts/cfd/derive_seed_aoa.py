#!/usr/bin/env python3
"""Derive the canonical solveCL seed from the SuperWing operating envelope.

This builder selects every successful SuperWing case inside the grader's accepted
target-lift band and takes the exact median recorded AoA without rounding.  The
result is one permanent initializer for every wing and every grading condition.

Failed cases are audited but excluded from selection because their AoA distribution
is dominated by buffet and divergence.  This script never runs CFD.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
INDEX_PATH = REPO_ROOT / "data/superwing/index.npy"
RUNNING_INFO_PATH = REPO_ROOT / "data/superwing/running_info.jsonl"
DEFAULT_OUTPUT = REPO_ROOT / "configs/cfd/seed_aoa_selection_v1.json"

SCHEMA_VERSION = "wingbench-cfd-seed-aoa-selection-v1"
TARGET_CL = Decimal("0.41")
CL_BAND = (Decimal("0.39"), Decimal("0.43"))
MACH_HALF_WIDTH = Decimal("0.01")
CONDITIONS = (
    ("mach_q1", Decimal("0.7853")),
    ("mach_q2", Decimal("0.8195")),
    ("mach_q3", Decimal("0.853")),
)
FAILED_STATES = ("CL fluctuation too large", "Simulation Diverge")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _decimal(value: Any) -> Decimal:
    """Preserve the shortest exact decimal representation stored by NumPy/JSON."""
    if isinstance(value, Decimal):
        return value
    return Decimal(str(float(value)))


def exact_median(values: Iterable[Any]) -> tuple[Decimal, Decimal, Decimal, int]:
    ordered = sorted(_decimal(value) for value in values)
    count = len(ordered)
    if count == 0:
        raise ValueError("cannot take the median of an empty population")
    upper = ordered[count // 2]
    lower = upper if count % 2 else ordered[count // 2 - 1]
    return lower, upper, (lower + upper) / Decimal(2), count


def _median_record(values: Iterable[Any]) -> dict[str, Any]:
    lower, upper, median, count = exact_median(values)
    return {
        "count": count,
        "lower_middle_deg": float(lower),
        "upper_middle_deg": float(upper),
        "median_deg": float(median),
    }


def _load_running_info(path: Path) -> dict[str, Any]:
    top_level_states: Counter[str] = Counter()
    condition_states: Counter[str] = Counter()
    successful: list[Decimal] = []
    failed: list[Decimal] = []
    recorded: list[Decimal] = []
    missing_aoa: Counter[str] = Counter()
    record_count = 0

    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            row = json.loads(line, parse_float=Decimal)
            if not isinstance(row, dict):
                raise ValueError(f"running_info line {line_number} is not an object")
            record_count += 1
            top_level_states[str(row.get("state"))] += 1
            for condition_index in range(8):
                state = row.get(f"{condition_index}_state")
                state_label = "null" if state is None else str(state)
                condition_states[state_label] += 1
                info = row.get(f"{condition_index}_running_info") or {}
                working_conditions = info.get("working_conditions") or {}
                alpha = working_conditions.get("AA")
                if alpha is None:
                    missing_aoa[state_label] += 1
                    continue
                alpha = _decimal(alpha)
                recorded.append(alpha)
                if state == "Success":
                    successful.append(alpha)
                elif state in FAILED_STATES:
                    failed.append(alpha)
                else:
                    raise ValueError(
                        f"unexpected recorded-AoA state {state!r} at line {line_number}"
                    )

    return {
        "record_count": record_count,
        "scheduled_condition_slots": record_count * 8,
        "top_level_states": dict(sorted(top_level_states.items())),
        "condition_states": dict(sorted(condition_states.items())),
        "missing_aoa_by_state": dict(sorted(missing_aoa.items())),
        "successful_aoa": successful,
        "failed_aoa": failed,
        "recorded_aoa": recorded,
    }


def build_selection(
    index_path: Path = INDEX_PATH,
    running_info_path: Path = RUNNING_INFO_PATH,
) -> dict[str, Any]:
    index = np.load(index_path, allow_pickle=False)
    if index.ndim != 2 or index.shape[1] < 11:
        raise ValueError("index.npy must be a two-dimensional table with at least 11 columns")
    if not np.isfinite(index[:, [2, 3, 9]]).all():
        raise ValueError("index.npy AoA, Mach, and CL columns must be finite")

    audit = _load_running_info(running_info_path)
    successful_index_aoa = [_decimal(value) for value in index[:, 2]]
    if sorted(successful_index_aoa) != sorted(audit["successful_aoa"]):
        raise ValueError("index.npy and running_info.jsonl successful AoA populations differ")

    successful = _median_record(successful_index_aoa)
    failed = _median_record(audit["failed_aoa"])
    all_recorded = _median_record(audit["recorded_aoa"])

    cl = index[:, 9]
    mach = index[:, 3]
    alpha = index[:, 2]
    cl_band_mask = (cl >= float(CL_BAND[0])) & (cl <= float(CL_BAND[1]))
    target_band = _median_record(alpha[cl_band_mask])

    per_condition: list[dict[str, Any]] = []
    pooled_mask = np.zeros(len(index), dtype=bool)
    condition_medians: list[Decimal] = []
    for condition_id, target_mach in CONDITIONS:
        mach_mask = np.abs(mach - float(target_mach)) < float(MACH_HALF_WIDTH)
        selected = cl_band_mask & mach_mask
        pooled_mask |= selected
        record = _median_record(alpha[selected])
        condition_medians.append(_decimal(record["median_deg"]))
        per_condition.append({
            "condition_id": condition_id,
            "mach": float(target_mach),
            **record,
        })

    lower, upper, equal_weight_median, condition_count = exact_median(condition_medians)
    canonical_seed = _decimal(target_band["median_deg"])
    if canonical_seed != Decimal("6.6401110468"):
        raise ValueError(f"canonical seed changed unexpectedly to {canonical_seed}")

    successful_count = len(audit["successful_aoa"])
    failed_count = len(audit["failed_aoa"])
    recorded_count = len(audit["recorded_aoa"])
    missing_count = sum(audit["missing_aoa_by_state"].values())
    if successful_count != len(index):
        raise ValueError("successful running_info count does not match index.npy")
    if successful_count + failed_count != recorded_count:
        raise ValueError("recorded AoA population is not success plus simulated failure")
    if recorded_count + missing_count != audit["scheduled_condition_slots"]:
        raise ValueError("scheduled condition-slot audit does not close")

    return {
        "schema_version": SCHEMA_VERSION,
        "selection_status": "canonical_candidate_input",
        "canonical_seed_alpha0_deg": float(canonical_seed),
        "selection_rule": {
            "population": "successful_superwing_cases",
            "target_cl": float(TARGET_CL),
            "cl_band_inclusive": [float(CL_BAND[0]), float(CL_BAND[1])],
            "cl_band_basis": "target_cl_plus_or_minus_protocol_cl_match_tolerance",
            "mach_population": "all_recorded_superwing_machs",
            "statistic": "exact_median_recorded_aoa",
            "rounding": "none",
            "applies_to_every_wing_and_condition": True,
            "future_reselection_allowed": False,
            "failed_cases_in_selection": False,
            "reason_failed_cases_are_excluded": (
                "The initializer targets a successful CL=0.41 trim branch; failed-case "
                "AoA is strongly shifted toward buffet and divergence."
            ),
        },
        "source_files": {
            "successful_case_table": {
                "path": "data/superwing/index.npy",
                "sha256": sha256_file(index_path),
                "shape": list(index.shape),
                "dtype": str(index.dtype),
                "columns": {"aoa": 2, "mach": 3, "cl": 9},
            },
            "all_case_state_table": {
                "path": "data/superwing/running_info.jsonl",
                "sha256": sha256_file(running_info_path),
            },
        },
        "population_audit": {
            "wing_records": audit["record_count"],
            "scheduled_condition_slots": audit["scheduled_condition_slots"],
            "recorded_aoa_cases": recorded_count,
            "successful_cases": successful_count,
            "failed_simulated_cases": failed_count,
            "missing_aoa_cases": missing_count,
            "top_level_states": audit["top_level_states"],
            "condition_states": audit["condition_states"],
            "missing_aoa_by_state": audit["missing_aoa_by_state"],
            "successful_aoa": successful,
            "failed_simulated_aoa": failed,
            "all_recorded_aoa": all_recorded,
        },
        "target_cl_evidence": {
            "canonical_selection_population": target_band,
            "benchmark_mach_context": {
                "benchmark_machs": [float(mach) for _, mach in CONDITIONS],
                "mach_neighborhood_abs_less_than": float(MACH_HALF_WIDTH),
                "per_condition": per_condition,
                "pooled_cases": _median_record(alpha[pooled_mask]),
                "equal_weight_condition_summary": {
                "condition_count": condition_count,
                "lower_middle_deg": float(lower),
                "upper_middle_deg": float(upper),
                "median_of_condition_medians_deg": float(equal_weight_median),
                },
            },
        },
    }


def rendered_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", type=Path, default=INDEX_PATH)
    parser.add_argument("--running-info", type=Path, default=RUNNING_INFO_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()

    expected = rendered_bytes(build_selection(args.index, args.running_info))
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != expected:
            raise SystemExit(f"STALE: {args.output}")
        print(f"OK: {args.output} is valid and current")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
