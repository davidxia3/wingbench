#!/bin/bash
# Read-only status check for run wb-adjoint-v2-20261107b. It prints one summary block and
# appends it to ~/wingbench-rebuttal/watchdog.log. It never creates, deletes or patches anything.
export KUBECONFIG=~/.kube/research-common-h100
NS=jisenli2 RUN=wb-adjoint-v2-20261107b OP=wb-verify-operator-b
LOG=~/wingbench-rebuttal/watchdog.log
k() { kubectl -n "$NS" --request-timeout=20s "$@"; }
{
echo "=== $(date -u +%FT%TZ) (local $(date +%H:%M))"
echo "operator: $(k get pod $OP -o jsonpath='{.status.phase} restarts={.status.containerStatuses[0].restartCount} exit={.status.containerStatuses[0].state.terminated.exitCode}' 2>&1)"
echo "jobs: $(k get jobs -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | ((.status.conditions // []) | map(select(.status=="True").type)) as $c | {n:(.metadata.labels["wingbench-phase"]), s:(if ($c|index("Complete")) then "complete" elif ($c|index("Failed")) then "FAILED" elif .status.active then "running" else "pending" end)}] | group_by(.n) | map("\(.[0].n): " + (group_by(.s) | map("\(.[0].s)=\(length)") | join(" "))) | join(" | ")')"
echo "pod problems: $(k get pods -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | select((.status.containerStatuses // [])[0] | (.restartCount > 0) or (.state.terminated.reason // "" | test("OOM|Error|Evict")) or (.state.waiting.reason // "" | test("BackOff|Err"))) | "\(.metadata.name):\((.status.containerStatuses[0].state | tostring)[0:120])"] | if length==0 then "none" else join("; ") end')"
echo "evicted/failed pods: $(k get pods -l wingbench-run=$RUN --field-selector=status.phase=Failed -o name 2>/dev/null | tr '\n' ' ')"
echo "GPU holders: $(k get pods dev-059-8g jisenli-dev-0 -o jsonpath='{range .items[*]}{.metadata.name}={.status.phase}/restarts={.status.containerStatuses[0].restartCount}/started={.status.startTime} {end}' 2>&1)"
for n in 059 090; do echo "node $n: $(kubectl get node research-common-h100-$n.cloud.together.ai --request-timeout=20s -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}' 2>&1 | tr ' ' '\n' | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready' | tr '\n' ' ')"; done
echo "usage: $(k top pods -l wingbench-run=$RUN --no-headers 2>/dev/null | awk '{c+=$2; m=$3; sub(/Mi/,"",m); if (m+0>mx) mx=m+0; n++} END {printf "%d pods, max mem %.1f GiB", n, mx/1024}')"
if [ "$(k get pod $OP -o jsonpath='{.status.phase}' 2>/dev/null)" = Running ]; then
  k exec $OP -c launcher -- bash -c "cd /results/$RUN 2>/dev/null && echo \"claims=\$(ls claims | wc -l)/41 finished=\$(ls claims/*/process.json 2>/dev/null | wc -l) nonzero_rc=\$(grep -l '\"return_code\": [1-9]' claims/*/process.json 2>/dev/null | xargs -n1 dirname 2>/dev/null | xargs -n1 basename 2>/dev/null | tr '\n' ',') qualification=\$(ls qualification 2>/dev/null | tr '\n' ',') final_summary=\$(test -f final_summary.json && echo yes || echo no)\"" 2>&1 | tail -1
  echo "launcher last: $(k logs $OP -c launcher --tail=1 2>/dev/null | cut -c1-300)"
fi
} 2>&1 | tee -a "$LOG"
