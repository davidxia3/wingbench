set -e
id
ls -ln /workspace/repo/agents/tasks/task_documents.json
sha256sum /workspace/repo/agents/tasks/task_documents.json
cd /workspace && n=$(grep -c '  repo/' /chk/v2-SHA256SUMS) && grep '  repo/' /chk/v2-SHA256SUMS | sha256sum -c --quiet && echo "v2 package repo files unchanged: $n/$n OK"
/bin/bash /workspace/repo/rebuttal/container_entry.sh python3 /chk/preflight.py
