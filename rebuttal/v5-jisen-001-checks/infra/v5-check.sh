#!/bin/bash
# Infrastructure wrapper for David's WingBench v5 software/runtime checks (CHECKS ONLY, zero CFD).
# It runs the unchanged run-checks.sh, mounted so that the handoff root is /workspace and its repo is
# /workspace/repo. The v2 package's resource_sampler.py records 15 s CPU/memory samples. No retries.
set -u -o pipefail
: "${WINGBENCH_CHECK_OUT:?}" "${WINGBENCH_NODE_NAME:?}" "${WINGBENCH_RUN_ID:?}"
V=/results/v5-jisen; id=$(basename "$WINGBENCH_CHECK_OUT")
mkdir -p "$V/checks/logs" "$V/checks/resource_samples"
log=$V/checks/logs/$id.log
{
echo "$(date -u +%FT%TZ) start id=$id node=$WINGBENCH_NODE_NAME run_id=$WINGBENCH_RUN_ID uid=$(id -u) gid=$(id -g) host=$(hostname)"
echo "cgroup cpu.max=$(cat /sys/fs/cgroup/cpu.max 2>/dev/null) memory.max=$(cat /sys/fs/cgroup/memory.max 2>/dev/null) nproc=$(nproc)"
echo "math env (parent): OMP=${OMP_NUM_THREADS-unset} OPENBLAS=${OPENBLAS_NUM_THREADS-unset} MKL=${MKL_NUM_THREADS-unset}"
echo "run-checks.sh sha256=$(sha256sum /workspace/run-checks.sh | cut -d' ' -f1)"
echo "check-authorization.json sha256=$(sha256sum /workspace/check-authorization.json | cut -d' ' -f1)"
echo "release.json sha256=$(sha256sum /workspace/repo/rebuttal/adjoint/campaign_v5/release.json | cut -d' ' -f1)"
} 2>&1 | tee -a "$log"
/usr/bin/python3 "$V/infra/resource_sampler.py" --output "$V/checks/resource_samples/$id.jsonl" -- \
  /bin/bash /workspace/run-checks.sh 2>&1 | tee -a "$log"
rc=$?
echo "$(date -u +%FT%TZ) exit rc=$rc" | tee -a "$log"
exit $rc
