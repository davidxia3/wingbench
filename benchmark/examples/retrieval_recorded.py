"""An example optimizer that only proposes wings from the released records.

It exists so that ``run_episode.py --evaluator recorded`` has something to
answer. It reads which wings of ``data/wings.csv`` were graded under its task,
shuffles them with its seed, and proposes them one by one. It never looks at
the recorded outcomes. It recommends the best delivered wing it has seen, and
before the first delivery it recommends the next wing it is about to propose.

This is a plumbing test, not a method. A real optimizer searches the design
box with ``ctx.cheap_query`` and proposes new wings.

    python3.11 benchmark/run_episode.py \
        --optimizer benchmark.examples.retrieval_recorded:RetrievalRecorded \
        --task nominal --seed 1 --evaluator recorded --out submissions/example
"""
from __future__ import annotations

import csv
from pathlib import Path
import random
from typing import Any, Sequence

from benchmark.optimizer_api import Box, EpisodeContext, OnlineResult, Optimizer, TaskSpec

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"


class RetrievalRecorded(Optimizer):
    name = "Retrieval from records (example)"

    def __init__(self, task: TaskSpec, seed: int, box: Box, **options: Any) -> None:
        super().__init__(task, seed, box, **options)
        graded_here: set[str] = set()
        with (DATA / "evaluations.csv").open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["task"] == task.name and row["wing"]:
                    graded_here.add(row["wing"])
        # Only wings inside the design box, within the machine's edge tolerance.
        pool: list[tuple[float, ...]] = []
        with (DATA / "wings.csv").open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row["wing"] in graded_here:
                    wing = tuple(float(row[f"x{index:02d}"]) for index in range(38))
                    if box.contains(wing):
                        pool.append(wing)
        if len(pool) < task.online_budget + 1:
            raise RuntimeError(f"only {len(pool)} recorded wings exist for task {task.name}")
        random.Random(seed).shuffle(pool)
        self.queue = pool
        self.position = 0
        self.results: list[OnlineResult] = []

    def propose(self, ctx: EpisodeContext) -> Sequence[float]:
        wing = self.queue[self.position]
        self.position += 1
        return wing

    def observe(self, result: OnlineResult) -> None:
        self.results.append(result)

    def recommend(self) -> Sequence[float]:
        delivered = [row for row in self.results if row.delivered and row.ld is not None]
        if delivered:
            return max(delivered, key=lambda row: row.ld).geometry
        return self.queue[self.position]
