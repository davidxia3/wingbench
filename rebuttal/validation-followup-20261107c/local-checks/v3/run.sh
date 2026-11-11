set -e
cd /workspace/repo
/bin/bash /workspace/repo/rebuttal/container_entry.sh env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 /out/check.py 2>&1 | grep -v -e VSPAERO -e "Expected here"
echo "== test_core.py"
start=$(date +%s)
/bin/bash /workspace/repo/rebuttal/container_entry.sh env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1 python3 /workspace/repo/rebuttal/adjoint/campaign_v3/test_core.py /tmp/optimizer-tests > /tmp/test_core.log 2>&1; rc=$?
echo "test_core rc=$rc wall=$(( $(date +%s)-start ))s"; tail -15 /tmp/test_core.log | cut -c1-300
mkdir -p /out/optimizer-tests && cp -r /tmp/optimizer-tests/. /out/optimizer-tests/ 2>/dev/null || true; cp /tmp/test_core.log /out/
