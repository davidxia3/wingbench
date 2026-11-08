#!/bin/bash
# Evidence collection for wb-validation-followup-20261107c. Read-only on the PVC.
set -euo pipefail
export KUBECONFIG=~/.kube/research-common-h100
NS=jisenli2 RUN=wb-validation-followup-20261107c POD=wb-validation-followup-20261107c-reader
E=~/wingbench-rebuttal/followup-20261107/evidence; mkdir -p "$E/k8s" "$E/pvc"
k() { kubectl -n $NS --request-timeout=120s "$@"; }
# k8s records
for p in $(k get pods -l wingbench-run=$RUN -o name); do k logs --timestamps "$p" > "$E/k8s/$(basename $p).log" 2>&1 || true; done
k get jobs -l wingbench-run=$RUN -o json > "$E/k8s/jobs.json"
k get pods -l wingbench-run=$RUN -o json > "$E/k8s/pods.json"
k get events -o json > "$E/k8s/events-namespace.json"
# reader pod
k create -f ~/wingbench-rebuttal/followup-20261107/infra/reader-pod.yaml
k wait --for=condition=Ready pod/$POD --timeout=180s
R=/results/$RUN
k exec $POD -- bash -c "cd $R && find . -type f -print0 | sort -z | xargs -0 sha256sum" > "$E/pvc/SHA256SUMS-cluster"
# compact selection: by extension, size <= 20 MB, excluding meshes/solutions/restarts
k exec $POD -- bash -c "cd $R && find . -type f -size -20M \( -name '*.json' -o -name '*.jsonl' -o -name '*.log' -o -name '*.txt' -o -name '*.csv' -o -name '*.sh' -o -name '*.py' -o -name '*.out' -o -name 'README*' -o -name 'SHA256SUMS' \) ! -name '*.cgns' ! -path './v3-source/*' | sort" > "$E/pvc/included.txt"
k exec $POD -- bash -c "cd $R && find . -type f ! -path './v3-source/*' -printf '%s %p\n' | sort -k2" > "$E/pvc/all-files-with-size.txt"
k exec -i $POD -- bash -c "cd $R && tar -cf - -T - " < "$E/pvc/included.txt" > "$E/pvc/compact.tar"
mkdir -p "$E/pvc/tree" && tar -xf "$E/pvc/compact.tar" -C "$E/pvc/tree"
# verify every copied file against the cluster sums
( cd "$E/pvc/tree" && awk 'NR==FNR{inc[$0]=1; next} ($2 in inc)' ../included.txt ../SHA256SUMS-cluster > ../SHA256SUMS-included && [ "$(wc -l < ../SHA256SUMS-included)" = "$(wc -l < ../included.txt)" ] && shasum -a 256 -c --quiet ../SHA256SUMS-included && echo "verified $(wc -l < ../SHA256SUMS-included) files against cluster sums" )
# excluded list with size and sha256
/usr/bin/python3 - "$E/pvc" <<'PY'
import sys
from pathlib import Path
d = Path(sys.argv[1])
inc = {l.strip().lstrip('./') for l in (d/'included.txt').read_text().splitlines()}
sums = {}
for l in (d/'SHA256SUMS-cluster').read_text().splitlines():
    h, p = l.split('  ', 1); sums[p.lstrip('./')] = h
rows = []
for l in (d/'all-files-with-size.txt').read_text().splitlines():
    s, p = l.split(' ', 1); p = p.lstrip('./')
    if p not in inc: rows.append(f"{s}\t{sums.get(p,'?')}\t{p}")
(d/'EXCLUDED-bulk-files.tsv').write_text('size_bytes\tsha256\tpath\n' + '\n'.join(rows) + '\n')
print(len(rows), 'excluded files listed')
PY
rm -f "$E/pvc/compact.tar"
k delete pod $POD --wait=false
du -sh "$E/pvc/tree"
