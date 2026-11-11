# WingBench adjoint verification, run wb-adjoint-v2-20261107b

## 中文摘要（给 Jisen）

**最终状态：** `stopped_after_failed_or_incomplete_qualification`，launcher 退出码 2。这个结果在预期之内。整批运行时间为 2026-11-07 09:06:50Z 到 11:16:56Z，共 **2 小时 10 分钟**。

**数值认证通过了：**
- 39 个任务（3 个 anchor + 36 个 point）全部正常结束，返回码都是 0，没有超时。
- 6 个 native adjoint 终值（CL 和 CD 各 3 个）的相对残差在 **2.8e-11 到 3.6e-11** 之间，都是第一次尝试就通过。新门槛是 1.1e-9，余量大约 30 倍；但这些值全部高于旧门槛 1.1e-11，所以旧协议下它们仍然会失败。
- 18 组比较和 36 个标量比较都没有错误。`coverage_summary` 显示 `passed=true`、`can_release_pilot=true`。

**严格认证没通过，4 条错误都追溯到同一个原因：David 的包里缺少 `agents/tasks/task_documents.json`。**
- 原版评分器的两个对照任务 `reference_a` 和 `reference_b`，在 0.1 秒内就因 `FileNotFoundError` 失败，没有生成 `grade.json`。
- "Worker2/Worker4 processed inventory differs from claims" 是连带错误：这两个 worker 正好跑了这两个失败的 reference。`certify.py` 只把成功的任务计入 claims，而 worker 自己的 processed 列表里包含了 reference，两边因此对不上。
- 因为这个原因，pilot 和 regrade 都没有跑。

**资源使用：**
- 每个任务的耗时：anchor 平均 0.90 小时（41–66 分钟），point 平均 0.37 小时（17–30 分钟）。两个节点速度相同：059 平均 0.402 小时，090 平均 0.416 小时。39 个任务共分配 255 个核时。
- 内存是每 15 秒采样一次的 `memory.current` 最大值，**只能看作下限**：跑过 anchor 的 worker 是 70.8 GiB，只跑 point 的 worker 是 56.8 GiB，上限是 96 GiB。`oom_kill` 计数为 0。内核没有提供 `memory.peak`，所以没有真实峰值。
- CPU 节流：大约 33% 的 CFS 周期发生过节流，但累计被节流的时间每个 worker 只有 2.5 到 4.1 分钟（运行 2 小时左右），影响可以忽略。

**集群状况：**
- 两个 GPU pod `dev-059-8g` 和 `jisenli-dev-0` 全程没有被碰过，没有重启，启动时间也没变。
- 两个节点都没有出现内存压力或磁盘压力。
- 运行期间没有任何 OOM、驱逐或 pod 重启。

**保留下来的对象：** 8 个已完成的 worker Job 和它们的 pod、两个 operator pod（a 和 b，都是 Error 状态）、PVC `wb-verify-results`（里面是 a 和 b 两次运行的目录）、secret `ghcr-wb-pull`。用于取证的 reader pod 在拷贝完成后已经删除。

**本地文件：**
- `pvc/`：完整拷贝了 PVC 上的运行目录，共 765 个文件，与 `pvc-SHA256SUMS` 逐个核对一致。总大小 36 MB，PVC 上没有大文件。
- `k8s/`：launcher 日志、Job/pod JSON、事件。

## Ready-to-send note for David (English)

> Hi David, the v2 verification batch ran on research-common-h100 (2× EPYC 9534 per node, 8 workers, 4 + 4, 16 CPU and 96 Gi each).
>
> **Result.** Run `wb-adjoint-v2-20261107b` started 2026-11-07T09:06:50Z and finished 11:16:56Z (2 h 10 min). Status `stopped_after_failed_or_incomplete_qualification`, certify exit 2.
>
> **Numerical qualification passed.**
> - All 39 anchor/point tasks finished with return code 0 and no timeouts.
> - The 6 native adjoint finals (CL/CD × 3 anchors) qualified on attempt 1, with relative residuals 2.8e-11 to 3.6e-11 against the 1.1e-9 gate.
> - All 18 comparisons and 36 scalar comparisons passed with no errors. `coverage_summary.passed=true`, `can_release_pilot=true`.
> - Note that every residual is above the old 1.1e-11 gate.
>
> **The strict certificate failed for one packaging reason: `agents/tasks/task_documents.json` is missing from the bundle (v1 and v2 alike).**
> - `benchmark/common.py` reads it. Both original-grader controls (`reference_a`, `reference_b`) failed in about 0.1 s with `FileNotFoundError`, so neither wrote a `grade.json`.
> - The two "Worker2/Worker4 processed inventory differs from claims" errors are a consequence. Those workers ran the failed controls: `certify.py` adds only successful tasks to `claimed_by`, while `worker_finished.json` lists every processed task.
> - Because of this, the pilot and regrade were not reached. All 8 worker preflights passed.
>
> **Resources.**
> - Wall time per task: anchors 0.90 h mean (41–66 min); points 0.37 h mean (17–30 min).
> - Total 255 allocated core-hours for the 39 numerical tasks.
> - Memory, as the maximum of 15 s `memory.current` samples (a sampled lower bound, not a true peak or RSS): 70.8 GiB on workers that ran an anchor, 56.8 GiB on point-only workers. The `oom_kill` counter stayed at 0. `memory.peak` is unavailable on kernel 5.15.
> - CFS throttling: about 33 % of periods were throttled, but total throttled time was only 2.5–4.1 min per worker.
> - No OOMs, evictions or restarts.
>
> **Two launcher issues on our side, both before any CFD, documented in `INFRA_RETRY_NOTE.md`.**
> 1. *Collision check bug.* The first attempt, `wb-adjoint-v2-20261107a`, stopped about 1 s after launch with `JSONDecodeError`. For the collision check, `kubectl get jobs <names> --ignore-not-found -o json` prints nothing (exit 0) when no Job exists. This happens with kubectl v1.27.6 and v1.36.1. `Kubectl.get()` then calls `json.loads('')`. No Job had been created. The same call runs before the pilot and the regrade.
> 2. *kubectl shim.* For the retry I pointed `--kubectl` at a small operator-side shim in front of kubectl v1.27.6. It does two things, for read-only `get` only:
>    - it returns an empty v1 List when that call comes back blank;
>    - it retries transient API errors up to 3 times, staying within the launcher's 60 s timeout. Without this, one API blip during the 30 s Job polling would stop the launcher.
>
>    It changes nothing else. Package source, hashes, image, Job specs and numerics are all untouched. Following your v2 infra-only retry rule, run a's evidence is preserved and the retry used a fresh run ID.
>
>    Suggested package fix: treat blank stdout from `--ignore-not-found` as `{"items": []}`. Gotcha if you add per-call timeouts: kubectl `--request-timeout` makes in-pod kubectl skip in-cluster config and dial `localhost:8080`.
>
> **Evidence.** We hold the full run directory (765 files, 36 MB, SHA256-listed), the launcher log, Job/pod JSON and events. All Jobs and PVC data are retained on the cluster. Happy to send everything.
>
> **Suggested next step.** Re-issue the bundle with `agents/tasks/task_documents.json` included, plus the launcher fix if you like. We can then re-run the batch: about 2.2 h at 8 workers if the pilot is not reached, plus the pilot and regrade time if it is.
