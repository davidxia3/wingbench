# Data

Every number in the paper comes from the tables in this folder. They link to
each other through the `episode` and `wing` columns. An episode is one
optimization run.

| File | One row per | Rows |
| --- | --- | --- |
| `episodes.csv` | episode | 240 |
| `evaluations.csv` | CFD evaluation, either an online query or a private grade | 6,629 |
| `recommendations.csv` | recommended wing graded at a checkpoint | 1,410 |
| `wings.csv` | wing evaluated by the four surrogate-assisted baselines | 3,294 |
| `cfd_attempts.csv` | mesh or flow-condition attempt in the baseline study | 13,248 |
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
| `seed` | the episode's random seed |
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

The table covers the wings of the four surrogate-assisted baselines. SMAC3 and
agent wings appear in the other tables by their hash only.

## `cfd_attempts.csv`

One row per mesh or flow-condition attempt of a baseline-study evaluation, with
the `evaluation` and `episode` it belongs to, the `stage` (`mesh` or `solve`),
the flight `condition`, the `termination_reason`, the process `returncode`, the
`outcome` of the attempt, and `wall_seconds`.

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
- The agent export left out the scores of the 90 private grades made before the
  first query. Their outcome type is known, so `delivered` is set, but `ld` is
  empty. The export also left out the reason codes of all agent private grades.
- SMAC3's `cfd_core_seconds` and `timed_evaluations` include two checkpoint-32
  grades that were replaced when two SMAC3 episodes were run again. The paper's
  cost numbers use these totals.
- The 44 GB of raw CFD output, the optimizers' full logs, and the agents' full
  execution traces are not included.
