# Jisen: finish destination validation only

Date: November 7, 2026 (America/Chicago)

Thanks for the detailed report. The 39 numerical checks passing is useful. The missing `agents/tasks/task_documents.json` was a packaging omission on our side. The original failed certificate and both earlier run directories must remain unchanged.

**Please finish validation only. Do not launch, take ownership of, or migrate any production episode. We will start production on Quest or AWS.** This handoff authorizes two original-grader controls and one isolated, four-attempt validation replica of our current v3 optimizer. It does not authorize a 30-episode batch, continuation to 32 attempts, or replacement starting wings.

## What changed since your last package

The v2 numerical qualification is still useful evidence, but its old SLSQP optimization pilot is not the current baseline. The current method uses ADflow discrete adjoints with open-source IPOPT through cyipopt, MUMPS, 38 geometry variables and three angles of attack. Please do **not** resume the old launcher into its old optimization pilot.

The accompanying `wingbench-jisen-validation-followup-20261107.tar.gz` contains:

- `pilot-package.tar.gz`: the exact frozen v3 source archive used on AWS, including both initialization and pilot manifests and the previously missing task-document file;
- `task_documents.json`: a separate copy for the original-grader control image;
- `run_grade_v2.py`: the unchanged original-grader control runner from our v2 package;
- this handoff, `MANIFEST.json`, and `SHA256SUMS`.

The outer archive has a separate `.sha256` companion. Verify that, extract, then run `sha256sum -c SHA256SUMS`. Extract the nested pilot archive into a **new** directory; it contains `repo/`. Do not overlay or modify your frozen v2 tree.

Pinned nested archive SHA256:

```text
86326a1c06566491cbadb7bd16f5b422ae14562a3365b9b760f0529c2fca9ba1  pilot-package.tar.gz
```

Pinned manifests within its `repo/rebuttal/adjoint/campaign_v3/`:

```text
ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043  initial_release.json
97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4  pilot_release.json
```

Both manifests were checked locally against the nested pilot source: all 133 initialization files and all 186 pilot files match. The included task-document SHA256 is `396865584f718036b5bc9c3a9e500364b582eb8be59473d80e4ecd898e24a6e0`.

## 1. Preserve and return the existing numerical evidence

Please send the compact 36 MB evidence directory from `wb-adjoint-v2-20261107b`, including its checksums, raw anchor/point results, coverage summary, failed certificate, package/runtime identities, and Kubernetes records. We have your summary but have not independently audited those raw files yet.

Do not repeat all 39 numerical tasks just to repair two control tasks that failed before CFD. Keep the original failed certificate failed. Any combined assessment must explicitly identify the old numerical run and the new controls; it must not claim that a single old run passed. Reuse is conditional on matching scientific source, runtime, reference mesh and numerical protocol hashes. A mismatch is a blocker to reuse, not permission to ignore a pin.

## 2. Repair packaging and run the two original-grader controls

Create a new, separately tagged control image derived from the **full recorded digest** of your v2 image. Add the supplied `task_documents.json` at `/workspace/repo/agents/tasks/task_documents.json`. Retain all other scientific files unchanged. Record the old image digest, new digest, added-file hash and build recipe as an infrastructure supplement. Do not claim the old v2 package manifest covers the added file.

Before expensive work, confirm that imports, `load_tasks()`, and construction of the original grader work as UID 1000. The original grader must not inherit `OMP_*`, `OPENBLAS_*`, `MKL_*`, or `ADFLOW_*` overrides; use its existing clean parent environment.

Use a fresh dedicated PVC subdirectory, for example `/results/wb-validation-followup-20261107c`. Run one control on each node, 16 CPU and 96 GiB per pod. Each pod needs its own `/workspace` and scratch directory. Run the unchanged v2 control runner already inside that image:

```bash
/bin/bash /workspace/repo/rebuttal/container_entry.sh python3 \
  /workspace/repo/rebuttal/compute/jisen_verify_v2/run_grade.py \
  --out /results/wb-validation-followup-20261107c/reference_a
```

Use `reference_b` on the other node. Both paths must be new. Require `verification_result.json: passed=true`, valid original `grade.json`, and the existing 0.5% per-condition coefficient agreement check. Process exit status alone is insufficient. Preserve failures; do not retry a convergence failure or timeout.

Your existing narrowly scoped `kubectl` shim is acceptable if its exact source/hash is retained. Alternatively, in a **new transport version only**, make the read-only `get --ignore-not-found` parser interpret successful blank output as `{"items": []}`. Do not apply that normalization to failed commands. Keep bounded retries restricted to read-only transient API failures. No blind Job recreation or automatic scientific retries.

## 3. Prepare the current v3 runtime before its validation replica

This is a different source package from your v2 verifier. Mount the extracted frozen v3 `repo/` read-only at `/workspace/repo` in a new image/pod. Retain the original solver toolchain and CST checkout/path. Required optimizer versions are **cyipopt 1.4.1 and IPOPT 3.13.4 with MUMPS**. Do not install floating/latest versions or bypass runtime checks.

The source archive is **not a container image**, and your current v2 image is not assumed to contain the optimizer. Check this before launching CFD. If the required optimizer build is absent, report that prerequisite and the exact image/build details; do not substitute another solver or spend compute on the old SLSQP pilot. We can supply the matching runtime separately. A local Docker image ID from AWS is not a pullable registry reference.

Inside the prospective v3 pod, verify both source manifests, imports and runtime pins. The v3 initialization `--preflight` below exercises the grader constructor without CFD. Run the real-optimizer synthetic tests with single-threaded BLAS:

```bash
/bin/bash /workspace/repo/rebuttal/container_entry.sh \
  env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 /workspace/repo/rebuttal/adjoint/campaign_v3/test_core.py \
  /results/wb-validation-followup-20261107c/optimizer-tests
```

These cover accounting, failed evaluations, failed gradients and exact callback replay. Record runtime/library hashes and CPU identity. An AMD destination need not have the AWS CPU model, but its local replay must use an unchanged runtime and matching requests. This validation does not authorize migration of an active optimizer.

## 4. One isolated v3 validation replica, then independent grading

Run this only after both original-grader controls and v3 preflight/tests pass. Use the low-lift starting wing at roster index 0. It has already exercised real optimizer steps on AWS, so this is deliberately an **integration control**, not a random performance sample. Do not count it in the 30 production episodes or select among replicas by score.

The frozen code retains its internal episode name `adjoint-v3-s01-low`. Identify this copy externally as `jisen-validation-only/wb-validation-followup-20261107c`. Keep its entire output tree separate. Do not upload it into the AWS campaign prefix or modify the central ownership registry. Do not copy live AWS optimizer state.

Run one 16-CPU worker with **288 GiB** for native optimization, 96 GiB for original grading, UID 1000 and single-threaded BLAS in the optimizer only. The previous sampled 70.8 GiB anchor usage does not establish memory needs for the current three-condition optimizer: AWS has already observed roughly 208 GiB cgroup usage. Prefer running this one worker alone after the controls, rather than eight optimizer replicas. Keep the existing node selector and the narrowly scoped `capacity.together.ai/pool=single-node-only:NoSchedule` toleration where needed; no hard core binding.

Commands below run inside the pod, with the new PVC root mounted at the stated path:

```bash
# Preflight only; no CFD. Use a separate output directory.
/bin/bash /workspace/repo/rebuttal/container_entry.sh python3 \
  /workspace/repo/rebuttal/adjoint/campaign_v3/initial.py \
  --index 0 --preflight \
  --out /results/wb-validation-followup-20261107c/preflight \
  --release-sha256 ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043

# Fresh original-grader initialization for this validation replica.
/bin/bash /workspace/repo/rebuttal/container_entry.sh python3 \
  /workspace/repo/rebuttal/adjoint/campaign_v3/initial.py \
  --index 0 --out /results/wb-validation-followup-20261107c/initial \
  --release-sha256 ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043

# Only after initialization.json confirms delivered=true.
/bin/bash /workspace/repo/rebuttal/container_entry.sh \
  env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 \
  python3 /workspace/repo/rebuttal/adjoint/campaign_v3/run.py \
  --initial /results/wb-validation-followup-20261107c/initial/adjoint-v3-s01-low/initialization.json \
  --out /results/wb-validation-followup-20261107c/optimization/adjoint-v3-s01-low \
  --release-sha256 97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4 \
  --stage-limit 4

# Separate grading process, clean original-grader parent environment.
# Require a terminal, non-blocked stage-04.json; no live optimizer remains.
/bin/bash /workspace/repo/rebuttal/container_entry.sh python3 \
  /workspace/repo/rebuttal/adjoint/campaign_v3/private.py \
  --initial /results/wb-validation-followup-20261107c/initial/adjoint-v3-s01-low/initialization.json \
  --stage /results/wb-validation-followup-20261107c/optimization/adjoint-v3-s01-low/stage-04.json \
  --out /results/wb-validation-followup-20261107c/private/adjoint-v3-s01-low/checkpoint-4
```

Do not execute the bundled AWS orchestration scripts: they contain AWS-specific ownership, storage and host-stop behavior. Your agent may wrap these explicit commands in Kubernetes Jobs, with `backoffLimit: 0`, `restartPolicy: Never`, fresh paths and collected termination evidence. Orchestration changes must not modify frozen numerical sources. No continuation-authorization file is supplied or authorized.

Do not impose the old 2.2-hour batch estimate as a total timeout. These are different workloads; the four-attempt replica plus original grades can take many hours. Keep the existing per-Mach limits: forward 7,200 s and gradient 14,400 s. Native forward acceptance remains 1.1e-12; adjoint acceptance remains 1.1e-9, internal targets 1e-12. No numerical changes for this follow-up.

## 5. What constitutes completion

Return separate verdicts for numerical qualification, both original-grader controls, runtime/integration tests, actual v3 optimization, and independent grading. A successful Python exit is not proof of optimizer convergence or improvement.

For the v3 replica, report search attempts reserved/completed/failed, actual changed geometries, native forward and adjoint checks, starting score, nominated score, and final original-grader score. Require valid final delivery and `native_original_agreement_passed=true` (0.5% agreement) for a changed-wing regrade. If it returns the starting wing, report **zero improvement and no new-wing regrade**; that does not establish changed-wing end-to-end validation. If a scientific failure prevents completion, return the failure and stop—no replacement wing, looser tolerance or repeated scientific attempt.

Infrastructure failures may be corrected in a separately versioned deployment after their cause is established. Preserve failed runs and costs, verify the old process is dead, and record fresh reservations. Do not reset or erase attempted scientific work. For an interrupted live optimizer, return evidence before restarting; replay compatibility must be checked, and an arbitrary cold restart is not authorized.

Send compact JSON/logs and SHA256SUMS first, including the build recipe, full image digest, source verification, synthetic tests, reservations/transcript/stage, nominations, raw solver checks, original grades, private result, CPU/memory records, and Kubernetes events. Keep bulk meshes/solutions on the dedicated PVC. Label 15-second `memory.current` maxima as sampled lower bounds, not true peaks. No public uploads; agree on a private destination if the compact archive is inconvenient to send.

**Stop after this validation report. No production work moves to your cluster under this handoff, even if every check passes.**
