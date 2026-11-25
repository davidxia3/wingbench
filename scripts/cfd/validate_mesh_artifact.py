#!/usr/bin/env python3
"""Read-only validation of the mesh artifacts consumed by ADflow.

The file existence checks that preceded this helper could not distinguish a
real CGNS volume grid from an empty, truncated, or otherwise unreadable file.
This module opens the grid with the same ``cgnsutilities.readGrid`` API used by
the mesh generator and emits deterministic JSON evidence.  It never runs a
mesher or a flow solve.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import numbers
import os
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np


REPORT_SCHEMA_VERSION = "wingbench-cfd-mesh-artifact-validation-v1"
GRID_READER_ID = "cgnsutilities.cgnsutilities.readGrid"
GEOMETRY_FIELDS = ("surface_area", "ref_area", "half_span")

GridReader = Callable[..., Any]


class _DuplicateKeyError(ValueError):
    pass


class _GridValidationError(ValueError):
    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def canonical_json_bytes(payload: Any) -> bytes:
    """Serialize one report representation, rejecting non-finite numbers."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _sha256_stream(stream: Any) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


def _file_record(path: Path) -> tuple[dict[str, Any], tuple[int, int, int, int] | None]:
    """Hash a regular file and return the identity of the opened file."""
    record: dict[str, Any] = {
        "path": str(path),
        "present": False,
        "regular_file": False,
        "size_bytes": None,
        "sha256": None,
    }
    try:
        path_stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return record, None
    except OSError:
        return record, None

    record["present"] = True
    record["regular_file"] = stat.S_ISREG(path_stat.st_mode)
    if not record["regular_file"]:
        return record, None

    try:
        with path.open("rb") as stream:
            opened_stat = os.fstat(stream.fileno())
            record["size_bytes"] = opened_stat.st_size
            record["sha256"] = _sha256_stream(stream)
    except OSError:
        return record, None

    identity = (
        opened_stat.st_dev,
        opened_stat.st_ino,
        opened_stat.st_size,
        opened_stat.st_mtime_ns,
    )
    return record, identity


def _read_hashed_bytes(path: Path) -> tuple[dict[str, Any], bytes | None]:
    """Read and hash the exact byte sequence that will be parsed."""
    record: dict[str, Any] = {
        "path": str(path),
        "present": False,
        "regular_file": False,
        "size_bytes": None,
        "sha256": None,
    }
    try:
        path_stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return record, None
    except OSError:
        return record, None
    record["present"] = True
    record["regular_file"] = stat.S_ISREG(path_stat.st_mode)
    if not record["regular_file"]:
        return record, None

    try:
        with path.open("rb") as stream:
            opened_stat = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened_stat.st_mode):
                record["regular_file"] = False
                return record, None
            data = stream.read()
    except OSError:
        return record, None

    record["size_bytes"] = len(data)
    record["sha256"] = hashlib.sha256(data).hexdigest()
    return record, data


def _path_identity(path: Path) -> tuple[int, int, int, int] | None:
    try:
        observed = path.stat()
    except OSError:
        return None
    return (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns)


def _integer(value: Any) -> int:
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, numbers.Integral):
        raise _GridValidationError("cgns_grid_invalid_structure")
    return int(value)


def _validate_grid_object(grid: Any) -> dict[str, Any]:
    if grid is None:
        raise _GridValidationError("cgns_grid_invalid_structure")

    cell_dimension = _integer(getattr(grid, "cellDim", None))
    if cell_dimension != 3:
        raise _GridValidationError("cgns_grid_not_three_dimensional")

    blocks = getattr(grid, "blocks", None)
    if not isinstance(blocks, (list, tuple)):
        raise _GridValidationError("cgns_grid_invalid_structure")
    if not blocks:
        raise _GridValidationError("cgns_grid_empty")

    zone_dimensions: list[list[int]] = []
    block_cell_total = 0
    block_node_total = 0
    for block in blocks:
        try:
            raw_dimensions = list(block.dims)
        except (AttributeError, TypeError):
            raise _GridValidationError("cgns_grid_invalid_structure") from None
        if len(raw_dimensions) != 3:
            raise _GridValidationError("cgns_grid_invalid_structure")
        dimensions = [_integer(value) for value in raw_dimensions]
        if any(value <= 1 for value in dimensions):
            raise _GridValidationError("cgns_grid_empty")

        expected_nodes = math.prod(dimensions)
        expected_cells = math.prod(value - 1 for value in dimensions)
        get_cells = getattr(block, "getNumCells", None)
        get_nodes = getattr(block, "getNumNodes", None)
        if not callable(get_cells) or not callable(get_nodes):
            raise _GridValidationError("cgns_grid_invalid_structure")
        try:
            reported_cells = _integer(get_cells())
            reported_nodes = _integer(get_nodes())
        except _GridValidationError:
            raise
        except Exception:
            raise _GridValidationError("cgns_grid_invalid_structure") from None
        if reported_cells != expected_cells or reported_nodes != expected_nodes:
            raise _GridValidationError("cgns_grid_count_mismatch")

        coordinates = getattr(block, "coords", None)
        try:
            coordinate_array = np.asarray(coordinates)
        except Exception:
            raise _GridValidationError("cgns_grid_invalid_structure") from None
        if coordinate_array.shape != tuple(dimensions) + (3,):
            raise _GridValidationError("cgns_grid_coordinate_shape_mismatch")
        if not np.issubdtype(coordinate_array.dtype, np.number):
            raise _GridValidationError("cgns_grid_invalid_structure")
        try:
            coordinates_finite = bool(np.isfinite(coordinate_array).all())
        except TypeError:
            raise _GridValidationError("cgns_grid_invalid_structure") from None
        if not coordinates_finite:
            raise _GridValidationError("cgns_grid_nonfinite_coordinates")

        zone_dimensions.append(dimensions)
        block_cell_total += reported_cells
        block_node_total += reported_nodes

    get_totals = getattr(grid, "getTotalCellsNodes", None)
    if not callable(get_totals):
        raise _GridValidationError("cgns_grid_invalid_structure")
    try:
        totals = get_totals()
        if not isinstance(totals, (list, tuple)) or len(totals) != 2:
            raise _GridValidationError("cgns_grid_invalid_structure")
        total_cells = _integer(totals[0])
        total_nodes = _integer(totals[1])
    except _GridValidationError:
        raise
    except Exception:
        raise _GridValidationError("cgns_grid_invalid_structure") from None
    if total_cells <= 0 or total_nodes <= 0:
        raise _GridValidationError("cgns_grid_empty")
    if total_cells != block_cell_total or total_nodes != block_node_total:
        raise _GridValidationError("cgns_grid_count_mismatch")

    return {
        "cell_dimension": cell_dimension,
        "zone_count": len(blocks),
        "zone_dimensions": zone_dimensions,
        "total_cells": total_cells,
        "total_nodes": total_nodes,
        "coordinates_finite": True,
    }


def _load_grid_reader() -> GridReader:
    # Import lazily: non-CFD development environments intentionally do not
    # carry the compiled CGNS library, while the grading image must carry it.
    from cgnsutilities.cgnsutilities import readGrid

    return readGrid


def _reject_duplicate_keys(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(key)
        result[key] = value
    return result


def _finite_json_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise ValueError("non-finite JSON number")
    return parsed


def _parse_json_object(data: bytes) -> Mapping[str, Any]:
    text = data.decode("utf-8")
    payload = json.loads(
        text,
        object_pairs_hook=_reject_duplicate_keys,
        parse_float=_finite_json_float,
        parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
    )
    if not isinstance(payload, dict):
        raise TypeError("JSON root is not an object")
    return payload


def _positive_geometry(payload: Mapping[str, Any]) -> tuple[dict[str, float | None], list[str]]:
    geometry: dict[str, float | None] = {}
    invalid_fields: list[str] = []
    for field in GEOMETRY_FIELDS:
        value = payload.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            geometry[field] = None
            invalid_fields.append(field)
            continue
        try:
            number = float(value)
        except OverflowError:
            geometry[field] = None
            invalid_fields.append(field)
            continue
        if not math.isfinite(number) or number <= 0.0:
            geometry[field] = None
            invalid_fields.append(field)
            continue
        geometry[field] = number
    return geometry, invalid_fields


def validate_mesh_artifacts(
    volume_grid_path: str | os.PathLike[str],
    post_mesh_input_path: str | os.PathLike[str],
    *,
    reader: GridReader | None = None,
) -> dict[str, Any]:
    """Validate a completed mesh without changing either input artifact.

    Artifact problems are represented in the returned report rather than
    raised.  This is intentional: the grader must classify an unreadable mesh
    as inconclusive infrastructure/output evidence, never as aerodynamic or
    geometric failure.
    """
    volume_path = Path(volume_grid_path).resolve()
    input_path = Path(post_mesh_input_path).resolve()
    reasons: list[str] = []

    grid_record, grid_identity = _file_record(volume_path)
    grid_summary: dict[str, Any] = {
        "artifact": grid_record,
        "reader": GRID_READER_ID,
        "validation_state": "not_read",
        "cell_dimension": None,
        "zone_count": None,
        "zone_dimensions": None,
        "total_cells": None,
        "total_nodes": None,
        "coordinates_finite": None,
    }
    if not grid_record["present"]:
        reasons.append("volume_grid_missing_or_unreadable")
    elif not grid_record["regular_file"]:
        reasons.append("volume_grid_not_regular_file")
    elif grid_record["size_bytes"] == 0:
        reasons.append("volume_grid_empty_file")
    elif grid_record["sha256"] is None:
        reasons.append("volume_grid_unreadable")
    else:
        selected_reader = reader
        if selected_reader is None:
            try:
                selected_reader = _load_grid_reader()
            except Exception:
                grid_summary["validation_state"] = "reader_unavailable"
                reasons.append("cgns_reader_unavailable")
        if selected_reader is not None:
            try:
                grid = selected_reader(fileName=str(volume_path))
            except Exception:
                grid_summary["validation_state"] = "read_failed"
                reasons.append("cgns_read_failed")
            else:
                if _path_identity(volume_path) != grid_identity:
                    grid_summary["validation_state"] = "artifact_changed"
                    reasons.append("volume_grid_changed_during_validation")
                else:
                    try:
                        validated = _validate_grid_object(grid)
                    except _GridValidationError as exc:
                        grid_summary["validation_state"] = "invalid"
                        reasons.append(exc.reason_code)
                    else:
                        grid_summary.update(validated)
                        grid_summary["validation_state"] = "valid"

    input_record, input_bytes = _read_hashed_bytes(input_path)
    input_summary: dict[str, Any] = {
        "artifact": input_record,
        "validation_state": "not_parsed",
        "geometry": {field: None for field in GEOMETRY_FIELDS},
        "invalid_fields": list(GEOMETRY_FIELDS),
    }
    if not input_record["present"]:
        reasons.append("post_mesh_input_missing_or_unreadable")
    elif not input_record["regular_file"]:
        reasons.append("post_mesh_input_not_regular_file")
    elif input_record["size_bytes"] == 0:
        reasons.append("post_mesh_input_empty_file")
    elif input_bytes is None:
        reasons.append("post_mesh_input_unreadable")
    else:
        try:
            input_payload = _parse_json_object(input_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateKeyError, ValueError):
            input_summary["validation_state"] = "invalid_json"
            reasons.append("post_mesh_input_invalid_json")
        except TypeError:
            input_summary["validation_state"] = "not_object"
            reasons.append("post_mesh_input_not_object")
        else:
            geometry, invalid_fields = _positive_geometry(input_payload)
            input_summary["geometry"] = geometry
            input_summary["invalid_fields"] = invalid_fields
            if invalid_fields:
                input_summary["validation_state"] = "invalid_geometry"
                reasons.append("post_mesh_input_invalid_geometry")
            else:
                input_summary["validation_state"] = "valid"

    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "valid": not reasons,
        "reason_codes": reasons,
        "volume_grid": grid_summary,
        "post_mesh_input": input_summary,
    }
    # Assert the public contract is canonical-JSON safe before returning it.
    canonical_json_bytes(report)
    return report


def atomic_write_report(path: str | os.PathLike[str], report: Mapping[str, Any]) -> None:
    """Publish a canonical report atomically without overwriting evidence."""
    destination = Path(path).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite mesh validation report: {destination}")
    data = canonical_json_bytes(report) + b"\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # link() is an atomic no-replace publish on the same filesystem.
        os.link(temporary, destination)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--volume-grid", required=True, type=Path)
    parser.add_argument("--post-mesh-input", required=True, type=Path)
    parser.add_argument("--output", type=Path, help="atomic canonical JSON output; stdout if omitted")
    return parser


def main(argv: Sequence[str] | None = None, *, reader: GridReader | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    volume_path = args.volume_grid.resolve()
    input_path = args.post_mesh_input.resolve()
    if args.output is not None and args.output.resolve() in {volume_path, input_path}:
        parser.error("--output must not name either validated input artifact")

    report = validate_mesh_artifacts(volume_path, input_path, reader=reader)
    if args.output is None:
        sys.stdout.write(canonical_json_bytes(report).decode("utf-8") + "\n")
    else:
        try:
            atomic_write_report(args.output, report)
        except OSError as exc:
            print(f"cannot publish mesh validation report: {exc}", file=sys.stderr)
            return 2
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
