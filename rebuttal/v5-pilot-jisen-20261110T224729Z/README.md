# WingBench v5 pilot, launch jisen-20261110T224729Z: results

Prepared 2026-11-11 by Jisen Li. Cluster research-common-h100 (k3s v1.27.6), namespace `jisenli2`.

## Summary

**No episode reached a scientific outcome.** The launch ran the six approved episodes in three lanes from 04:23Z to 16:09Z on 2026-11-11. Every initialization delivered, and two independent problems stopped the episodes:

1. **Software defect in the frozen v5 release, affecting 4 of 6 episodes.** Indices 0, 2, 3 and 5 are the low- and high-task episodes. Each blocked at its first optimizer call, the proposal-zero live trim, with `ValueError: Initial benchmark protocol changed`. `campaign_v5/live_initial.py`, lines 34–36, loads the default protocol, which is nominal (`403410fc…`). It then compares that hash with the task protocol used by the initialization: low is `7ba78aef…` and high is `99f5ff27…`. These can never match. Each of these episodes has 0 search attempts and returns its starting wing.
2. **External eviction, affecting index 1 and preventing index 4.** At 16:07:01Z an SRE cluster-downsize drain of node 090 (SRE-10111) evicted lane 1 through the Eviction API. Index 1 (s01-nominal) was in search attempt 5 when its pod was killed. Index 4 (s02-nominal) never started.

Index 1 did test the v5 repairs:
- the live proposal-zero trim worked;
- all 7 completed gradient requests (14 native CL/CD adjoint solves) were accepted on the first native attempt, with the larger adjoint basis, at true relative residuals of 2.5e-11 to 5.9e-11 against the 1.1e-9 gate;
- 4 search attempts completed: 3 scientific forward failures, then one accepted step with native score 28.9805, up 0.16% from 28.9341.

It has no private grade and no terminal stage.

Nothing was retried, recreated, rerolled or resumed. All failures are preserved.

## Identities

| Item | Value |
|---|---|
| Launch package | `jisen-20261110T224729Z-launch.tar.gz`, sha256 `505eb2323afa51a6c089ffe0903835c554708fd38b178353603343843ad40552`. All 20 `SHA256SUMS` entries were verified locally and on the cluster |
| Release | `0d501cb205d5d9cfd98a2bb863981f1cf1be716b54ddd39ecd51bc69e5c455ca`, frozen source at `/results/v5-jisen/handoff/wingbench-v5-jisen-001/repo` (read-only, unchanged) |
| Authorization | `authorization.json`, `launch_authorized=true`, indices 0..5, cap 8 |
| Image | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e`. This is the `imageID` reported by all three pods |
| Check certificates | node 059 `f4b9abfb…c8`, node 090 `ac1ec75b…d6`. Unchanged; rechecked by `run-lane.py` and the pipeline |

## Lanes

The Jobs were created with `kubectl create -f lane-{0,1,2}.job.json` at 2026-11-11T04:23:30Z. Each pod requested 16 CPU / 384 GiB (Guaranteed QoS) and had 0 restarts.

| Lane | Node | Job UID | Pod | Run (UTC) | Terminal state |
|---|---|---|---|---|---|
| 0: indices 0, 3 | 059 | `fdafc4f3-d6e5-4f9a-8ca8-26b01921aa56` | `…-lane-0-plplh` | 04:23:38 → 07:42:36 | exit 1. Both episodes returned code 1, so `run-lane.py` exits 1. Job `Failed` / `BackoffLimitExceeded` |
| 1: indices 1, 4 | 090 | `d46da6bd-8269-4749-acbb-07e95a238a1d` | `…-lane-1-r9hwz` | 04:23:32 → evicted 16:07:01, gone 16:09:11 | **evicted** (`EvictionByEvictionAPI`, SRE-10111 drain). Job `Failed` / `BackoffLimitExceeded` |
| 2: indices 2, 5 | 090 | `b30af4ef-9e02-4a5d-9b87-63be90d9c76f` | `…-lane-2-st8vt` | 04:23:32 → 10:33:24 | exit 1. Both episodes returned code 1. Job `Failed` / `BackoffLimitExceeded` |

## Episodes

| Index | Episode | Task | Initialization (UTC) | Starting score (original grader) | Init core-h (estimate) | Outcome |
|---|---|---|---|---|---|---|
| 0 | adjoint-v5-s01-low | low | 04:23:44 → 06:10:34 | 18.93438473394857 | 28.5 | **blocked** at 06:10:40. Protocol defect; 0 attempts; returns the start |
| 1 | adjoint-v5-s01-nominal | nominal | 04:23:35 → 07:58:51 | 28.934085411592456 | 57.4 | **interrupted by eviction** in attempt 5. Last nomination: attempt 4, native 28.980501037934467 |
| 2 | adjoint-v5-s01-high | high | 04:23:35 → 07:19:16 | 31.873332291653366 | 46.8 | **blocked** at 07:19:23. Protocol defect; 0 attempts |
| 3 | adjoint-v5-s02-low | low | 06:10:42 → 07:42:30 | 16.50832956725999 | 24.5 | **blocked** at 07:42:36. Protocol defect; 0 attempts |
| 4 | adjoint-v5-s02-nominal | nominal | — | — | — | **never started**, because lane 1 was evicted. No claim |
| 5 | adjoint-v5-s02-high | high | 07:19:26 → 10:33:17 | 31.792962418145876 | 51.7 | **blocked** at 10:33:24. Protocol defect; 0 attempts |

Notes on the table:
- All starting scores equal the roster's `historical_ld`.
- For the four blocked episodes the failing call is recorded as `infrastructure_failure` (0.003 rank-hours, no CFD) in `optimization/workers/oracle-00000/responses/00000.json`. `stage-08.json` reports `phase=blocked` and `pipeline-status.json` reports `phase=blocked` with "Search blocked; do not restart".

### Index 1 oracle calls

| Worker | Request | Status | Key numbers | Rank-h |
|---|---|---|---|---|
| oracle-00000 | proposal-zero forwards, Mach 0/1/2 (live trim) | ok | CL 0.41002 / 0.41001 / 0.41005. Five benchmark trim solves each; final trim residuals 7.7e-11 / 7.5e-11 / 2.6e-11 against the 1e-10 gate. One intermediate Mach 2 trim solve was at 1.2e-6 | 17.4 / 18.3 / 20.0 |
| oracle-00000 | proposal-zero gradients, Mach 0/1/2 | ok | true relative residuals 2.5e-11 to 5.9e-11 against 1.1e-9, first native attempt | 4.8 / 5.3 / 5.7 |
| oracle-00002/3/4 | attempts 1–3, Mach 0 forward | **scientific_failure** | relative residuals 2.28e-10, 2.82e-10, 6.86e-10 against 1e-10. IPOPT cut back alpha each time | 5.2 / 5.5 / 4.7 |
| oracle-00005 | attempt 4: forwards and gradients | ok | CL 0.41003 / 0.41001 / 0.41004; gradients 2.5e-11 to 5.9e-11. **Native score 28.9805** | 3.6+3.2+3.6 / 4.2+4.6+4.9 |
| oracle-00007 | attempt 5: forwards | ok | CL 0.4023 / 0.4019 / 0.3939; residuals ≤ 3.7e-11 | 3.9 / 3.9 / 4.1 |
| oracle-00007 | attempt 5: gradient Mach 0 | ok | 2.7e-11 to 4.4e-11 | 4.1 |
| oracle-00007 | attempt 5: gradient Mach 1 | **no response, killed by eviction** | — | unknown |

The recorded oracle cost is 126.9 allocated rank-hours over 19 responses. The cost of the killed request is unknown.

Index 1 has no `stage-08.json`, `pipeline-status.json`, `claims/index-1/process-status.json` or `lane-1-status.json`, because the process was killed and could not write them. `nominations.json` shows `search_attempts=4`, and nominations at stages 0–2 point to the starting wing and at stage 4 to the attempt-4 wing.

## Resources

The frozen manifests carry no in-pod sampler. A read-only external sampler read each pod's cgroup v2 counters every 60 s with `kubectl exec cat`. The memory figures are **sampled lower bounds of `memory.current`, not true peaks**; `memory.peak` does not exist on kernel 5.15.

| Lane | Wall | Allocated core-h (16 × wall) | CPU used (cgroup `usage_usec`) | Sampled max memory | OOM kills | Throttled periods |
|---|---|---|---|---|---|---|
| 0 | 3.32 h | 53.1 | 52.5 core-h | 59.1 GiB | 0 | 32% (3.8 min total) |
| 1 | 11.72 h | 187.6 | 186.6 core-h | **257.6 GiB** (12:25Z, during an adjoint) | 0 | 41% (26.0 min total) |
| 2 | 6.16 h | 98.6 | 98.0 core-h | 58.2 GiB | 0 | 39% (14.4 min total) |
| Total | | **339.3** | 337.1 core-h | | | |

- Index 1's memory rose to about 160 GiB with three Mach states resident during the proposal-zero trims, and to about 250–258 GiB during each three-Mach adjoint sequence.
- The worker reports per-rank high-water marks of up to 17.4 GiB per rank during gradients.
- The 384 GiB request left roughly 125 GiB of headroom over the sampled maximum.

**Sampler gap.** The first sampler version exited by mistake at 06:20:38Z. A transient API-server `ServiceUnavailable` made a failed query look like "no live pods". Samples are missing from 06:19:24Z to 06:21:04Z. Version 2, in `infra/external-sampler.sh`, exits only when all three Jobs are confirmed terminal.

## Interpretation limits

- No episode produced a private grade, so there is no independent original-grader confirmation of any search result. Index 1's 28.9805 is a native in-search score.
- The protocol defect affects every low and high task in this release. A corrected release needs new approval before those episodes can be run.
- Index 1 was interrupted mid-search by an external drain. It is not a scientific termination and must not be cold-restarted under the current launch rules. Index 4 never ran.
- The historical whole-host runtime fingerprint mismatch (`matches_campaign=false`, observed `ce490678…`) is documented again in every initialization's constructor check.
- Episode-1 angle finite-difference validation remains unavailable. This pilot is not a new gradient certification.

## Storage

- **Kept on the PVC (`wb-verify-results`):**
  - the full raw launch tree at `/results/v5-jisen/launches/jisen-20261110T224729Z`, 493 files and 4.2 GB, including 60 mesh and flow files (`.cgns` and `.plt`, 4.39 GB);
  - a tar archive of that tree, `/results/v5-jisen/launches/jisen-20261110T224729Z-collection/jisen-20261110T224729Z-full-raw.tar`, sha256 `f3ecfa8b3b9eb6b516d09946bbdd7328fefbda92f8bf6fed882611af5ad667a8`.
- **Not deleted:** the PVC and the terminal lane 0 pod. Lane 1 and lane 2 pods were removed by the node drain.
- **No live work remains.** All three Jobs are terminal.

## Contents

| Path | Contents |
|---|---|
| `results-tree/` | Compact copy of the launch tree, 433 files: every file except `.cgns` and `.plt`. Includes all claims, reservations, initialization records, grades, `stage-08`, transcripts, oracle requests, responses and logs, the IPOPT log, the per-index logs and the lane status files |
| `collection/` | `SHA256SUMS-all-files-cluster` (all 493 files, computed on the PVC), `SHA256SUMS-included`, `EXCLUDED-bulk-files.tsv`, `all-files-with-size.tsv`, the full-archive hash and the collection time |
| `records/` | Incident notes 01–05 with pod, Job and event JSON and the extracted files; `external-cgroup-samples.jsonl`; `watchdog.log` |
| `k8s/` | Create output; Job and pod JSON at start and end; lane pod logs; the lane 1 pod during eviction; the node 090 object (the cordoning SRE's email address in its annotation is replaced by `[redacted]`, here and in `records/incident-05`); events |
| `launch-package/` | The launch archive as received, with its hash computed on the cluster |
| `infra/` | `watchdog.sh`, `external-sampler.sh` (v2) and `external-sampler.v1.sh` |
| `SHA256SUMS` | SHA256 of every file in this package |

## Verify

```bash
sha256sum -c SHA256SUMS
cd results-tree && sha256sum -c ../collection/SHA256SUMS-included   # 433 files, computed on the cluster
```
