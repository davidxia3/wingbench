# Analysis

This folder turns the tables in `data/` into the tables, figures and numbers in
the paper, and checks the counts the paper reports along the way.

Run everything:

```bash
bash run_all.sh
```

It reads the tables in `data/`, writes tables to `out/`, writes figures to
`figures/`, and writes the number files in `paper/`. It takes a few minutes. You need Python 3.11
with `pandas`, `matplotlib`, `numpy`, `scipy` and `scikit-learn`.

## What each script does

| Script | What it does |
| --- | --- |
| `01_load.py` | Turns the baseline rows of `data/` into the working tables the next scripts read, such as one row per evaluation (`out/grades.csv`) and the recommendation curves (`out/curves.csv`). Also checks them and reports any problem |
| `02_yield.py` | How often each program produced a usable wing, overall and as the run goes on |
| `03_recommendation_curves.py` | How good each program's answer was after 1 simulation, after 2, after 4, and so on |
| `04_discovery_vs_recommendation.py` | Whether a program found a good wing but failed to choose it as its answer |
| `05_paper_macros.py` | Writes the paper's number macros. `paper/main.tex` embeds a copy of each macro file, so after a rerun compare the two |
| `06_failure_predictability.py` | The offline failure-classifier study on the SuperWing records |
| `07_solver_error.py` | The early-stop outcome: how fast it fails, whether it repeats, and what removing it would change |
| `08_recommendation_curves_all.py` | The recommendation-delivery figure for all five baselines |
| `09_failure_transfer.py` | Trains the failure classifiers on the dataset records and tests them on the designs the optimizers actually proposed |
| `10_smac3_numbers.py` | Every SMAC3 number in the paper, and the totals that combine SMAC3 with the other baselines |
| `11_agent_endpoints.py` | Agent delivery, quality, and paired comparisons, with the paper's agent tables and number macros |
| `12_agent_cfd_outcomes.py` | Checks the outcome of every agent-study CFD request |
| `13_cfd_cost.py` | Processor-core hours per CFD evaluation over both studies |
| `14_wing_meshing_examples.py` | Draws Figure 1, the delivered and unmeshable wing examples (needs `cst_modeling3d`) |
| `15_confirmatory_contrasts.py` | The six exploratory baseline contrasts of Section 6.1: seed-block estimates, sign-flip tests, bootstrap intervals, and Holm correction |
| `16_marginal_intervals.py` | An interval for every delivery rate (exact binomial) and conditional mean (bootstrap) the paper reports, and the agent contrasts at query 4 with the two methods' episodes resampled independently, the two tables in Appendix A |
| `17_appendix_tables.py` | Every result table of the full-results appendix, per checkpoint, per method and task, per outcome type, and per episode, written straight into `paper/app_results.tex` |
| `18_wing_gallery.py` | Renders the best delivered wing of every method and task and tabulates their geometry, the gallery appendix (needs `cst_modeling3d`, like `14`) |
| `19_appendix_figures.py` | Redraws the outcome-composition, online-yield, and discovery-versus-recommendation figures in the paper's style for the supplementary-figures appendix |
| `20_resource_accounting.py` | Actual surrogate usage, online/private CFD counts, agent sessions/tokens/reference charges and available CFD timing at explicit horizons; writes `out/resources/` and `paper/app_resources.tex` |
| `extract_surrogate_usage.py` | Optional regeneration of `data/surrogate_usage.csv` from the original campaign archives, using `--archive-root PATH`; not needed for the released analyses |
| `render_wing.py` | Builds a wing surface from its 38 design variables, used by `14` and `18` |
| `common.py` | Shared paths, names and loaders used by the other scripts |

## Rules the scripts follow

**Resource accounting does not infer matched-resource rankings.** Run
`python3.11 analysis/20_resource_accounting.py` from the repository root to
reproduce just the resource tables. It checks that final-horizon totals
reconcile to all 240 episodes, 6,000 online and 629 private grades, and all
recorded model usage and CFD timing. The query-4 and query-32 rows for a method
overlap and must not be added. Session time includes tools and local code; it
is not pure model inference or end-to-end wall time. Missing CPU/GPU and elapsed
time measurements remain missing. Source hashes document the surrogate extract;
the archives themselves are not distributed.

**Every figure says which version of the data it came from.** The baseline
records carry a version number, set in `common.py`. Each figure and table is
stamped with it.

**Each evaluation is one row, and every row counts.** A wing either produced a
usable answer or it did not. When it did not, we leave it as "no answer". We
never guess a value, and we never drop the wing from the count.

**The recommendation curves are the recorded recommendations.**
`data/recommendations.csv` holds the wing each method recommended at each
checkpoint and its grade, including the cases where a recommendation reused an
online result.

**The paper is `paper/main.tex`.** Build it from `paper/` with `latexmk -pdf main.tex`. `paper/smac3_numbers.tex` is written by `10_smac3_numbers.py`, `paper/agent_endpoint_numbers.tex` by `11_agent_endpoints.py`, and `paper/cfd_cost_numbers.tex` by `13_cfd_cost.py`. The last two are embedded in `main.tex`.


## Revision evidence

`python3.11 analysis/21_revision_evidence.py` verifies frozen extract hashes,
joins every handoff to its CFD grade, and reproduces the flow-condition audit,
mesh-control counts, all 270 common-horizon agent transitions, explicit
placeholder mentions and bounded-recovery counts. Outputs are in
`analysis/out/revision_evidence/`. These are descriptive audits, not new runs
or causal tests. The optional `extract_revision_evidence.py --archive-root ...`
rebuilds extracts from the original local campaign/audit archives, which are
not distributed. Public extracts suffice for the reported summaries.

The historical `15_confirmatory_contrasts.py` filename is retained for
compatibility. Its three-task pooling is **exploratory**, because the recovered
frozen plan (`configs/benchmark/goldilocks_v2_phase4_analysis_plan_v1.json`)
specified low/nominal as the core population and high as a separate stress
track. No pre-results amendment justifying the broader pool was recovered.
