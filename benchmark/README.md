# WingBench for competitors

This folder lets you plug your own optimizer into WingBench, run episodes on
your own machine, and compare your results with the six reference methods in
the released records. The six are the five baselines of the paper (CMA-ES no
feedback, CMA-ES selection only, CMA-ES feedback-guided, DE feedback-guided,
SMAC3) and the agent method Open-ended research.

`RULES.md` in this folder states the rules. This file explains the tools.

An episode works like this. Your optimizer may score up to 4,096 wings with
the released surrogate. It sends up to 32 wings to CFD, one at a time, and
sees each result before it picks the next wing. At the checkpoints 0, 1, 2, 4,
8, 16 and 32 CFD queries it names one wing as its recommendation. Each
recommendation is graded in private. Your optimizer never sees that grade.
The reported outcomes are recommendation delivery (did the recommended wing
get a valid score), conditional quality (its L/D when it did), and online
delivery (the share of your CFD queries that got a valid score). There are
three tasks, called low, nominal and high. They differ in the target lift
coefficient, which is 0.15, 0.41 and 0.66.

## What runs today and what needs more

| Part | Runs today | Needs |
| --- | --- | --- |
| `run_episode.py --evaluator recorded`, `verify.py`, `score.py`, the example optimizer | yes, on any machine with Python 3.11 | nothing beyond `requirements.txt` |
| `ctx.cheap_query` (the surrogate) | after you download the weights | the ATsurf_L weights and the `flowvae` module, see below |
| `run_episode.py --evaluator cfd` | yes, inside the container | the container, the SuperWing case scripts, ADflow, see below. Run once on a fresh m7a.4xlarge, where one recorded grade came back identical |

The `recorded` evaluator is a test of the plumbing. It can only answer wings
that the released records already graded. Results made with it are not a
submission. A submission is made with the `cfd` evaluator.

## Install

You need Python 3.11. From the repository root run

```bash
python3.11 -m pip install -r requirements.txt
```

Run every command from the repository root. The scripts add the root to the
Python path themselves, so both `python3.11 benchmark/run_episode.py` and
`python3.11 -m benchmark.run_episode` work.

## Write an optimizer

Subclass `Optimizer` from `benchmark/optimizer_api.py` and implement
`propose`, `observe` and `recommend`. `start` is optional.

```python
from benchmark.optimizer_api import Optimizer

class MyOptimizer(Optimizer):
    name = "My optimizer"

    def __init__(self, task, seed, box, **options):
        super().__init__(task, seed, box, **options)
        self.seen = []

    def start(self, ctx):
        # Runs once before checkpoint 0. Search with the surrogate here.
        z = [0.0] * 38
        results = ctx.cheap_query([ctx.box.to_physical(z)])
        self.candidate = results[0].geometry

    def propose(self, ctx):
        # 38 physical values inside the box. ctx.remaining_cheap_queries
        # and ctx.history are available.
        return self.candidate

    def observe(self, result):
        # result.delivered, result.ld, result.outcome, result.reason_codes
        self.seen.append(result)

    def recommend(self):
        delivered = [r for r in self.seen if r.delivered]
        if delivered:
            return max(delivered, key=lambda r: r.ld).geometry
        return self.candidate
```

A wing is a list of 38 physical design values, in the units and order of
`data/wings.csv`. Every value must lie inside the design box. The box is the
mean of the SuperWing population plus or minus 3.5 standard deviations, one
coordinate at a time. `ctx.box.to_physical` turns box coordinates in
[-3.5, 3.5] into physical values, `ctx.box.to_standardized` does the reverse,
and `ctx.box.contains` checks a wing. A wing outside the box stops the
episode with an error. Nothing is clipped or repaired for you.

`ctx.cheap_query` takes a list of wings and returns one `CheapResult` per
wing. `score` is the predicted geometric mean L/D over the three Mach numbers
when all three conditions trimmed to the target lift and each L/D lies in
(0, 40]. Otherwise `score` is `None` and `status` says why. Every wing in a
call costs one unit of the 4,096. A repeated wing costs again.

`observe` receives an `OnlineResult`. `outcome` is `pass`, `partial`,
`undetermined` or `conclusive_failure`, and `reason_codes` are the grader's
codes, the same ones as in `data/evaluations.csv`.

A wing you already queried in the same episode returns its earlier result
again and still costs a query. Surrogate calls are not cached. A repeated
wing costs again.

Your optimizer runs in a child process. Its working directory is
`<out>/<episode>/optimizer/`, and anything it prints goes to
`optimizer.log` there. It receives the task, the box, the seed, the online
history and the budgets. It never receives a private grade.

## Run an episode

```bash
python3.11 benchmark/run_episode.py \
    --optimizer mypackage.mymodule:MyOptimizer \
    --task nominal --seed 1 --evaluator cfd \
    --out submissions/mine \
    --surrogate-model-dir /path/to/atsurf_l
```

`--optimizer` is `module:Class` or `path/to/file.py:Class`. `--task` is
`low`, `nominal` or `high`. The seed is passed to your optimizer and recorded
in the trace. `--optimizer-options` passes a JSON object to your class as
keyword arguments. Run ten seeds per task to match the reference methods,
which have ten episodes per task.

The runner writes one folder per episode, `U000`, `U001` and so on.

| File | What it is |
| --- | --- |
| `trace.json` | the hash chained episode trace of the released episode machine, written when the episode ends |
| `episodes.csv`, `evaluations.csv`, `recommendations.csv`, `wings.csv` | your records, in exactly the schemas of `data/`, with study `submission` |
| `summary.json` | budgets, checkpoint outcomes, the optimizer's source hash, and the evaluator used |
| `private_grades.enc` | the private grades, sealed with a key made for this episode |
| `private_key.hex`, `private_grades.json` | the key and the grades in the clear, written when the episode ends |
| `optimizer/` | your optimizer's working directory and its log |
| `cfd/` | the CFD case folders, only with `--evaluator cfd` |

If the runner stops before the episode ends, it writes `trace.partial.json`
and a summary with status `failed`. Such an episode is void. Run it again
with a new episode id.

The recorded evaluator answers a wing by its hash from `data/evaluations.csv`
joined to `data/wings.csv`. It refuses a wing that was never graded, and a
wing that was graded under another task. When a wing has several records
under the same task, it uses the earliest by `recorded_at`. Nine wings have
records that disagree with each other, because CFD sometimes timed out on a
wing that passed in another episode.

## The surrogate

`benchmark/surrogate_local.py` wraps the released
`wingbench/surrogate/aerotransformer.py`. The model is ATsurf_L, the large
surface variant of AeroTransformer. The wrapper checks the two model files by
content.

| File | SHA-256 |
| --- | --- |
| `model_config` | `a6cedb0a3dba73f99c41bcfdf3913d37a912c6b0558d5f906e6633aebdca8ef2` |
| `best_model_weights` | `4fa8fb4e3986cdb7a4a24f1f74410c54412beb5191857c76fdd4c4f0c5dc8bfa` |

The weights come from the public Hugging Face repository
`thuerey-group/AeroTransformer`. Put both files in one folder and pass it as
`--surrogate-model-dir`. The wrapper imports `torch`, `einops` and `flowvae`.
`flowvae` is the module of the floGen code that ships with AeroTransformer.
If it is not installed, pass its folder as `--flogen-root`.

The trim settings come from the frozen protocol
`configs/benchmark/multifidelity_benchmark_protocol_v0.json`, under
`cheap_evaluator.trim`. They are two secant updates starting 0.5 degrees
apart, a lift tolerance of 0.001, and an angle of attack window of [-10, 25]
degrees. The lift target and the starting angle of each task come from
`agents/tasks/task_documents.json`. The wrapper's own defaults differ from the
protocol, so the protocol values are passed explicitly. The surrogate runs on
the CPU by default. One call scores a batch. Batches are recorded in the
trace in groups of at most 16 wings.

Without `--surrogate-model-dir` the runner still works, and `cheap_query`
raises an error that your optimizer can catch.

## The CFD evaluator

The released grader `scripts/cfd/grade_benchmark.py` cannot run in a rebuilt
environment. It needs an execution authorization file and a runtime
attestation file whose generators are not released, a publication journal,
a worker lock at a fixed path, and a runtime fingerprint that only the
original machines match. `benchmark/grade_local.py` is the local adapter. It
reuses the grader's own functions for every scientific step and skips the
campaign machinery. On 2026-09-25 the adapter graded one recorded wing on a
fresh m7a.4xlarge inside the container built from `environment/`. Lift, drag,
angle of attack, and the score came back identical to the recorded values at
all three conditions, and the grade took 2 hours 13 minutes. The runtime
fingerprint differed from the campaign's, as `environment/ENVIRONMENT.md`
predicts, and the result document records that.

What the adapter reuses from the released code.

| Step | Released function |
| --- | --- |
| load the frozen protocol and check the pinned files by content | `scripts/cfd/grading_protocol.py` `load_protocol`, `require_mode`, `reject_parent_overrides` |
| derive the low and high load protocols | `scripts/cfd/task_protocols.py` `load_instance_protocol`, from a load instance file the adapter writes into the work folder from the current pinned files |
| the solver environment injected into every `mpirun` child | `GradingProtocol.child_environment` |
| build the case folder from the 38 values with the SuperWing scripts | `scripts/cfd/prepare_superwing_cases.py` `prepare_geometry_case` |
| the `mpirun -np 16` command and the process record with the time limits | `scripts/cfd/grade_benchmark.py` `mpi_python_command`, `run_process`, `bind_process_environment` |
| check the case input and the generated scripts against the protocol | `_expected_case_input`, `_validate_case_input`, `_validate_post_mesh_derived_geometry` |
| cross check the mesh and solver logs against the JSON outputs | `_mesh_log_crosscheck`, `_adflow_log_crosscheck`, `_read_optional_json`, `artifact` |
| validate the volume mesh file | `scripts/cfd/validate_mesh_artifact.py` `validate_mesh_artifacts`, `atomic_write_report` |
| classify the mesh, each condition, and the whole wing, with the lift matching and convergence gates | `scripts/cfd/grade_schema.py` `classify_mesh_attempt`, `classify_condition_attempt`, `finalize_condition`, `aggregate_wing` |
| the (0, 40] envelope of the objective | `wingbench/benchmark/multifidelity_protocol.py` `evaluate_ld_envelope` |
| the runtime probe | `scripts/cfd/grade_benchmark.py` `probe_runtime`, `validate_runtime_fingerprint` |
| result files | `atomic_write_json`, `_fresh_directory`, `_select_final_mesh`, `_not_run_condition` |

What the adapter writes itself. The loop that strings these calls together,
which follows the grader's internal `_grade_wing_impl` step by step. The
mapping of the whole wing verdict to the benchmark's delivered flag, L/D,
outcome type and reason codes, which an unreleased adapter did in the
campaign and which follows `data/README.md`. The handling of the runtime
probe, which is attempted and recorded but not required. The load instance
file for the low and high tasks, which the adapter writes into the work
folder from the current pinned files and the released loader then checks.

What the adapter skips. The benchmark manifest, the execution authorization,
the runtime attestation, the publication preflight and journal, the release
lock, the worker lock, the fingerprint match, and `validate_result`. Each
result document keeps the shape of the released one and adds
`grading_mode` set to `local` and the protocol id it followed, so a trace
made this way is clearly not a sealed campaign grade.

It needs three things.

1. The container. `benchmark/environment/ENVIRONMENT.md` describes it and
   the two build files next to it rebuild it. The frozen protocol
   `configs/cfd/final_protocol_candidate_v2.json` pins the base image and
   the `cst_modeling3d` commit by digest. Inside the container, the Python
   that runs the adapter must import `adflow`, `baseclasses`,
   `cgnsutilities`, `cst_modeling`, `mpi4py`, `petsc4py` and `pyhyp`, and
   `mpirun` must be on the path. Do not run as root. Do not set any
   `ADFLOW_` variable or the thread variables in the shell that runs the
   adapter. The adapter exports them itself, from the protocol.
2. The SuperWing case scripts. Run
   `python3 scripts/cfd/fetch_superwing_scripts.py` once from the repository
   root. It downloads `gen-mesh.superwing.py`, `run-adflow.py`,
   `single-point.py` and `original_tip.xyz` from the SuperWing authors' public
   repository, applies the release's patch to `run-adflow.py`, and checks each
   file against the protocol's SHA-256. The adapter refuses to run if any of
   them differs. The folder is ignored by git, so the files never enter the
   repository.
3. ADflow and its MPI stack, which the container provides.

Before the first grade the adapter tries the released runtime probe,
`scripts/cfd/runtime_probe.py`. The hash of the probe report goes into every
result and into `summary.json`. When the probe's fingerprint equals the one
pinned in the protocol, your machine matches the campaign. When it differs,
or when the probe cannot run, grading still proceeds and the fact is
recorded. State it in your submission. `--no-runtime-probe` skips the probe.

Each grade meshes the wing once and solves the three Mach numbers in series
at 16 MPI ranks. The mesh step may take up to 1,800 seconds and each solve up
to 7,200 seconds. `benchmark/RULES.md` names the machine the reference
results ran on. One episode has 32 online queries plus one private grade for
every checkpoint whose recommendation you did not query online, up to 7. The
CFD cases are written under `<out>/<episode>/cfd/` unless you pass
`--cfd-work`. They can be large.

If `mpirun` is missing or the scripts are absent, the runner stops before the
episode starts and prints what it found on the machine.

## Verify

```bash
python3.11 benchmark/verify.py submissions/mine
```

For every episode this replays `trace.json` with the released
`wingbench/benchmark/replay.py`, which recomputes every hash in the chain and
every budget transition. It then compares the four record tables with the
replayed trace row by row, rehashes every wing in `wings.csv`, and opens the
sealed private grades with the revealed key to compare each grade with the
hash the trace recorded when the grade was made. It exits with 1 when any
episode fails.

## Score

```bash
python3.11 benchmark/score.py submissions/mine --out submissions/mine/score
```

The scorer verifies every episode first and leaves out the ones that fail.
Then it prints, for each task and checkpoint, the recommendation delivery
rate and the mean L/D of delivered recommendations, for your submission next
to the six reference methods. It also prints the online delivery rate. Your
rates and means carry 95 percent bootstrap intervals from 10,000 resamples
of your episodes. For the conditional mean, each resample averages the
delivered episodes it contains. For the online rate, episodes are resampled
and the pooled rate is recomputed. The reference numbers are point estimates
from the released records. `--seed` sets the bootstrap seed.

It writes `recommendation_comparison.csv`, `online_comparison.csv`, the
verification report and the settings used into `--out`.

Open-ended research was graded at checkpoints 0, 1, 2 and 4 only. Its online
delivery covers all 32 queries.

## Private grades stay sealed during the episode

Your optimizer runs in its own process with its own working directory. It
gets only what `observe` and `ctx` expose. The runner keeps every private
grade in memory. After each one it writes the whole list to
`private_grades.enc`, outside the optimizer's working directory, sealed with
a random key made for the episode. The trace records the hash of each grade
at the moment it is made. When the episode ends, the runner writes the key
and the grades in the clear, and `verify.py` checks that the revealed grades
match the sealed file and the hashes in the trace.

This stops accidental peeking, for example by an LLM agent that browses its
workspace and reads whatever it finds. It is not a defense against a
competitor who edits the runner. The honor code and the replay check cover
that. With the `cfd` evaluator, the raw solver output of a private grade
stays on disk in the CFD work folder. Put that folder somewhere the optimizer
cannot browse, with `--cfd-work`, if this matters to you.

## Submit

Submissions work on the honor code. Open a pull request against this
repository that adds one folder under `submissions/` with

* every episode folder as the runner wrote it, with `trace.json`, the four
  record tables, `summary.json`, `private_grades.enc`, `private_key.hex` and
  `private_grades.json`,
* the source code of your optimizer, or a link to a tagged public release
  whose file hash matches the `source_sha256` in `summary.json`,
* a short note that names the method, the container digest and runtime
  fingerprint status of your CFD machine, the seeds you ran, and anything
  that differs from the rules above.

Leave out the CFD work folders and the surrogate weights.

Every episode in the pull request must pass `benchmark/verify.py`. The
maintainers rerun it. Results made with the `recorded` evaluator are not
accepted. By submitting you state that your optimizer used only the surrogate,
the online CFD results of the current episode, and its own code, and that no
person or process read a private grade before the episode ended.

## Dry run

The example optimizer `benchmark/examples/retrieval_recorded.py` proposes
wings from `data/wings.csv` without looking at their recorded outcomes, so
the recorded evaluator can answer. These commands run one episode, verify
it, and score it.

```bash
python3.11 benchmark/run_episode.py \
    --optimizer benchmark.examples.retrieval_recorded:RetrievalRecorded \
    --task nominal --seed 1 --evaluator recorded --out submissions/example
python3.11 benchmark/verify.py submissions/example
python3.11 benchmark/score.py submissions/example --out submissions/example/score
```

## Preservation and maintenance policy

The original results and content-pinned evaluator settings are preserved as a
reference version. Documentation corrections do not silently change those
grades. Changes to meshing, solver settings, budgets or outcome rules require
a new protocol version and separately reported results. A rebuilt runtime has
its own fingerprint; it must not be presented as the historical environment.

For interface changes, run the recorded integration example and trace verifier
above. For analysis changes, regenerate the affected tables from the preserved
records. Evaluator changes additionally need CFD validation under their new
version; a recorded replay is not a numerical validation. Dependency recipes
and source/weight hashes support rebuilding when services or packages change.
Report reproducible failures with the version, environment and relevant trace
through repository issues, without credentials or private model logs.

There is no hosted grading service, guaranteed response time or guaranteed
maintenance duration. The original distributed agent controller is not part of
the release. An agent may call its model from the optimizer interface, but
recreating the original campaign scheduler requires additional engineering.

## Files in this folder

| File | What it is |
| --- | --- |
| `optimizer_api.py` | the `Optimizer` base class and the small record types |
| `run_episode.py` | runs one episode and writes the trace and the records |
| `optimizer_host.py` | the child process that hosts your optimizer |
| `evaluators.py` | the recorded evaluator and the CFD evaluator |
| `grade_local.py` | the local adapter around the released CFD grader |
| `environment/` | how to rebuild the CFD container, written separately |
| `RULES.md` | the rules of the benchmark, written separately |
| `surrogate_local.py` | the surrogate wrapper with the 4,096 counter |
| `sealed.py` | the sealing of private grades |
| `records.py` | the four record schemas |
| `verify.py` | the replay check |
| `score.py` | the comparison with the reference methods |
| `common.py` | shared constants and loaders |
| `examples/retrieval_recorded.py` | the example optimizer |

## Resource instrumentation for additional runs

The runner writes `resources.jsonl` alongside the scientific trace. It records
setup, decision operations, optimizer CPU measurements, surrogate batches,
actual evaluator executions, result reuse, checkpoint nominations and grade
availability. `CfdEvaluator` also writes `cfd/cfd_resources.jsonl` with each
mesh and flow attempt's elapsed time, CPU measurements and termination status.
The scientific grader, acceptance criteria and budgets are unchanged.

`--stop-after-checkpoint 4` observes the four-query prefix of the original
32-query task. The optimizer still receives the original budgets. The output
status is `checkpoint_complete`, and the scientific trace remains partial.
This output is not a completed 32-query benchmark submission.

CPU measurements inside the optimizer host cover the process and its reaped
children. For agents with background processes, use an exclusive Linux cgroup
covering the entire launch. `benchmark.measure_command` records cumulative
cgroup CPU counters before and after a command, enforces a timeout, and reports
remaining processes. It requires a CPU-only execution environment. Its
`usage_usec` difference is total CPU consumption; user and system counters are
components, not additional consumption. The wrapper's small CPU overhead is
included when it shares that cgroup. Do not sum parent and child cgroup totals.
Allocated cores multiplied by elapsed seconds remain a separate quantity.

`benchmark.instrument_agent_controller` builds a source-fingerprinted copy of
the archived controller. It adds manager lifecycle timestamps, surrogate timing,
launch-cgroup CPU snapshots and per-HTTP-attempt model resource events. Set
`WINGBENCH_RESOURCE_DIR` to a manager-owned directory outside the agent's
writable workspace. Use the new instrumentation manifest when configuring the
pilot; do not reuse the old controller hashes. The archived source is untouched.
The copied manager sources require the original deployment's remaining modules,
templates and runtime configuration; this command does not deploy a campaign.

The new `model_request_start` and `model_request_end` events are authoritative
for resource reporting. They include retries and work launched in separate
planning processes. Legacy session usage fields are retained for compatibility
and must not be used to replace the new nullable accounting. Missing usage is
`null`, never zero. The report provides known token totals and missing-request
counts separately. Failed requests can consume provider resources without
returning usage. Cached and reasoning tokens may be subsets of other token
fields, and are not blindly added to input or output totals. Client latency and
tokens do not establish model-server CPU/GPU consumption.

Run the local checks with:

```bash
python3.11 -m unittest benchmark.tests.test_resource_logging -v
python3.11 -m benchmark.resource_report /path/to/episode --output audit.json
```

The frozen-baseline adapter has an additional equivalence check that requires
the original numpy 2.0.2 and cma 4.4.4 environment:

```bash
python -m unittest benchmark.tests.test_frozen_baseline_adapter -v
```

The report refuses to qualify replay-only records as live CFD validation.
Agent runs additionally need their model logs and `--require-model`, plus
exclusive cgroup records. An audit of one episode is not a campaign release
decision. The pilot set must exercise real meshing and flow, surrogate charges,
optimizer process-tree accounting, and successful live model responses in the
actual agent deployment. A fake HTTP provider verifies logging behavior only.

Elapsed episode time comes from endpoint timestamps. Surrogate execution is
nested within decision operations, and agent/model sessions may overlap. Do
not sum these durations to reconstruct elapsed episode time. Worker provisioning,
queue intervals, private grades and cache reuse must be reported with their
original scope and execution identifiers.

`benchmark.resource_baseline_pilot` runs the frozen feedback-guided or
selection-only CMA-ES adapter through a selected checkpoint. It uses the real
surrogate in the controller environment and `benchmark.container_evaluator` to
run each fresh grade in the digest-pinned CFD image. This keeps the CFD Python
environment separate from the optimizer's required numpy/cma environment.
The container calls the same `CfdEvaluator`; it does not change scientific
settings. Record the controller Python/dependency versions and image digest.

For this execution mode, wrap the controller in an exclusive cgroup with stage
`baseline-controller` and its `--episode-id`. Each CFD container produces its
own cgroup record with stage `CFD-container` and the same episode ID. Pass both
scopes to `resource_report`, together with every attempt's
`cfd/cfd_resources.jsonl`. The controller CPU total includes surrogate execution
and transport overhead, but excludes Docker's separately placed CFD processes.
The optimizer-operation CPU is a subset of controller CPU. Report these scopes
separately rather than adding overlapping totals. The audit checks episode IDs,
paired measurements, and the number of CFD containers against fresh grades.
No language model is called by these two baseline adapters.
