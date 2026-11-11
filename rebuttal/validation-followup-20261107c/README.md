# WingBench validation follow-up: `wb-validation-followup-20261107c`

**Validation only.** External label: `jisen-validation-only/wb-validation-followup-20261107c`. This is not a production episode and is not counted among the 30. It ran on cluster research-common-h100 (k3s v1.27.6), namespace `jisenli2`, on nodes with 2× AMD EPYC 9534, from 2026-11-08 09:43Z to 20:44Z. It stopped after stage 4, as the handoff requires.

This follows David's handoff `JISEN_VALIDATION_FOLLOWUP_2026-11-07.md`. The verdicts below are separate, as its section 5 asks. They come from **different runs**: the old v2 numerical run, plus the new controls and the v3 replica. No single run passed everything.

## Verdicts

| Part | Verdict | Key evidence |
|---|---|---|
| a. Numerical qualification (old run `wb-adjoint-v2-20261107b`) | Coverage **passed**. The strict certificate is still **FAILED** and unchanged | 39/39 tasks rc 0. Native adjoint residuals 2.8e-11 to 3.6e-11 against the 1.1e-9 gate. The certificate failed only because `task_documents.json` was missing |
| b. Original-grader controls (`reference_a`, `reference_b`) | **PASSED, both** | Both delivered, score 32.052252. Per-Mach CL/CD relative error **0.0** at all three Mach numbers (gate 0.5%) |
| c. v3 runtime and integration tests | **PASSED** | Both release manifests verify. 9/9 pinned runtime hashes match. cyipopt 1.4.1 / IPOPT 3.13.4 (MUMPS). `initial.py --preflight` passed with `CFD_calls: 0`. `test_core.py` passed |
| d. v3 optimization (one 4-attempt replica, roster index 0) | **Ran to the pilot allowance.** Phase `pilot_paused`. 4 attempts: 3 OK, 1 scientific failure. A **changed wing** was nominated | Native score went from 11.5488 (start) to 12.2608 (nominee, attempt 3). `starting_wing_returned=false` |
| e. Independent grading of the nominee | **PASSED** | Delivered, original-grader score **12.2514**, **+6.08%** over the starting 11.5488. Native vs original disagreement **0.076%**, so `native_original_agreement_passed=true` (gate 0.5%) |

## a. Numerical qualification: old run, unchanged

Run `wb-adjoint-v2-20261107b` (2026-11-07):
- All 39 anchor and point tasks returned rc 0.
- The 6 native adjoint finals qualified on attempt 1, with relative residuals 2.8e-11 to 3.6e-11 against the 1.1e-9 gate.
- All 18 comparisons and 36 scalar comparisons passed. `coverage_summary.passed=true`.
- The strict host certificate failed with 4 errors, all caused by the missing `agents/tasks/task_documents.json`.
- **That certificate is preserved as it was and remains failed.** It was not re-run or edited. Its evidence is in `run-b-evidence/`.

## b. Original-grader controls

Each control ran the unchanged v2 `run_grade.py` through `container_entry.sh`, as a Kubernetes Job with `backoffLimit: 0`, 16 CPU and 96 GiB. Outputs went to new paths.

| Control | Node | Wall | passed | delivered | Score | Max per-Mach rel. error (CL, CD) | Runtime probe |
|---|---|---|---|---|---|---|---|
| `reference_a` | 059 | 88.7 min | true | true | 32.05225247077079 | 0.0 | complete |
| `reference_b` | 090 | 86.7 min | true | true | 32.05225247077079 | 0.0 | complete |

Case E1236 against its recorded coefficients:

| Mach | Recorded CL | Recorded CD | Relative error, both controls |
|---|---|---|---|
| 0.7853 | 0.4100288569865937 | 0.012661093112055637 | 0.0 / 0.0 |
| 0.8195 | 0.41001242093544216 | 0.012729520316834216 | 0.0 / 0.0 |
| 0.853 | 0.40995782482703824 | 0.012986487746186966 | 0.0 / 0.0 |

**Runtime fingerprint note.**
- Every original-grader run on this cluster (both controls, the initialization and the independent grade) recorded `matches_campaign: false`. They all observed fingerprint `ce49067865f6…47f3`, where the campaign expects `60263209c65d…314d`. The grader records this and does not abort; it runs in local, non-official mode.
- The report includes host-specific fields. For example `platform` is `Linux-5.15.0-176-generic-x86_64-with-glibc2.31` here, which necessarily differs from the AWS hosts.
- The fingerprint is identical on nodes 059 and 090.
- The pinned scientific runtime hashes (9/9) do match. The controls reproduced the recorded coefficients exactly.
- We cannot tell which fields differ without the AWS report. The full JSON reports are in `evidence/pvc/tree/*/work/runtime_fingerprint_*.json`, ready to diff.

## c. v3 runtime and integration tests

- **Job.** `v3-checks` ran on node 059 with 16 CPU and 96 GiB. It finished in under a minute and wrote `v3-checks/result.json` with `passed: true`.
- **Image.** The original v2 image digest was used, with **nothing installed**. It already contains cyipopt 1.4.1 linked to IPOPT 3.13.4 with MUMPS (`libcoinmumps.so.3`), numpy 1.21.5 and Python 3.9.12.
- **Source.**
  - The frozen v3 source was extracted on the PVC from the pinned `pilot-package.tar.gz`, after its sha256 was checked in the pod. That gave 187 files.
  - It was made read-only and mounted read-only at `/workspace/repo`. A write probe confirmed it was read-only.
- **`runtime_record.json`** (CPU `AMD EPYC 9534 64-Core Processor`, cgroup `cpu.max 1600000 100000`):
  - `initial_release.json` and `pilot_release.json` verify (133 and 186 files);
  - all 9 `runtime_expected_sha256` entries match;
  - `optimizer_runtime()` reports 1.4.1, [3, 13, 4], with `OMP`, `OPENBLAS` and `MKL` set to 1.
- **Preflight.** `initial.py --index 0 --preflight` passed with `CFD_calls: 0`.
- **Synthetic tests.** `test_core.py` returned `TEST_RESULT={"passed": true}`. The test cases are full, split/replay, failure, gradient-failure, budget and unresolved.

## d. v3 optimization replica

**Initialization** (free, original grader): `delivered=true`, phase `initialized`, task `low` (target CL 0.15). Starting score **11.548797447527662**. Angles 2.974, 2.902 and 2.791 deg. Wall time 1.91 h.

**Optimization.** `run.py --stage-limit 4` with single-threaded BLAS ran 13:07Z to 19:00Z (5.89 h). `run.py`'s own runtime checks passed. `stage-04.json` reports:
- `phase: pilot_paused`, message "Pilot allowance reached before next trial";
- `search_attempts: 4`, `recorded_oracle_calls: 9`, `new_oracle_calls: 9`, `replayed_oracle_calls: 0`;
- `starting_wing_returned: false`.

| # | Op | Search attempt | Status | Wall | Native L/D (geometric mean) | Native checks |
|---|---|---|---|---|---|---|
| 0 | forward | 0 (free start) | ok | 36.9 min | 11.5488 | 3/3 qualified, max rel. residual 4.46e-13 |
| 1 | gradient | 0 | ok | 43.6 min | | 6/6 adjoints qualified on attempt 1, 1.42e-11 to 2.15e-11 |
| 2 | forward | 1 | ok | 36.2 min | 11.5573 | 3/3 qualified, max 4.44e-13 |
| 3 | gradient | 1 | ok | 44.0 min | | 6/6 qualified, 1.38e-11 to 2.20e-11 |
| 4 | forward | 2 | ok | 38.1 min | 11.7563 | 3/3 qualified, max 5.00e-13 |
| 5 | gradient | 2 | ok | 43.1 min | | 6/6 qualified, 1.46e-11 to 2.20e-11 |
| 6 | forward | 3 | ok | 37.0 min | **12.2608** | 3/3 qualified, max 7.07e-13 |
| 7 | gradient | 3 | ok | 42.3 min | | 6/6 qualified, 1.46e-11 to 2.07e-11 |
| 8 | forward | 4 | **scientific_failure** | 31.8 min | | Mach index 0 qualified at 7.64e-13. At Mach index 1 the solve failed (`solve_failed: true`) at only 3.27e-11 against the 1.1e-12 forward gate: "Forward solve failed v2 qualification" |

- **Search attempts.** 4 reserved, 3 completed OK and 1 failed (scientific, forward). The protocol counts a failed forward against the attempt allowance and reports it to the optimizer. The allowance was then reached, so the search paused. Nothing was retried.
- **Geometries.** 5 distinct geometries were requested: the start plus 4 changed trials. 3 changed trials returned qualified results.
- **Native gates.** All 13 qualified forward solves stayed at or below 7.64e-13 against the 1.1e-12 gate. All 24 adjoint solves qualified on their first attempt, at 1.38e-11 to 2.20e-11 against the 1.1e-9 gate.
- **Nominations** (`nominations.json`):

  | Source | Native score |
  |---|---|
  | Starting wing | 11.5488 |
  | Attempt 1 | 11.5573 |
  | Attempt 2 | 11.7563 |
  | **Attempt 3 (nominee)** | **12.26078924724386** |

## e. Independent original-grader grading

`private.py` ran as a separate process with the clean original-grader parent environment, from 19:00Z to 20:44Z (1.73 h). It made 1 new CFD call, because the nominee differs from the starting wing, so no grade was reused.

| Mach | Outcome | CL | CD | L/D | err_cl | Realized AoA (deg) |
|---|---|---|---|---|---|---|
| 0.7853 | valid | 0.149677 | 0.0091318 | 16.3907 | 6.8e-7 | 2.9764 |
| 0.8195 | valid | 0.149992 | 0.0115243 | 13.0153 | 1.9e-7 | 2.9026 |
| 0.853 | valid | 0.149929 | 0.0173930 | 8.6200 | 7.9e-7 | 2.7905 |

`result.json`:

| Field | Value |
|---|---|
| `delivered` | true |
| `score` | **12.251435441985432** |
| `starting_score` | 11.548797447527662 |
| `improvement` | **+6.084%** |
| `native_prediction` | 12.26078924724386 |
| `relative_native_disagreement` | 7.63e-4 |
| `native_original_agreement_passed` | **true** |
| `search_feedback` | false |

This is a changed-wing end-to-end validation: the search moved the wing, and the independent original grader confirms the native prediction within 0.5%. It is one integration control, not a performance sample.

## Resources

The memory figures are the maximum of `memory.current` sampled every 15 s by the v2 package's `resource_sampler.py`. **They are sampled lower bounds, not true peaks or RSS.** Kernel 5.15 has no `memory.peak`.

| Stage | Wall | Max memory, sampled lower bound | Limit | oom_kill | Throttled periods | Throttled time | CPU used |
|---|---|---|---|---|---|---|---|
| `reference_a` (059) | 1.48 h | 56.9 GiB | 96 GiB | 0 | 34% | 2.0 min | 23.6 core-h |
| `reference_b` (090) | 1.45 h | 57.0 GiB | 96 GiB | 0 | 59% | 1.6 min | 23.1 core-h |
| v3 initialization | 1.91 h | 57.1 GiB | 288 GiB | 0 | 29% | 2.5 min | 30.4 core-h |
| v3 optimization | 5.89 h | **209.0 GiB** | 288 GiB | 0 | 33% | 9.9 min | 94.0 core-h |
| v3 independent grade | 1.73 h | 57.8 GiB | 288 GiB | 0 | 33% | 2.1 min | 27.5 core-h |

- The optimization peak (209.0 GiB, during each gradient) is consistent with the roughly 208 GiB seen on AWS.
- In total, about 199 core-hours were used.
- The replica pod held 16 CPU for 10.9 h. That includes 1.3 h spent waiting for the control verdicts before any CFD started.
- There were no OOMs, evictions or restarts. The nodes reported no pressure.

## Identities

| Item | sha256 |
|---|---|
| v2 image (v3 checks and replica) | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e` |
| Control image (v2 plus `task_documents.json` only) | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:7c1fb226f1df0e7b526cad1570f22538a5935c0a33ca4d86a0d77791aaa57587` |
| `agents/tasks/task_documents.json` (added file) | `396865584f718036b5bc9c3a9e500364b582eb8be59473d80e4ecd898e24a6e0` |
| v3 `pilot-package.tar.gz` | `86326a1c06566491cbadb7bd16f5b422ae14562a3365b9b760f0529c2fca9ba1` |
| `initial_release.json` | `ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043` |
| `pilot_release.json` | `97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4` |
| `resource_sampler.py` (unchanged v2 copy) | `670227502d3b84f4e4b88b3cba840b6cf7a6e31715c0ba8a2fc1432d6afd4a5f` |
| `v3_runtime_record.py` | `bb0c19638505d786bf7462a65b2c8e46e403c9fb053841188ddb3336054266e9` |
| `v3-checks.sh` | `63af805f0e078cbd59ab4a0f45d5fc4baf9b642b3c163b571a7d59fd4cd63571` |
| `v3-replica.sh` | `0e9449b3e8607d2b3b033a0ed0e0ff75e697237ee36d5777ab844db59646a78f` |
| Control image Dockerfile | `6e999bb0db10b6005a9c8ec254f6fe9fc83938362c94bfb15c47f508b885aecb` |

**Control image.** It is the full v2 digest plus one layer containing only `task_documents.json`, owned by 1000. Env, user, entrypoint and the other 87 layers are unchanged, and all 157 v2 repo files still match the v2 SHA256SUMS. It is an infrastructure supplement; the v2 package manifest does not cover the added file.

## Infrastructure and orchestration

- **Jobs.** All steps ran as plain Kubernetes Jobs: `backoffLimit: 0`, `restartPolicy: Never`, UID 1000, Guaranteed QoS. Node 059 Jobs carried the `capacity.together.ai/pool=single-node-only:NoSchedule` toleration. There was no hard core binding. The manifests are in `infra/`.
- **Replica gates.** The replica Job ran `infra/pvc-infra/v3-replica.sh`, which calls the handoff's explicit commands in order:
  1. it waits for both control verdicts to report `passed=true`;
  2. `initial.py` must report `delivered=true`;
  3. `run.py --stage-limit 4` must leave a terminal, non-blocked `stage-04.json` with no live optimizer;
  4. `private.py`.

  Its event log is `evidence/pvc/tree/pipeline/events.jsonl`.
- **What was not used.** No launcher, no AWS orchestration scripts, no continuation authorization, and no edits to frozen sources, tolerances or solver settings. Nothing scientific was retried.
- **Sampling wrapper.** Each grading and optimization process was wrapped only by the observational `resource_sampler.py`.

## Contents of this directory

| Path | Contents |
|---|---|
| `evidence/pvc/tree/` | 345 compact files (JSON, logs, samples, IPOPT log, transcripts), each checked against SHA256 computed on the cluster (`SHA256SUMS-included`) |
| `evidence/pvc/SHA256SUMS-cluster` | SHA256 of **every** file in the run directory (594 files, including the read-only v3 source) |
| `evidence/pvc/EXCLUDED-bulk-files.tsv` | 61 bulk files not copied (2.9 GB: CGNS meshes and solutions, `.plt`, `.dat`, `.xyz`), with size and sha256. They remain on the PVC |
| `evidence/k8s/` | Pod logs, Job and pod JSON, namespace events |
| `evidence/watchdog.log` | The cron watchdog log for this run |
| `run-b-evidence/` | Evidence package for the old numerical run `wb-adjoint-v2-20261107b`, 3.9 MB, with its own README and SHA256SUMS |
| `infra/` | Job and pod manifests, the scripts that ran from the PVC, evidence and watchdog scripts, and the control image Dockerfile |
| `infra/control-image/` | Also the local build and push logs and `chk/`, the local preflight scripts and the v2 image hash list they checked against |
| `local-checks/v3/` | The laptop-side v3 check before the cluster run: `check.py`, `run.sh`, logs, `test_core.log`, the emulated runtime records and the optimizer test transcripts |
| `input-package/` | David's handoff archive `wingbench-jisen-validation-followup-20261107.tar.gz` as received, its `.sha256`, and `JISEN_VALIDATION_FOLLOWUP_2026-11-07.md` |
| `notes/` | The two notes sent to David for this run |
| `SHA256SUMS` | SHA256 of every file in this directory |
