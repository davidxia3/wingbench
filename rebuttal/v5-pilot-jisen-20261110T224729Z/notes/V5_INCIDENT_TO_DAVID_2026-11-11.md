Hi David,

There is an urgent issue with launch `jisen-20261110T224729Z`. Index 0 (`adjoint-v5-s01-low`) was blocked at its first optimizer call. The cause appears to be a deterministic bug for the low and high tasks.

**What happened**
- Initialization delivered at about 06:10Z. The starting wing scored 18.9344 under `superwing-load-low-cl015-qualification-v1`, protocol sha `7ba78aef…a0a9`.
- The first oracle request was the proposal-zero forward at Mach index 0. It returned `infrastructure_failure` with `ValueError: Initial benchmark protocol changed`.
- `stage-08.json` reports `phase=blocked` with 0 search attempts. `pipeline.py` raised "Search blocked; do not restart" and exited with code 1.
- Lane 0 kept the failure, as designed, and went on to index 3. Nothing was retried, recreated or patched.

**Cause.** In `campaign_v5/live_initial.py`, lines 34–36, `retain()` calls `load_protocol(verify_files=True)` with no path. That loads `DEFAULT_PROTOCOL_PATH`, which is `configs/cfd/final_protocol_candidate_v2.json`: the nominal protocol, sha `403410fc…5852`. It then compares that hash with the initialization's `original_protocol_sha256`. For a low or high task, the initialization used the task's own protocol, so the hashes can never match. The next check, against `seed_alpha0`, would also use the nominal seed.

**Expected effect (inference).**
- Indices 2 (s01-high), 3 (s02-low) and 5 (s02-high) should block the same way, each after an initialization of about 1.8 h.
- Indices 1 and 4 (nominal) should pass this check. Index 1 is still initializing.

**Our plan.** Following the launch rules, we are not intervening: no deletion, no retry, no re-run. Each blocked episode keeps its exclusive claim. If you want lanes 0 and 2 stopped early to save the remaining initializations, or want to issue a corrected release and a new authorization for the affected indices, please tell us.

The evidence is in our `records/incident-01-index0-blocked/`. It includes `INCIDENT.md`, the pod, Job and event JSON, index 0's claims, the pipeline status, `stage-08.json`, the transcript, the oracle-00000 request, response and log, the initialization records, and `logs/index-0.log`. The full raw tree remains on the PVC.

Thanks,
Jisen
