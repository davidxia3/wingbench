#!/usr/bin/env python3
"""Check every graded CFD request of the agent study.

The agent study made 1,336 graded CFD requests: 1,200 online queries, 90
private grades before the first query, and 46 later private grades. Another
224 recommendations reused an online result and are not new requests.
"""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTCOMES = ("pass", "partial", "undetermined", "conclusive_failure")


def rows(name: str) -> list[dict]:
    with (ROOT / "data" / f"{name}.csv").open() as f:
        return [r for r in csv.DictReader(f) if r["study"] == "agent"]


def count() -> dict:
    evaluations, recommendations = rows("evaluations"), rows("recommendations")
    online = [r["outcome"] for r in evaluations if r["kind"] == "online"]
    first_private = [r["outcome"] for r in evaluations if r["kind"] == "private" and r["checkpoint"] == "0"]
    later_private = [r["outcome"] for r in evaluations if r["kind"] == "private" and r["checkpoint"] != "0"]
    reused = [r for r in recommendations if r["graded_by"] == "online"]
    assert len(rows("episodes")) == 90
    assert len(online) == 1200
    assert len(later_private) == 46
    assert len(first_private) == 90
    assert len(reused) == 224
    assert all(value in OUTCOMES for value in online + later_private + first_private)
    by_group = {
        "online": Counter(online),
        "initial_private": Counter(first_private),
        "later_private": Counter(later_private),
    }
    total = sum(by_group.values(), Counter())
    assert sum(total.values()) == 1336
    assert total == Counter({
        "pass": 910, "partial": 218, "undetermined": 154,
        "conclusive_failure": 54,
    })
    return {
        "graded_requests": sum(total.values()),
        "reused_online_recommendations_not_new_requests": len(reused),
        "groups": {group: dict(sorted(values.items()))
                   for group, values in by_group.items()},
        "outcome_types": dict(sorted(total.items())),
    }


if __name__ == "__main__":
    print(json.dumps(count(), indent=2, sort_keys=True))
