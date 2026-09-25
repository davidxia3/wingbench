"""Resolve the physical geometry of a SuperWing dataset row.

``configs.dat`` row positions are the dataset indices used by ``index.npy``, but
column 0 contains the non-sequential source design ID.  The physical geometry
meshed by the dataset is the ``shape`` stored in ``running_info.jsonl`` under the
matching ``running_id``.  In particular, its index-6 ``rootadj`` value uses a
different convention from ``configs.dat``.

This module makes that identity chain explicit and deliberately returns the
entire 38-D ``running_info.shape``.  It never derives or rounds ``rootadj``.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import numbers
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np

from scripts.cfd.geometry_hash import GEOMETRY_HASH_ALGORITHM, geometry_sha256


DESIGN_DIM = 38
ROOTADJ_INDEX = 6
MAX_EXACT_FLOAT_INTEGER = 2**53 - 1
GEOMETRY_HASH_CANONICALIZATION = GEOMETRY_HASH_ALGORITHM
ID_RESOLUTION = (
    "dataset_index -> configs.dat row -> configs.dat column 0 source_design_id "
    "-> running_info.jsonl running_id -> running_info.jsonl shape"
)


@dataclass(frozen=True)
class DatasetGeometryResolution:
    """A lossless, provenance-bearing dataset geometry resolution."""

    dataset_index: int
    source_design_id: int
    running_id: int
    geometry_38d: tuple[float, ...]
    geometry_sha256: str
    provenance: dict[str, Any]

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable representation without rounding geometry."""

        return {
            "dataset_index": self.dataset_index,
            "source_design_id": self.source_design_id,
            "running_id": self.running_id,
            "geometry_38d": list(self.geometry_38d),
            "geometry_dimension": DESIGN_DIM,
            "geometry_sha256": self.geometry_sha256,
            "geometry_hash_canonicalization": GEOMETRY_HASH_CANONICALIZATION,
            "provenance": self.provenance,
        }


@dataclass(frozen=True)
class _ConfigsRecord:
    dataset_index: int
    source_design_id: int
    geometry_38d: tuple[float, ...]


@dataclass(frozen=True)
class _RunningRecord:
    running_id: int
    line_number: int
    geometry_38d: tuple[float, ...]


class DatasetGeometryResolver:
    """Validated resolver for one ``configs.dat``/``running_info.jsonl`` pair.

    Construction validates the full crosswalk eagerly.  Every configs row must
    have exactly one matching running record, and all parameters other than
    ``rootadj`` must agree exactly.  Extra running records are allowed because
    ``running_info.jsonl`` also contains designs absent from the filtered configs
    table.
    """

    def __init__(
        self,
        *,
        configs_path: str | Path,
        running_info_path: str | Path,
    ) -> None:
        self.configs_path = Path(configs_path)
        self.running_info_path = Path(running_info_path)
        self._configs = _load_configs(self.configs_path)
        self._running = _load_running_info(self.running_info_path)
        _validate_crosswalk(self._configs, self._running)
        self._configs_sha256 = _file_sha256(self.configs_path)
        self._running_info_sha256 = _file_sha256(self.running_info_path)

    @classmethod
    def from_files(
        cls,
        configs_path: str | Path,
        running_info_path: str | Path,
    ) -> "DatasetGeometryResolver":
        return cls(configs_path=configs_path, running_info_path=running_info_path)

    def __len__(self) -> int:
        return len(self._configs)

    def resolve(self, dataset_index: int) -> DatasetGeometryResolution:
        index = _require_dataset_index(dataset_index, len(self._configs))
        configs_record = self._configs[index]
        running_record = self._running[configs_record.source_design_id]
        geometry = running_record.geometry_38d
        geometry_sha256 = canonical_geometry_sha256(geometry)
        provenance = {
            "geometry_source": "running_info.jsonl:shape",
            "id_resolution": ID_RESOLUTION,
            "configs_dataset_index": index,
            "configs_source_design_id_column": 0,
            "source_design_id": configs_record.source_design_id,
            "running_info_id_field": "running_id",
            "running_info_shape_field": "shape",
            "running_info_line_number": running_record.line_number,
            "rootadj_index": ROOTADJ_INDEX,
            "configs_rootadj": configs_record.geometry_38d[ROOTADJ_INDEX],
            "running_info_rootadj": geometry[ROOTADJ_INDEX],
            "non_rootadj_exact_match": True,
            "precision": "IEEE-754 binary64 values from running_info.shape; no rounding",
            "source_files": {
                "configs_dat": {
                    "path": str(self.configs_path),
                    "sha256": self._configs_sha256,
                },
                "running_info_jsonl": {
                    "path": str(self.running_info_path),
                    "sha256": self._running_info_sha256,
                },
            },
        }
        return DatasetGeometryResolution(
            dataset_index=index,
            source_design_id=configs_record.source_design_id,
            running_id=running_record.running_id,
            geometry_38d=geometry,
            geometry_sha256=geometry_sha256,
            provenance=provenance,
        )

    def resolve_all(self) -> list[DatasetGeometryResolution]:
        return [self.resolve(index) for index in range(len(self))]


def resolve_dataset_geometry(
    dataset_index: int,
    *,
    configs_path: str | Path,
    running_info_path: str | Path,
) -> DatasetGeometryResolution:
    """Convenience wrapper for resolving one dataset index."""

    return DatasetGeometryResolver.from_files(configs_path, running_info_path).resolve(dataset_index)


def canonical_geometry_sha256(geometry_38d: Sequence[float]) -> str:
    """Backward-compatible function name for the final binary64 identity hash."""

    return geometry_sha256(_validated_shape(geometry_38d, context="geometry"))


def _load_configs(path: Path) -> tuple[_ConfigsRecord, ...]:
    if not path.is_file():
        raise FileNotFoundError(f"configs.dat not found: {path}")
    try:
        table = np.loadtxt(path, skiprows=1, dtype=np.float64)
    except (OSError, ValueError) as exc:
        raise ValueError(f"could not parse configs.dat {path}: {exc}") from exc
    if table.ndim == 1:
        table = table.reshape(1, -1)
    if table.ndim != 2 or table.shape[1] < 1 + DESIGN_DIM:
        raise ValueError(
            f"configs.dat must contain source ID plus at least {DESIGN_DIM} geometry columns; "
            f"got shape {table.shape}"
        )

    records: list[_ConfigsRecord] = []
    seen: dict[int, int] = {}
    for dataset_index, row in enumerate(table):
        source_design_id = _require_integer_id(
            row[0], context=f"configs.dat row {dataset_index} column 0 source_design_id"
        )
        if source_design_id in seen:
            raise ValueError(
                "duplicate configs.dat source_design_id "
                f"{source_design_id} at rows {seen[source_design_id]} and {dataset_index}"
            )
        seen[source_design_id] = dataset_index
        geometry = _validated_shape(
            row[1 : 1 + DESIGN_DIM], context=f"configs.dat row {dataset_index} geometry"
        )
        records.append(_ConfigsRecord(dataset_index, source_design_id, geometry))
    return tuple(records)


def _load_running_info(path: Path) -> dict[int, _RunningRecord]:
    if not path.is_file():
        raise FileNotFoundError(f"running_info.jsonl not found: {path}")
    records: dict[int, _RunningRecord] = {}
    with path.open() as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(
                    f"invalid JSON in running_info.jsonl line {line_number}: {exc}"
                ) from exc
            if not isinstance(row, dict):
                raise ValueError(f"running_info.jsonl line {line_number} must be an object")
            if "running_id" not in row:
                raise ValueError(f"running_info.jsonl line {line_number} is missing running_id")
            if "shape" not in row:
                raise ValueError(f"running_info.jsonl line {line_number} is missing shape")
            running_id = _require_integer_id(
                row["running_id"], context=f"running_info.jsonl line {line_number} running_id"
            )
            if running_id in records:
                raise ValueError(
                    "duplicate running_info running_id "
                    f"{running_id} at lines {records[running_id].line_number} and {line_number}"
                )
            geometry = _validated_shape(
                row["shape"], context=f"running_info.jsonl line {line_number} shape"
            )
            records[running_id] = _RunningRecord(running_id, line_number, geometry)
    if not records:
        raise ValueError("running_info.jsonl contains no records")
    return records


def _validate_crosswalk(
    configs: Iterable[_ConfigsRecord],
    running: dict[int, _RunningRecord],
) -> None:
    for configs_record in configs:
        source_design_id = configs_record.source_design_id
        running_record = running.get(source_design_id)
        if running_record is None:
            raise ValueError(
                f"configs.dat dataset_index {configs_record.dataset_index} references "
                f"source_design_id {source_design_id}, but running_info has no matching running_id"
            )
        mismatched = [
            index
            for index, (configs_value, running_value) in enumerate(
                zip(configs_record.geometry_38d, running_record.geometry_38d)
            )
            if index != ROOTADJ_INDEX and configs_value != running_value
        ]
        if mismatched:
            details = ", ".join(
                f"{index}:configs={configs_record.geometry_38d[index]!r},"
                f"running={running_record.geometry_38d[index]!r}"
                for index in mismatched[:5]
            )
            raise ValueError(
                f"non-rootadj geometry mismatch for dataset_index "
                f"{configs_record.dataset_index}, source_design_id {source_design_id} "
                f"at indices {mismatched}: {details}"
            )


def _validated_shape(values: Sequence[float], *, context: str) -> tuple[float, ...]:
    if isinstance(values, (str, bytes)):
        raise ValueError(f"{context} must be a {DESIGN_DIM}-value numeric sequence")
    try:
        raw = list(values)
    except TypeError as exc:
        raise ValueError(f"{context} must be a {DESIGN_DIM}-value numeric sequence") from exc
    if len(raw) != DESIGN_DIM:
        raise ValueError(f"{context} must contain exactly {DESIGN_DIM} values; got {len(raw)}")
    geometry: list[float] = []
    for index, value in enumerate(raw):
        if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
            raise ValueError(f"{context}[{index}] must be numeric, got {value!r}")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{context}[{index}] must be finite, got {value!r}")
        geometry.append(number)
    return tuple(geometry)


def _require_integer_id(value: Any, *, context: str) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Real):
        raise ValueError(f"{context} must be an integer, got {value!r}")
    number = float(value)
    if not math.isfinite(number) or not number.is_integer():
        raise ValueError(f"{context} must be a finite integer, got {value!r}")
    if abs(number) > MAX_EXACT_FLOAT_INTEGER:
        raise ValueError(f"{context} exceeds the exactly representable integer range: {value!r}")
    return int(number)


def _require_dataset_index(value: Any, size: int) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Integral):
        raise TypeError(f"dataset_index must be an integer, got {value!r}")
    index = int(value)
    if index < 0 or index >= size:
        raise IndexError(f"dataset_index {index} is outside [0, {size})")
    return index


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
