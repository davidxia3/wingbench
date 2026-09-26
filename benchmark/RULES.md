# WingBench rules, version 1.0

These are the rules the paper's study ran under. A result is comparable to
the reference results only if it follows them. Later versions will carry a
new number, and results from different versions are reported separately.

## The task

A design is a wing described by 38 numbers. The benchmark standardizes each
number with the SuperWing dataset and searches the box from -3.5 to 3.5 in
every coordinate. A wing is scored at a fixed target lift coefficient and at
three Mach numbers, 0.7853, 0.8195 and 0.8530, with Reynolds number 20
million. The score is the geometric mean of the three lift-to-drag ratios.

There are three tasks. They differ only in the target lift coefficient, which
is 0.15 (low load), 0.41 (nominal load) or 0.66 (high load).

A CFD evaluation delivers a score only if all three conditions return a valid
result, the lift is within 0.02 of the target, every lift-to-drag ratio is
above 0 and at most 40, and the angle of attack is between -10 and 25
degrees. Otherwise the evaluation returns a failure type and no score.

## Budgets

One episode is one optimization run on one task. An episode may use at most

- 4,096 surrogate evaluations. One surrogate evaluation scores one wing at all
  three Mach numbers, including the angle-of-attack adjustment that reaches
  the target lift.
- 32 online CFD evaluations, one at a time. The optimizer submits one wing,
  receives its score or failure type, and only then chooses the next.

At the checkpoints 0, 1, 2, 4, 8, 16 and 32 online queries, the optimizer
nominates the one wing it would hand to a user. That wing is graded
privately. The optimizer never sees the private grade, and the grade costs no
query. A nominated wing that already has an online result reuses that result.

## What an optimizer may use

Every optimizer may use the surrogate, the SuperWing records in
`data/superwing/`, and the 62 earlier CFD results in `configs/benchmark/`
that the reference baselines started from. Nothing may be shared between
episodes.

Each track has two variants.

- Closed. Only the information above.
- Open. Any public information, including the released WingBench records.

A submission states which variant it ran. Reference results are closed.

## Repeated designs and failures

A wing that was already evaluated in the same episode returns the recorded
result again and still costs a query.

A failure of the machine is not a failure of the wing. If a worker is lost,
a job is killed by the system, or a result file is never written, the
evaluation is retried and does not cost a query. Failures of the wing itself
are outcomes and cost the query. They are a rejected geometry, a mesh
generator that stops without a mesh, a flow condition that reaches its time
limit, and a solver process that stops with an error.

## Time limits and hardware

Meshing has a 30-minute limit. Each flow condition has a two-hour limit. Both
are measured in wall-clock time. The reference results ran one CFD job at a
time on an AWS m7a.4xlarge machine with 16 cores and 64 GB of memory, inside
the container described in `environment/`. About one third of the reference
nondeliveries were set by a time limit, so a slower machine produces more
failures for the same wings. Official comparisons require an m7a.4xlarge or a
faster machine. A later version will replace the time limits with iteration
limits so that results no longer depend on the machine.

## Recording and verification

The runner writes an append-only trace of every surrogate call, online
query, result and nomination. `wingbench/benchmark/replay.py` checks a trace
independently for budget use, information leakage and consistent hashes.
Private grades are kept outside the optimizer's reach until the episode
ends, and their hashes enter the trace when they are produced.

A submission contains the trace of every episode, the records in the
formats of `data/`, and the 38 coordinates of every evaluated wing. The
benchmark is run on the honor system. There is no central grading service.

## Reporting

For each task and checkpoint, report recommendation delivery, which is the
share of episodes whose nominated wing received a valid score, and
conditional quality, which is the mean score of the delivered nominations.
Also report online delivery, the share of online queries that returned a
score. `score.py` produces these next to the six reference methods, the five
baselines and open-ended research, with bootstrap intervals.

Run ten seeds per task. For a language-model agent a seed is only a label
that pairs its episodes with the other methods.

## Tracks

- Full. Three tasks, ten seeds, 32 queries. Compares with every reference
  result.
- Lite. One task, five seeds, 8 queries. Compares with the reference results
  at checkpoint 8.
- Recommendation only. Search any way you like and grade only the final
  nominated wing per episode with the container. Compares with the reference
  recommendations at checkpoint 32.

Each track is run closed or open.
