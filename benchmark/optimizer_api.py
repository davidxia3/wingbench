"""The interface a competitor implements.

Subclass ``Optimizer`` and implement three methods.

``propose(ctx)``
    Return the next wing to send to CFD. A wing is a list of 38 physical
    design values, in the same units and order as ``data/wings.csv``. Every
    value must lie inside the benchmark box. The box is the mean of the
    SuperWing population plus or minus 3.5 standard deviations, coordinate by
    coordinate. ``ctx.box`` converts between physical values and box
    coordinates. Values outside the box are rejected and the episode stops
    with an error.

``observe(result)``
    Receive the CFD result of the wing you proposed. You see whether the wing
    delivered a score, its L/D when it did, the outcome type, and the grader's
    reason codes. You never see the grades of your recommendations.

``recommend()``
    Return the wing you recommend right now. It is called at the checkpoints
    0, 1, 2, 4, 8, 16 and 32 online queries. The recommended wing is graded in
    private. If you already saw its online result, that result is reused. If
    not, a fresh CFD grade is made that you do not see.

``start(ctx)`` is optional. It runs once before checkpoint 0 and is the place
for any search you want to do before the first recommendation.

``ctx`` gives you the task, the box, your seed, the online history so far,
the remaining budgets, and ``cheap_query``. ``cheap_query`` scores wings with
the released surrogate. It costs one unit per wing out of 4,096 per episode.
Repeated wings cost again.

Your optimizer runs in its own process. It only receives what ``observe`` and
``ctx`` expose. It may keep files in its working directory.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Mapping, Protocol, Sequence

DESIGN_DIM = 38


@dataclass(frozen=True)
class TaskSpec:
    """One of the three tasks. ``name`` is ``low``, ``nominal`` or ``high``."""

    name: str
    task_id: str
    target_cl: float
    aoa_seed_deg: float
    mach: tuple[float, float, float]
    online_budget: int = 32
    cheap_budget: int = 4096
    cheap_batch_size: int = 16
    checkpoints: tuple[int, ...] = (0, 1, 2, 4, 8, 16, 32)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "task_id": self.task_id,
            "target_cl": self.target_cl,
            "aoa_seed_deg": self.aoa_seed_deg,
            "mach": list(self.mach),
            "online_budget": self.online_budget,
            "cheap_budget": self.cheap_budget,
            "cheap_batch_size": self.cheap_batch_size,
            "checkpoints": list(self.checkpoints),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "TaskSpec":
        return cls(
            name=str(payload["name"]),
            task_id=str(payload["task_id"]),
            target_cl=float(payload["target_cl"]),
            aoa_seed_deg=float(payload["aoa_seed_deg"]),
            mach=tuple(float(value) for value in payload["mach"]),  # type: ignore[arg-type]
            online_budget=int(payload["online_budget"]),
            cheap_budget=int(payload["cheap_budget"]),
            cheap_batch_size=int(payload["cheap_batch_size"]),
            checkpoints=tuple(int(value) for value in payload["checkpoints"]),
        )


@dataclass(frozen=True)
class Box:
    """The design box. Physical value = mean + sd * z, with z in [z_lower, z_upper]."""

    mean: tuple[float, ...]
    sd: tuple[float, ...]
    z_lower: float = -3.5
    z_upper: float = 3.5

    @property
    def lower(self) -> tuple[float, ...]:
        return tuple(mu + sigma * self.z_lower for mu, sigma in zip(self.mean, self.sd))

    @property
    def upper(self) -> tuple[float, ...]:
        return tuple(mu + sigma * self.z_upper for mu, sigma in zip(self.mean, self.sd))

    def to_physical(self, z: Sequence[float]) -> list[float]:
        """Box coordinates to physical values."""
        values = [float(value) for value in z]
        if len(values) != DESIGN_DIM:
            raise ValueError(f"expected {DESIGN_DIM} coordinates, got {len(values)}")
        return [mu + sigma * value for mu, sigma, value in zip(self.mean, self.sd, values)]

    def to_standardized(self, x: Sequence[float]) -> list[float]:
        """Physical values to box coordinates."""
        values = [float(value) for value in x]
        if len(values) != DESIGN_DIM:
            raise ValueError(f"expected {DESIGN_DIM} values, got {len(values)}")
        return [(value - mu) / sigma for mu, sigma, value in zip(self.mean, self.sd, values)]

    def contains(self, x: Sequence[float]) -> bool:
        """True when every physical value is finite and inside the box."""
        try:
            z = self.to_standardized(x)
        except (TypeError, ValueError):
            return False
        return all(math.isfinite(value) and self.z_lower <= value <= self.z_upper for value in z)

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean": list(self.mean),
            "sd": list(self.sd),
            "z_lower": self.z_lower,
            "z_upper": self.z_upper,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "Box":
        return cls(
            mean=tuple(float(value) for value in payload["mean"]),
            sd=tuple(float(value) for value in payload["sd"]),
            z_lower=float(payload["z_lower"]),
            z_upper=float(payload["z_upper"]),
        )


@dataclass(frozen=True)
class ConditionPrediction:
    """The surrogate's prediction at one Mach number."""

    mach: float
    cl: float | None
    cd: float | None
    ld: float | None
    aoa_deg: float | None
    converged: bool


@dataclass(frozen=True)
class CheapResult:
    """One surrogate answer.

    ``score`` is the predicted geometric mean L/D over the three Mach numbers.
    It is ``None`` unless all three conditions trimmed to the target lift and
    every L/D lies in (0, 40]. ``status`` is ``complete``, ``trim_failed`` or
    ``envelope_violation``.
    """

    geometry: tuple[float, ...]
    wing: str
    status: str
    score: float | None
    conditions: tuple[ConditionPrediction, ...]
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "geometry": list(self.geometry),
            "wing": self.wing,
            "status": self.status,
            "score": self.score,
            "conditions": [
                {
                    "mach": row.mach,
                    "cl": row.cl,
                    "cd": row.cd,
                    "ld": row.ld,
                    "aoa_deg": row.aoa_deg,
                    "converged": row.converged,
                }
                for row in self.conditions
            ],
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "CheapResult":
        return cls(
            geometry=tuple(float(value) for value in payload["geometry"]),
            wing=str(payload["wing"]),
            status=str(payload["status"]),
            score=None if payload["score"] is None else float(payload["score"]),
            conditions=tuple(
                ConditionPrediction(
                    mach=float(row["mach"]),
                    cl=None if row["cl"] is None else float(row["cl"]),
                    cd=None if row["cd"] is None else float(row["cd"]),
                    ld=None if row["ld"] is None else float(row["ld"]),
                    aoa_deg=None if row["aoa_deg"] is None else float(row["aoa_deg"]),
                    converged=bool(row["converged"]),
                )
                for row in payload["conditions"]
            ),
            reason_codes=tuple(str(value) for value in payload.get("reason_codes", [])),
        )


@dataclass(frozen=True)
class OnlineResult:
    """The CFD result of one online query, as the optimizer sees it.

    ``outcome`` is ``pass``, ``partial``, ``undetermined`` or
    ``conclusive_failure``. ``ld`` is set only when ``delivered`` is true.
    """

    query: int
    geometry: tuple[float, ...]
    wing: str
    delivered: bool
    ld: float | None
    outcome: str
    reason_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "geometry": list(self.geometry),
            "wing": self.wing,
            "delivered": self.delivered,
            "ld": self.ld,
            "outcome": self.outcome,
            "reason_codes": list(self.reason_codes),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "OnlineResult":
        return cls(
            query=int(payload["query"]),
            geometry=tuple(float(value) for value in payload["geometry"]),
            wing=str(payload["wing"]),
            delivered=bool(payload["delivered"]),
            ld=None if payload["ld"] is None else float(payload["ld"]),
            outcome=str(payload["outcome"]),
            reason_codes=tuple(str(value) for value in payload.get("reason_codes", [])),
        )


class EpisodeContext(Protocol):
    """What the runner exposes to the optimizer."""

    task: TaskSpec
    box: Box
    seed: int

    @property
    def history(self) -> tuple[OnlineResult, ...]:
        """Every online result so far, in query order."""

    @property
    def online_queries_used(self) -> int: ...

    @property
    def remaining_online_queries(self) -> int: ...

    @property
    def cheap_queries_used(self) -> int: ...

    @property
    def remaining_cheap_queries(self) -> int: ...

    @property
    def surrogate_available(self) -> bool:
        """False when the runner was started without the surrogate weights."""

    def cheap_query(self, geometries: Sequence[Sequence[float]]) -> list[CheapResult]:
        """Score wings with the surrogate. Each wing costs one of 4,096 units."""


class Optimizer:
    """Base class. Subclass it and implement ``propose`` and ``recommend``."""

    name: str | None = None

    def __init__(self, task: TaskSpec, seed: int, box: Box, **options: Any) -> None:
        self.task = task
        self.seed = int(seed)
        self.box = box
        self.options = dict(options)
        self.ctx: EpisodeContext | None = None

    def start(self, ctx: EpisodeContext) -> None:
        """Runs once before checkpoint 0. The default only stores ``ctx``."""
        self.ctx = ctx

    def propose(self, ctx: EpisodeContext) -> Sequence[float]:
        """Return the next wing for CFD, as 38 physical values inside the box."""
        raise NotImplementedError

    def observe(self, result: OnlineResult) -> None:
        """Receive the CFD result of the wing you proposed."""

    def recommend(self) -> Sequence[float]:
        """Return the wing you recommend now, as 38 physical values inside the box."""
        raise NotImplementedError


@dataclass
class EpisodeState:
    """Plain record of the budgets the runner sends with every request."""

    cheap_queries_used: int = 0
    online_queries_used: int = 0
    surrogate_available: bool = False
    history: list[OnlineResult] = field(default_factory=list)
