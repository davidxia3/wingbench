#!/bin/bash
# Read-only external cgroup sampler for launch jisen-20261110T224729Z. The frozen Job manifests carry no
# in-pod sampler, so every 60 s this reads each running lane pod's cgroup v2 counters with kubectl exec
# (cat only) and appends one JSON line per pod. memory.current maxima are SAMPLED LOWER BOUNDS, not peaks.
# It exits once no lane pod is Running or Pending.
export KUBECONFIG=~/.kube/research-common-h100
OUT=~/wingbench-rebuttal/v5-launch-20261110T224729Z/records/external-cgroup-samples.jsonl
while :; do
  pods=$(kubectl -n jisenli2 --request-timeout=20s get pods -l wingbench-run=jisen-20261110t224729z -o json 2>/dev/null)
  live=$(echo "$pods" | jq -r '[.items[] | select(.status.phase=="Running" or .status.phase=="Pending")] | length' 2>/dev/null)
  [ "$live" = "0" ] && { echo "{\"t\":\"$(date -u +%FT%TZ)\",\"event\":\"no live lane pods; sampler exiting\"}" >> "$OUT"; exit 0; }
  for p in $(echo "$pods" | jq -r '.items[] | select(.status.phase=="Running") | .metadata.name'); do
    raw=$(kubectl -n jisenli2 --request-timeout=20s exec "$p" -- /bin/sh -c 'echo mc $(cat /sys/fs/cgroup/memory.current); echo ev $(cat /sys/fs/cgroup/memory.events | tr "\n" " "); echo cs $(cat /sys/fs/cgroup/cpu.stat | tr "\n" " "); echo np $(cat /sys/fs/cgroup/pids.current)' 2>/dev/null)
    t=$(date -u +%FT%TZ)
    echo "$raw" | /usr/bin/awk -v t="$t" -v p="$p" '
      $1=="mc"{mc=$2} $1=="np"{np=$2}
      $1=="ev"{for(i=2;i<NF;i+=2) ev=ev sprintf("%s\"%s\":%s", (ev==""?"":","), $i, $(i+1))}
      $1=="cs"{for(i=2;i<NF;i+=2) cs=cs sprintf("%s\"%s\":%s", (cs==""?"":","), $i, $(i+1))}
      END{ if (mc=="") printf("{\"t\":\"%s\",\"pod\":\"%s\",\"error\":\"exec failed\"}\n", t, p);
           else printf("{\"t\":\"%s\",\"pod\":\"%s\",\"memory_current\":%s,\"pids\":%s,\"memory_events\":{%s},\"cpu_stat\":{%s}}\n", t, p, mc, (np==""?"null":np), ev, cs) }' >> "$OUT"
  done
  sleep 60
done
