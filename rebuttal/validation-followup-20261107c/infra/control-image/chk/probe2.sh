cd /workspace/repo
source /home/mdolabuser/.bashrc_mdolab >/dev/null 2>&1
export PYTHONPATH="/workspace/repo:/workspace/runtime/cst-modeling3d"
unset ADFLOW_USE_ANK ADFLOW_USE_NK ADFLOW_NK_SWITCH OMP_NUM_THREADS OPENBLAS_NUM_THREADS MKL_NUM_THREADS
timeout 300 python3 scripts/cfd/runtime_probe.py --output /tmp/probe.json --mpi-ranks 16 --required-child-environment-json '{"OMP_NUM_THREADS":"1","OPENBLAS_NUM_THREADS":"1","MKL_NUM_THREADS":"1"}' >/dev/null 2>&1
python3 - <<'PY'
import json
d=json.load(open('/tmp/probe.json'))
print('status:', d.get('status'))
def walk(o,p=''):
    if isinstance(o,dict):
        if o.get('error') or o.get('passed') is False:
            print('FAIL', p, '->', str(o.get('error'))[:200], '| output:', str(o.get('output'))[-300:].replace('\n',' / '))
        for k,v in o.items(): walk(v,p+'.'+k)
    elif isinstance(o,list):
        for i,v in enumerate(o): walk(v,p+f'[{i}]')
walk(d)
PY
