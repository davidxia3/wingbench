#!/usr/bin/env python3
"""SMAC3 BlackBoxFacade baseline for WingBench (method smac3_blackbox_cfd_only_penalty_v1).

A post-freeze external reference arm. It is deliberately NOT part of the sealed
four-method panel in surrogate_assisted.py and must never be added to that module.

The policy inside BlackBoxFacade is stock. This module supplies only:
  * a search-space bridge  (38 ordered z-coordinates in [-3.5, 3.5] -> ConfigSpace)
  * a grade adapter        (delivered y -> cost 1 - y/40 ; any nondelivery -> cost 1, CRASHED)
  * an external ask/tell loop (WingBench owns evaluation, persistence and hidden grading)
  * a recommendation adapter and a deterministic replay bridge

It makes zero calls to the pretrained aerodynamic surrogate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from ConfigSpace import Configuration, ConfigurationSpace, Float
from smac import BlackBoxFacade, Scenario
from smac.runhistory.dataclasses import TrialInfo, TrialValue
from smac.runhistory.enumerations import StatusType

METHOD_ID = "smac3_blackbox_cfd_only_penalty_v1"
DISPLAY_NAME = "SMAC3-BlackBox"
ADAPTER_VERSION = "wingbench-smac-adapter-v1"

DESIGN_DIM = 38
Z_LOWER, Z_UPPER = -3.5, 3.5
LD_CAP = 40.0            # benchmark's valid per-condition L/D cap
NONDELIVERY_COST = 1.0   # every typed nondelivery, regardless of reason
ONLINE_BUDGET = 32
PARAM_NAMES = tuple(f"x{i:02d}" for i in range(DESIGN_DIM))


def delivered_cost(y: float) -> float:
    """Map a delivered geometric-mean L/D to SMAC cost. Range separation is required:
    every delivery lands in [0, 1) and every nondelivery at exactly 1.0."""
    if not np.isfinite(y) or not (0.0 < y <= LD_CAP):
        raise ValueError(f"delivered L/D out of contract range (0, {LD_CAP}]: {y!r}")
    return 1.0 - y / LD_CAP


def build_space(seed: int) -> ConfigurationSpace:
    cs = ConfigurationSpace(seed=int(seed))
    cs.add([Float(n, (Z_LOWER, Z_UPPER)) for n in PARAM_NAMES])
    return cs


def config_to_z(cfg: Configuration) -> np.ndarray:
    """Schema order is authoritative; never rely on ConfigSpace's own ordering."""
    z = np.asarray([float(cfg[n]) for n in PARAM_NAMES], dtype=np.float64)
    if z.shape != (DESIGN_DIM,) or not np.all(np.isfinite(z)):
        raise ValueError("proposal is not 38 finite floats")
    if z.min() < Z_LOWER - 1e-9 or z.max() > Z_UPPER + 1e-9:
        raise ValueError(f"proposal outside box: [{z.min()}, {z.max()}]")
    return z


@dataclass
class Outcome:
    """One committed WingBench scientific grade."""
    delivered: bool
    value: float | None = None          # geometric-mean L/D, only when delivered
    reason: str = ""                    # typed reason, retained for audit only
    outcome_kind: str = ""

    def to_trial_value(self) -> TrialValue:
        if self.delivered:
            return TrialValue(
                cost=delivered_cost(float(self.value)),
                status=StatusType.SUCCESS,
                additional_info={"adapter": ADAPTER_VERSION, "delivered": True,
                                 "ld": float(self.value), "reason": self.reason,
                                 "outcome_kind": self.outcome_kind},
            )
        return TrialValue(
            cost=NONDELIVERY_COST,
            status=StatusType.CRASHED,
            additional_info={"adapter": ADAPTER_VERSION, "delivered": False,
                             "ld": None, "reason": self.reason,
                             "outcome_kind": self.outcome_kind},
        )


@dataclass
class Smac3BlackBoxEpisode:
    """One WingBench episode: one instance, one matched seed, 32 sequential CFD grades."""

    instance_id: str
    seed: int
    output_dir: Path
    n_trials: int = ONLINE_BUDGET

    _smac: BlackBoxFacade = field(init=False, repr=False)
    _pending: TrialInfo | None = field(init=False, default=None, repr=False)
    _reserved: TrialInfo | None = field(init=False, default=None, repr=False)
    _observed: list[dict[str, Any]] = field(init=False, default_factory=list, repr=False)

    def __post_init__(self) -> None:
        self.output_dir = Path(self.output_dir)
        self._smac = self._new_facade(self.n_trials)

    def _new_facade(self, n_trials: int) -> BlackBoxFacade:
        scenario = Scenario(
            configspace=build_space(self.seed),
            name=f"{METHOD_ID}-{self.instance_id}-s{self.seed}",
            output_directory=self.output_dir,
            deterministic=True,
            n_trials=int(n_trials),
            n_workers=1,
            seed=int(self.seed),
            crash_cost=NONDELIVERY_COST,
            use_default_config=False,
        )
        return BlackBoxFacade(
            scenario,
            target_function=None,
            intensifier=BlackBoxFacade.get_intensifier(scenario, max_config_calls=1),
            overwrite=True,
            logging_level=40,
        )

    # ---- online loop -------------------------------------------------------
    def propose(self) -> np.ndarray:
        """Next online CFD design. Only valid when no action is unresolved."""
        if self._pending is not None:
            raise RuntimeError("an online action is already unresolved")
        self._pending = self._reserved if self._reserved is not None else self._smac.ask()
        self._reserved = None
        return config_to_z(self._pending.config)

    def observe(self, outcome: Outcome) -> None:
        if self._pending is None:
            raise RuntimeError("no unresolved online action to observe")
        tv = outcome.to_trial_value()
        self._smac.tell(self._pending, tv, save=False)
        self._observed.append(
            {"index": len(self._observed), "z": config_to_z(self._pending.config).tolist(),
             "delivered": bool(outcome.delivered),
             "value": (float(outcome.value) if outcome.delivered else None),
             "reason": outcome.reason, "outcome_kind": outcome.outcome_kind,
             "cost": float(tv.cost), "status": int(tv.status)}
        )
        self._pending = None

    @property
    def n_observed(self) -> int:
        return len(self._observed)

    # ---- recommendation (spec section 6.2) --------------------------------
    def recommendation(self) -> tuple[np.ndarray, str]:
        """Best delivered online design; before the first delivery, the next
        unqueried SMAC proposal (reserved so it becomes the next online query)."""
        delivered = [r for r in self._observed if r["delivered"]]
        if delivered:
            best = max(delivered, key=lambda r: (r["value"], -r["index"]))
            return np.asarray(best["z"], dtype=np.float64), "best_delivered"
        if self._pending is not None:
            return config_to_z(self._pending.config), "pending_action"
        if self.n_observed >= self.n_trials:
            return self._terminal_recommendation(), "replay_clone_proposal"
        if self._reserved is None:
            self._reserved = self._smac.ask()
        return config_to_z(self._reserved.config), "reserved_next_proposal"

    def _terminal_recommendation(self) -> np.ndarray:
        """All 32 grades failed, so every cost ties at 1.0 and SMAC's incumbent would
        be a known-failed design. Build a disposable clone, replay the committed
        grades, ask once, and discard it without telling (spec section 6.4)."""
        clone = Smac3BlackBoxEpisode(
            instance_id=self.instance_id, seed=self.seed,
            output_dir=self.output_dir / "_disposable_clone",
            n_trials=self.n_trials + 1,
        )
        for rec in self._observed:
            clone.propose()
            clone.observe(Outcome(delivered=rec["delivered"], value=rec["value"],
                                  reason=rec["reason"], outcome_kind=rec["outcome_kind"]))
        return config_to_z(clone._smac.ask().config)

    # ---- replay -----------------------------------------------------------
    @classmethod
    def replay(cls, instance_id: str, seed: int, output_dir: Path,
               records: Sequence[dict[str, Any]]) -> "Smac3BlackBoxEpisode":
        """Rebuild deterministically from the append-only WingBench trace. Aborts on
        any divergence; never coerces, skips or repairs."""
        ep = cls(instance_id=instance_id, seed=seed, output_dir=output_dir)
        for i, rec in enumerate(records):
            z = ep.propose()
            want = np.asarray(rec["z"], dtype=np.float64)
            if not np.array_equal(z, want):
                raise RuntimeError(f"replay divergence at online index {i}")
            ep.observe(Outcome(delivered=bool(rec["delivered"]), value=rec.get("value"),
                               reason=rec.get("reason", ""),
                               outcome_kind=rec.get("outcome_kind", "")))
        return ep

    def state(self) -> dict[str, Any]:
        return {"method_id": METHOD_ID, "adapter_version": ADAPTER_VERSION,
                "instance_id": self.instance_id, "seed": int(self.seed),
                "n_trials": int(self.n_trials), "observed": self._observed}
