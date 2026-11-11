# WingBench verification update for Jisen

The new archive is **`wingbench-jisen-verify-v2-20261107.tar.gz`**. Its SHA256 is:

```text
986064b5f06a51b41f2310d884ca0e5e5b4bd3b208b0900164ca126c24606a70  wingbench-jisen-verify-v2-20261107.tar.gz
```

The archive was independently checked after creation: **159/159 inner files, all package source pins, and 41 tests from the extracted copy passed.** An additional **85 tests passed in the pinned Python 3.9 solver environment on Quest**, with 295 source/asset checks and all nine scientific runtime hashes verified. The new Docker image still needs to be built and validated on your cluster.

Thanks for checking the archive. The original outer SHA256 is confirmed:

```text
b7110e6d16c12d8143754a8b964cb58dde7da03302a8e653eca95b37601d2f55  wingbench-jisen-verify-v1-20261106.tar.gz
```

Your **123/123 inner checks** match the original package. Please retain it unchanged. Use the separately supplied **v2 archive and its new checksum file** for the next run; do not patch the old archive or reuse its image digest.

There are two distinct changes in v2:

- **Infrastructure:** both `render_jobs.py` and `launch.py` accept repeatable `--toleration KEY=VALUE:EFFECT`. The requested toleration is applied to worker, pilot and regrade pods. The new resource recorder samples cgroup memory every 15 seconds.
- **Scientific protocol, explicitly approved by David:** native adjoint residual acceptance is `1.1e-9`, replacing the debugging threshold `1.1e-11`. Internal forward and adjoint targets remain `1e-12`, forward acceptance remains `1.1e-12`, and all derivative, reproduction, finite-value, failure, pilot and independent-regrade checks remain. This is a new prospective protocol, not an infrastructure-only patch. Earlier failed results remain failed. All 39 numerical qualification jobs must run fresh.

**Use v2 only after its companion checksum and inner `SHA256SUMS` pass. Do not launch CFD from the old v1 package.** This package runs one verification batch; it does not launch production episodes or automatically retry failures.

## Toleration and launch

After verifying/extracting the new archive and checking its `SHA256SUMS`, build/push its Linux/amd64 Dockerfile and use the uploaded **image digest**. The old digest cannot be reused.

Run these from the extracted `wingbench-jisen-verify-v2-20261107` directory, which contains both `repo/` and `mesh/`. Docker can reuse the unchanged base-image/CST layers, but the v2 source layer and final image digest must be new.

```bash
sha256sum -c SHA256SUMS
WB_IMAGE_TAG=YOUR_REGISTRY/wingbench-adjoint-verify:v2-20261107
docker build --platform linux/amd64 \
  -f repo/rebuttal/compute/jisen_verify_v2/Dockerfile \
  -t "$WB_IMAGE_TAG" .
docker push "$WB_IMAGE_TAG"
docker image inspect "$WB_IMAGE_TAG" --format '{{index .RepoDigests 0}}'
```

Use the printed `repository@sha256:...` in the launch command. Pre-pull that new image on both nodes if your cluster setup permits; keep the old digest for historical evidence only.

```bash
python3 repo/rebuttal/compute/jisen_verify_v2/launch.py \
  --image YOUR_REGISTRY/wingbench-adjoint-verify@sha256:ACTUAL_V2_DIGEST \
  --namespace YOUR_NAMESPACE \
  --pvc YOUR_EXISTING_RWX_PVC \
  --node-a FIRST_NODE_HOSTNAME_LABEL \
  --node-b SECOND_NODE_HOSTNAME_LABEL \
  --results-host /results \
  --run-id wb-adjoint-v2-20261107a \
  --workers 8 \
  --toleration capacity.together.ai/pool=single-node-only:NoSchedule
```

The example `wb-adjoint-v2-20261107a` must be unused; choose another lowercase DNS-compatible fresh run ID if necessary. Add `--image-pull-secret NAME` if needed. The same toleration option works with `render_jobs.py` when you only want to inspect manifests. A toleration permits scheduling past that taint; it does not guarantee available capacity or change the node selectors. [Kubernetes tolerations](https://kubernetes.io/docs/concepts/scheduling-eviction/taint-and-toleration/).

## Running the launcher inside Kubernetes

An in-cluster launcher is fine. It needs **Python 3.9+, `kubectl` in its PATH, the complete frozen v2 source bundle, network access to the Kubernetes API, and a writable mount of the same RWX PVC** used by the worker pods. If that mount is `/results`, use `--results-host /results`. The launcher must remain alive through all phases; do not run it in a short-lived interactive shell. Its source bundle can be mounted read-only. The launcher image is an operator tool; the CFD pods must use the pinned scientific image.

When using the verification image for the operator pod, override its default worker entrypoint. Use the following pod `command` and pass the launcher options above in `args` (with `/results` mounted):

```yaml
command:
  - /bin/bash
  - /workspace/repo/rebuttal/container_entry.sh
  - python3
  - /workspace/repo/rebuttal/compute/jisen_verify_v2/launch.py
```

The scientific image does not install `kubectl`; supply it in the operator image or through a read-only binary mount. Do not alter the scientific worker image merely to give the operator Kubernetes credentials.

Give the launcher its own service account/token or a suitable kubeconfig. Worker pods intentionally set `automountServiceAccountToken: false`; do not copy that setting to the launcher without supplying another credential. The launcher invokes these operations:

| Resource/API | Required verbs | Why |
|---|---|---|
| `jobs.batch` in the chosen namespace | `get`, `list`, `create` | Check unique names, create jobs, poll completion |
| `pods` | `get`, `list` | Pod status, exit reasons and OOM evidence |
| `pods/log` | `get` | Retain worker logs |
| `events` | `get`, `list` | Retain scheduling, eviction and failure events |
| `pods.metrics.k8s.io` | `get`, `list` — optional | Supplementary `kubectl top pods` samples |

No secret reads, pod exec, node writes, job patches or automatic job deletion are required by the launcher. Creating the service account/Role/RoleBinding, mounting the existing PVC, and arranging image pulls are operator setup. `launcher_rbac.example.json` provides the namespaced role template; replace its namespace before using it. A separate operator may need delete permission for the deliberate cleanup below. [Kubernetes RBAC](https://kubernetes.io/docs/reference/access-authn-authz/rbac/).

## Memory and failure evidence

Sampling `memory.current` every 15 seconds is acceptable. Report the largest observed sample as a **sampled lower bound on peak cgroup usage**, not “peak RSS” or proof of sufficient RAM. It can miss brief spikes and includes more than process RSS. If available, retain `memory.peak` separately as the kernel's cgroup-lifetime peak; do not subtract cumulative peaks to infer per-task peaks. Keep raw `memory.events`, pod termination reason/exit code, OOM/eviction events and CPU-throttling counters. Missing metrics mean unavailable, not zero. [Kernel cgroup memory documentation](https://docs.kernel.org/admin-guide/cgroup-v2.html).

The package writes `resource_samples/*.jsonl` and corresponding summaries on the PVC. A killed pod may leave only partial samples, which are still useful. The launcher also records pod status and logs; capture Kubernetes events promptly because cluster retention can be short. Keep the separate **288 GiB pilot allocation**: eight successful 96 GiB workers do not establish that eight complete adjoint episodes fit concurrently.

## Infrastructure-only retry

A fresh-run-ID retry is reasonable **only after confirming the first attempt failed before any CFD started**—for example, image pull, taint scheduling, PVC permissions, or preflight setup. Preserve its launcher output, pod/job JSON, logs, events and result directory first. Stop the old launcher, cancel/delete only Jobs with that exact old run label, and confirm their pods are gone before creating a new run ID and directory. Do not delete the old PVC evidence.

If a task claim or solver log exists, or it is unclear whether CFD began, return that evidence before rerunning. Do not automatically restart a numerical failure, change tolerances/timeouts, overwrite a result directory, or reuse a completed initial episode. The launcher itself has no retry or deletion path.

## Return compact results first

Please send the small JSON/log results first: `final_summary.json`, `run_plan.json`, both certificates, worker preflight/process/barrier records, the 39 raw numerical results and process records, both original-grader controls, pilot/regrade records if reached, resource samples/summaries, image digest, and Kubernetes status/events/logs. Include the explicit blocker if the next phase did not run.

Keep meshes, flow solutions and other bulk scratch data intact. We can arrange private bulk storage separately; no public bucket or public link is needed. Finishing the bounded pilot is not a claim of optimizer convergence or a competitive baseline result.
