# WingBench adjoint: one verification batch before the rebuttal campaign

Hi Jisen — thanks again. Please run this verification batch on the two CPU nodes. We want to finish tonight if possible, then use the measured capacity to divide the actual episodes between your machines and ours.

**This is verification only. Do not start the 21-episode campaign yet.** The minimum campaign target remains **21 total episodes: seven per task**, with 30 preferred if capacity allows. Your portion can be smaller. We will choose disjoint episode IDs before launch; changing the number of episodes or their host assignments must not change the numerical protocol.

## Required input

Use the companion archive **`wingbench-jisen-verify-v1-20261106.tar.gz`** supplied with this Markdown. It contains the frozen source, reference mesh, container build recipe, numerical protocol, tests, and launcher. **The public GitHub repository alone is insufficient:** these private verification files are included in the archive.

The archive includes `SHA256SUMS` and a separately supplied `.sha256` file. Verify the outer archive before extraction, then verify `SHA256SUMS` inside. The package manifest additionally checks every scientific source and reference asset before a worker may run.

No credentials, cloud access keys, language-model endpoint, or GPU are required. Use your own container registry and existing Kubernetes access.

## Frozen numerical choices

The authoritative machine-readable specification is:

`repo/rebuttal/compute/jisen_verify_v1/numerical_protocol.json`

Keep the following unchanged:

| Item | Frozen choice |
|---|---|
| CFD software | Existing digest-pinned MDO Lab image; pinned CST commit; no library upgrades |
| Geometry and flow | Original 38-variable SuperWing representation; RANS/SA; Mach 0.7853, 0.8195, 0.853; Reynolds 20 million |
| Gradients | Coupled ADflow discrete adjoints, IDWarp mesh derivatives, existing geometry derivative chain |
| Forward solve | Target `1e-12`; measured relative residual must be `<=1.1e-12` |
| Adjoint solve | Internal target `1e-12`; measured native relative residual must be `<=1.1e-11`; at most three existing refinement attempts |
| Reference reproduction | CL/CD relative difference at most 0.5% |
| CFD derivative checks | All three directions, both steps 0.002 and 0.001, all three Mach conditions; original componentwise tolerance unchanged |
| Pilot optimizer | SLSQP; four iterations maximum, ten joint forward evaluations maximum; existing local bounds and `ftol=1e-6` |
| Independent regrade | Original scientific grader, local adapter; score difference from the pilot at most 0.5% |
| Original-grader limits | Mesh 30 minutes; each flow condition two hours; do not extend to compensate for hardware |

**Known numerical blocker:** our recent Quest runs missed the strict native CL adjoint residual gate, despite very stable gradients. Moving to your machine is not assumed to fix this. Preserve failures, and do not relax tolerances, replace the native residual with a different quantity, skip failed cases, or label a completed process as a passed scientific check. The independent certifier enforces this.

The numerical specification also records the intended production method: an additional **gradient-access SQP track**, with original-grader evaluation of its recommendations. Its chosen-angle SQP trials are not identical to the existing fixed-lift black-box queries. We will report joint attempts, individual forward and adjoint solves, original-grader calls, CPU time, and wall time separately. The four-iteration pilot is not a competitive baseline episode or evidence of optimizer convergence. The full production episode runner and its budget/initialization accounting remain separate release requirements.

## What this single batch runs

The launcher executes these phases automatically, preserving results from every attempt:

1. **Preflight on every worker:** UID, container/library/source/mesh hashes, MPI with 16 ranks, CPU quota and visible topology, RAM limit, local storage, shared storage and locks. Every worker must see every other worker before CFD starts.
2. **Parallel numerical and capacity checks:** three reference forward-plus-CL/CD-gradient jobs, 36 independent cold CFD endpoints, and two original-grader reference wings—one on each node. That is **41 scheduled tasks**, distributed over eight or ten workers. The 36 endpoints produce 18 directional pairs, each checking CL and CD. These are not 41 episodes.
3. **Independent strict certification:** recomputes the checks from raw evidence, including all six final native adjoints. The observational coverage summary cannot release the pilot by itself. Any missing evidence or failed criterion blocks the next phase.
4. **Conditional integration pilot:** only after certification passes, run the same bounded SLSQP mathematics with three Mach conditions sequentially on 16 ranks. It keeps three independent solver instances resident, so it has a separate RAM allocation.
5. **Conditional independent regrade:** only after the pilot returns an eligible wing, grade it with the original scientific grader. Its score is checked against the pilot's score.

The launcher ends with an explicit status and **never starts baseline episodes, retries failed tasks, changes tolerances, deletes Jobs, or deletes evidence**. A failed numerical task does not prevent the other independent checks in phase 2 from collecting useful evidence.

## Resource layout

| Phase | Allocation |
|---|---|
| Default parallel checks | **8 pods**, each 16 Kubernetes CPU units and 96 GiB; four on each node |
| Optional larger check batch | **10 pods**, four on node A and six on node B; only if all 160 CPU units are continuously available |
| Conditional serial pilot | One pod on node B, **16 CPU units and 288 GiB**; starts after the parallel phase has released its resources |
| Conditional regrade | One pod on node B, 16 CPU units and 96 GiB |

The pilot's 288 GiB request is conservative and **not a measured production requirement**. Eight successful 96-GiB workers do not establish that eight complete adjoint episodes fit simultaneously. Return the actual memory measurements so we can determine that capacity.

Kubernetes CPU units are not proof of exclusive physical cores. Please provide the stated physical-core headroom, avoid hard MPI core binding, and report contention or CPU throttling. The package disables MPI binding in both direct solves and the original grader's child processes. Each pod has its own `/workspace`; only `/results` is shared.

## Run commands

The operator machine needs Python 3.9 or newer, `kubectl`, a builder for a Linux/amd64 OCI image, and a writable host mount of the same **ReadWriteMany PVC** mounted at `/results` in the pods. Use a persistent terminal such as `tmux` for the launcher.

```bash
# Put the archive and its .sha256 file together.
sha256sum -c wingbench-jisen-verify-v1-20261106.tar.gz.sha256
tar -xzf wingbench-jisen-verify-v1-20261106.tar.gz
cd wingbench-jisen-verify-v1-20261106
sha256sum -c SHA256SUMS

# Replace this with your own registry/repository.
WB_IMAGE_TAG=YOUR_REGISTRY/wingbench-adjoint-verify:v1-20261106
docker build --platform linux/amd64 \
  -f repo/rebuttal/compute/jisen_verify_v1/Dockerfile \
  -t "$WB_IMAGE_TAG" .
docker push "$WB_IMAGE_TAG"
docker image inspect "$WB_IMAGE_TAG" --format '{{index .RepoDigests 0}}'
```

Use the printed **`repository@sha256:...`** value below. The launcher rejects mutable image tags. Registry login and pull-secret names are infrastructure settings; keep credentials out of the returned artifacts.

```bash
python3 repo/rebuttal/compute/jisen_verify_v1/launch.py \
  --image YOUR_REGISTRY/wingbench-adjoint-verify@sha256:ACTUAL_DIGEST \
  --namespace YOUR_NAMESPACE \
  --pvc YOUR_EXISTING_RWX_PVC \
  --node-a FIRST_NODE_HOSTNAME_LABEL \
  --node-b SECOND_NODE_HOSTNAME_LABEL \
  --results-host /YOUR/HOST/MOUNT/OF/THAT/PVC \
  --run-id wb-adjoint-verify-20261106 \
  --workers 8
```

Add `--image-pull-secret YOUR_SECRET_NAME` if needed. Use `--workers 10` only if the larger continuous allocation is confirmed. For this package, each supplied node name must match both its Kubernetes `metadata.name` and its `kubernetes.io/hostname` label; check this before launch. The host result directory must correspond to the PVC's root; the launcher creates a fresh run-specific subdirectory. Do not reuse a run ID or run directory.

Only those infrastructure arguments are intended to vary. Do not edit the source, hashes, solver settings, geometry, tolerances, numerical tests, or optimizer to get a passing result. If a build, preflight, numerical check, or integration phase fails, retain the error and return the evidence; do not improvise another verification round.

## Timing and what to return

Please start the build and parallel phase as soon as practical. **Finishing tonight is a target, not a reliable promise.** Phase 2 is parallel, but the pilot and its regrade depend on previous results. If strict certification passes and the complete integration sequence takes longer, it may continue overnight. A stopped batch is a failure/blocker report, not readiness confirmation.

Return the entire run directory, preferably as a compressed archive. At minimum include:

- `final_summary.json`, `run_plan.json`, and `qualification/certificate.json`.
- Worker preflight, ready/barrier and process records; all claim logs and raw numerical JSON.
- Both original-grader controls, including their `grade.json` and timings.
- Pilot and independent regrade records if those phases ran; otherwise their explicit blocking status.
- Kubernetes status/events/logs, final image digest, CPU/core availability and allocation duration.
- Recorded wall times, CPU/core-hour accounting, cgroup memory peaks, any OOMs or throttling, and your estimate of continuously available **whole-episode** capacity.

Keep large CFD scratch files until we confirm receipt; compact JSON/logs are sufficient for initial review. Never report absent memory metrics as zero.

**Before the actual campaign:** we will use these results and the production runner's measured requirements to select your share, then place the remaining predeclared episodes on Quest or another provider. We will retain the frozen numerical settings and include every launched episode—including failures—in the report.
