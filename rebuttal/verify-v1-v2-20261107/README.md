# WingBench adjoint verification, v1 and v2 packages (2026-11-06 to 2026-11-07)

This directory archives the first verification round on research-common-h100 (namespace `jisenli2`, nodes 059 and 090). It covers David's two verification packages, the infrastructure we used to run them, and the evidence from runs `wb-adjoint-v2-20261107a` and `wb-adjoint-v2-20261107b`. The later rounds are on their own branches: `jisen/validation-followup-20261107c`, `jisen/v5-jisen-001-checks` and `jisen/v5-pilot-jisen-20261110T224729Z`.

## What happened

- **v1 package (2026-11-06).** It was not launched. Its rendered Jobs carried no tolerations, so node 059's taint would have blocked one of the two nodes. David replaced it with v2, and his v2 note says not to launch CFD from v1.
- **Run a (`wb-adjoint-v2-20261107a`).**
  - The launcher stopped about 1 s after its start at 09:00:00Z with `JSONDecodeError`.
  - Cause: kubectl prints nothing for `get --ignore-not-found -o json` when no named object exists, and the launcher parsed that empty output as JSON.
  - No Job was created and no CFD ran. See `evidence/INFRA_RETRY_NOTE.md`.
- **Run b (`wb-adjoint-v2-20261107b`).**
  - An infrastructure-only retry behind `infra/tools/kubectl-shim.sh`. It ran from 09:06:50Z to 11:16:56Z.
  - **Numerical qualification passed:**
    - all 39 tasks returned 0;
    - the 6 native adjoint finals were 2.8e-11 to 3.6e-11, against the 1.1e-9 gate;
    - `can_release_pilot` was true.
  - **The strict certificate failed.** All 4 errors trace back to `agents/tasks/task_documents.json`, which was missing from the package, so the two original-grader reference tasks could not run. No pilot or regrade ran.
  - The follow-up branch re-ran those controls with a fixed image.

## Contents

| Path | Contents |
|---|---|
| `input-packages/v1/`, `input-packages/v2/` | David's archives as received, each with a locally computed `.sha256` |
| `docs/` | David's verification handoff (2026-11-06), the forwardable v2 update (2026-11-07), and our CPU request (2026-11-06) |
| `infra/` | PVC, prepull, operator and reader manifests (`01`–`04`, `03b`, the v1 draft); the rendered v2 and v2b Job JSON; image build and push logs; `watchdog-check.sh`; `tools/kubectl-shim.sh`; the kubectl v1.27.6 linux-amd64 binary we ran in-cluster, with its sha256 |
| `evidence/INFRA_RETRY_NOTE.md` | The run a failure and the reason for the infrastructure-only retry |
| `evidence/wb-adjoint-v2-20261107a/` | Run a operator pod, events and logs, and the PVC run directory, also as `pvc-run-dir.tgz` |
| `evidence/wb-adjoint-v2-20261107b/` | Run b Kubernetes records, worker logs, the PVC run directory (`pvc/`, `pvc-run-dir.tgz`, `pvc-SHA256SUMS`, `pvc-file-list.txt`), and `SUMMARY.md`. `SUMMARY.md` is a Chinese summary with the English note to David; the English evidence package is `run-b-evidence/` on the follow-up branch |
| `watchdog.log` | The cron watchdog log for run b |
| `SHA256SUMS` | SHA256 of every file in this directory |

The archives' unpacked copies are not included. We checked that they are byte-identical to the archive contents.

## Identities

| Item | Value |
|---|---|
| v1 archive | `wingbench-jisen-verify-v1-20261106.tar.gz`, sha256 `b7110e6d16c12d8143754a8b964cb58dde7da03302a8e653eca95b37601d2f55` |
| v2 archive | `wingbench-jisen-verify-v2-20261107.tar.gz`, sha256 `986064b5f06a51b41f2310d884ca0e5e5b4bd3b208b0900164ca126c24606a70`, confirmed by David |
| v1 image (built, never run) | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:9c961d727934f5f3e77f381af688b4ac9443629cb12972b0fbd708c5593a4508` |
| v2 image (runs a and b) | `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e` |

## Verify

```bash
sha256sum -c SHA256SUMS
```
