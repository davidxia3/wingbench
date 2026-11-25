"""Read and write the record tables in the schemas of ``data/``.

The four tables have exactly the columns of ``data/evaluations.csv``,
``data/recommendations.csv``, ``data/wings.csv`` and ``data/episodes.csv``.
Floats are written with Python's shortest round trip text, so a wing read back
from ``wings.csv`` hashes to the same identifier.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from benchmark.common import DESIGN_DIM, geometry_sha256

EVALUATION_COLUMNS = (
    "evaluation", "episode", "study", "method", "task", "seed", "kind", "query",
    "checkpoint", "wing", "delivered", "ld", "outcome", "reasons", "recorded_at",
    "original_id",
)
RECOMMENDATION_COLUMNS = (
    "episode", "study", "method", "task", "seed", "checkpoint", "wing", "delivered",
    "ld", "graded_by",
)
WING_COLUMNS = ("wing",) + tuple(f"x{index:02d}" for index in range(DESIGN_DIM))
EPISODE_COLUMNS = (
    "episode", "study", "method", "task", "target_cl", "seed", "online_queries",
    "cfd_core_seconds", "timed_evaluations", "original_id",
)
TABLES = {
    "evaluations": EVALUATION_COLUMNS,
    "recommendations": RECOMMENDATION_COLUMNS,
    "wings": WING_COLUMNS,
    "episodes": EPISODE_COLUMNS,
}


def cell_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, float):
        if value != value:
            return ""
        return repr(value)
    return str(value)


def write_table(path: Path, columns: Sequence[str], rows: Iterable[Mapping[str, Any]]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(list(columns))
        for row in rows:
            unknown = set(row) - set(columns)
            if unknown:
                raise ValueError(f"row has columns outside the schema: {sorted(unknown)}")
            writer.writerow([cell_text(row.get(column)) for column in columns])


def read_table(path: Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as stream:
        return list(csv.DictReader(stream))


def check_columns(path: Path, columns: Sequence[str]) -> None:
    with Path(path).open(newline="", encoding="utf-8") as stream:
        header = next(csv.reader(stream), None)
    if header != list(columns):
        raise ValueError(f"{path} does not have the columns of the released schema")


def wing_row(geometry: Sequence[float]) -> dict[str, Any]:
    values = [float(value) for value in geometry]
    row: dict[str, Any] = {"wing": geometry_sha256(values)}
    for index, value in enumerate(values):
        row[f"x{index:02d}"] = value
    return row


def geometry_from_row(row: Mapping[str, str]) -> list[float]:
    return [float(row[f"x{index:02d}"]) for index in range(DESIGN_DIM)]


def text_bool(value: str) -> bool:
    if value == "True":
        return True
    if value == "False":
        return False
    raise ValueError(f"not a True/False cell: {value!r}")


def text_float(value: str) -> float | None:
    return None if value == "" else float(value)


def text_int(value: str) -> int | None:
    if value == "":
        return None
    return int(float(value))
