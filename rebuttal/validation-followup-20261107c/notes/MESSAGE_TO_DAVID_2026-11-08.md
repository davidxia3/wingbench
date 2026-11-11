Hi David, thanks for the follow-up. Here is where things stand as of 2026-11-08 10:00Z. Results will follow in a separate note.

**1. Evidence from run b (attached)**
- `wb-adjoint-v2-20261107b-evidence.tar.gz`: 3.9 MB, sha256 `eaaea0ffffcc54ad07a6031d4c6a8d8042bcc3456b416767d4bbe1a9686ff16a`.
- Contents:
  - the full run directory (765 files, each checked against SHA256 computed on the cluster);
  - both certificates, unchanged; the strict certificate stays failed;
  - run a, the Kubernetes records, the operator manifests, and the kubectl shim (sha256 `e9de084c…7bfb2a`).
- `README.md` lists the identities. `SHA256SUMS` covers all 787 files.

**2. Original-grader controls (running since 09:43Z)**
- **Control image.**
  - `FROM` the full v2 digest `sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e`, plus one layer containing only `task_documents.json` (sha256 `39686558…a6e0`) at `/workspace/repo/agents/tasks/`. The two-line Dockerfile is attached.
  - The new digest is `sha256:7c1fb226f1df0e7b526cad1570f22538a5935c0a33ca4d86a0d77791aaa57587` (private GHCR).
  - Env, user, entrypoint and the other 87 layers are unchanged. All 157 v2 repo files still match the v2 SHA256SUMS. This is an infrastructure supplement; the v2 package manifest does not cover the added file.
- **Checks before CFD.** We first checked as UID 1000 that imports, `load_tasks()`, `control()` and `LocalGrader` construction all work, and that no `OMP_*`, `OPENBLAS_*`, `MKL_*` or `ADFLOW_*` variable reaches the parent process. In both pods the grader's runtime probe completed, including the 16-rank MPI smoke test.
- **How they run.**
  - Two plain Jobs with no launcher: `backoffLimit: 0`, `restartPolicy: Never`, 16 CPU / 96 GiB each.
  - `reference_a` runs on node 059 and `reference_b` on node 090. Outputs go to new paths under `/results/wb-validation-followup-20261107c/`.
  - Each runs the unchanged `run_grade.py` through `container_entry.sh`. It is wrapped only by the v2 package's own `resource_sampler.py`, which records 15 s CPU and memory samples.
  - No retries.

**3. v3 runtime checks: passed on node 059**
- **Runtime.** The v2 image already contains cyipopt 1.4.1 linked to IPOPT 3.13.4 with MUMPS (`libcoinmumps.so.3`), numpy 1.21.5 and Python 3.9.12. That meets the handoff's pins, so we followed its gates without installing anything.
- **Source.**
  - We copied the pinned `pilot-package.tar.gz` (sha256 `86326a1c…9ba1`) to the PVC, extracted it there (187 files) and made it read-only.
  - It is mounted read-only at `/workspace/repo` in pods that use the original v2 image digest.
- **Results in the pod** (AMD EPYC 9534, cgroup 16 CPU):
  - both release manifests verify;
  - all 9 `runtime_expected_sha256` entries match;
  - `optimizer_runtime()` reports 1.4.1 and [3, 13, 4] with single-threaded BLAS;
  - `initial.py --index 0 --preflight` passed with `CFD_calls: 0`;
  - `test_core.py` passed.
- **Optimizer file hashes.** The attached `v3-runtime-local-emulated-20261108.json` lists the 17 optimizer file hashes, in the same form as `run.py`'s `runtime.json`. The pod's own record is on the PVC and will come with the results. Could you compare them against AWS?

**4. v3 validation replica (queued)**
- **Setup.** One Job on node 059: 16 CPU / 288 GiB, UID 1000, with the existing toleration.
- **Gates.** The Job waits until both controls write `verification_result.json` with `passed=true`. Then it runs your explicit commands in order, each gated on the previous one:
  1. `initial.py --index 0`, which must report `delivered=true`;
  2. `run.py --stage-limit 4` with single-threaded BLAS, which must produce a terminal, non-blocked `stage-04.json` with no live optimizer left;
  3. `private.py` as a separate process with the clean parent environment.
- **Stop rules.** If a control fails, the replica does not start. Nothing scientific is retried, and there is no continuation past stage 4.
- **Labelling.** The output is labelled `jisen-validation-only/wb-validation-followup-20261107c` and stays in its own tree on our PVC.

Thanks,
Jisen
