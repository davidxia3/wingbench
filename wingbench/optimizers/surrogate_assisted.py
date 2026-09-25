"""Prospectively frozen method implementations for the V2 Phase 3 pilot.

The methods in this module have no CFD, Slurm, network, sealed-data, or
cross-episode execution path.  They consume a pure cheap batch objective and
typed current-episode online feedback.  Hidden checkpoint grades are
structurally absent from the public method interface.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np

from wingbench.benchmark.multifidelity_protocol import PhysicalBounds
from wingbench.optimizers.simple_multifidelity_control import (
    CheapCandidate,
    PairedObservation,
    choose_next_cfd,
)
from wingbench.protocol_context import (
    build_initial_context,
    canonical_sha256 as initial_context_sha256,
)
from scripts.cfd.geometry_hash import geometry_sha256


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "configs/optimizer/wingbench_v2_phase3_method_panel_v1.json"
)
CONFIG_SCHEMA = "wingbench-v2-phase3-method-panel-v1"
STATE_SCHEMA = "wingbench-v2-phase3-method-state-v1"
EVENT_SCHEMA = "wingbench-v2-phase3-method-event-v1"
REQUIRED_CMA_VERSION = "4.4.4"
DESIGN_DIM = 38
ZERO_SHA256 = "0" * 64
METHOD_IDS = (
    "static-precommitted-cmaes-v2-pilot",
    "conventional-adaptive-cmaes-v2-pilot",
    "fsd-cma-v1-port-v2-pilot",
    "checkpointed-de-feedback-v2-pilot",
)
STATIC_METHOD = METHOD_IDS[0]
CONVENTIONAL_METHOD = METHOD_IDS[1]
FSD_METHOD = METHOD_IDS[2]
DE_METHOD = METHOD_IDS[3]
OUTCOME_KINDS = {
    "pass",
    "conclusive_failure",
    "undetermined",
    "partial",
}


class Phase3MethodError(ValueError):
    """A Phase 3 method transition violates its frozen contract."""


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise Phase3MethodError(message)


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(
        f".{target.name}.tmp-{os.getpid()}-{hashlib.sha256(os.urandom(16)).hexdigest()[:8]}"
    )
    temporary.write_bytes(canonical_json_bytes(dict(payload)) + b"\n")
    os.replace(temporary, target)


def load_method_panel(path: Path = DEFAULT_CONFIG) -> tuple[dict[str, Any], str]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), "method panel must be a JSON object")
    _require(payload.get("schema_version") == CONFIG_SCHEMA, "method panel schema changed")
    _require(
        payload.get("status")
        == "prospectively_frozen_before_phase3_scientific_compute",
        "method panel is not prospectively frozen",
    )
    _require(payload.get("official") is False, "method panel became official")
    _require(payload.get("sealed") is False, "method panel became sealed")
    shared = payload.get("shared_search") or {}
    _require(
        shared.get("dimension") == 38
        and shared.get("population_size") == 16
        and shared.get("generations") == 256
        and shared.get("cheap_queries") == 4096
        and shared.get("cheap_batch_size") == 16
        and shared.get("online_cfd_queries") == 32
        and shared.get("generations_per_online_block") == 8
        and shared.get("active_archive_size") == 64
        and shared.get("checkpoint_roster") == [0, 1, 2, 4, 8, 16, 32],
        "shared Phase 3 search contract changed",
    )
    observed_methods = tuple(row.get("method_id") for row in payload.get("methods", []))
    _require(observed_methods == METHOD_IDS, "Phase 3 method roster or order changed")
    _require(
        payload.get("implementation_dependencies")
        == {"python": "3.10.x", "numpy": "2.0.2", "cma": "4.4.4"},
        "Phase 3 implementation dependencies changed",
    )
    for key in ("pilot_plan_binding", "protocol_binding"):
        binding = payload.get(key) or {}
        bound_path = ROOT / str(binding.get("path", ""))
        _require(bound_path.is_file(), f"{key} path is missing")
        _require(
            file_sha256(bound_path) == binding.get("file_sha256"),
            f"{key} file binding changed",
        )
    return payload, file_sha256(Path(path))


@dataclass(frozen=True)
class OnlineFeedback:
    """The complete optimizer-visible result for one online scientific query."""

    candidate_id: str
    outcome_kind: str
    delivery: bool
    conditional_value: float | None
    reason_codes: tuple[str, ...] = ()
    evaluator_cpu_seconds: float = 0.0

    def validate(self) -> None:
        _require(bool(self.candidate_id), "feedback candidate ID is empty")
        _require(self.outcome_kind in OUTCOME_KINDS, "unsupported online outcome kind")
        _require(
            self.delivery is (self.outcome_kind == "pass"),
            "delivery must be true exactly for pass feedback",
        )
        if self.delivery:
            _require(
                self.conditional_value is not None
                and math.isfinite(float(self.conditional_value))
                and float(self.conditional_value) > 0.0,
                "pass feedback requires a positive finite conditional value",
            )
        else:
            _require(
                self.conditional_value is None,
                "nondelivery feedback cannot carry a conditional value",
            )
        _require(
            math.isfinite(float(self.evaluator_cpu_seconds))
            and float(self.evaluator_cpu_seconds) >= 0.0,
            "evaluator CPU seconds must be finite and nonnegative",
        )

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "candidate_id": self.candidate_id,
            "outcome_kind": self.outcome_kind,
            "delivery": self.delivery,
            "conditional_value": (
                float(self.conditional_value)
                if self.conditional_value is not None
                else None
            ),
            "reason_codes": list(self.reason_codes),
            "evaluator_cpu_seconds": float(self.evaluator_cpu_seconds),
        }


@dataclass(frozen=True)
class _BeliefRow:
    candidate_id: str
    geometry_38d: tuple[float, ...]
    cheap_geomean_ld: float
    valid: bool
    delivery_weight: float
    cfd_geomean_ld: float | None
    outcome_kind: str


@dataclass(frozen=True)
class _DiscrepancyFit:
    coefficients: np.ndarray
    inverse_precision: np.ndarray
    residual_variance: float


def _initial_context() -> tuple[list[_BeliefRow], set[str]]:
    context = build_initial_context()
    _require(
        initial_context_sha256(context)
        == "094ac003418da96aa92696096ac79b2134b4ef13e85ab83cd3853c1768ab8d77",
        "62-record reset context changed",
    )
    rows: list[_BeliefRow] = []
    historical: set[str] = set()
    for index, record in enumerate(context["corpus50_rows"]):
        rows.append(
            _BeliefRow(
                candidate_id=f"initial-corpus50-{index:02d}",
                geometry_38d=tuple(float(value) for value in record["geometry_38d"]),
                cheap_geomean_ld=float(record["predicted_score_geomean_ld"]),
                valid=bool(record["score_eligible"]),
                delivery_weight=1.0,
                cfd_geomean_ld=None,
                outcome_kind="initial_context",
            )
        )
        historical.add(str(record["geometry_sha256"]))
    for index, item in enumerate(context["matched_validation_ungated"]):
        candidate = item["candidate"]
        result = item["result"]
        cfd = result.get("cfd_ld_geomean")
        rows.append(
            _BeliefRow(
                candidate_id=f"initial-matched-{index:02d}",
                geometry_38d=tuple(float(value) for value in candidate["geometry_38d"]),
                cheap_geomean_ld=float(candidate["predicted_score_geomean_ld"]),
                valid=bool(result["score_eligible"]),
                delivery_weight=1.0,
                cfd_geomean_ld=float(cfd) if cfd is not None else None,
                outcome_kind="initial_context",
            )
        )
        historical.add(str(candidate["geometry_sha256"]))
    _require(
        len(rows) == 62 and len(historical) == 62,
        "reset context must contain 62 unique records",
    )
    return rows, historical


def _feature(
    geometry_38d: Sequence[float],
    cheap_geomean_ld: float,
    bounds: PhysicalBounds,
) -> np.ndarray:
    geometry = np.asarray(geometry_38d, dtype=np.float64)
    cheap = float(cheap_geomean_ld)
    _require(
        geometry.shape == (DESIGN_DIM,) and bool(np.all(np.isfinite(geometry))),
        "belief geometry must contain 38 finite values",
    )
    _require(math.isfinite(cheap) and cheap > 0.0, "belief cheap score is invalid")
    mean = np.asarray(bounds.mean, dtype=np.float64)
    sd = np.asarray(bounds.sd, dtype=np.float64)
    z_scaled = ((geometry - mean) / sd) / max(
        abs(float(bounds.z_lower)), abs(float(bounds.z_upper))
    )
    return np.concatenate(([1.0], z_scaled, [math.log(cheap) - math.log(30.0)]))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(values, -40.0, 40.0)))


def _fit_logistic(
    features: np.ndarray,
    labels: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    dimensions = int(features.shape[1])
    coefficients = np.zeros(dimensions, dtype=np.float64)
    prevalence = (float(weights @ labels) + 1.0) / (float(weights.sum()) + 2.0)
    coefficients[0] = math.log(prevalence / (1.0 - prevalence))
    penalty = np.full(dimensions, 4.0, dtype=np.float64)
    penalty[0] = 0.1
    for _ in range(40):
        probabilities = _sigmoid(features @ coefficients)
        curvature = weights * np.maximum(probabilities * (1.0 - probabilities), 1e-6)
        gradient = features.T @ (weights * (probabilities - labels))
        gradient += penalty * coefficients
        hessian = (features.T * curvature) @ features + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        coefficients -= step
        if float(np.linalg.norm(step)) < 1e-10:
            break
    return coefficients


def _fit_discrepancy(
    features: np.ndarray,
    residuals: np.ndarray,
) -> _DiscrepancyFit | None:
    if not len(residuals):
        return None
    dimensions = int(features.shape[1])
    penalty = np.full(dimensions, 10.0, dtype=np.float64)
    penalty[0] = 0.1
    precision = features.T @ features + np.diag(penalty)
    inverse = np.linalg.inv(precision)
    coefficients = inverse @ features.T @ residuals
    errors = residuals - features @ coefficients
    variance = max(float(np.mean(errors * errors)), 0.05**2)
    return _DiscrepancyFit(coefficients, inverse, variance)


def _belief_merits(
    candidates: Sequence[Mapping[str, Any]],
    observations: Sequence[_BeliefRow],
    bounds: PhysicalBounds,
) -> tuple[dict[str, float], dict[str, Any]]:
    _require(bool(observations), "belief requires reset observations")
    features = np.vstack(
        [
            _feature(row.geometry_38d, row.cheap_geomean_ld, bounds)
            for row in observations
        ]
    )
    labels = np.asarray([1.0 if row.valid else 0.0 for row in observations])
    weights = np.asarray([float(row.delivery_weight) for row in observations])
    logistic = _fit_logistic(features, labels, weights)
    discrepancy_rows = [
        row
        for row in observations
        if row.valid
        and row.cfd_geomean_ld is not None
        and float(row.cfd_geomean_ld) > 0.0
    ]
    if discrepancy_rows:
        discrepancy_features = np.vstack(
            [
                _feature(row.geometry_38d, row.cheap_geomean_ld, bounds)
                for row in discrepancy_rows
            ]
        )
        residuals = np.asarray(
            [
                math.log(float(row.cfd_geomean_ld))
                - math.log(float(row.cheap_geomean_ld))
                for row in discrepancy_rows
            ]
        )
    else:
        discrepancy_features = np.empty((0, features.shape[1]), dtype=np.float64)
        residuals = np.empty(0, dtype=np.float64)
    discrepancy = _fit_discrepancy(discrepancy_features, residuals)
    merits: dict[str, float] = {}
    detail: dict[str, Any] = {}
    for candidate in candidates:
        candidate_id = str(candidate["candidate_id"])
        feature = _feature(
            candidate["geometry_38d"],
            float(candidate["cheap_geomean_ld"]),
            bounds,
        )
        probability = float(_sigmoid(np.asarray([feature @ logistic]))[0])
        probability = min(max(probability, 0.05), 0.95)
        if discrepancy is None:
            residual_mean = 0.0
            sigma = 0.25
        else:
            residual_mean = float(feature @ discrepancy.coefficients)
            variance = discrepancy.residual_variance * (
                1.0 + float(feature @ discrepancy.inverse_precision @ feature)
            )
            sigma = max(math.sqrt(max(variance, 0.0)), 0.05)
        mu = math.log(float(candidate["cheap_geomean_ld"])) + residual_mean
        entropy = -probability * math.log(probability) - (
            1.0 - probability
        ) * math.log(1.0 - probability)
        value = math.log(probability) + mu + 0.5 * sigma + 0.1 * entropy
        merits[candidate_id] = float(value)
        detail[candidate_id] = {
            "p_delivery": probability,
            "mu_log_cfd": mu,
            "sigma_log_cfd": sigma,
            "entropy": entropy,
            "private_merit": value,
        }
    metadata = {
        "delivery_rows": len(observations),
        "delivery_effective_weight": float(weights.sum()),
        "delivery_positive_weight": float(weights @ labels),
        "discrepancy_rows": len(discrepancy_rows),
        "delivery_coefficients_sha256": canonical_sha256(
            [float(value) for value in logistic]
        ),
        "discrepancy_coefficients_sha256": (
            canonical_sha256([float(value) for value in discrepancy.coefficients])
            if discrepancy is not None
            else None
        ),
        "candidate_detail": detail,
    }
    return merits, metadata


def _bounds_identity(bounds: PhysicalBounds) -> dict[str, Any]:
    return {
        "normalization_id": bounds.normalization_id,
        "z_lower": float(bounds.z_lower),
        "z_upper": float(bounds.z_upper),
        "lower_sha256": canonical_sha256([float(value) for value in bounds.lower]),
        "upper_sha256": canonical_sha256([float(value) for value in bounds.upper]),
    }


class Phase3PilotEpisode:
    """One restartable, episode-isolated Phase 3 method instance."""

    def __init__(
        self,
        *,
        method_id: str,
        seed: int,
        bounds: PhysicalBounds,
        state_path: Path | None = None,
        config_path: Path = DEFAULT_CONFIG,
    ):
        _require(method_id in METHOD_IDS, "unknown Phase 3 method")
        self.method_id = str(method_id)
        self.seed = int(seed)
        self.bounds = bounds
        self.state_path = Path(state_path) if state_path is not None else None
        self.config, self.config_sha256 = load_method_panel(config_path)
        _require(
            np.__version__ == "2.0.2",
            f"Phase 3 methods require numpy==2.0.2, got {np.__version__}",
        )
        self.initial_observations, self.historical_hashes = _initial_context()
        if self.state_path is not None and self.state_path.exists():
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
            _require(isinstance(state, dict), "method state must be an object")
            self.state = state
            self._validate_state()
        else:
            self.state = self._new_state()
            self._append_event(
                "episode_initialized",
                {
                    "method_id": self.method_id,
                    "seed": self.seed,
                    "config_sha256": self.config_sha256,
                },
            )
            self._save()

    def _new_state(self) -> dict[str, Any]:
        return {
            "schema_version": STATE_SCHEMA,
            "method_id": self.method_id,
            "seed": self.seed,
            "config_sha256": self.config_sha256,
            "bounds_identity": _bounds_identity(self.bounds),
            "generations_completed": 0,
            "cheap_queries_used": 0,
            "generation_ledger": [],
            "candidates": [],
            "online_observations": [],
            "pending_candidate_id": None,
            "precommitted_candidate_ids": [],
            "de_state": None,
            "events": [],
            "event_head_sha256": ZERO_SHA256,
            "terminal": None,
        }

    def _validate_state(self) -> None:
        state = self.state
        _require(state.get("schema_version") == STATE_SCHEMA, "state schema changed")
        _require(state.get("method_id") == self.method_id, "state method changed")
        _require(int(state.get("seed")) == self.seed, "state seed changed")
        _require(
            state.get("config_sha256") == self.config_sha256,
            "state method-panel binding changed",
        )
        _require(
            state.get("bounds_identity") == _bounds_identity(self.bounds),
            "state physical bounds changed",
        )
        _require(
            int(state.get("cheap_queries_used", -1))
            == len(state.get("candidates", []))
            == 16 * int(state.get("generations_completed", -1)),
            "state cheap-query accounting changed",
        )
        _require(
            len(state.get("generation_ledger", []))
            == int(state.get("generations_completed", -1)),
            "state generation ledger is inconsistent",
        )
        previous = ZERO_SHA256
        for index, event in enumerate(state.get("events", [])):
            _require(event.get("event_index") == index, "method event index changed")
            _require(
                event.get("previous_event_sha256") == previous,
                "method event chain changed",
            )
            core = dict(event)
            observed = core.pop("event_sha256", None)
            _require(observed == canonical_sha256(core), "method event hash changed")
            previous = str(observed)
        _require(
            state.get("event_head_sha256") == previous,
            "method event head changed",
        )
        _require(
            len(state.get("online_observations", [])) <= 32,
            "online feedback budget exceeded",
        )
        pending = state.get("pending_candidate_id")
        if pending is not None:
            _require(
                pending in self._candidate_map(),
                "pending candidate is absent from the candidate ledger",
            )

    def _append_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        event = {
            "schema_version": EVENT_SCHEMA,
            "event_index": len(self.state["events"]),
            "event_type": str(event_type),
            "previous_event_sha256": self.state["event_head_sha256"],
            "payload": deepcopy(dict(payload)),
        }
        event["event_sha256"] = canonical_sha256(event)
        self.state["events"].append(event)
        self.state["event_head_sha256"] = event["event_sha256"]

    def _save(self) -> None:
        self._validate_state()
        if self.state_path is not None:
            _atomic_write_json(self.state_path, self.state)

    @property
    def state_sha256(self) -> str:
        return canonical_sha256(self.state)

    @property
    def online_query_count(self) -> int:
        return len(self.state["online_observations"])

    @property
    def generations_completed(self) -> int:
        return int(self.state["generations_completed"])

    def _candidate_map(self) -> dict[str, dict[str, Any]]:
        return {
            str(candidate["candidate_id"]): candidate
            for candidate in self.state["candidates"]
        }

    def _belief_observations(self) -> list[_BeliefRow]:
        rows = list(self.initial_observations)
        candidates = self._candidate_map()
        weights = {
            "pass": 1.0,
            "conclusive_failure": 1.0,
            "undetermined": 0.5,
            "partial": 0.5,
        }
        for feedback in self.state["online_observations"]:
            candidate = candidates[str(feedback["candidate_id"])]
            rows.append(
                _BeliefRow(
                    candidate_id=str(candidate["candidate_id"]),
                    geometry_38d=tuple(
                        float(value) for value in candidate["geometry_38d"]
                    ),
                    cheap_geomean_ld=float(candidate["cheap_geomean_ld"]),
                    valid=bool(feedback["delivery"]),
                    delivery_weight=weights[str(feedback["outcome_kind"])],
                    cfd_geomean_ld=(
                        float(feedback["conditional_value"])
                        if feedback["conditional_value"] is not None
                        else None
                    ),
                    outcome_kind=str(feedback["outcome_kind"]),
                )
            )
        return rows

    def _new_cma_strategy(self):
        import cma

        _require(
            cma.__version__ == REQUIRED_CMA_VERSION,
            f"Phase 3 CMA requires cma=={REQUIRED_CMA_VERSION}",
        )
        options = {
            "bounds": [[0.0] * DESIGN_DIM, [1.0] * DESIGN_DIM],
            "popsize": 16,
            "seed": self.seed,
            "verbose": -9,
            "verb_disp": 0,
            "verb_log": 0,
            "CMA_mirrors": 0,
        }
        return cma.CMAEvolutionStrategy([0.5] * DESIGN_DIM, 0.25, options)

    def _reconstruct_cma(self):
        strategy = self._new_cma_strategy()
        for row in self.state["generation_ledger"]:
            expected = np.asarray(row["unit_population"], dtype=np.float64)
            observed = np.asarray(strategy.ask(), dtype=np.float64)
            _require(
                np.array_equal(observed, expected),
                "saved CMA candidate stream does not replay exactly",
            )
            strategy.tell(observed.tolist(), [float(value) for value in row["losses"]])
        return strategy

    def _candidate_rows(
        self,
        *,
        unit: np.ndarray,
        physical: np.ndarray,
        scores: np.ndarray,
        generation: int,
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for population_index, (unit_row, geometry, score) in enumerate(
            zip(unit, physical, scores)
        ):
            digest = geometry_sha256([float(value) for value in geometry])
            complete = bool(math.isfinite(float(score)) and float(score) > 0.0)
            rows.append(
                {
                    "candidate_id": (
                        f"phase3-{self.method_id}-s{self.seed}-"
                        f"g{generation + 1:03d}-p{population_index:02d}-{digest[:8]}"
                    ),
                    "generation": int(generation),
                    "population_index": int(population_index),
                    "unit_geometry_38d": [float(value) for value in unit_row],
                    "geometry_38d": [float(value) for value in geometry],
                    "geometry_sha256": digest,
                    "cheap_status": "complete" if complete else "invalid",
                    "cheap_geomean_ld": float(score) if complete else None,
                }
            )
        return rows

    def _losses(
        self,
        candidates: Sequence[Mapping[str, Any]],
    ) -> tuple[list[float], dict[str, Any]]:
        valid = [
            candidate
            for candidate in candidates
            if candidate["cheap_status"] == "complete"
        ]
        if self.method_id in {STATIC_METHOD, CONVENTIONAL_METHOD}:
            merit = {
                str(candidate["candidate_id"]): float(candidate["cheap_geomean_ld"])
                for candidate in valid
            }
            metadata = {
                "generator_objective": "cheap_geomean_ld",
                "online_feedback_rows_available": self.online_query_count,
            }
        else:
            merit, metadata = _belief_merits(
                valid,
                self._belief_observations(),
                self.bounds,
            )
            metadata["generator_objective"] = "private_feedback_steered_merit"
            metadata["online_feedback_rows_available"] = self.online_query_count
        losses = [
            -float(merit[str(candidate["candidate_id"])])
            if candidate["cheap_status"] == "complete"
            else 1e12
            for candidate in candidates
        ]
        return losses, metadata

    def _advance_cma(
        self,
        objective: Callable[[np.ndarray], Sequence[float]],
        target_generation: int,
    ) -> None:
        strategy = self._reconstruct_cma()
        lower = np.asarray(self.bounds.lower, dtype=np.float64)
        upper = np.asarray(self.bounds.upper, dtype=np.float64)
        while self.generations_completed < target_generation:
            generation = self.generations_completed
            unit = np.asarray(strategy.ask(), dtype=np.float64)
            _require(unit.shape == (16, DESIGN_DIM), "CMA returned an invalid population")
            physical = lower[None, :] + unit * (upper - lower)[None, :]
            scores = np.asarray(objective(physical), dtype=np.float64).reshape(-1)
            _require(scores.shape == (16,), "cheap objective must return 16 scores")
            candidates = self._candidate_rows(
                unit=unit,
                physical=physical,
                scores=scores,
                generation=generation,
            )
            losses, belief = self._losses(candidates)
            strategy.tell(unit.tolist(), losses)
            generation_row = {
                "generation": generation,
                "generator": "CMA-ES",
                "unit_population": [[float(value) for value in row] for row in unit],
                "candidate_ids": [
                    str(candidate["candidate_id"]) for candidate in candidates
                ],
                "losses": [float(value) for value in losses],
                "belief": belief,
            }
            self.state["generation_ledger"].append(generation_row)
            self.state["candidates"].extend(candidates)
            self.state["generations_completed"] = generation + 1
            self.state["cheap_queries_used"] += 16
            self._append_event(
                "cheap_generation_observed",
                {
                    "generation_one_based": generation + 1,
                    "candidate_geometry_sha256": [
                        candidate["geometry_sha256"] for candidate in candidates
                    ],
                    "losses_sha256": canonical_sha256(losses),
                    "cheap_queries_used": self.state["cheap_queries_used"],
                    "online_feedback_rows_available": self.online_query_count,
                },
            )
            self._save()

    def _restore_de_rng(self) -> np.random.Generator:
        state = self.state["de_state"]
        generator = np.random.Generator(np.random.PCG64(self.seed))
        if state is not None:
            generator.bit_generator.state = deepcopy(state["rng_state"])
        return generator

    def _advance_de(
        self,
        objective: Callable[[np.ndarray], Sequence[float]],
        target_generation: int,
    ) -> None:
        rng = self._restore_de_rng()
        lower = np.asarray(self.bounds.lower, dtype=np.float64)
        upper = np.asarray(self.bounds.upper, dtype=np.float64)
        de_state = self.state["de_state"]
        population = (
            np.asarray(de_state["population"], dtype=np.float64)
            if de_state is not None
            else None
        )
        incumbent_scores = (
            np.asarray(de_state["scores"], dtype=np.float64)
            if de_state is not None
            else None
        )
        while self.generations_completed < target_generation:
            generation = self.generations_completed
            if generation == 0:
                unit = rng.random((16, DESIGN_DIM), dtype=np.float64)
            else:
                _require(
                    population is not None and incumbent_scores is not None,
                    "DE continuation state is missing",
                )
                unit = np.empty_like(population)
                indices = np.arange(16)
                for target in range(16):
                    pool = indices[indices != target]
                    r1, r2, r3 = rng.choice(pool, size=3, replace=False)
                    mutant = population[r1] + 0.8 * (
                        population[r2] - population[r3]
                    )
                    violated = (mutant < 0.0) | (mutant > 1.0)
                    if np.any(violated):
                        mutant = mutant.copy()
                        mutant[violated] = rng.random(int(np.sum(violated)))
                    crossover = rng.random(DESIGN_DIM) < 0.9
                    crossover[int(rng.integers(0, DESIGN_DIM))] = True
                    unit[target] = np.where(crossover, mutant, population[target])
            physical = lower[None, :] + unit * (upper - lower)[None, :]
            cheap_scores = np.asarray(objective(physical), dtype=np.float64).reshape(-1)
            _require(cheap_scores.shape == (16,), "cheap objective must return 16 scores")
            candidates = self._candidate_rows(
                unit=unit,
                physical=physical,
                scores=cheap_scores,
                generation=generation,
            )
            losses, belief = self._losses(candidates)
            challenger_scores = -np.asarray(losses, dtype=np.float64)
            if generation == 0:
                population = unit.copy()
                incumbent_scores = challenger_scores.copy()
                replaced = [True] * 16
            else:
                replace = challenger_scores >= incumbent_scores
                population[replace] = unit[replace]
                incumbent_scores[replace] = challenger_scores[replace]
                replaced = [bool(value) for value in replace]
            generation_row = {
                "generation": generation,
                "generator": "DE/rand/1/bin",
                "unit_population": [[float(value) for value in row] for row in unit],
                "candidate_ids": [
                    str(candidate["candidate_id"]) for candidate in candidates
                ],
                "losses": [float(value) for value in losses],
                "replaced_parent": replaced,
                "belief": belief,
            }
            self.state["generation_ledger"].append(generation_row)
            self.state["candidates"].extend(candidates)
            self.state["generations_completed"] = generation + 1
            self.state["cheap_queries_used"] += 16
            self.state["de_state"] = {
                "rng_state": deepcopy(rng.bit_generator.state),
                "population": [
                    [float(value) for value in row] for row in population
                ],
                "scores": [float(value) for value in incumbent_scores],
            }
            self._append_event(
                "cheap_generation_observed",
                {
                    "generation_one_based": generation + 1,
                    "candidate_geometry_sha256": [
                        candidate["geometry_sha256"] for candidate in candidates
                    ],
                    "losses_sha256": canonical_sha256(losses),
                    "cheap_queries_used": self.state["cheap_queries_used"],
                    "online_feedback_rows_available": self.online_query_count,
                },
            )
            self._save()

    def prepare_for_next_online_query(
        self,
        objective: Callable[[np.ndarray], Sequence[float]],
    ) -> None:
        """Consume the prospectively scheduled cheap budget for the next query."""

        _require(self.state["terminal"] is None, "episode is terminal")
        _require(self.state["pending_candidate_id"] is None, "an online action is pending")
        _require(self.online_query_count < 32, "online query budget is exhausted")
        if self.method_id in {STATIC_METHOD, CONVENTIONAL_METHOD}:
            target = 256
        else:
            target = min(256, (self.online_query_count + 1) * 8)
        _require(
            target >= self.generations_completed,
            "cheap schedule would move backwards",
        )
        if self.method_id == DE_METHOD:
            self._advance_de(objective, target)
        else:
            self._advance_cma(objective, target)
        if self.method_id == STATIC_METHOD and not self.state[
            "precommitted_candidate_ids"
        ]:
            archive = self.active_archive()
            _require(len(archive) >= 32, "static method has fewer than 32 nominees")
            committed = [row["candidate_id"] for row in archive[:32]]
            self.state["precommitted_candidate_ids"] = committed
            self._append_event(
                "online_sequence_precommitted",
                {
                    "candidate_ids": committed,
                    "before_any_online_feedback": self.online_query_count == 0,
                },
            )
            self._save()

    def active_archive(self) -> list[dict[str, Any]]:
        complete = [
            deepcopy(candidate)
            for candidate in self.state["candidates"]
            if candidate["cheap_status"] == "complete"
            and candidate["geometry_sha256"] not in self.historical_hashes
        ]
        complete.sort(
            key=lambda row: (
                -float(row["cheap_geomean_ld"]),
                int(row["generation"]),
                int(row["population_index"]),
                str(row["candidate_id"]),
            )
        )
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in complete:
            if row["geometry_sha256"] in seen:
                continue
            seen.add(str(row["geometry_sha256"]))
            unique.append(row)
            if len(unique) == 64:
                break
        return unique

    def _selector_observations(self) -> list[PairedObservation]:
        rows = [
            PairedObservation(
                candidate_id=row.candidate_id,
                geometry_38d=row.geometry_38d,
                cheap_geomean_ld=row.cheap_geomean_ld,
                valid=row.valid,
                cfd_geomean_ld=row.cfd_geomean_ld,
            )
            for row in self.initial_observations
        ]
        candidates = self._candidate_map()
        for feedback in self.state["online_observations"]:
            candidate = candidates[str(feedback["candidate_id"])]
            rows.append(
                PairedObservation(
                    candidate_id=str(candidate["candidate_id"]),
                    geometry_38d=tuple(
                        float(value) for value in candidate["geometry_38d"]
                    ),
                    cheap_geomean_ld=float(candidate["cheap_geomean_ld"]),
                    valid=bool(feedback["delivery"]),
                    cfd_geomean_ld=(
                        float(feedback["conditional_value"])
                        if feedback["conditional_value"] is not None
                        else None
                    ),
                )
            )
        return rows

    def next_online_candidate(self) -> dict[str, Any]:
        """Freeze and return the next online action for dispatch."""

        _require(self.state["terminal"] is None, "episode is terminal")
        _require(self.state["pending_candidate_id"] is None, "an online action is pending")
        _require(self.online_query_count < 32, "online query budget is exhausted")
        required = 256 if self.method_id in {
            STATIC_METHOD,
            CONVENTIONAL_METHOD,
        } else (self.online_query_count + 1) * 8
        _require(
            self.generations_completed == required,
            "required cheap search block is incomplete",
        )
        queried = {
            str(row["candidate_id"]) for row in self.state["online_observations"]
        }
        if self.method_id == STATIC_METHOD:
            committed = self.state["precommitted_candidate_ids"]
            _require(len(committed) == 32, "static sequence was not precommitted")
            candidate_id = str(committed[self.online_query_count])
        else:
            archive = self.active_archive()
            acquisition = choose_next_cfd(
                [
                    CheapCandidate(
                        candidate_id=str(row["candidate_id"]),
                        geometry_38d=tuple(
                            float(value) for value in row["geometry_38d"]
                        ),
                        cheap_geomean_ld=float(row["cheap_geomean_ld"]),
                    )
                    for row in archive
                ],
                self._selector_observations(),
                bounds=self.bounds,
                online_candidate_ids=queried,
            )
            candidate_id = acquisition.candidate_id
        _require(candidate_id not in queried, "method repeated an online candidate")
        candidate = deepcopy(self._candidate_map()[candidate_id])
        self.state["pending_candidate_id"] = candidate_id
        self._append_event(
            "online_candidate_frozen",
            {
                "query_index_one_based": self.online_query_count + 1,
                "candidate_id": candidate_id,
                "geometry_sha256": candidate["geometry_sha256"],
            },
        )
        self._save()
        return candidate

    def record_infrastructure_loss(self, reason_code: str) -> dict[str, Any]:
        """Retain the exact pending action without consuming feedback or budget."""

        candidate_id = self.state["pending_candidate_id"]
        _require(candidate_id is not None, "there is no pending action to retry")
        self._append_event(
            "infrastructure_loss_recorded",
            {
                "candidate_id": candidate_id,
                "reason_code": str(reason_code),
                "scientific_query_consumed": False,
                "model_update": False,
            },
        )
        self._save()
        return deepcopy(self._candidate_map()[str(candidate_id)])

    def observe_online(self, feedback: OnlineFeedback) -> None:
        """Apply exactly one optimizer-visible online result."""

        feedback.validate()
        pending = self.state["pending_candidate_id"]
        _require(pending is not None, "there is no pending online action")
        _require(
            feedback.candidate_id == pending,
            "feedback does not match the immutable pending action",
        )
        _require(self.online_query_count < 32, "online feedback budget is exhausted")
        self.state["online_observations"].append(feedback.to_dict())
        self.state["pending_candidate_id"] = None
        self._append_event(
            "online_feedback_observed",
            {
                "query_index_one_based": self.online_query_count,
                "feedback": feedback.to_dict(),
            },
        )
        self._save()

    def recommendation(self) -> dict[str, Any]:
        """Return the current checkpoint recommendation without observing its grade."""

        _require(bool(self.state["candidates"]), "no cheap candidate exists")
        candidates = self._candidate_map()
        delivered = [
            row for row in self.state["online_observations"] if row["delivery"]
        ]
        if delivered:
            best = min(
                delivered,
                key=lambda row: (
                    -float(row["conditional_value"]),
                    str(row["candidate_id"]),
                ),
            )
            candidate_id = str(best["candidate_id"])
            rule = "best_online_delivery"
        else:
            queried = {
                str(row["candidate_id"]) for row in self.state["online_observations"]
            }
            eligible = [
                row for row in self.active_archive() if row["candidate_id"] not in queried
            ]
            _require(bool(eligible), "no unqueried recommendation candidate remains")
            if self.method_id in {FSD_METHOD, DE_METHOD}:
                merits, _ = _belief_merits(
                    eligible,
                    self._belief_observations(),
                    self.bounds,
                )
                candidate_id = min(
                    (str(row["candidate_id"]) for row in eligible),
                    key=lambda value: (-merits[value], value),
                )
                rule = "highest_private_merit_unqueried"
            elif self.method_id == CONVENTIONAL_METHOD:
                acquisition = choose_next_cfd(
                    [
                        CheapCandidate(
                            candidate_id=str(row["candidate_id"]),
                            geometry_38d=tuple(
                                float(value) for value in row["geometry_38d"]
                            ),
                            cheap_geomean_ld=float(row["cheap_geomean_ld"]),
                        )
                        for row in eligible
                    ],
                    self._selector_observations(),
                    bounds=self.bounds,
                    online_candidate_ids=queried,
                )
                candidate_id = acquisition.candidate_id
                rule = "highest_selector_acquisition_unqueried"
            else:
                candidate_id = str(eligible[0]["candidate_id"])
                rule = "highest_cheap_unqueried"
        result = deepcopy(candidates[candidate_id])
        result["recommendation_rule"] = rule
        result["online_queries_observed"] = self.online_query_count
        return result

    def finalize(self) -> dict[str, Any]:
        """Freeze the terminal recommendation after all 32 online results."""

        _require(self.online_query_count == 32, "terminal requires 32 online results")
        _require(self.state["pending_candidate_id"] is None, "an action remains pending")
        _require(self.generations_completed == 256, "cheap budget is incomplete")
        if self.state["terminal"] is None:
            recommendation = self.recommendation()
            self.state["terminal"] = {
                "candidate_id": recommendation["candidate_id"],
                "geometry_sha256": recommendation["geometry_sha256"],
                "recommendation_rule": recommendation["recommendation_rule"],
                "reuses_checkpoint_32": True,
            }
            self._append_event("episode_terminal", self.state["terminal"])
            self._save()
        return deepcopy(self.state["terminal"])


def synthetic_cheap_objective(bounds: PhysicalBounds) -> Callable[[np.ndarray], np.ndarray]:
    """Return the deterministic no-dispatch qualification objective."""

    indices = np.arange(DESIGN_DIM, dtype=np.float64)
    target = 0.9 * np.sin(0.37 * indices) + 0.35 * np.cos(0.13 * indices)
    mean = np.asarray(bounds.mean, dtype=np.float64)
    sd = np.asarray(bounds.sd, dtype=np.float64)

    def objective(geometries: np.ndarray) -> np.ndarray:
        values = np.asarray(geometries, dtype=np.float64)
        _require(values.ndim == 2 and values.shape[1] == DESIGN_DIM, "invalid batch")
        z = (values - mean[None, :]) / sd[None, :]
        distance = np.mean((z - target[None, :]) ** 2, axis=1)
        ripple = 0.15 * np.mean(np.cos(2.1 * z), axis=1)
        return 20.0 + 14.0 * np.exp(-distance / 3.0) + ripple

    return objective
