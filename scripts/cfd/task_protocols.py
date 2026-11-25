#!/usr/bin/env python3
"""Load the two prospectively frozen load-instance qualification protocols.

The V1/V2 continuity protocol remains byte-for-byte unchanged.  This loader
starts from that validated protocol and permits only three in-memory changes:
the protocol identity, the common CL target, and its data-derived AoA seed.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.cfd.grading_protocol import (  # noqa: E402
    GradingProtocol,
    ProtocolError,
    load_protocol,
    protocol_sha256,
    sha256_file,
)


ROOT = Path(__file__).resolve().parents[2]
SCHEMA = "wingbench-v2-load-instance-qualification-v1"
MACH_GRID = [0.7853, 0.8195, 0.853]
ALLOWED = {
    "superwing-load-low-cl015": {
        "cl_target": 0.15,
        "alpha0_deg": 4.4259755152,
    },
    "superwing-load-high-cl066": {
        "cl_target": 0.66,
        "alpha0_deg": 8.924059800550001,
    },
}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read qualification instance {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("qualification instance root must be an object")
    return value


def load_instance_protocol(
    path: str | Path,
    *,
    verify_files: bool = True,
) -> GradingProtocol:
    resolved = Path(path).resolve()
    spec = _load_json(resolved)
    instance_id = spec.get("instance_id")
    expected = ALLOWED.get(str(instance_id))
    if (
        spec.get("schema_version") != SCHEMA
        or expected is None
        or spec.get("development_only") is not True
        or spec.get("official") is not False
        or spec.get("sealed") is not False
        or spec.get("mach_grid") != MACH_GRID
        or spec.get("cl_target") != expected["cl_target"]
        or spec.get("aoa_seed_deg") != expected["alpha0_deg"]
        or spec.get("only_scientific_differences_from_base")
        != ["common_cl_target", "fixed_aoa_seed"]
    ):
        raise ProtocolError("load-instance qualification scope changed")

    base_binding = spec.get("base_protocol_binding") or {}
    base_path = ROOT / str(base_binding.get("path", ""))
    if not base_path.is_file() or sha256_file(base_path) != base_binding.get("file_sha256"):
        raise ProtocolError("base protocol binding changed")
    base = load_protocol(base_path, verify_files=verify_files)
    if base.sha256 != base_binding.get("canonical_sha256"):
        raise ProtocolError("base protocol canonical identity changed")

    seed_binding = spec.get("seed_selection_binding") or {}
    seed_path = ROOT / str(seed_binding.get("path", ""))
    if not seed_path.is_file() or sha256_file(seed_path) != seed_binding.get("file_sha256"):
        raise ProtocolError("seed-selection binding changed")
    seed = _load_json(seed_path)
    population = (seed.get("target_cl_evidence") or {}).get(
        "canonical_selection_population"
    ) or {}
    if (
        seed.get("selection_status") != "frozen_qualification_input"
        or (seed.get("selection_rule") or {}).get("target_cl") != expected["cl_target"]
        or seed.get("canonical_seed_alpha0_deg") != expected["alpha0_deg"]
        or population.get("median_deg") != expected["alpha0_deg"]
    ):
        raise ProtocolError("seed-selection content changed")

    payload = copy.deepcopy(base.payload)
    payload["protocol_id"] = f"{instance_id}-qualification-v1"
    payload["protocol_status"] = "qualification_required"
    payload["official_grading_enabled"] = False
    for condition in payload["conditions"]:
        condition["cl_target"] = expected["cl_target"]
    payload["seed_policy"] = {
        "type": "fixed_per_condition",
        "alpha0_deg": expected["alpha0_deg"],
        "source": "superwing-successful-target-cl-band-median-load-qualification-v1",
        "selection_record_path": str(seed_path.relative_to(ROOT)),
        "selection_rule": (
            f"exact_median_successful_cases_in_cl_"
            f"{expected['cl_target'] - 0.02:.2f}_"
            f"{expected['cl_target'] + 0.02:.2f}_no_rounding"
        ),
        "applies_to_every_wing_and_condition": True,
        "future_reselection_allowed": False,
    }
    return GradingProtocol(
        path=resolved,
        payload=payload,
        sha256=protocol_sha256(payload),
    )


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("instance", type=Path)
    parser.add_argument("--no-verify-files", action="store_true")
    args = parser.parse_args()
    try:
        protocol = load_instance_protocol(
            args.instance,
            verify_files=not args.no_verify_files,
        )
    except (OSError, ValueError, ProtocolError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "conditions": protocol.conditions,
                "path": str(protocol.path),
                "protocol_id": protocol.protocol_id,
                "protocol_sha256": protocol.sha256,
                "seed_alpha0_deg": protocol.seed_alpha0,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
