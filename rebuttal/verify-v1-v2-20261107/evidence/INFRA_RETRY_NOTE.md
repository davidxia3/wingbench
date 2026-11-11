# Infrastructure-only retry: wb-adjoint-v2-20261107a to wb-adjoint-v2-20261107b

Cluster research-common-h100, namespace jisenli2. Image `ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e`. Package v2: outer sha `986064b5…6a70`, inner SHA256SUMS 159/159.

## What happened in run a

- **Launch.** The launcher started at 2026-11-07T09:00:00Z inside pod `wb-verify-operator` and stopped about 1 s later with `launcher_or_infrastructure_failure` and the error `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.
- **No CFD started.** The only kubectl call was `000001-workers-check-names`, the collision check:
  `kubectl --namespace jisenli2 get jobs <8 worker names> --ignore-not-found -o json`.
  It returned code 0 with empty stdout (length 0). `Kubectl.get()` then parsed `''` as JSON. No `create` was issued, and no Job, task claim or solver log exists.
- **Root cause.** Real kubectl prints nothing for `--ignore-not-found -o json` when no named object exists. It does not print an empty List. This was reproduced on 2026-11-07 against this cluster with kubectl v1.27.6, which matches the server, and with v1.36.1. The same call runs before the pilot and the regrade, so every launch would fail at the same point.

## Evidence preserved

- **On the PVC.** `/results/wb-adjoint-v2-20261107a/` is untouched: `run_plan.json`, `final_summary.json` and `launcher/kubectl/000001-workers-check-names.json`.
- **Local copy.** `evidence/wb-adjoint-v2-20261107a/`: the PVC tarball, the operator pod JSON, launcher and init logs, and pod events.
- **Pod kept.** The failed operator pod `wb-verify-operator` is retained in Failed state and was not deleted.
- **Nothing to cancel.** No Jobs with label `wingbench-run=wb-adjoint-v2-20261107a` exist, so there was nothing to cancel or delete.

## Change for run b (operator infrastructure only)

The launcher's `--kubectl` now points at `/tools/kubectl`, a small shim (`infra/tools/kubectl-shim.sh`) in front of the verified kubectl v1.27.6 binary at `/tools/kubectl-real`. The shim does two things, and only for read-only `get` calls:

1. When `get … --ignore-not-found -o json` exits 0 with blank stdout, it prints `{"apiVersion":"v1","kind":"List","items":[]}`. That is the launcher's own meaning of "no existing Jobs".
2. When a `get` fails with a non-permanent API error, it retries up to 3 times: 15 s request timeout per attempt, 5 s apart, at most 55 s, which is inside the launcher's 60 s call timeout. NotFound, Forbidden and invalid errors are never retried. This keeps one API blip during the 30-second Job polling from killing a 16–34 h run.

All other calls, including `create`, `logs` and `top`, are exec'd unchanged. The following are all unchanged: package source and hashes, image, Job specs, solver settings, tolerances, numerical tests, and optimizer.

The retry uses a fresh run ID and directory, `wb-adjoint-v2-20261107b`, under the v2 handoff's infrastructure-only retry rule: the first attempt failed before any CFD started.

**Suggested package fix for David:** in `Kubectl.get()`, treat blank stdout as `{"items": []}`, or check collisions without `--ignore-not-found` and treat NotFound as absence.
