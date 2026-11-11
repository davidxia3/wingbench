# Incident 02: index 2 (adjoint-v5-s01-high) blocked. Same cause as incident 01

Recorded 2026-11-11 about 07:21Z. Nothing was retried, recreated, patched or deleted.

- **Initialization.** Delivered at 07:19Z. The starting wing scored 31.873332291653366 under `superwing-load-high-cl066-qualification-v1`, `original_protocol_sha256` `99f5ff27a3ce61605763f5dbe91fb5f852b432a6bf08314d8e469ecca339b4b0`.
- **First oracle request.** `op=forward` with `proposal_zero=true`. Status `infrastructure_failure`, `ValueError: Initial benchmark protocol changed`.
- **stage-08.json.** `phase=blocked`, 0 search attempts, 1 recorded oracle call, starting wing returned.
- **Pipeline.** "Search blocked; do not restart", process return code 1, finished 07:19:23Z.
- **Lane 2.** Went on to index 5 (s02-high) as designed.

Cause, as in incident 01: `live_initial.py:34` loads the default nominal protocol (`403410fc…`) and compares it with the high-task initialization protocol (`99f5ff27…`). The prediction in incident 01 has now been confirmed for a high task.

Evidence in `files/`: claims, pipeline status, `stage-08.json`, transcript, binding, the oracle-00000 directory, the initialization records and `logs/index-2.log`. Pod and event JSON are in this folder.
