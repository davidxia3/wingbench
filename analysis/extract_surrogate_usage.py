#!/usr/bin/env python3
"""Extract recorded surrogate usage; no surrogate, model, or CFD is executed.

The original campaign archives are not distributed. Released extracts and their
source fingerprints let 20_resource_accounting.py run without those archives.
"""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def extract(archive: Path) -> None:
    episodes = list(csv.DictReader((ROOT / "data/episodes.csv").open()))
    records, sources = [], {}

    def bind(path: Path, base: Path) -> str:
        key = str(path.relative_to(base))
        sources[key] = hashlib.sha256(path.read_bytes()).hexdigest()
        return key

    for ep in episodes:
        if ep["study"] == "baseline":
            if ep["method"] == "SMAC3":
                # This method has no surrogate interface, not a missing log.
                source = bind(ROOT / "wingbench/optimizers/smac3.py", ROOT)
                counts = {4: 0, 32: 0}
                basis = "method_without_surrogate"
            else:
                path = archive / "campaign_snapshot/episodes" / ep["original_id"] / "trace.json.gz"
                source = bind(path, archive)
                with gzip.open(path, "rt") as handle:
                    trace = json.load(handle)
                assert ep["original_id"].endswith(trace[0]["episode_id"])
                counts = {
                    event["event_payload"]["checkpoint"]: event["budget_after"]["cheap_queries_used"]
                    for event in trace if event["event_type"] == "checkpoint_nominated"
                }
                counts = {q: counts[q] for q in (4, 32)}
                assert counts[32] == 4096
                basis = "trace_budget_counter"
        else:
            folder = archive / "host_pull_2026-09-25/extracted_private/private" / ep["original_id"]
            source = bind(folder / "surrogate_ledger.jsonl", archive)
            counter_source = bind(folder / "counters.json", archive)
            ledger = [json.loads(line) for line in (folder / "surrogate_ledger.jsonl").read_text().splitlines()]
            counters = json.loads((archive / counter_source).read_text())
            charged = [event for event in ledger if event["kind"] == "chunk_committed"]
            for event in charged:
                assert re.fullmatch(r"D-q\d+", event["decision_id"]), event["decision_id"]
            assert sum(event["n_charged"] for event in charged) == counters["surrogate_queries_used"]
            horizon = int(ep["online_queries"])
            assert all(int(event["decision_id"].split("q")[-1]) <= horizon for event in charged)
            counts = {
                q: sum(event["n_charged"] for event in charged
                       if int(event["decision_id"].split("q")[-1]) <= q)
                for q in sorted({4, horizon})
            }
            basis = "committed_ledger_charges"
        for q, count in sorted(counts.items()):
            assert 0 <= count <= 4096
            records.append(dict(episode=ep["episode"], checkpoint=q,
                                surrogate_evaluations=count, basis=basis, source=source))

    assert len(records) == 420
    records.sort(key=lambda row: (row["episode"], row["checkpoint"]))
    destination = ROOT / "data/surrogate_usage.csv"
    with destination.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(records[0]))
        writer.writeheader()
        writer.writerows(records)
    provenance = {
        "description": "SHA-256 of the exact source bytes; archive paths are relative to the original data archive, wingbench/ paths to this repository.",
        "extraction": "analysis/extract_surrogate_usage.py",
        "source_sha256": dict(sorted(sources.items())),
        "episodes_sha256": hashlib.sha256((ROOT / "data/episodes.csv").read_bytes()).hexdigest(),
        "extract_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "definition": "Cumulative charged candidate evaluations through checkpoint. Baselines use checkpoint nomination counters; agents sum committed charges for decisions q01 through qNN. Exact repeats served from the agent episode cache are not charged. SMAC3 uses no surrogate.",
    }
    (ROOT / "data/surrogate_usage_provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    print(f"Extracted {len(records)} checkpoint counts for {len(episodes)} episodes.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True, type=Path)
    extract(parser.parse_args().archive_root)
