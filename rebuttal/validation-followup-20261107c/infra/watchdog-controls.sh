#!/bin/bash
# Read-only status check for run wb-validation-followup-20261107c (two original-grader controls).
# It prints one summary block and appends it to ~/wingbench-rebuttal/followup-20261107/watchdog.log.
# It never creates, deletes or patches anything.
export KUBECONFIG=~/.kube/research-common-h100
NS=jisenli2 RUN=wb-validation-followup-20261107c R=/results/wb-validation-followup-20261107c
LOG=~/wingbench-rebuttal/followup-20261107/watchdog.log
k() { kubectl -n "$NS" --request-timeout=20s "$@"; }
{
echo "=== $(date -u +%FT%TZ) (local $(date +%H:%M))"
echo "jobs: $(k get jobs -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | ((.status.conditions // []) | map(select(.status=="True").type)) as $c | "\(.metadata.labels["wingbench-control"])=" + (if ($c|index("Complete")) then "complete" elif ($c|index("Failed")) then "FAILED" elif .status.active then "running" else "pending" end)] | join(" ")')"
echo "pods: $(k get pods -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | "\(.metadata.labels["wingbench-control"]):\(.status.phase)/restarts=\((.status.containerStatuses // [{}])[0].restartCount // 0)/\(((.status.containerStatuses // [{}])[0].state // {}) | keys | join(","))\(((.status.containerStatuses // [{}])[0].state.terminated // null) | if . then "(exit=\(.exitCode),\(.reason))" else "" end)@\(.spec.nodeName // "-" | sub(".cloud.together.ai";""))"] | join(" ")')"
echo "GPU holder: $(k get pod dev-059-8g -o jsonpath='{.metadata.name}={.status.phase}/restarts={.status.containerStatuses[0].restartCount}/started={.status.startTime}' 2>&1)"
for n in 059 090; do echo "node $n: $(kubectl get node research-common-h100-$n.cloud.together.ai --request-timeout=20s -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}' 2>&1 | tr ' ' '\n' | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready' | tr '\n' ' ')"; done
echo "usage: $(k top pods -l wingbench-run=$RUN --no-headers 2>/dev/null | awk '{printf "%s cpu=%s mem=%s; ", $1, $2, $3}')"
pod=$(k get pods -l wingbench-run=$RUN --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$pod" ]; then
  ctr=$(k get pod "$pod" -o jsonpath='{.spec.containers[0].name}' 2>/dev/null)
  k exec "$pod" -c "$ctr" -- bash -c "for c in reference_a reference_b; do d=$R/\$c; v=\$d/verification_result.json; if [ -f \$v ]; then echo \"\$c: DONE \$(/usr/bin/python3 -c \"import json;d=json.load(open('\$v'));print('passed=%s score=%s wall_s=%s err=%s' % (d.get('passed'),d.get('original_score'),round(d.get('wall_seconds',0)),d.get('error')))\")\"; else echo \"\$c: running, work files=\$(find \$d/work -type f 2>/dev/null | wc -l), newest=\$(find \$d/work -type f -printf '%T@ %P\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2 | cut -c1-90)\"; fi; done
    echo \"v3 pipeline: \$(tail -1 $R/pipeline/events.jsonl 2>/dev/null)\"
    O=$R/optimization/adjoint-v3-s01-low; I0=$R/initial/adjoint-v3-s01-low
    [ -d \$I0 ] && echo \"v3 initial: files=\$(find \$I0 -type f | wc -l) initialization.json=\$(test -f \$I0/initialization.json && echo yes || echo no)\"
    [ -d \$O ] && echo \"v3 optimization: oracle_dirs=\$(ls -d \$O/workers/oracle-* 2>/dev/null | wc -l) stage04=\$(test -f \$O/stage-04.json && echo yes || echo no) newest=\$(find \$O -type f -printf '%T@ %P\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2 | cut -c1-90)\"
    [ -f $R/private/adjoint-v3-s01-low/checkpoint-4/result.json ] && echo \"v3 private: result.json present\"
    echo samples: \$(for f in $R/resource_samples/*.jsonl; do printf '%s=%s ' \$(basename \$f .jsonl) \$(wc -l < \$f); done)" 2>&1 | grep -v -e VSPAERO -e 'Expected here'
fi
} 2>&1 | tee -a "$LOG"
