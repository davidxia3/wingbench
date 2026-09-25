"""Small deterministic adaptive multi-fidelity reference optimizer.

This module is intentionally conventional.  It learns a regularized feasibility
model and a regularized log-discrepancy model, then applies feasibility-weighted
expected improvement to a fixed cheap CMA-ES archive.  It has no geometry gate,
manifold model, LLM, cross-episode state, or hidden label access.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Sequence

import numpy as np

from wingbench.benchmark.multifidelity_protocol import PhysicalBounds


SQRT_TWO = math.sqrt(2.0)
INV_SQRT_TWO_PI = 1.0 / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class CheapCandidate:
    candidate_id: str
    geometry_38d: tuple[float, ...]
    cheap_geomean_ld: float


@dataclass(frozen=True)
class PairedObservation:
    candidate_id: str
    geometry_38d: tuple[float, ...]
    cheap_geomean_ld: float
    valid: bool
    cfd_geomean_ld: float | None = None


@dataclass(frozen=True)
class Acquisition:
    candidate_id: str
    p_valid: float
    mu_log_cfd: float
    sigma_log_cfd: float
    value: float
    kind: str


@dataclass(frozen=True)
class SimpleMFConfig:
    logistic_l2: float = 4.0
    discrepancy_l2: float = 10.0
    discrepancy_intercept_l2: float = 0.1
    discrepancy_sigma_floor: float = 0.05
    zero_label_sigma: float = 0.25
    probability_floor: float = 0.05
    probability_ceiling: float = 0.95
    ucb_beta: float = 1.0
    ei_xi: float = 0.0
    logistic_iterations: int = 40


def _normal_pdf(value: float) -> float:
    return INV_SQRT_TWO_PI * math.exp(-0.5 * value * value)


def _normal_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / SQRT_TWO))


def _feature(
    geometry_38d: Sequence[float],
    cheap_geomean_ld: float,
    bounds: PhysicalBounds,
) -> np.ndarray:
    geometry = np.asarray(geometry_38d, dtype=np.float64)
    if geometry.shape != (38,) or not np.all(np.isfinite(geometry)):
        raise ValueError("geometry must contain 38 finite values")
    cheap = float(cheap_geomean_ld)
    if not math.isfinite(cheap) or cheap <= 0.0:
        raise ValueError("cheap score must be finite and positive")
    mean = np.asarray(bounds.mean, dtype=np.float64)
    sd = np.asarray(bounds.sd, dtype=np.float64)
    z_scaled = ((geometry - mean) / sd) / max(abs(bounds.z_lower), abs(bounds.z_upper))
    # log(30) is a fixed scale origin, not a fitted statistic from the formal seeds.
    return np.concatenate(([1.0], z_scaled, [math.log(cheap) - math.log(30.0)]))


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -40.0, 40.0)
    return 1.0 / (1.0 + np.exp(-clipped))


def _fit_logistic(features: np.ndarray, labels: np.ndarray, config: SimpleMFConfig) -> np.ndarray:
    dimensions = features.shape[1]
    coefficients = np.zeros(dimensions, dtype=np.float64)
    prevalence = (float(labels.sum()) + 1.0) / (float(len(labels)) + 2.0)
    coefficients[0] = math.log(prevalence / (1.0 - prevalence))
    penalty = np.full(dimensions, float(config.logistic_l2), dtype=np.float64)
    penalty[0] = 0.1
    for _ in range(int(config.logistic_iterations)):
        probabilities = _sigmoid(features @ coefficients)
        weights = np.maximum(probabilities * (1.0 - probabilities), 1e-6)
        gradient = features.T @ (probabilities - labels) + penalty * coefficients
        hessian = (features.T * weights) @ features + np.diag(penalty)
        step = np.linalg.solve(hessian, gradient)
        coefficients -= step
        if float(np.linalg.norm(step)) < 1e-10:
            break
    return coefficients


@dataclass(frozen=True)
class _DiscrepancyFit:
    coefficients: np.ndarray
    inverse_precision: np.ndarray
    residual_variance: float


def _fit_discrepancy(
    features: np.ndarray,
    residuals: np.ndarray,
    config: SimpleMFConfig,
) -> _DiscrepancyFit | None:
    if not len(residuals):
        return None
    dimensions = features.shape[1]
    penalty = np.full(dimensions, float(config.discrepancy_l2), dtype=np.float64)
    penalty[0] = float(config.discrepancy_intercept_l2)
    precision = features.T @ features + np.diag(penalty)
    inverse = np.linalg.inv(precision)
    coefficients = inverse @ features.T @ residuals
    errors = residuals - features @ coefficients
    variance = max(
        float(np.mean(errors * errors)) if len(errors) else 0.0,
        float(config.discrepancy_sigma_floor) ** 2,
    )
    return _DiscrepancyFit(coefficients, inverse, variance)


def acquisition_rows(
    candidates: Sequence[CheapCandidate],
    observations: Sequence[PairedObservation],
    *,
    bounds: PhysicalBounds,
    online_candidate_ids: set[str],
    config: SimpleMFConfig = SimpleMFConfig(),
) -> list[Acquisition]:
    """Score unqueried candidates from initial-context plus current-episode labels."""

    if not observations:
        raise ValueError("the simple control requires at least one declared paired observation")
    x_all = np.vstack(
        [_feature(row.geometry_38d, row.cheap_geomean_ld, bounds) for row in observations]
    )
    labels = np.asarray([1.0 if row.valid else 0.0 for row in observations], dtype=np.float64)
    logistic = _fit_logistic(x_all, labels, config)
    valid_rows = [
        row
        for row in observations
        if row.valid and row.cfd_geomean_ld is not None and float(row.cfd_geomean_ld) > 0.0
    ]
    if valid_rows:
        x_valid = np.vstack(
            [_feature(row.geometry_38d, row.cheap_geomean_ld, bounds) for row in valid_rows]
        )
        residuals = np.asarray(
            [math.log(float(row.cfd_geomean_ld)) - math.log(row.cheap_geomean_ld) for row in valid_rows],
            dtype=np.float64,
        )
    else:
        x_valid = np.empty((0, x_all.shape[1]), dtype=np.float64)
        residuals = np.empty(0, dtype=np.float64)
    discrepancy = _fit_discrepancy(x_valid, residuals, config)
    current_successes = [
        float(row.cfd_geomean_ld)
        for row in observations
        if row.candidate_id in online_candidate_ids
        and row.valid
        and row.cfd_geomean_ld is not None
        and float(row.cfd_geomean_ld) > 0.0
    ]
    y_best = math.log(max(current_successes)) if current_successes else None

    rows: list[Acquisition] = []
    for candidate in candidates:
        if candidate.candidate_id in online_candidate_ids:
            continue
        feature = _feature(candidate.geometry_38d, candidate.cheap_geomean_ld, bounds)
        probability = float(_sigmoid(np.asarray([feature @ logistic]))[0])
        probability = min(max(probability, config.probability_floor), config.probability_ceiling)
        if discrepancy is None:
            residual_mean = 0.0
            sigma = float(config.zero_label_sigma)
        else:
            residual_mean = float(feature @ discrepancy.coefficients)
            variance = discrepancy.residual_variance * (
                1.0 + float(feature @ discrepancy.inverse_precision @ feature)
            )
            sigma = max(math.sqrt(max(variance, 0.0)), config.discrepancy_sigma_floor)
        mu = math.log(candidate.cheap_geomean_ld) + residual_mean
        if y_best is None:
            value = probability * (mu + float(config.ucb_beta) * sigma)
            kind = "p_valid_weighted_ucb"
        else:
            improvement = mu - y_best - float(config.ei_xi)
            standardized = improvement / sigma
            ei = improvement * _normal_cdf(standardized) + sigma * _normal_pdf(standardized)
            value = probability * max(0.0, ei)
            kind = "p_valid_weighted_ei"
        rows.append(
            Acquisition(
                candidate_id=candidate.candidate_id,
                p_valid=probability,
                mu_log_cfd=mu,
                sigma_log_cfd=sigma,
                value=float(value),
                kind=kind,
            )
        )
    rows.sort(key=lambda row: (-row.value, row.candidate_id))
    return rows


def choose_next_cfd(
    candidates: Sequence[CheapCandidate],
    observations: Sequence[PairedObservation],
    *,
    bounds: PhysicalBounds,
    online_candidate_ids: set[str],
    config: SimpleMFConfig = SimpleMFConfig(),
) -> Acquisition:
    """Choose one deterministic action; query one is fixed to the cheap champion."""

    eligible = [row for row in candidates if row.candidate_id not in online_candidate_ids]
    if not eligible:
        raise ValueError("no unqueried cheap candidate remains")
    if not online_candidate_ids:
        champion = min(eligible, key=lambda row: (-row.cheap_geomean_ld, row.candidate_id))
        return Acquisition(
            candidate_id=champion.candidate_id,
            p_valid=math.nan,
            mu_log_cfd=math.log(champion.cheap_geomean_ld),
            sigma_log_cfd=math.nan,
            value=champion.cheap_geomean_ld,
            kind="matched_first_cheap_champion",
        )
    rows = acquisition_rows(
        candidates,
        observations,
        bounds=bounds,
        online_candidate_ids=online_candidate_ids,
        config=config,
    )
    if not rows:
        raise ValueError("no acquisition row remains")
    return rows[0]
