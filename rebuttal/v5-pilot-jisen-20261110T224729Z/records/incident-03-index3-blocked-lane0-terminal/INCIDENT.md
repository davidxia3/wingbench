# Incident 03: index 3 (adjoint-v5-s02-low) blocked; lane 0 terminal

Recorded 2026-11-11 about 07:52Z. Nothing was retried, recreated, patched or deleted.

- **Index 3.** Initialization delivered. The starting wing scored 16.50832956725999 under the load-low protocol, `7ba78aef…`. The first proposal-zero forward returned `infrastructure_failure`, `ValueError: Initial benchmark protocol changed`. `stage-08` reports `phase=blocked` with 0 search attempts. The pipeline finished blocked at 07:42:36Z with return code 1. The cause is the same as in incidents 01 and 02.
- **Lane 0** (node 059, indices 0 and 3). `lane-0-status.json` records both episodes with return code 1 and `automatic_retry=false`. `run-lane.py` exits 1 unless every return code is 0 or 2, so the pod `wb-v5-jisen-20261110t224729z-lane-0-plplh` terminated with exit 1 (`Error`) at 07:42:36Z after 3 h 19 min, with 0 restarts. The Job condition is `Failed=True`, reason `BackoffLimitExceeded`, because `backoffLimit` is 0.
- **This is the designed lane outcome for two blocked episodes, not a pod or node fault.** There was no OOM and no eviction. Per the launch rules, the Job is not recreated.

Evidence: `pods.json`, `jobs.json`, `events.json`, `lane-0-pod.log`, and in `files/` the index-3 claims, pipeline status, `stage-08`, transcript, binding, the oracle-00000 directory, the initialization records, `logs/index-3.log`, `claims/lane-0` and `lane-0-status.json`.
