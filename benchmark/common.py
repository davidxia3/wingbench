"""Shared constants and loaders for the competitor tools.

Everything here is read from files that the release already contains. Nothing
is invented. The task settings come from the task documents the agents were
given, the design box comes from the frozen normalization file, and the episode
rules come from the frozen episode protocol.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.cfd.geometry_hash import geometry_sha256, validated_geometry  # noqa: E402
from wingbench.benchmark.episode import (  # noqa: E402
    canonical_json_bytes,
    canonical_sha256,
    load_protocol as load_episode_protocol,
)
from wingbench.benchmark.multifidelity_protocol import (  # noqa: E402
    physical_bounds,
    resolve_protocol,
)

from benchmark.optimizer_api import DESIGN_DIM, Box, TaskSpec  # noqa: E402

DATA = ROOT / "data"
TASK_DOCUMENTS = ROOT / "agents" / "tasks" / "task_documents.json"
PROFILE = "pilot32"
STUDY = "submission"
CHECKPOINTS = (0, 1, 2, 4, 8, 16, 32)
ONLINE_BUDGET = 32
CHEAP_BUDGET = 4096
CHEAP_BATCH_SIZE = 16
TASK_NAMES = ("low", "nominal", "high")
TASK_IDS = {
    "low": "superwing-load-low-cl015",
    "nominal": "superwing-load-nominal-cl041",
    "high": "superwing-load-high-cl066",
}
TASK_BY_ID = {value: key for key, value in TASK_IDS.items()}
REFERENCE_METHODS = (
    "CMA-ES no feedback",
    "CMA-ES selection only",
    "CMA-ES feedback-guided",
    "DE feedback-guided",
    "SMAC3",
    "Open-ended research",
)
OUTCOMES = ("pass", "partial", "undetermined", "conclusive_failure")

__all__ = [
    "CHEAP_BATCH_SIZE",
    "CHEAP_BUDGET",
    "CHECKPOINTS",
    "DATA",
    "DESIGN_DIM",
    "ONLINE_BUDGET",
    "OUTCOMES",
    "PROFILE",
    "REFERENCE_METHODS",
    "ROOT",
    "STUDY",
    "TASK_BY_ID",
    "TASK_IDS",
    "TASK_NAMES",
    "canonical_json_bytes",
    "canonical_sha256",
    "file_sha256",
    "geometry_sha256",
    "load_box",
    "load_episode_protocol",
    "load_tasks",
    "utc_now",
    "validated_geometry",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_tasks() -> dict[str, TaskSpec]:
    """The three tasks, read from the released task documents.

    The lift targets and angle of attack seeds are checked against the CFD
    grading code, which holds the same numbers for the low and high tasks and
    for the nominal task.
    """
    from scripts.cfd.grading_protocol import load_protocol as load_grading_protocol
    from scripts.cfd.task_protocols import ALLOWED, MACH_GRID

    documents = json.loads(TASK_DOCUMENTS.read_text(encoding="utf-8"))
    base = load_grading_protocol(verify_files=False)
    expected = {
        TASK_IDS["nominal"]: (
            float(base.conditions[0]["cl_target"]),
            float(base.seed_alpha0),
        ),
    }
    for task_id, values in ALLOWED.items():
        expected[task_id] = (float(values["cl_target"]), float(values["alpha0_deg"]))
    tasks: dict[str, TaskSpec] = {}
    for name in TASK_NAMES:
        task_id = TASK_IDS[name]
        document = documents[task_id]
        target_cl = float(document["target_cl"])
        seed = float(document["aoa_seed_deg"])
        mach = tuple(float(value) for value in document["mach_grid"])
        if (target_cl, seed) != expected[task_id]:
            raise ValueError(f"task document for {task_id} disagrees with the CFD grader")
        if list(mach) != list(MACH_GRID):
            raise ValueError(f"task document for {task_id} has an unexpected Mach grid")
        if int(document["official_surrogate_budget"]) != CHEAP_BUDGET:
            raise ValueError(f"task document for {task_id} has an unexpected surrogate budget")
        if int(document["nominal_horizon_online_queries"]) != ONLINE_BUDGET:
            raise ValueError(f"task document for {task_id} has an unexpected CFD budget")
        tasks[name] = TaskSpec(
            name=name,
            task_id=task_id,
            target_cl=target_cl,
            aoa_seed_deg=seed,
            mach=mach,  # type: ignore[arg-type]
            online_budget=ONLINE_BUDGET,
            cheap_budget=CHEAP_BUDGET,
            cheap_batch_size=CHEAP_BATCH_SIZE,
            checkpoints=CHECKPOINTS,
        )
    return tasks


def load_box() -> Box:
    """The design box, from the frozen normalization file and its hash binding."""
    protocol = resolve_protocol(ROOT)
    bounds = physical_bounds(ROOT, protocol)
    return Box(
        mean=tuple(bounds.mean),
        sd=tuple(bounds.sd),
        z_lower=float(bounds.z_lower),
        z_upper=float(bounds.z_upper),
    )


def as_geometry(values: Sequence[float], label: str) -> tuple[float, ...]:
    """Check that ``values`` is a list of 38 finite numbers and return it as floats."""
    try:
        return validated_geometry(values)
    except ValueError as exc:
        raise ValueError(f"{label}: {exc}") from exc


def out_of_box_report(geometry: Sequence[float], box: Box) -> list[str]:
    """Describe every coordinate that lies outside the design box."""
    problems: list[str] = []
    for index, value in enumerate(geometry):
        z = (float(value) - box.mean[index]) / box.sd[index]
        if not (box.z_lower <= z <= box.z_upper) or not math.isfinite(z):
            problems.append(f"x{index:02d}={float(value)!r} is {z:.3f} standard deviations from the mean")
    return problems


def json_ready(value: Any) -> Any:
    """Turn a nested value into plain JSON with no NaN or infinity."""
    if isinstance(value, dict):
        return {str(key): json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_ready(item) for item in value]
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    try:
        import numpy as np

        if isinstance(value, np.generic):
            return json_ready(value.item())
        if isinstance(value, np.ndarray):
            return json_ready(value.tolist())
    except ImportError:  # pragma: no cover
        pass
    return str(value)
