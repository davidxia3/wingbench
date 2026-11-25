#!/usr/bin/env python3
"""Check a submission folder with the released replay.

    python3.11 benchmark/verify.py submissions/mine

For every episode folder the check

* replays ``trace.json`` with ``wingbench.benchmark.replay`` and recomputes
  every hash in the chain,
* compares the record tables with the replayed trace, row by row,
* rehashes every wing in ``wings.csv``,
* opens the sealed private grades with the revealed key and compares each
  grade with the hash the trace recorded when the grade was made.

It prints one line per episode and exits with 1 when any episode fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wingbench.benchmark.episode import V2Protocol, V2ProtocolError, validate_geometry, verify_trace  # noqa: E402
from wingbench.benchmark.replay import V2ReplayError, replay_trace  # noqa: E402

from benchmark import sealed  # noqa: E402
from benchmark.common import (  # noqa: E402
    CHECKPOINTS,
    ONLINE_BUDGET,
    OUTCOMES,
    PROFILE,
    STUDY,
    TASK_BY_ID,
    canonical_json_bytes,
    canonical_sha256,
    geometry_sha256,
    json_ready,
    load_episode_protocol,
)
from benchmark.records import (  # noqa: E402
    EPISODE_COLUMNS,
    EVALUATION_COLUMNS,
    RECOMMENDATION_COLUMNS,
    WING_COLUMNS,
    check_columns,
    geometry_from_row,
    read_table,
    text_bool,
    text_float,
    text_int,
)

TABLE_FILES = {
    "episodes.csv": EPISODE_COLUMNS,
    "evaluations.csv": EVALUATION_COLUMNS,
    "recommendations.csv": RECOMMENDATION_COLUMNS,
    "wings.csv": WING_COLUMNS,
}


def find_episode_dirs(paths: Sequence[Path]) -> list[Path]:
    found: list[Path] = []
    for path in paths:
        path = Path(path)
        if (path / "trace.json").is_file() or (path / "summary.json").is_file():
            found.append(path)
            continue
        if path.is_dir():
            for child in sorted(path.rglob("summary.json")):
                found.append(child.parent)
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(path)
    return unique


class Check:
    def __init__(self) -> None:
        self.problems: list[str] = []

    def require(self, condition: bool, message: str) -> bool:
        if not condition:
            self.problems.append(message)
        return bool(condition)


def verify_episode(folder: Path, protocol: V2Protocol) -> dict[str, Any]:
    folder = Path(folder)
    check = Check()
    report: dict[str, Any] = {"folder": str(folder), "episode": folder.name, "ok": False, "problems": check.problems}
    summary: dict[str, Any] = {}
    if (folder / "summary.json").is_file():
        summary = json.loads((folder / "summary.json").read_text(encoding="utf-8"))
        report["episode"] = str(summary.get("episode", folder.name))
        if summary.get("status") != "complete":
            check.require(False, f"episode status is {summary.get('status')!r}, not complete")
    trace_path = folder / "trace.json"
    if not check.require(trace_path.is_file(), "trace.json is missing"):
        return report
    try:
        events = json.loads(trace_path.read_bytes())
        head = verify_trace(events, protocol)
        replay = replay_trace(events, protocol.payload)
    except (ValueError, V2ProtocolError, V2ReplayError) as exc:
        check.require(False, f"trace does not replay: {exc}")
        return report
    report["trace_head_sha256"] = head
    report["replay"] = {
        "final_budget": replay["final_budget"],
        "checkpoint_count": replay["checkpoint_count"],
        "online_grade_count": replay["online_grade_count"],
        "terminal": replay["terminal"],
    }
    episode_id = str(replay["episode_id"])
    report["episode"] = episode_id
    identity = replay["identity"]
    task_name = TASK_BY_ID.get(str(identity["instance_id"]))
    check.require(task_name is not None, f"trace instance {identity['instance_id']!r} is not a task")
    check.require(replay["profile"] == PROFILE, f"trace profile is {replay['profile']!r}, not {PROFILE}")
    check.require(replay["stopped"], "the episode did not stop")
    check.require(
        replay["final_budget"]["online_cfd_queries_used"] == ONLINE_BUDGET,
        f"{replay['final_budget']['online_cfd_queries_used']} online queries, not {ONLINE_BUDGET}",
    )
    curve = {int(row["checkpoint"]): row for row in replay["recommendation_curve"]}
    check.require(set(curve) == set(CHECKPOINTS), f"checkpoints {sorted(curve)} differ from {list(CHECKPOINTS)}")
    check.require(replay["terminal"] is not None, "no terminal grade")
    if summary:
        check.require(summary.get("trace_head_sha256") == head, "summary trace head differs from the trace")
    method = str(identity["optimizer_identity"]["method_id"])
    seed = int(identity["matched_seed"])

    for name, columns in TABLE_FILES.items():
        path = folder / name
        if check.require(path.is_file(), f"{name} is missing"):
            try:
                check_columns(path, columns)
            except ValueError as exc:
                check.require(False, str(exc))
    if check.problems:
        return report
    episodes = read_table(folder / "episodes.csv")
    evaluations = read_table(folder / "evaluations.csv")
    recommendations = read_table(folder / "recommendations.csv")
    wings = read_table(folder / "wings.csv")

    def common_fields(row: dict[str, str], label: str) -> None:
        check.require(row["episode"] == episode_id, f"{label}: episode {row['episode']!r} is not {episode_id}")
        check.require(row["study"] == STUDY, f"{label}: study is {row['study']!r}, not {STUDY}")
        check.require(row["method"] == method, f"{label}: method {row['method']!r} differs from the trace")
        check.require(row["task"] == task_name, f"{label}: task {row['task']!r} differs from the trace")
        check.require(text_int(row["seed"]) == seed, f"{label}: seed differs from the trace")

    check.require(len(episodes) == 1, f"episodes.csv has {len(episodes)} rows, not 1")
    for row in episodes:
        common_fields(row, "episodes.csv")
        check.require(text_int(row["online_queries"]) == ONLINE_BUDGET, "episodes.csv online_queries is not 32")

    online_events = [event for event in events if event["event_type"] == "online_cfd_observed"]
    fresh_events = {
        int(event["event_payload"]["checkpoint"]): event
        for event in events
        if event["event_type"] == "checkpoint_grade_bound"
        and event["event_payload"]["provenance"] == "fresh_hidden"
    }
    online_rows = [row for row in evaluations if row["kind"] == "online"]
    private_rows = [row for row in evaluations if row["kind"] == "private"]
    check.require(
        len(online_rows) + len(private_rows) == len(evaluations),
        "evaluations.csv has a kind other than online or private",
    )
    check.require(len(online_rows) == len(online_events) == ONLINE_BUDGET, "online evaluation rows do not match the trace count")
    check.require(len(private_rows) == len(fresh_events), "private evaluation rows do not match the fresh grades in the trace")
    ids = [row["evaluation"] for row in evaluations]
    check.require(len(set(ids)) == len(ids), "evaluation ids repeat")

    def grade_matches(row: dict[str, str], grade: dict[str, Any], label: str) -> None:
        delivered = text_bool(row["delivered"])
        ld = text_float(row["ld"])
        check.require(delivered == grade["delivery"], f"{label}: delivered differs from the trace")
        check.require(ld == grade["conditional_value"], f"{label}: L/D differs from the trace")
        check.require(row["outcome"] in OUTCOMES, f"{label}: outcome {row['outcome']!r} is unknown")
        check.require(
            row["reasons"].split(";") == list(grade["reason_codes"]),
            f"{label}: reason codes differ from the trace",
        )

    for index, (row, event) in enumerate(zip(online_rows, online_events), start=1):
        label = f"evaluations.csv online query {index}"
        common_fields(row, label)
        check.require(text_int(row["query"]) == index, f"{label}: query number is {row['query']!r}")
        check.require(row["checkpoint"] == "", f"{label}: an online row carries a checkpoint")
        check.require(row["wing"] == event["geometry_identity"]["sha256"], f"{label}: wing differs from the trace")
        grade_matches(row, event["event_payload"]["grade"], label)
    private_by_checkpoint: dict[int, dict[str, str]] = {}
    for row in private_rows:
        label = f"evaluations.csv private grade at checkpoint {row['checkpoint']}"
        common_fields(row, label)
        checkpoint = text_int(row["checkpoint"])
        check.require(row["query"] == "", f"{label}: a private row carries a query number")
        if not check.require(checkpoint in fresh_events, f"{label}: the trace has no fresh grade there"):
            continue
        check.require(checkpoint not in private_by_checkpoint, f"{label}: repeated")
        private_by_checkpoint[checkpoint] = row  # type: ignore[index]
        event = fresh_events[checkpoint]  # type: ignore[index]
        check.require(row["wing"] == event["geometry_identity"]["sha256"], f"{label}: wing differs from the trace")
        grade_matches(row, event["event_payload"]["grade"], label)

    check.require(len(recommendations) == len(CHECKPOINTS), f"recommendations.csv has {len(recommendations)} rows, not {len(CHECKPOINTS)}")
    seen_checkpoints: set[int] = set()
    for row in recommendations:
        checkpoint = text_int(row["checkpoint"])
        label = f"recommendations.csv checkpoint {row['checkpoint']}"
        common_fields(row, label)
        if not check.require(checkpoint in curve, f"{label}: not a checkpoint of the trace"):
            continue
        check.require(checkpoint not in seen_checkpoints, f"{label}: repeated")
        seen_checkpoints.add(checkpoint)  # type: ignore[arg-type]
        point = curve[checkpoint]  # type: ignore[index]
        check.require(row["wing"] == point["geometry_sha256"], f"{label}: wing differs from the trace")
        check.require(text_bool(row["delivered"]) == point["delivery"], f"{label}: delivered differs from the trace")
        check.require(text_float(row["ld"]) == point["conditional_value"], f"{label}: L/D differs from the trace")
        expected = "online" if point["provenance"] == "reused_online_visible" else "private"
        check.require(row["graded_by"] == expected, f"{label}: graded_by is {row['graded_by']!r}, not {expected}")

    by_wing: dict[str, list[float]] = {}
    for row in wings:
        try:
            geometry = geometry_from_row(row)
        except ValueError:
            check.require(False, f"wings.csv row {row['wing'][:12]} has a bad value")
            continue
        check.require(geometry_sha256(geometry) == row["wing"], f"wings.csv row {row['wing'][:12]} does not hash to its wing id")
        try:
            validate_geometry(geometry, protocol)
        except V2ProtocolError as exc:
            check.require(False, f"wings.csv row {row['wing'][:12]} is outside the box: {exc}")
        by_wing[row["wing"]] = geometry
    referenced = {row["wing"] for row in evaluations} | {row["wing"] for row in recommendations}
    missing = sorted(referenced - set(by_wing))
    check.require(not missing, f"wings.csv lacks {len(missing)} referenced wings")

    reveal_path = folder / "private_grades.json"
    key_path = folder / "private_key.hex"
    sealed_path = folder / "private_grades.enc"
    if check.require(reveal_path.is_file() and key_path.is_file() and sealed_path.is_file(), "the private grade reveal is incomplete"):
        try:
            reveal = json.loads(reveal_path.read_text(encoding="utf-8"))
            key = bytes.fromhex(key_path.read_text(encoding="utf-8").strip())
            plaintext = sealed.unseal(key, sealed_path.read_bytes())
        except ValueError as exc:
            check.require(False, f"the sealed private grades do not open with the revealed key: {exc}")
        else:
            entries = reveal.get("private_grades", [])
            expected_bytes = canonical_json_bytes(json_ready(entries))
            check.require(plaintext == expected_bytes, "the sealed private grades differ from the revealed ones")
            check.require(
                hashlib.sha256(plaintext).hexdigest() == reveal.get("sealed_plaintext_sha256"),
                "the sealed plaintext hash in the reveal is wrong",
            )
            check.require(len(entries) == len(fresh_events), "the reveal does not have one entry per fresh grade")
            for entry in entries:
                checkpoint = int(entry["checkpoint"])
                label = f"revealed private grade at checkpoint {checkpoint}"
                if not check.require(checkpoint in fresh_events, f"{label}: the trace has no fresh grade there"):
                    continue
                event = fresh_events[checkpoint]
                grade = dict(entry["grade"])
                claimed = grade.pop("result_sha256", None)
                check.require(canonical_sha256(grade) == claimed, f"{label}: grade hash does not match its content")
                check.require(claimed == event["event_payload"]["grade"]["result_sha256"], f"{label}: grade hash differs from the trace")
                check.require(claimed == event["artifact_bindings"][0]["sha256"], f"{label}: artifact binding differs from the trace")
                check.require(entry["wing"] == event["geometry_identity"]["sha256"], f"{label}: wing differs from the trace")
                row = private_by_checkpoint.get(checkpoint)
                check.require(
                    row is not None and row["evaluation"] == entry["evaluation"],
                    f"{label}: evaluation id differs from evaluations.csv",
                )
    report["ok"] = not check.problems
    report["task"] = task_name
    report["method"] = method
    report["seed"] = seed
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path, help="submission folders or episode folders")
    parser.add_argument("--json", type=Path, default=None, help="write the full report to this file")
    args = parser.parse_args(argv)
    protocol = load_episode_protocol()
    folders = find_episode_dirs(args.paths)
    if not folders:
        print("no episode folders found", file=sys.stderr)
        return 2
    reports = []
    failures = 0
    for folder in folders:
        report = verify_episode(folder, protocol)
        reports.append(report)
        if report["ok"]:
            budget = report["replay"]["final_budget"]
            print(
                f"OK    {report['episode']}  task {report['task']}  seed {report['seed']}  "
                f"{budget['online_cfd_queries_used']} online queries, "
                f"{budget['fresh_checkpoint_grades_used']} private grades  head {report['trace_head_sha256'][:12]}"
            )
        else:
            failures += 1
            print(f"FAIL  {report['episode']}  ({folder})")
            for problem in report["problems"]:
                print(f"      {problem}")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(reports, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"{len(folders) - failures} of {len(folders)} episodes verified")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
