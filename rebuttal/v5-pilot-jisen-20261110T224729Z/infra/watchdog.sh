#!/bin/bash
# Read-only status check for WingBench v5 pilot launch jisen-20261110T224729Z (3 lanes, 6 episodes).
# Prints one block and appends it to ~/wingbench-rebuttal/v5-launch-20261110T224729Z/watchdog.log.
# It never creates, deletes or patches anything.
export KUBECONFIG=~/.kube/research-common-h100
NS=jisenli2 RUN=jisen-20261110t224729z R=/results/v5-jisen/launches/jisen-20261110T224729Z
LOG=~/wingbench-rebuttal/v5-launch-20261110T224729Z/watchdog.log
k() { kubectl -n "$NS" --request-timeout=20s "$@"; }
{
echo "=== $(date -u +%FT%TZ) (local $(date +%H:%M))"
echo "jobs: $(k get jobs -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | ((.status.conditions // []) | map(select(.status=="True").type)) as $c | "lane\(.metadata.labels["wingbench-lane"])=" + (if ($c|index("Complete")) then "complete" elif ($c|index("Failed")) then "FAILED" elif .status.active then "running" else "pending" end)] | join(" ")')"
echo "pods: $(k get pods -l wingbench-run=$RUN -o json 2>/dev/null | jq -r '[.items[] | "lane\(.metadata.labels["wingbench-lane"]):\(.status.phase)/restarts=\((.status.containerStatuses // [{}])[0].restartCount // 0)\(((.status.containerStatuses // [{}])[0].state.terminated // null) | if . then "(exit=\(.exitCode),\(.reason))" else "" end)@\(.spec.nodeName // "-" | sub(".cloud.together.ai";"") | sub("research-common-h100-";""))"] | join(" ")')"
echo "GPU holder: $(k get pod dev-059-8g -o jsonpath='{.metadata.name}={.status.phase}/restarts={.status.containerStatuses[0].restartCount}' 2>&1)"
for n in 059 090; do echo "node $n: $(kubectl get node research-common-h100-$n.cloud.together.ai --request-timeout=20s -o jsonpath='{range .status.conditions[*]}{.type}={.status} {end}' 2>&1 | tr ' ' '\n' | grep -E 'MemoryPressure|DiskPressure|PIDPressure|Ready' | tr '\n' ' ')"; done
echo "usage: $(k top pods -l wingbench-run=$RUN --no-headers 2>/dev/null | awk '{printf "%s cpu=%s mem=%s; ", $1, $2, $3}' | sed 's/wb-v5-jisen-20261110t224729z-//g')"
pod=$(k get pods -l wingbench-run=$RUN --field-selector=status.phase=Running -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
[ -z "$pod" ] && pod=$(kubectl -n $NS get pods -l app.kubernetes.io/name=wingbench-reader -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
if [ -n "$pod" ]; then
  k exec "$pod" -- bash -c "cd $R 2>/dev/null || exit 0
    for i in 0 1 2 3 4 5; do d=results/index-\$i; [ -d \$d ] || { echo \"index \$i: not started\"; continue; }
      init=\$(ls \$d/initial/*/initialization.json 2>/dev/null | head -1)
      st=\$(ls \$d/optimization/stage-0*.json 2>/dev/null | sort | tail -1 | xargs -r basename)
      ps=\$(test -f claims/index-\$i/process-status.json && /usr/bin/python3 -c \"import json;d=json.load(open('claims/index-\$i/process-status.json'));print('rc=%s' % d.get('returncode'))\")
      pl=\$(test -f \$d/pipeline-status.json && /usr/bin/python3 -c \"import json;d=json.load(open('\$d/pipeline-status.json'));print('%s err=%s' % (d.get('phase'), (d.get('error') or '')[:80]))\")
      newest=\$(find \$d -type f -printf '%T@ %P\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2 | cut -c1-80)
      echo \"index \$i: files=\$(find \$d -type f | wc -l) init=\$([ -n \"\$init\" ] && echo yes || echo no) stage=\${st:-none} private=\$(ls -d \$d/private 2>/dev/null >/dev/null && echo started || echo no) process=\${ps:-running} pipeline=\${pl:-running} newest=\$newest\"
    done
    for l in 0 1 2; do test -f lane-\$l-status.json && echo \"lane \$l status: \$(cat lane-\$l-status.json | tr -d '\n ' | cut -c1-200)\"; done
    df -h /results | tail -1 | awk '{print \"pvc used=\" \$3 \" free=\" \$4}'" 2>&1 | grep -v -e VSPAERO -e 'Expected here'
fi
} 2>&1 | tee -a "$LOG"
