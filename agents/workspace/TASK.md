# Wing design task

Find a wing geometry with the best geometric mean of lift-to-drag across three
transonic conditions, trimmed to a fixed target lift coefficient.

| | |
|---|---|
| design vector | 38 physical parameters, `source/PARAMETERIZATION.md` |
| target lift coefficient | see `source/task.json` |
| Mach conditions | see `source/task.json` |
| objective | geometric mean of L/D over the three conditions |
| score eligibility | only a profile where all three conditions converge and trim |
| nominal evaluation horizon | 32 expensive evaluations |
| official surrogate budget | 4096 evaluations |

## How an evaluation happens

You do not run the expensive evaluation yourself and there is no way to launch,
poll or retry one from here. When you are ready, you hand over exactly one wing
with `python3 source/campaign.py hand-off` and finish your turn. The campaign
manager takes it from there and returns the result and every file it produced to
`observations/` before resuming you.

An evaluation can come back complete, or incomplete with no aggregate score. An
incomplete profile is a real outcome and is not a mistake on your part.

Two different things can be recorded:

* the **wing you hand over** is the next experiment;
* the **wing you nominate** (`campaign.py nominate`) is your current best
  recommendation.

They do not have to be the same wing.

## What is in here

| path | what it is |
|---|---|
| `source/superwing_public_dataset_v1.csv` | the SuperWing wing table, one row per wing, including the runs that failed |
| `source/running_info.jsonl` | the original run records: every flight condition tried on every wing, with its operating conditions, state and recorded outputs |
| `source/conditions.jsonl` | the same records as one row per (wing, condition), every field copied |
| `source/conditions.csv` | those rows flattened to scalar fields |
| `source/CONDITIONS_README.md` | field mapping, units, what each state means, and how absent slots are represented |
| `source/load_conditions.py` | a loader example that prints one converged condition, one failed condition and one absent slot |
| `source/PARAMETERIZATION.md` | what each of the 38 parameters means |
| `source/normalization.json` | population mean and standard deviation per parameter |
| `source/schema.json` | the fields an evaluation returns |
| `source/render_wing.py` | builds and draws a wing from a 38-D vector |
| `source/geometry.py` | optional normalization conversions and a dataset loader |
| `source/campaign.py` | task, budget, validation, official surrogate, handoff, nomination |
| `work/` | yours: code, models, candidates, plots, notes |
| `observations/` | evaluation results and their files, written for you |

`work/` is writable. `source/` and `observations/` are not.

## Analysis validity

Local models are useful search tools, but their diagnostics are not returned
evaluation results. When fitting or validating one:

* keep every condition row from the same `running_id` in the same train or
  validation split; a row split leaks the same wing across both sets;
* set and record random seeds before constructing the split or model, and save
  enough information to reproduce the split;
* evaluate the quantity used to choose a wing. Label condition-level or
  standardized errors as proxy diagnostics rather than held-out-wing objective
  performance;
* do not let a sentinel value or empty-filter fallback silently rank candidates
  that violate a stated feasibility or trim threshold. Either revise the rule
  explicitly or use a documented fallback;
* retain the local predictions for shortlisted candidates, and keep them
  distinct from official-surrogate results and returned expensive evaluations.

For a long surrogate batch, inspect status once if needed. To wait, use
`campaign.py surrogate-wait`; after it reaches a terminal state, use
`campaign.py surrogate-fetch`. Do not repeatedly poll `surrogate-status`.

## Domain

A geometry is admissible when every parameter lies inside the unit box after the
normalization in `source/normalization.json`. `campaign.py validate` tells you,
and nothing is silently clipped.
