# Data

The tables in this folder hold the CFD records behind the paper's tables and
numbers. Claims that come from the agents' execution traces, such as the count
of placeholder plans, are not in them. The tables link to each other through
the `episode` and `wing` columns. An episode is one
optimization run.

| File | One row per | Rows |
| --- | --- | --- |
| `episodes.csv` | episode | 240 |
| `evaluations.csv` | CFD evaluation, either an online query or a private grade | 6,629 |
| `recommendations.csv` | recommended wing graded at a checkpoint | 1,410 |
| `wings.csv` | wing evaluated in either study | 5,200 |
| `cfd_attempts.csv` | mesh or flow-condition attempt in the baseline study | 13,248 |
| `cfd_conditions.csv` | flow condition of an evaluation that has a result document | 18,723 |
| `agent_nominations.csv` | wing an agent named as its current recommendation | 1,782 |
| `agent_turns.csv` | model session of an agent episode, with its usage and cost | 1,805 |
| `weight_sensitivity_refits.csv` | recommendation that the inconclusive-outcome weight can change | 103 |
| `weight_sensitivity_wings.csv` | design that the weight chooses between at checkpoint 32 | 4 |
| `superwing/` | the SuperWing records used by the failure-prediction study | |

## `episodes.csv`

| Column | Meaning |
| --- | --- |
| `episode` | `B000`–`B149` for the baseline study, where `B120`–`B149` are SMAC3, and `A000`–`A089` for the agent study |
| `study` | `baseline` or `agent` |
| `method` | the method, named as in the paper |
| `task` | `low`, `nominal` or `high` load |
| `target_cl` | the target lift coefficient of the task |
| `seed` | the random seed of a baseline episode. For an agent episode it is only a label that pairs episodes across methods and does not set the model's sampling |
| `online_queries` | how many CFD queries the method made: 32, or 4 in the agent comparison |
| `cfd_core_seconds` | processor-core seconds of CFD run for the episode, which is wall time times the 16 cores of each job |
| `timed_evaluations` | how many of the episode's evaluations that time covers |
| `original_id` | the name the episode had while the experiment ran |

## `evaluations.csv`

| Column | Meaning |
| --- | --- |
| `evaluation` | `E0001`–`E6629` |
| `episode`, `study`, `method`, `task`, `seed` | as in `episodes.csv` |
| `kind` | `online` for a query whose result the method saw, `private` for a grade of a recommended wing that the method never saw |
| `query` | the online query number, 1–32 |
| `checkpoint` | for a private grade, the checkpoint of the recommendation |
| `wing` | the wing's identifier, a SHA-256 hash of its design |
| `delivered` | whether CFD returned a valid score |
| `ld` | the score when delivered, the geometric mean of L/D over the three flight conditions |
| `outcome` | `pass` (delivered), `partial` (some but not all flight conditions gave a result), `undetermined` (no condition gave a result) or `conclusive_failure` (the geometry was rejected) |
| `reasons` | the grader's reason codes, separated by `;` |
| `recorded_at` | when the result was recorded, in UTC |
| `original_id` | the name the evaluation had while the experiment ran |

The reason codes, with the names the paper uses for them:

| Code | In the paper |
| --- | --- |
| `complete_score_eligible` | delivered a score |
| `not_run_unmeshable` | mesh generator rejected geometry |
| `not_run_mesh_inconclusive` | mesh generator stopped without a mesh |
| `inconclusive_timeout` | flow condition reached time limit |
| `inconclusive_infrastructure` | solver process stopped with an error |
| `undetermined` | other errors |
| `grade_exit_2`, `grade_exit_134`, `grader_exited_without_result` | the grading job stopped before writing a result (agent study only) |

## `recommendations.csv`

| Column | Meaning |
| --- | --- |
| `episode`, `study`, `method`, `task`, `seed` | as in `episodes.csv` |
| `checkpoint` | how many online queries the method had made when it recommended the wing |
| `wing` | the recommended wing |
| `delivered`, `ld` | the recommendation's grade |
| `graded_by` | `online` when the wing already had an online result, which is reused, or `private` when it needed its own grade, which is a row of `evaluations.csv` |

## `wings.csv`

The column `wing` and the 38 design variables `x00`–`x37`, in physical units.

| Columns | Variable |
| --- | --- |
| `x00` | leading-edge sweep, in degrees |
| `x01` | tip dihedral, in degrees |
| `x02` | inner dihedral, in degrees |
| `x03` | aspect ratio |
| `x04` | taper ratio |
| `x05` | kink location, as a fraction of the span |
| `x06` | root chord adjustment |
| `x07`–`x09` | thickness factors at three stations |
| `x10`–`x12` | camber at three stations |
| `x13`–`x16` | twist at four stations |
| `x17`–`x26` | upper-surface CST weights |
| `x27`–`x36` | lower-surface CST weights |
| `x37` | root thickness ratio |

The table covers the 3,294 wings of the four surrogate-assisted baselines, the
741 SMAC3 wings, and the 1,165 agent wings whose grader result documents were
kept. The SMAC3 coordinates were rebuilt from the coefficient files in the
grader's evidence and each was checked against its fingerprint. Five agent
wings whose requests stopped before a document was written appear in the other
tables by their hash only. 41 of the agent wings lie on the
edge of the design box. The campaign accepted and graded them, but the released
episode machine rejects a proposal on the edge, so `benchmark/run_episode.py`
will not accept them as proposals.

## `cfd_attempts.csv`

One row per mesh or flow-condition attempt of a baseline-study evaluation, with
the `evaluation` and `episode` it belongs to, the `stage` (`mesh` or `solve`),
the flight `condition`, the `termination_reason`, the process `returncode`, the
`outcome` of the attempt, and `wall_seconds`.

## `cfd_conditions.csv`

One row per flow condition of every evaluation whose grader result document
was kept, which is 1,297 of the 1,336 agent evaluations, 4,252 of the 4,333
non-SMAC3 baseline evaluations, and 692 of the 960 SMAC3 evaluations. The SMAC3
documents come from its publication ledger, where each is stored encoded. The
other evaluations stopped before a document was written, their documents were
lost, or the only document kept belongs to a different attempt at the same
wing.

| Column | Meaning |
| --- | --- |
| `evaluation`, `episode`, `study`, `method`, `task` | as in `evaluations.csv` |
| `mesh_outcome` | the mesh step's outcome for the whole evaluation |
| `condition` | `mach_q1`, `mach_q2` or `mach_q3`, the three Mach numbers in order |
| `mach`, `cl_target` | the flight condition |
| `outcome` | `valid`, `invalid` or `inconclusive` for this condition |
| `reasons` | the grader's reason codes for this condition, separated by `;` |
| `cl`, `cd`, `ld` | the computed lift, drag, and lift-to-drag ratio |
| `err_cl` | the lift oscillation over the last iterations, which must stay below 0.0005 |
| `realized_aoa_deg` | the angle of attack that reached the target lift |
| `seed_alpha0_deg` | the angle of attack the lift matching started from |
| `solvecl_iterations`, `solvecl_l2convergence`, `solvecl_time_seconds` | the lift-matching loop's iteration count, final residual, and run time |

Measurements are empty when a condition was not solved or gave no usable
result. The lift is accepted when it is within 0.02 of the target.

## `agent_nominations.csv`

Every time an agent recorded a recommendation with `campaign.py nominate`. The
campaign asked for one at the checkpoints 0, 1, 2 and 4, and agents could also
record one at any time during their search. The 32-query continuation asked
for none, so the agents' standing recommendations at 8, 16 and 32 queries are
the last nomination made at or before that many results.

| Column | Meaning |
| --- | --- |
| `episode`, `study`, `method`, `task` | as in `episodes.csv` |
| `sequence` | the order of the nomination within its episode |
| `kind` | `checkpoint` when the campaign asked for it, `during_search` when the agent recorded it on its own |
| `results_seen` | how many online CFD results the agent had seen when it made the nomination |
| `decision` | the campaign's name for the turn |
| `wing` | the nominated wing's hash |
| `recorded_at` | when the campaign recorded it, in UTC |

## `agent_turns.csv`

One row per model session the campaign launched, with what the chat loop
reported about it. A research session ran until the agent handed over a wing,
a review session handled a checkpoint, and a reflection session wrote one
reflection. The planning method's planner, evaluator and executor calls were
made inside its research sessions and are counted in them.

| Column | Meaning |
| --- | --- |
| `episode`, `study`, `method`, `task` | as in `episodes.csv` |
| `launch` | the session's number within its episode |
| `kind` | `research`, `review` or `reflection` |
| `decision` | the campaign's name for the turn |
| `wall_seconds` | how long the session ran |
| `input_tokens`, `output_tokens`, `cache_read_input_tokens` | the token counts the model provider reported, summed over the session's calls |
| `model_requests` | how many model calls the session made |
| `cost_usd` | the session's cost at the provider's list prices |
| `is_error` | whether the session ended with an error |
| `ended_at` | when it ended, in UTC |

Eleven sessions have no token counts because the provider did not report them.

## The weight-sensitivity tables

These hold the evidence for the appendix on the inconclusive-outcome weight.
`weight_sensitivity_refits.csv` has one row for each of the 103 checkpoint
recommendations the weight can change, naming the design that weights 0, 0.5
and 1 each pick. `weight_sensitivity_wings.csv` has the four designs the weight
chooses between at checkpoint 32, with their 38 variables and CFD outcome. The
campaign graded two of them. The other two were graded afterwards, once the
refit showed which designs a weight of 1 would have recommended, so that the
appendix could say whether the weight changes what gets delivered and not only
what gets recommended.

## `superwing/`

SuperWing is the wing dataset the benchmark is built on. The paper cites it.

- `superwing_records.csv` has one row per wing for all 4,544 wings: the same 38
  variables as `wings.csv`, whether the mesh step worked, and the result of each
  of the wing's 8 flight conditions. It was built from SuperWing's complete run
  records. The public release contains only the converged results.
- `running_info.jsonl` is that complete run record.
- `cruise_ld_percentiles.json` holds the dataset's cruise-band lift-to-drag
  percentiles.

Of the 36,344 tests, 28,856 succeeded. 4,563 failed because the lift kept
changing and never settled, 2,788 failed because the simulation blew up, and 137
were never run. The often-used table of 4,239 wings lists only the wings where at
least one of the 8 tests succeeded, so it leaves out the 305 wings that failed
every test. To study why wings fail, start from the 4,544 wings here. The column
`any_success` is 0 for those 305.

## Extra details

- The SMAC3 records name the reason for each nondelivery but not the outcome
  type, so SMAC3's `outcome` column is empty. Its run controller recorded 18
  nondeliveries without a reason. They carry `undetermined`.
- Three SMAC3 evaluations have no wing hash, because their evidence was lost.
  The other SMAC3 wings have their coordinates in `wings.csv`.
- The agent export left out the scores of the 90 private grades made before the
  first query and the reason codes of all 136 agent private grades. Both were
  filled in from the grader's result documents. The reason codes follow the
  rule the export used for the other rows, checked against every evaluation
  that has both a code and a document. The 39 agent requests that stopped
  before a document was written keep their recorded outcome and no score.
- SMAC3's `cfd_core_seconds` and `timed_evaluations` include two checkpoint-32
  grades that were replaced when two SMAC3 episodes were run again. The paper's
  cost numbers use these totals.
- The 44 GB of raw CFD output, the optimizers' full logs, and the agents' full
  execution traces are not included.
