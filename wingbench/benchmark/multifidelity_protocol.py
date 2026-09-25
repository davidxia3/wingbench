"""Resolve and enforce the prospective WingBench multi-fidelity protocol."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


DEFAULT_AMENDMENT = Path("configs/benchmark/multifidelity_benchmark_protocol_v1.json")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_pointer_tokens(pointer: str) -> list[str]:
    if not pointer.startswith("/"):
        raise ValueError(f"JSON Pointer must start with '/': {pointer!r}")
    return [token.replace("~1", "/").replace("~0", "~") for token in pointer[1:].split("/")]


def _replace_pointer(document: dict[str, Any], pointer: str, value: Any) -> None:
    tokens = _json_pointer_tokens(pointer)
    if not tokens:
        raise ValueError("root replacement is not supported")
    parent: Any = document
    for token in tokens[:-1]:
        if not isinstance(parent, dict) or token not in parent:
            raise ValueError(f"JSON Pointer parent does not exist: {pointer!r}")
        parent = parent[token]
    if not isinstance(parent, dict):
        raise ValueError(f"JSON Pointer parent is not an object: {pointer!r}")
    parent[tokens[-1]] = deepcopy(value)


def resolve_protocol(
    repo_root: str | Path,
    amendment_path: str | Path = DEFAULT_AMENDMENT,
) -> dict[str, Any]:
    """Materialize a pinned amendment without mutating its base protocol."""

    root = Path(repo_root).resolve()
    amendment_file = Path(amendment_path)
    if not amendment_file.is_absolute():
        amendment_file = root / amendment_file
    amendment = json.loads(amendment_file.read_text(encoding="utf-8"))
    binding = amendment["base_protocol"]
    base_file = root / binding["path"]
    observed = file_sha256(base_file)
    if observed != binding["file_sha256"]:
        raise ValueError(
            f"base protocol hash mismatch: expected {binding['file_sha256']}, observed {observed}"
        )
    resolved = json.loads(base_file.read_text(encoding="utf-8"))
    for pointer, value in amendment["overrides"].items():
        _replace_pointer(resolved, pointer, value)
    if resolved["protocol_id"] != amendment["protocol_id"]:
        raise ValueError("resolved protocol ID does not match amendment")
    if resolved["protocol_revision"] != amendment["protocol_revision"]:
        raise ValueError("resolved protocol revision does not match amendment")
    resolved["amendment_binding"] = {
        "path": str(amendment_file.relative_to(root)),
        "file_sha256": file_sha256(amendment_file),
        "base_path": binding["path"],
        "base_file_sha256": binding["file_sha256"],
    }
    return resolved


@dataclass(frozen=True)
class PhysicalBounds:
    lower: tuple[float, ...]
    upper: tuple[float, ...]
    mean: tuple[float, ...]
    sd: tuple[float, ...]
    z_lower: float
    z_upper: float
    normalization_id: str


def physical_bounds(repo_root: str | Path, protocol: Mapping[str, Any]) -> PhysicalBounds:
    root = Path(repo_root).resolve()
    spec = protocol["design_action"]["coordinate_bounds"]
    binding = spec["normalization_binding"]
    path = root / binding["path"]
    observed = file_sha256(path)
    if observed != binding["file_sha256"]:
        raise ValueError(
            f"normalization hash mismatch: expected {binding['file_sha256']}, observed {observed}"
        )
    normalization = json.loads(path.read_text(encoding="utf-8"))
    if normalization["normalization_id"] != binding["normalization_id"]:
        raise ValueError("normalization ID mismatch")
    mean = tuple(float(value) for value in normalization["mean_38d"])
    sd = tuple(float(value) for value in normalization["sd_38d"])
    if len(mean) != 38 or len(sd) != 38 or not all(value > 0.0 for value in sd):
        raise ValueError("normalization must contain 38 positive scales")
    z_lower = float(spec["standardized_lower_38d"])
    z_upper = float(spec["standardized_upper_38d"])
    if not z_lower < z_upper:
        raise ValueError("invalid standardized bounds")
    lower = tuple(mu + sigma * z_lower for mu, sigma in zip(mean, sd))
    upper = tuple(mu + sigma * z_upper for mu, sigma in zip(mean, sd))
    return PhysicalBounds(
        lower=lower,
        upper=upper,
        mean=mean,
        sd=sd,
        z_lower=z_lower,
        z_upper=z_upper,
        normalization_id=normalization["normalization_id"],
    )


def geometry_in_bounds(values: Sequence[float], bounds: PhysicalBounds) -> bool:
    try:
        geometry = tuple(float(value) for value in values)
    except (TypeError, ValueError):
        return False
    return (
        len(geometry) == 38
        and all(math.isfinite(value) for value in geometry)
        and all(lo <= value <= hi for value, lo, hi in zip(geometry, bounds.lower, bounds.upper))
    )


@dataclass(frozen=True)
class ObjectiveEnvelopeResult:
    eligible: bool
    values: tuple[float, ...]
    violating_indices: tuple[int, ...]
    reason_codes: tuple[str, ...]


def evaluate_ld_envelope(
    values: Sequence[float],
    protocol: Mapping[str, Any],
) -> ObjectiveEnvelopeResult:
    envelope = protocol["objective"]["per_condition_ld_envelope"]
    lower = float(envelope["lower_exclusive"])
    upper = float(envelope["upper_inclusive"])
    parsed: list[float] = []
    violating: list[int] = []
    reasons: list[str] = []
    for index, value in enumerate(values):
        try:
            number = float(value)
        except (TypeError, ValueError):
            number = math.nan
        parsed.append(number)
        if not math.isfinite(number) or number <= lower:
            violating.append(index)
            reasons.append("per_condition_ld_nonpositive_or_nonfinite")
        elif number > upper:
            violating.append(index)
            reasons.append("per_condition_ld_above_40")
    eligible = len(parsed) == 3 and not violating
    if len(parsed) != 3:
        reasons.append("wrong_condition_count")
    return ObjectiveEnvelopeResult(
        eligible=eligible,
        values=tuple(parsed),
        violating_indices=tuple(violating),
        reason_codes=tuple(dict.fromkeys(reasons)),
    )
