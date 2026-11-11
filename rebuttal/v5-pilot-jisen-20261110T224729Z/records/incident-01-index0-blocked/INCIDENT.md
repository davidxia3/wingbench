# Incident 01: index 0 (adjoint-v5-s01-low) blocked at the first proposal-zero call

Recorded 2026-11-11 about 06:30Z by Jisen's watchdog. Nothing was retried, recreated, patched or deleted.

## What happened

| Step | Time (UTC) | Result |
|---|---|---|
| Initialization (`initial.py --index 0`) | 04:23 → about 06:10 | `delivered=true`. Starting wing graded at 18.93438473394857 under protocol `superwing-load-low-cl015-qualification-v1`, `original_protocol_sha256` `7ba78aef369fc85c68707b92023699cf8b0288b153b120ad5516a383a2f4a0a9` |
| `run.py`, first oracle request | 06:10:38 | `op=forward`, `mach=0`, `proposal_zero=true`, x = 38 zeros plus the initialization alphas. Response status `infrastructure_failure`: `ValueError: Initial benchmark protocol changed` |
| `stage-08.json` | 06:10:40 | `phase=blocked`, 0 search attempts, starting wing returned, 1 recorded oracle call |
| `pipeline.py` | 06:10:40 | `RuntimeError: Search blocked; do not restart`. `pipeline-status.json` reports `phase=blocked`. Process return code 1 |
| `run-lane.py` | 06:10:40 → | As designed, lane 0 kept the failure and went on to index 3 |

The failed oracle call cost 0.0031 allocated rank-hours. No CFD solve ran in it.

## Cause, from the frozen source

`rebuttal/adjoint/campaign_v5/live_initial.py`, lines 34–36:

```python
protocol = load_protocol(verify_files=True)
if protocol.sha256 != initial['original_protocol_sha256']:
    raise ValueError('Initial benchmark protocol changed')
```

`load_protocol()` is called with no path, so it loads `DEFAULT_PROTOCOL_PATH`, which is `configs/cfd/final_protocol_candidate_v2.json`. That is the nominal protocol, sha256 `403410fcb0daed2fbc6f46d0356e7e346adcb78b6f021edf1725cf0afc885852`. The same value appears in the check's nominal-task constructor record.

Index 0, however, is a `low` task. Its initialization was graded with the load-low protocol, `7ba78aef…`, so the comparison can never pass. The next check, `conds['AA'] != protocol.seed_alpha0`, would also compare against the nominal seed angle.

## Expected effect on the other episodes (inference, not yet observed)

| Index | Episode | Task | Lane | Expectation |
|---|---|---|---|---|
| 1 | adjoint-v5-s01-nominal | nominal | 1 | Should pass this check. The default protocol is nominal |
| 2 | adjoint-v5-s01-high | high | 2 | Expected to block the same way after initialization |
| 3 | adjoint-v5-s02-low | low | 0 | Expected to block the same way after initialization (now initializing) |
| 4 | adjoint-v5-s02-nominal | nominal | 1 | Should pass this check |
| 5 | adjoint-v5-s02-high | high | 2 | Expected to block the same way after initialization |

Each blocked episode still consumes its initialization, about 1.8 h on 16 ranks, and its exclusive claim.

## Evidence in this folder

- `pods.json`, `jobs.json`, `events.json`: cluster state at recording time. All three pods were Running with 0 restarts.
- `files/`: index 0's claims, pipeline status, reservation, `stage-08.json`, transcript, binding, the oracle-00000 worker directory (launch, request, response, log), `initialization.json`, `initial_grade.json` and `logs/index-0.log`.
- The full raw tree, including the work directories, remains on the PVC under `/results/v5-jisen/launches/jisen-20261110T224729Z/`.
