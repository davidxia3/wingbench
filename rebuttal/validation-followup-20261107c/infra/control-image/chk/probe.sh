cd /workspace/repo
source /home/mdolabuser/.bashrc_mdolab >/dev/null 2>&1
export PYTHONPATH="/workspace/repo:/workspace/runtime/cst-modeling3d"
unset ADFLOW_USE_ANK ADFLOW_USE_NK ADFLOW_NK_SWITCH OMP_NUM_THREADS OPENBLAS_NUM_THREADS MKL_NUM_THREADS
timeout 300 python3 scripts/cfd/runtime_probe.py --output /tmp/probe.json --mpi-ranks 16 --required-child-environment-json '{"ADFLOW_NK_SWITCH":"0","OMP_NUM_THREADS":"1"}' 2>&1 | tail -30
echo "rc=${PIPESTATUS[0]}"
