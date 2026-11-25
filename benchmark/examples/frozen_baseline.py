"""Thin runner adapter for the published surrogate-assisted baseline code.

Uses the original 62-record context, original generation schedule and method
implementation. The original numpy/cma version checks remain enforced.
"""
from benchmark.optimizer_api import Optimizer
from wingbench.optimizers.surrogate_assisted import Phase3PilotEpisode, OnlineFeedback, FSD_METHOD, CONVENTIONAL_METHOD
from wingbench.benchmark.multifidelity_protocol import physical_bounds, resolve_protocol
from benchmark.common import ROOT


class FrozenBaseline(Optimizer):
    def __init__(self, task, seed, box, method_id=FSD_METHOD, **options):
        super().__init__(task, seed, box, **options)
        if method_id not in (FSD_METHOD, CONVENTIONAL_METHOD):
            raise ValueError("resource pilot covers feedback-guided and selection-only CMA-ES")
        self.method = Phase3PilotEpisode(method_id=method_id, seed=seed,
                                        bounds=physical_bounds(ROOT, resolve_protocol(ROOT)))
        self.name = "CMA-ES feedback-guided" if method_id == FSD_METHOD else "CMA-ES selection only"
        self.pending = None

    def _objective(self, geometries):
        return [row.score if row.score is not None else float("nan")
                for row in self.ctx.cheap_query(geometries.tolist())]

    def start(self, ctx):
        self.ctx = ctx
        self.method.prepare_for_next_online_query(self._objective)

    def propose(self, ctx):
        self.ctx = ctx
        self.method.prepare_for_next_online_query(self._objective)
        self.pending = self.method.next_online_candidate()
        return self.pending["geometry_38d"]

    def observe(self, result):
        self.method.observe_online(OnlineFeedback(
            candidate_id=self.pending["candidate_id"], outcome_kind=result.outcome,
            delivery=result.delivered, conditional_value=result.ld, reason_codes=result.reason_codes))

    def recommend(self):
        return self.method.recommendation()["geometry_38d"]
