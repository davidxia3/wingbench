# WingBench

A benchmark for wing-design optimizers that use a cheap learned model together
with expensive computational fluid dynamics (CFD), where the CFD often returns
no score at all.

The repository has two sides.

- **The study.** The records, code, prompts, and analysis behind the paper.
  Five baseline optimizers ran 150 episodes with 32 sequential CFD queries
  each. Three AI-agent methods ran 90 episodes for a four-query comparison, and
  the open-ended method continued for 28 more queries in 30 episodes. Together
  the two studies recorded 6,629 graded CFD requests. This side is frozen. It
  is what the paper reports and what a reader checks.
- **The benchmark.** The `benchmark/` folder lets anyone plug in their own
  optimizer, run episodes on their own machine, verify the trace, and compare
  the result with the six reference methods. Its rules are in
  `benchmark/RULES.md`. There is no central grading service. Submissions work
  on the honor code, checked by an independent replay of every trace.

## Repository contents

```text
data/                every episode, CFD evaluation, graded recommendation, wing, and flow condition
analysis/            scripts for the paper's tables, figures, and numerical checks
paper/               the ICLR submission manuscript and its LaTeX dependencies
wingbench/           baseline optimizers, surrogate wrapper, and the episode machine
scripts/cfd/         the CFD grader, a fetch script for the SuperWing simulation files, and its patch
configs/             benchmark and CFD protocols, pinned to each other by fingerprint
agents/              the prompts and task files given to the AI agents
benchmark/           the rules, the runner, the verifier, the scorer, and the container recipe
```

`data/` holds linked tables, with one row per episode, per CFD evaluation, per
graded recommendation, per wing, per CFD attempt, and per flow condition, plus
the SuperWing records used by the failure-prediction study.
[`data/README.md`](data/README.md) explains every column.

The comparisons have different resource conditions. Four surrogate-assisted
baselines share 62 prior CFD records and final query budgets; SMAC3 is a
CFD-only reference. The three agents share source data, model, tools and query
caps, but not total model usage. Agent-versus-baseline results describe the
tested configurations: they do not establish a ranking under equal prior
information, computation or wall time. Recorded usage at query 4 and the final
horizon is in [`analysis/out/resources/by_method.csv`](analysis/out/resources/by_method.csv).
The paper's resource appendix defines the measurements and missing values.

## Rebuilding the numbers

You need Python 3.11 and the packages in `requirements.txt`.

```bash
bash analysis/run_all.sh
```

`analysis/run_all.sh` rebuilds the baseline analyses, the agent-study
analyses, the CFD cost numbers, and the six exploratory statistical contrasts. It
writes `analysis/out/`, `analysis/figures/`, and the number files in `paper/`,
and it checks the counts the paper reports along the way. The files in
`analysis/out/` are the outputs as published, so a rerun can be compared
against them. No CFD is needed.

Figure 1 is drawn by `analysis/14_wing_meshing_examples.py`, and Figure 2 is
`paper/fig_agent_pipeline.tex`. The paper source is `paper/main.tex`. Build it
with `latexmk -pdf main.tex` from `paper/`. Its numerical macros are embedded
in the file, so the typeset paper does not depend on a prior analysis run.

## Running the benchmark

Read [`benchmark/RULES.md`](benchmark/RULES.md), then
[`benchmark/README.md`](benchmark/README.md). The runner works today with
recorded outcomes, which tests the plumbing. A real submission needs the CFD
evaluator, which is rebuilt from
[`benchmark/environment/ENVIRONMENT.md`](benchmark/environment/ENVIRONMENT.md).
That path was run once on a fresh machine of the reference type and reproduced
a recorded evaluation exactly. The
[`scripts/cfd/README.md`](scripts/cfd/README.md) explains the grader and the
one fetch step it needs.

## What is not here

The raw CFD output of the studies, about 44 GB, is not included. The tables in
`data/` carry the per-condition measurements of every evaluation that has a
result document. The code that ran jobs on our cloud and cluster machines, the
controller that ran the agent sessions, and the agents' full execution traces
are not included. `agents/` holds the prompts and task files the agents were
given.

## License

MIT. See [`LICENSE`](LICENSE).


## First revision

The revision adds evaluator checks and an audit of agent behavior, together
with actual resource accounting. Reproduce the new summaries with
`python3.11 analysis/20_resource_accounting.py` and
`python3.11 analysis/21_revision_evidence.py`. The latter reads frozen extracts
and launches no CFD or model requests. Original grades are unchanged. Mesh
sensitivity results are diagnostic, and the three-task statistical contrasts
are explicitly exploratory. The adjoint comparison is in progress and is not
included in the reported results.
