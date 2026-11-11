# Incident 05: lane 1 evicted by a cluster-downsize drain of node 090 (SRE-10111)

Recorded 2026-11-11 about 16:08Z. Nothing was retried, recreated, patched or deleted by us.

## What happened

- **Drain.** At 16:07:01Z the lane 1 pod `wb-v5-jisen-20261110t224729z-lane-1-r9hwz` on node 090 received `DisruptionTarget=True`, reason `EvictionByEvictionAPI`. The kubelet event was "Stopping container pilot". The pod's `deletionTimestamp` was 16:09:01Z, given the 120 s grace period, and the pod was gone by 16:09:11Z.
- **Job.** At 16:07:02Z the Job `wb-v5-jisen-20261110t224729z-lane-1` reached `Failed=True` / `BackoffLimitExceeded` (`backoffLimit` 0).
- **Node 090** was cordoned (`unschedulable=true`), with the extra taint `ask-pablo-dont-remove=true:NoSchedule` and the annotation `cordon-reason: "SRE-10111: cluster downsize, node returned per Pablo (idle holders released). Cordoned 2026-11-11 by [redacted]"`.
- **Scope of the drain.** It was part of a cluster-wide downsize. Nodes 001, 044, 048, 057, 064, 068, 080, 087, 088, 090, 107, 116 and 125 carry the same SRE-10111 cordon reason. Node 059, where our GPU holder runs, was not cordoned and was not affected.
- **Lane 2 pod.** It had already terminated at 10:33Z and was also removed from 090 by the drain. Its terminal state was recorded at 10:52Z in incident 04.

## State of the interrupted episode (index 1, adjoint-v5-s01-nominal)

- Initialization delivered at 07:58:51Z. The starting wing scored 28.934085411592456 (nominal).
- **Proposal zero**, the free live trim and adjoint: 3 forwards passed the 1e-10 gate after trimming, and 3 native gradients were accepted with true relative residuals of 2.5e-11 to 5.9e-11 against the 1.1e-9 gate.
- **Search attempts 1–3** were line-search trials. Each Mach 0 forward failed the v5 numerical checks with relative residuals 2.28e-10, 2.82e-10 and 6.86e-10 against the 1e-10 gate. Each is a scientific failure and was not retried.
- **Search attempt 4** passed: all 3 forwards and 3 gradients were accepted. Native score 28.980501037934467, a gain of 0.16% over the start. It was the current nomination (`nominations.json`, `search_attempts=4`).
- **Search attempt 5**: all 3 forwards completed. CL was 0.4023, 0.4019 and 0.3939; trim is enforced by IPOPT's constraint, not the forward gate. The Mach 0 gradient was accepted. The **Mach 1 gradient** (`oracle-00007/requests/00004.json`) was running and has no response.
- **The pod was killed before the pipeline could write anything.** There is no `pipeline-status.json` for index 1, no `claims/index-1/process-status.json` and no `lane-1-status.json`. No `stage-08.json` exists, and no private grade was run.
- **Index 4** (adjoint-v5-s02-nominal) never started. It has no claim.

Under the launch rules there is no cold restart or resume of index 1 and no recreation of lane 1. A follow-up for index 1 or index 4 needs a new authorization from David.

## Evidence

- `jobs.json` and `pods.json` from the moment of eviction, `jobs-after.json` after termination, and `lane-1-pod-during-eviction.json`.
- `node-090.json`: cordon, taints and annotation.
- `events-all-namespace.json`.
- `lane-1-pod.log` is empty, because `run-lane.py` writes per-index logs on the PVC rather than to stdout.
- The full index-1 tree is in the collection, in compact form plus the bulk files kept on the PVC.
