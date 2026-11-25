"""Warm-start context for the sealed baselines.

The four surrogate-assisted baselines start every episode from the same fixed
context: the 50-wing corpus and the matched gate-validation results recorded
before the campaign. The files are under configs/benchmark/ and are hashed into
the frozen method panel, so they must not be edited.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_relative(path: str) -> object:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def build_initial_context() -> dict[str, object]:
    corpus50 = load_relative("configs/benchmark/undefended_50_wing_corpus_v1.json")
    batch1 = load_relative(
        "configs/benchmark/corpus50_gate_validation_batch1_candidates_v1.json"
    )
    batch2 = load_relative(
        "configs/benchmark/corpus50_gate_validation_batch2_candidates_v1.json"
    )
    results = load_relative(
        "configs/benchmark/corpus50_gate_matched_validation_results_v1.json"
    )
    candidates = {
        row["candidate_id"]: row
        for manifest in (batch1, batch2)
        for row in manifest["candidates"]
    }
    matched_ungated = []
    for pair in results["pairs"]:
        result = pair["ungated"]
        candidate = candidates[result["candidate_id"]]
        if candidate["arm"] != "ungated":
            raise ValueError(f"{candidate['candidate_id']} is not an ungated candidate")
        if candidate["geometry_sha256"] != result["geometry_sha256"]:
            raise ValueError(f"geometry mismatch for {candidate['candidate_id']}")
        matched_ungated.append({"candidate": candidate, "result": result})
    return {
        "schema_version": "wingbench-mf-warm-start-selection-v1",
        "corpus50_rows": corpus50["rows"],
        "matched_validation_ungated": matched_ungated,
    }
