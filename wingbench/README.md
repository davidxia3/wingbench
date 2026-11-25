# wingbench

The code that produced the benchmark data.

| Module | What it is |
| --- | --- |
| `benchmark/episode.py` | The episode rules: budgets, checkpoints, outcome records, and the replay used to verify every run |
| `benchmark/replay.py` | Replays a saved run and recomputes its checkpoint answers |
| `benchmark/multifidelity_protocol.py` | Loads the frozen protocol and the physical bounds of the 38 design variables |
| `optimizers/surrogate_assisted.py` | The four surrogate-assisted baselines: CMA-ES without feedback, CMA-ES with selection only, CMA-ES with feedback, and differential evolution with feedback |
| `optimizers/simple_multifidelity_control.py` | The rule that picks the next CFD evaluation from a batch of surrogate candidates |
| `optimizers/smac3.py` | The SMAC3 baseline, which uses CFD only |
| `surrogate/aerotransformer.py` | Wraps the pretrained AeroTransformer model and trims angle of attack to fixed lift |
| `protocol_context.py` | The fixed warm-start context every surrogate-assisted episode begins from |

The CFD grader is under `scripts/cfd/`. Its files are pinned by content in the
frozen protocol, so they keep their original paths.

The AeroTransformer weights and the ADflow environment are external. This
package documents the algorithms exactly as they ran; it does not bundle either.
