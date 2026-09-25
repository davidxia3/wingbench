# WingBench

A benchmark for wing-design optimizers that use a cheap learned model together
with expensive computational fluid dynamics (CFD), where the CFD often returns
no score at all.

Five baseline optimizers ran 150 episodes with
32 sequential CFD queries each. Three AI-agent methods ran 90 episodes for a
four-query comparison, and the open-ended method continued for 28 more
queries in 30 episodes. Together, the two studies recorded 6,629 graded CFD
requests. The paper reports recommendation delivery and aerodynamic quality,
including the distinction between an online query and a separate private
recommendation grade.

## Repository contents

```text
data/                every episode, CFD evaluation and graded recommendation of both studies
analysis/            scripts for the paper's tables, figures, and numerical checks
paper/               the ICLR submission manuscript and its LaTeX dependencies
wingbench/           baseline optimizers, surrogate wrapper, and episode rules
scripts/cfd/         CFD grading code
configs/             benchmark and CFD protocols
agents/              the prompts and task files given to the AI agents
```

`data/` holds a few linked tables, with one row per episode, per CFD
evaluation, per graded recommendation, per wing, and per CFD attempt, plus the
SuperWing records used by the failure-prediction study.
[`data/README.md`](data/README.md) explains every column.

## Rebuilding the numbers

You need Python 3.11 and the packages in `requirements.txt`. CFD reruns also
require the separately licensed simulator and model weights described below.

```bash
bash analysis/run_all.sh
```

`analysis/run_all.sh` rebuilds the baseline analyses, the agent-study
analyses, and the CFD cost numbers. It writes `analysis/out/`,
`analysis/figures/`, and the number files in `paper/`, and it checks the counts
the paper reports along the way. The files in
`analysis/out/` are the outputs as published, so a rerun can be compared
against them.

Figure 1 is drawn by `analysis/14_wing_meshing_examples.py`, and Figure 2 is
`paper/fig_agent_pipeline.tex`. The paper source is `paper/main.tex`. Build it
with `latexmk -pdf main.tex` from `paper/`. Its numerical macros are embedded
in the file, so the typeset paper does not depend on a prior analysis run.

The tables in `data/` were compiled from the experiment's records, which include
44 GB of raw CFD output that is not included here. The code that ran the agents
and sent CFD jobs to the cloud and cluster machines only works on those machines
and is not included. Neither are the agents' full execution traces. `agents/`
holds the prompts and task files the agents were given.

## License

MIT. See [`LICENSE`](LICENSE).
