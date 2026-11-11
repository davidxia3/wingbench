# WingBench v5 jisen-001: software and runtime checks

Prepared 2026-11-10 by Jisen Li. Cluster research-common-h100 (k3s v1.27.6), namespace `jisenli2`.

**Result: both checks passed. No pilot was run.** These are checks only and made zero CFD or adjoint calls. `pilot-authorization.NOT-APPROVED.json` was not touched.

## Identities

| Item | Value |
|---|---|
| Handoff archive | `wingbench-v5-jisen-001.tar.gz`, sha256 `c52cf51239e0bfb618d90a5b8266812137e8639928779ae0a2a62c8dbff0da98` |
| Outer `.sha256` | **Not received.** The value above was computed locally and again on the cluster. Please confirm it. |
| Internal hashes | All 195 `CONTENTS.json` entries match, checked locally and on the cluster. No unlisted files. |
| `release_sha256` | `0d501cb205d5d9cfd98a2bb863981f1cf1be716b54ddd39ecd51bc69e5c455ca` |
| Image | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e`, the v2 validation image, unchanged |

## Why the v2 image

The handoff warns not to assume the older validation image includes IPOPT. We checked rather than assumed:

- In this image, the pyenv Python 3.9.12 that `container_entry.sh` activates has cyipopt 1.4.1, linked to IPOPT 3.13.4 with MUMPS (`libcoinmumps.so.3`).
- The 9 `runtime_expected_sha256` pins in the v5 `release.json` are identical to the v3 pins, and those matched in this image during the 2026-11-08 v3 checks.
- `container_entry.sh` is byte-identical to the v3 copy.

The v5 preflight then confirmed every pin on both nodes:

- Python 3.9.12, Open MPI 4.1.6, PETSc 3.15.5;
- NumPy 1.21.5, SciPy 1.7.3, mpi4py 3.1.5;
- cyipopt 1.4.1 with IPOPT [3, 13, 4].

We modified neither the source nor the image.

## How the checks ran

- **Source.** The archive was copied to the PVC and its hash verified there. It was extracted under `/results/v5-jisen/handoff/` and made read-only with `chmod -R a-w`.
- **Mounts.** The handoff root is mounted read-only so that it appears as `/workspace` and its repo as `/workspace/repo`. In practice, `run-checks.sh` and `check-authorization.json` are subPath file mounts at `/workspace/`, and `repo/` is mounted at `/workspace/repo`. This makes `HANDOFF=/workspace` and `REPO=/workspace/repo` inside `run-checks.sh`, consistent with `container_entry.sh`. The image's own `/workspace` stays writable for `.wingbench-locks`.
- **Jobs.** One plain Job per node, `wb-v5-jisen-001-check-059` and `wb-v5-jisen-001-check-090`:
  - 16 CPU and 384 GiB, with requests equal to limits (Guaranteed QoS);
  - UID/GID 1000, all capabilities dropped, `backoffLimit: 0`, `restartPolicy: Never`, no retries.
  - Only node 059 carries the `capacity.together.ai/pool=single-node-only:NoSchedule` toleration; node 090 has no taint.
- **Command.** Each Job runs `infra/v5-check.sh`. That script records the cgroup limits and hashes of the mounted files, then runs the unchanged `run-checks.sh` under the v2 package's `resource_sampler.py`.
- **Provenance variables.**
  - `WINGBENCH_CHECK_OUT` is `/results/v5-jisen/checks/node-<node>-check-001`, a fresh path.
  - `WINGBENCH_NODE_NAME` comes from `spec.nodeName`.
  - `WINGBENCH_RUN_ID` is `wb-v5-jisen-001-check-<node>-001`.

## Results

| | Node 059 | Node 090 |
|---|---|---|
| `certificate.json` `passed` | **true** | **true** |
| `scientific_calls` | 0 | 0 |
| Runtime pins (9 scientific hashes and versions) | match | match |
| Regression suite | 39 tests, OK | 39 tests, OK |
| Real-IPOPT near-bound control, default push | reproduces the old failure: "invalid number (NaN or Inf) from the NLP" | same |
| Real-IPOPT near-bound, fixed (1e-10) | "terminated successfully at a locally optimal point" | same |
| Real-optimizer smoke | passed, `CFD_calls` 0 | passed, `CFD_calls` 0 |
| Original grader constructor, 16-rank MPI smoke | passed (`requested_ranks` 16, rc 0) | passed |
| Filesystem: exclusive create, atomic rename, exclusive mkdir, flock | all pass | all pass |
| cgroup CPU quota and memory limit | 16.0 and 384 GiB | 16.0 and 384 GiB |
| Affinity: logical CPUs and physical cores | 240 and 120 | 240 and 120 |
| CPU | AMD EPYC 9534 | AMD EPYC 9534 |
| Pod start and end (UTC) | 22:30:38 → 22:30:47 | 22:30:37 → 22:30:46 |
| Exit code, restarts | 0, 0 | 0, 0 |

The preflight records `physical_core_exclusivity_verified=false`. Kubernetes CPU units may be hardware threads, and we do not claim exclusive cores.

The resource sampler took only 2 samples because each check ran for about 9 s. The maxima of 105 MiB and 79 MiB are sampled lower bounds and are not meaningful for planning a pilot.

## Open item: original-grader runtime fingerprint

The constructor check records `matches_campaign=false`:

- expected `60263209c65d73810a768021b8043ced6038a9c55b0c7feaf37c82168e73314d`;
- observed `ce49067865f6e586df4bbb613a133762b81364f2c1df5fea64a61c431c6847f3`.

That observed value is identical on both nodes. It is also identical to every original-grader run in the 2026-11-07/08 validation follow-up, including the two controls that reproduced the recorded coefficients exactly. The full report is at `checks/node-*/grader-constructor/runtime_fingerprint_*.json`, so it can be compared field by field against the AWS report. The report includes the host platform string, `Linux-5.15.0-176-generic-x86_64-with-glibc2.31`.

## Capacity for a later pilot

The pilot is not approved, and none was started. Free allocatable capacity on 2026-11-10, after existing requests:

| Node | Free CPU | Free memory | 16 CPU / 384 GiB pods that fit |
|---|---|---|---|
| 059 | about 57 | about 419 GiB | 1 |
| 090 | about 124 | about 837 GiB | 2 |

So at most 3 of the 6 episodes could run at once right now. The rest would have to queue. Node 090 is shared with other teams, so its free capacity can change.

## Contents

| Path | Contents |
|---|---|
| `checks/node-059-check-001/`, `checks/node-090-check-001/` | Full check output: `certificate.json`, `evidence.json`, `runtime.json`, `runtime-probe-log.json`, `regression-tests.txt`, `constructor-check.json`, `real-optimizer/`, `reservation.json`, `grader-constructor/`, `filesystem-probe/` |
| `checks/logs/` | Wrapper and stdout log for each check |
| `checks/resource_samples/` | Sampler JSONL and summaries |
| `checks-SHA256SUMS-computed-on-cluster` | SHA256 of every check file, computed on the PVC before copying (76 files) |
| `k8s/` | Job and pod JSON, pod logs, events |
| `infra/` | `v5-check.sh`, `resource_sampler.py`, the two Job manifests, the short-lived stager pod manifest, creation time, and `SHA256SUMS-pvc-infra` (hashes of the two scripts as staged on the PVC) |
| `input-package/` | The handoff archive `wingbench-v5-jisen-001.tar.gz` as received, with its locally computed hash |
| `notes/` | The note sent to David with these results |
| `SHA256SUMS` | SHA256 of every file in this package |

## Verify

```bash
sha256sum -c SHA256SUMS
sha256sum -c checks-SHA256SUMS-computed-on-cluster   # 76 files
```
