#!/bin/bash
set -euo pipefail
CONTROL=$(cd -- "$(dirname -- "$0")" && pwd)
exec /bin/bash /workspace/repo/rebuttal/container_entry.sh env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 "$CONTROL/run-lane.py"
