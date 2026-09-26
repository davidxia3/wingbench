#!/usr/bin/env python3
"""Show how the condition records are laid out. Interface documentation only.

This is not a witness, a scoring rule, or a search strategy. It reads three rows
and prints them, so you can see what a successful condition, a failed condition
and an absent slot look like.

  python3 source/load_conditions.py
"""
from __future__ import annotations
import json
from pathlib import Path

SRC = Path(__file__).resolve().parent


def iter_conditions(path=None):
    """Yield one dict per (wing, condition slot). Streams; the file is large."""
    with open(path or SRC / "conditions.jsonl", encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield json.loads(line)


def conditions_for(running_id: int, path=None):
    return [r for r in iter_conditions(path) if r["running_id"] == running_id]


def show(row, label):
    print(f"--- {label} ---")
    print(f"  wing {row['running_id']}, condition slot {row['condition_index']}")
    print(f"  slot_recorded : {row['slot_recorded']}")
    print(f"  state         : {row['state']!r}")
    print(f"  mach          : {row['mach']!r}")
    print(f"  aoa_deg       : {row['aoa_deg']!r}")
    print(f"  reynolds      : {row['reynolds']!r}")
    print(f"  temperature   : {row['temperature']!r}")
    res = row.get("results")
    if res:
        keys = sorted(res)
        print(f"  results keys  : {keys}")
        for k in ("wing_cl", "wing_cd", "err_cl", "n_step"):
            if k in res:
                print(f"    {k:8s} = {res[k]!r}")
    else:
        print("  results       : none recorded")
    g = row.get("geometry_38d")
    print(f"  geometry_38d  : {len(g) if g else 0} values, first three "
          f"{[round(x, 6) for x in (g or [])[:3]]}")
    print(f"  source_pointer: {row['source_pointer']}")
    print()


def main():
    want = {"Success": None, "failed": None, "absent": None}
    for row in iter_conditions():
        if not row["slot_recorded"] and want["absent"] is None:
            want["absent"] = row
        elif row["state"] == "Success" and want["Success"] is None:
            want["Success"] = row
        elif row["state"] in ("CL fluctuation too large", "Simulation Diverge") \
                and want["failed"] is None:
            want["failed"] = row
        if all(v is not None for v in want.values()):
            break
    if want["Success"]:
        show(want["Success"], "a condition that converged")
    if want["failed"]:
        show(want["failed"], f"a condition that was run and failed "
                             f"({want['failed']['state']})")
    if want["absent"]:
        show(want["absent"], "a slot that is absent from the record entirely")
    else:
        print("--- no absent slot found in the scanned prefix; wing 1418 holds "
              "the eight absent slots ---")
    print("The whole file streams with iter_conditions(); conditions_for(id) "
          "picks one wing.")


if __name__ == "__main__":
    main()
