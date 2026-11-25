"""No CFD: compare adapter decisions against the unmodified frozen methods."""
from types import SimpleNamespace
import unittest
import numpy as np

from benchmark.common import load_tasks, load_box, ROOT
from benchmark.examples.frozen_baseline import FrozenBaseline
from wingbench.benchmark.multifidelity_protocol import resolve_protocol, physical_bounds
from wingbench.optimizers.surrogate_assisted import (
    Phase3PilotEpisode, FSD_METHOD, CONVENTIONAL_METHOD, OnlineFeedback, synthetic_cheap_objective,
)


@unittest.skipUnless(np.__version__ == "2.0.2", "requires original frozen numpy==2.0.2")
class FrozenAdapterTests(unittest.TestCase):
    def test_decisions_and_budgets_match_frozen_implementation(self):
        bounds = physical_bounds(ROOT, resolve_protocol(ROOT))
        objective = synthetic_cheap_objective(bounds)
        for method_id in (FSD_METHOD, CONVENTIONAL_METHOD):
            with self.subTest(method=method_id):
                count = [0]
                def cheap(rows):
                    count[0] += len(rows)
                    return [SimpleNamespace(score=float(score) if np.isfinite(score) else None)
                            for score in objective(np.asarray(rows))]
                ctx = SimpleNamespace(cheap_query=cheap)
                adapter = FrozenBaseline(load_tasks()["nominal"], 1, load_box(), method_id=method_id)
                original = Phase3PilotEpisode(method_id=method_id, seed=1, bounds=bounds)
                adapter.start(ctx)
                original.prepare_for_next_online_query(objective)
                self.assertEqual(adapter.recommend(), original.recommendation()["geometry_38d"])
                for query in range(1, 5):
                    proposed = adapter.propose(ctx)
                    original.prepare_for_next_online_query(objective)
                    reference = original.next_online_candidate()
                    self.assertEqual(proposed, reference["geometry_38d"])
                    delivered = query % 2 == 0
                    result = SimpleNamespace(outcome="pass" if delivered else "conclusive_failure",
                                             delivered=delivered, ld=30.0 if delivered else None, reason_codes=())
                    adapter.observe(result)
                    original.observe_online(OnlineFeedback(reference["candidate_id"],result.outcome,result.delivered,result.ld))
                    self.assertEqual(adapter.recommend(),original.recommendation()["geometry_38d"])
                self.assertEqual(count[0],512 if method_id == FSD_METHOD else 4096)


if __name__ == "__main__":
    unittest.main()
