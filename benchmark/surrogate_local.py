"""The surrogate for cheap queries, with the 4,096 counter.

The surrogate is the released wrapper ``wingbench.surrogate.aerotransformer``
around ATsurf_L, the large surface variant of AeroTransformer. The wrapper
pins the model by content. The files it needs are

``model_config``          SHA-256 a6cedb0a3dba73f99c41bcfdf3913d37a912c6b0558d5f906e6633aebdca8ef2
``best_model_weights``    SHA-256 4fa8fb4e3986cdb7a4a24f1f74410c54412beb5191857c76fdd4c4f0c5dc8bfa

Both come from the public Hugging Face repository ``thuerey-group/AeroTransformer``.
Put them in one folder and pass it as ``--surrogate-model-dir``. The wrapper
also imports ``flowvae``, the module of the floGen code that ships with
AeroTransformer. Pass its folder as ``--flogen-root`` if it is not installed.

The trim settings come from the frozen protocol
``configs/benchmark/multifidelity_benchmark_protocol_v0.json`` under
``cheap_evaluator.trim``. The lift target and the angle of attack seed of
each task come from the task documents. The wrapper's own defaults differ
from the protocol, so the protocol values are passed explicitly.
"""
from __future__ import annotations

import math
from pathlib import Path
import time
from typing import Any, Sequence

import numpy as np

from wingbench.benchmark.multifidelity_protocol import evaluate_ld_envelope, resolve_protocol

from benchmark.common import ROOT, geometry_sha256
from benchmark.optimizer_api import CheapResult, ConditionPrediction, TaskSpec

MODEL_NAME = "ATsurf_L"
HUGGING_FACE_REPOSITORY = "thuerey-group/AeroTransformer"
MODEL_FILES = ("model_config", "best_model_weights")


class CheapBudgetExhausted(RuntimeError):
    """The optimizer asked for more than 4,096 surrogate evaluations."""


class SurrogateUnavailable(RuntimeError):
    """The runner was started without the surrogate weights."""


class CheapCounter:
    """Count surrogate evaluations against the budget of one episode."""

    def __init__(self, budget: int) -> None:
        self.budget = int(budget)
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.budget - self.used

    def charge(self, count: int) -> None:
        if count <= 0:
            raise ValueError("a cheap batch must contain at least one wing")
        if self.used + count > self.budget:
            raise CheapBudgetExhausted(
                f"{count} more surrogate evaluations would exceed the budget of {self.budget} "
                f"({self.used} used)"
            )
        self.used += count


def trim_settings(protocol: dict[str, Any] | None = None) -> dict[str, Any]:
    """The frozen trim settings of the cheap evaluator."""
    payload = protocol if protocol is not None else resolve_protocol(ROOT)
    trim = payload["cheap_evaluator"]["trim"]
    return {
        "delta_deg": float(trim["alpha1_deg"]) - float(trim["alpha0_deg"]),
        "secant_updates": int(trim["secant_updates"]),
        "cl_tolerance": float(trim["cl_tolerance"]),
        "aoa_bounds_deg": (float(trim["aoa_bounds_deg"][0]), float(trim["aoa_bounds_deg"][1])),
        "protocol_alpha0_deg": float(trim["alpha0_deg"]),
        "model": str(payload["cheap_evaluator"]["model"]),
    }


def missing_model_files(model_dir: Path) -> list[str]:
    return [name for name in MODEL_FILES if not (Path(model_dir) / name).is_file()]


class LocalSurrogate:
    """ATsurf_L fixed lift scoring for one task."""

    def __init__(
        self,
        task: TaskSpec,
        model_dir: str | Path,
        *,
        flogen_root: str | Path | None = None,
        device: str = "cpu",
        inference_chunk_size: int = 16,
        threads: int = 1,
    ) -> None:
        from wingbench.surrogate.aerotransformer import (
            AeroTransformerEvaluator,
            MODEL_CONFIG_SHA256,
            MODEL_WEIGHTS_SHA256,
        )

        model_dir = Path(model_dir)
        missing = missing_model_files(model_dir)
        if missing:
            raise SurrogateUnavailable(
                f"{model_dir} lacks {', '.join(missing)}. Download model_config "
                f"(SHA-256 {MODEL_CONFIG_SHA256}) and best_model_weights "
                f"(SHA-256 {MODEL_WEIGHTS_SHA256}) of {MODEL_NAME} from the Hugging Face "
                f"repository {HUGGING_FACE_REPOSITORY}."
            )
        self.task = task
        self.trim = trim_settings()
        if self.trim["model"] != MODEL_NAME:
            raise RuntimeError("the frozen protocol names a different surrogate model")
        self.evaluator = AeroTransformerEvaluator(
            model_dir=model_dir,
            flogen_root=flogen_root,
            device=device,
            inference_chunk_size=inference_chunk_size,
            torch_intraop_threads=threads,
            torch_interop_threads=threads,
        )
        self.mf_protocol = resolve_protocol(ROOT)
        self.seconds = 0.0
        self.evaluations = 0

    def predict(self, geometries: Sequence[Sequence[float]]) -> list[CheapResult]:
        rows = np.asarray([[float(value) for value in geometry] for geometry in geometries], dtype=np.float64)
        started = time.perf_counter()
        result = self.evaluator.fixed_cl(
            rows,
            mach_grid=self.task.mach,
            cl_target=self.task.target_cl,
            aoa_seed_deg=self.task.aoa_seed_deg,
            delta_deg=self.trim["delta_deg"],
            tolerance=self.trim["cl_tolerance"],
            max_secant_updates=self.trim["secant_updates"],
            aoa_bounds=self.trim["aoa_bounds_deg"],
        )
        self.seconds += time.perf_counter() - started
        self.evaluations += len(rows)
        answers: list[CheapResult] = []
        for index, geometry in enumerate(rows):
            conditions = tuple(
                ConditionPrediction(
                    mach=float(self.task.mach[j]),
                    cl=_finite_or_none(result.cl[index, j]),
                    cd=_finite_or_none(result.cd[index, j]),
                    ld=_finite_or_none(result.ld[index, j]),
                    aoa_deg=_finite_or_none(result.aoa_deg[index, j]),
                    converged=bool(result.converged[index, j]),
                )
                for j in range(len(self.task.mach))
            )
            score = _finite_or_none(result.score[index])
            if not all(row.converged for row in conditions) or score is None:
                status, score, reasons = "trim_failed", None, ("trim_failed",)
            else:
                envelope = evaluate_ld_envelope([row.ld for row in conditions], self.mf_protocol)
                if envelope.eligible:
                    status, reasons = "complete", ("complete_score_eligible",)
                else:
                    status, score, reasons = "envelope_violation", None, tuple(envelope.reason_codes)
            answers.append(
                CheapResult(
                    geometry=tuple(float(value) for value in geometry),
                    wing=geometry_sha256(geometry),
                    status=status,
                    score=score,
                    conditions=conditions,
                    reason_codes=reasons,
                )
            )
        return answers


def _finite_or_none(value: Any) -> float | None:
    number = float(value)
    return number if math.isfinite(number) else None
