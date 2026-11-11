# Incident 04: index 5 (adjoint-v5-s02-high) blocked; lane 2 terminal

Recorded 2026-11-11 about 10:52Z. Nothing was retried, recreated, patched or deleted.

- **Index 5.** Initialization delivered. The starting wing scored 31.792962418145876 under the load-high protocol, `99f5ff27…`. The first proposal-zero forward returned `infrastructure_failure`, `ValueError: Initial benchmark protocol changed`. `stage-08` reports `phase=blocked` with 0 search attempts. The pipeline finished blocked at 10:33:24Z with return code 1. The cause is the same as in incidents 01–03.
- **Lane 2** (node 090, indices 2 and 5). Both episodes returned code 1. The pod `wb-v5-jisen-20261110t224729z-lane-2-st8vt` exited 1 at 10:33:24Z after 6 h 10 min, with 0 restarts. The Job is `Failed=True` (`BackoffLimitExceeded`, `backoffLimit` 0).
- **This is the designed lane outcome, not a pod or node fault.** There was no OOM and no eviction. The Job is not recreated.

With this, all four non-nominal episodes (0, 2, 3, 5) are blocked, as predicted in incident 01. Only lane 1 remains: index 1 is in optimization, then index 4.

Evidence: pod, Job and event JSON, `lane-2-pod.log`, and in `files/` the index-5 claims, pipeline status, `stage-08`, transcript, binding, the oracle-00000 directory, the initialization records, `logs/index-5.log`, `claims/lane-2` and `lane-2-status.json`.
