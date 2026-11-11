# WingBench ICLR rebuttal: CPU compute request

**Date:** November 6, 2026  
**From:** David  
**For:** Jisen and your agent

Hi Jisen, thanks for offering to help with the compute. We are focusing on adding one solid ADflow adjoint optimization baseline for the rebuttal. Please estimate the turnaround for **10, 21, and 30 total episodes**, and let us know which portion you could accommodate alongside your other workloads.

**Our minimum target is 21 episodes: seven runs for each of the three tasks.** We want at least 20 runs, rounded up to a multiple of three so the tasks are represented equally. **Thirty episodes remains the preferred full comparison.** The ten-episode scenario is a capacity reference or partial contribution, not our intended final campaign size. The missing adjoint baseline is a central concern in Reviewer 2's assessment, making this one of our highest-priority rebuttal experiments.

This document is for capacity planning. The final adjoint workload is still being validated; we will provide the tested run package before asking you to launch episodes.

## 1. Hardware discussed

| Item | Current understanding |
| --- | --- |
| Nodes potentially available | 2 |
| CPUs per node | 2 × AMD EPYC 9534; 128 physical cores total per node |
| Cores potentially available to us | 64 per node, leaving the remainder for other tasks |
| Total usable cores for this request | **128** |
| Possible allocation duration | Approximately one week, subject to availability |
| RAM available to our jobs | To be confirmed per node |
| GPU requirement | None for the CFD and adjoint computations |

You also mentioned that around ten parallel episodes might be possible. At our quoted allocation of 16 cores per worker, ten simultaneous workers would require **160 usable cores**, rather than the 128 above. Please clarify which capacity is realistic; the estimates below use 128.

## 2. Workload and dependencies

An episode is one optimization run. Its design evaluations are sequential: the optimizer uses one result to choose the next design. Different episodes can run independently in parallel.

The full benchmark comparison is **30 episodes: three lift-target tasks × ten runs per task**. The 21-episode target gives seven runs per task; the 30-episode target gives ten per task. These are total counts, not counts per task. The ten-episode case is only a capacity reference or partial contribution; its task allocation would be agreed before launch.

Each episode has a budget of up to **32 online whole-wing CFD evaluations**. One whole-wing evaluation covers all three Mach conditions. **Do not multiply the job counts below by three again.**

The grading workload we discussed has these planning assumptions:

| Per ordinary grading job | Assumption |
| --- | --- |
| CPU allocation | 16 physical CPU cores / MPI ranks |
| RAM allocation | 64 GiB |
| Elapsed time | Approximately 2–3 hours |
| Work performed | Mesh and evaluate one wing across the three Mach conditions |

The 2–3 hour figure is a planning estimate, not a guaranteed runtime on your hardware. Difficult or failed cases can take longer.

With 64 available cores per node, each node can host **four such jobs**, provided at least **256 GiB of RAM is available to those jobs**, in addition to memory needed by other workloads. Across two nodes, that gives **eight simultaneous grading jobs and 512 GiB of allocated RAM**. We can keep each 16-core worker within a single node.

## 3. Three scenarios: ordinary grading reference

These estimates assume every episode uses all 32 online evaluations. They exclude additional adjoint computation, private recommendation grades, setup, validation, infrastructure retries, and scheduler delays.

| Total episodes | Online wing evaluations | CPU core-hours at 2–3 h per evaluation | Ideal elapsed time with 8 busy workers | Fixed whole-episode batches of up to 8 |
| ---: | ---: | ---: | ---: | ---: |
| **10** | 320 | 10,240–15,360 | **80–120 h / 3.3–5.0 days** | 128–192 h / 5.3–8.0 days |
| **21 — minimum target** | 672 | 21,504–32,256 | **168–252 h / 7.0–10.5 days** | 192–288 h / 8.0–12.0 days |
| **30 — preferred** | 960 | 30,720–46,080 | **240–360 h / 10.0–15.0 days** | 256–384 h / 10.7–16.0 days |

**How to interpret the two elapsed-time columns:**

- **Ideal:** evaluation jobs from different episodes are interleaved to keep all eight workers busy, while preserving the sequence within each episode. This is a throughput estimate, not a promised ETA.
- **Fixed batches:** each worker stays assigned to an entire episode until it finishes. Ten episodes need two batches, twenty-one need three, and thirty need four. This is simpler to schedule but leaves unused capacity in the final batch.

One episode takes approximately **64–96 hours** of sequential grading under these assumptions. A continuous seven-day allocation of 128 cores provides **21,504 CPU core-hours**. Whether the allocation fits the available week depends on scheduling, actual runtimes, and the additional work described below.

Calculation rules for your agent:

```text
N = total episodes
Q = 32 evaluations per episode
C = 16 cores per grading worker
t = 2 to 3 hours per grading evaluation
P = 8 concurrent workers, assuming sufficient RAM

evaluation count = N * Q
CPU core-hours = N * Q * C * t
ideal elapsed hours = N * Q * t / P
fixed-batch elapsed hours = ceil(N / P) * Q * t
```

## 4. Additional work for the adjoint baseline

The new method needs both ordinary flow solutions and **adjoint solves**, which calculate gradients used to choose the next design. The adjoint calculations are substantial CPU work and need access to the solver's mesh and flow state. A service returning only a wing score would not provide the gradients.

Our current implementation calculates lift and drag gradients at each of the three Mach conditions. In an initial reference test, summed over the three conditions with 16 cores per condition, we measured approximately:

| Solver work | Sum of worker times |
| --- | ---: |
| Forward flow solutions | 80 minutes |
| Lift and drag adjoint calculations | An additional 148 minutes |
| Combined | 228 minutes |

This is approximately **2.85× the forward-only solver work for that test**. It is **not a measured multiplier for the complete 2–3 hour grading jobs**: meshing, lift adjustment, reused solutions, and the number of gradient requests differ. Please do not simply multiply the scenario table by three and treat that as a validated campaign ETA.

Also, **32 evaluations are not necessarily 32 optimizer iterations or 32 gradient requests**. Trial steps can require additional forward evaluations, and some evaluated designs do not need gradients. We will freeze and report that accounting before the campaign.

The quoted 64 GiB allocation is for ordinary grading. **Adjoint peak RAM still needs measurement.** Our current instrumented diagnostic requests 96 GiB for a 16-core worker; this includes diagnostic overhead and is not yet a final production requirement. For example, if the final requirement were 96 GiB and only 256 GiB were available per node, memory would limit us to two workers per node despite having CPU capacity for four.

The current small optimization pilot is configured differently from the ordinary grader: it requests **48 cores and 192 GiB**, using three groups of 16 cores to run the Mach conditions concurrently. With 128 usable cores, at most two such pilot jobs fit by CPU count. That pilot has not yet established production runtime or memory requirements. The eight-worker table above therefore must not be read as eight concurrent jobs using this pilot configuration. A production layout that runs conditions sequentially would have a different allocation and timing profile.

We propose timing **one representative forward-plus-adjoint job on your hardware** before committing to the full allocation. That will establish the actual runtime, peak RAM, and feasible concurrency. Persistent episode workers may also be preferable if reloading solver state between jobs is expensive.

## 5. Work we can manage separately

We can handle orchestration, experiment records, and numerical optimizer decisions separately. Those parts are light compared with CFD and adjoint computation. The solver workers need the compatible ADflow/MPI environment, adequate RAM, and scratch storage.

Independent grading of recommendations uses the ordinary grader and requires no adjoint calculations. It is additional compute. A checkpoint grade can reuse a result only if the exact nominated design already has an eligible original-grader evaluation under the frozen grading protocol. An adjoint optimizer's own result cannot automatically substitute for this: its deformed mesh and solver path can differ from the original grader.

For capacity planning, reserve up to **seven additional ordinary grading jobs per episode** for the recommendation checkpoints: **70, 147, or 210 extra grades** for the three scenarios. These are excluded from the scenario table. Their results are not available to the optimizer during the episode.

No language model or language-model endpoint is required for the adjoint optimizer. Any endpoint use for separate rebuttal experiments will be coordinated with you separately, with the model and time window specified in advance. We are not including the MacBook in this compute plan.

## 6. What would help us finalize the plan

Please let us know:

1. **Available capacity:** Can we count on 64 cores per node continuously, or would access be intermittent? Is ten-worker capacity possible?
2. **Memory and storage:** How much RAM and scratch space are available to our jobs on each node while other tasks run?
3. **Scheduling and software:** Which scheduler, maximum job duration, container runtime, and MPI setup should our run package support?
4. **ETA:** For each of the 10-, 21-, and 30-episode scenarios, what turnaround would you estimate for the ordinary grading reference workload? Please state the assumed concurrency and scheduling approach. We can then update the estimate after the adjoint timing test.
5. **Your portion:** What number of episodes or grading jobs would be comfortable for you to run? We can distribute the remainder elsewhere.

Thanks again. A smaller, predictable contribution would still be very useful; we can combine it with other compute to reach at least 21 episodes. We will prepare the run package to minimize the time you need to spend managing it.
