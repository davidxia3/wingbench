Hi David,

Launch `jisen-20261110T224729Z` is submitted and all three lanes are running. Details for your status record:

**Staging**
- I copied the archive (sha256 `505eb2323afa51a6c089ffe0903835c554708fd38b178353603343843ad40552`, verified again on the cluster) to the PVC.
- It is unpacked, unchanged, at `/results/v5-jisen/launches/jisen-20261110T224729Z`. All 20 entries in `SHA256SUMS` pass on the cluster. The control files are made read-only; the launch root stays writable for `claims/`, `logs/` and `results/`.
- The frozen source at `/results/v5-jisen/handoff/wingbench-v5-jisen-001/repo` was not modified.
- Both check certificates on the PVC still match `launch-plan.json`: `f4b9abfb…c8` for node 059 and `ac1ec75b…d6` for node 090. Their `evidence.json` hashes still match the certificates.

**Read-only dry check before submission.** In a scratch pod I repeated `run-lane.py`'s checks that come before any claim is created:
- control hashes;
- `verify_release` over all source hashes;
- `require_approval` for indices 0..5;
- certificate and evidence hashes for each lane.

All passed, and no claim was created.

**Capacity recheck at submission.** Node 059 had about 57 free CPU and 419 GiB free; node 090 had about 214 free CPU and 1177 GiB free. Nothing was evicted and no requests were changed.

**Create output** (`kubectl create -f`, 2026-11-11T04:23:30Z):

```
job.batch/wb-v5-jisen-20261110t224729z-lane-0 created
job.batch/wb-v5-jisen-20261110t224729z-lane-1 created
job.batch/wb-v5-jisen-20261110t224729z-lane-2 created
```

| Lane | Job UID | Pod | Pod UID | Node | Started (UTC) |
|---|---|---|---|---|---|
| 0, indices 0 then 3 | `fdafc4f3-d6e5-4f9a-8ca8-26b01921aa56` | `…-lane-0-plplh` | `044ac194-1030-4264-bc91-074937e55aea` | research-common-h100-059 | 04:23:38 |
| 1, indices 1 then 4 | `d46da6bd-8269-4749-acbb-07e95a238a1d` | `…-lane-1-r9hwz` | `83401488-49b9-47c0-9977-3188d7db89fa` | research-common-h100-090 | 04:23:32 |
| 2, indices 2 then 5 | `b30af4ef-9e02-4a5d-9b87-63be90d9c76f` | `…-lane-2-st8vt` | `cf7c774d-6129-4239-8214-2ffed04c3611` | research-common-h100-090 | 04:23:32 |

All three pods:
- run with Guaranteed QoS (16 CPU / 384 GiB) and 0 restarts;
- have image ID `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e`.

**Early progress (04:24Z)**
- Indices 0, 1 and 2 passed the pipeline's own allocation and runtime-certificate checks.
- Each has written `claims/index-I/reservation.json` and `results/index-I/reservation.json` and is in initialization (`initial/adjoint-v5-s01-{low,nominal,high}`, mesh attempt 00).
- Indices 3, 4 and 5 will start after their lane's first episode finishes.

**Monitoring and memory evidence**
- A read-only watchdog checks every 30 minutes.
- The frozen manifests carry no in-pod sampler. So every 60 s an external read-only sampler reads each lane pod's cgroup v2 `memory.current`, `memory.events` (including `oom_kill`), `cpu.stat` and `pids.current` through `kubectl exec` (`cat` only).
- The `memory.current` maxima are sampled lower bounds, not true peaks. `memory.peak` is not available on kernel 5.15.

We will not retry, recreate or cold-restart anything. When the lanes finish I will send the full results, logs, terminal Job and pod states, costs and hashes.

Thanks,
Jisen
