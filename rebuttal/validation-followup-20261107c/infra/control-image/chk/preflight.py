"""UID 1000 preflight for the control image: imports, load_tasks(), control(), grader construction. No CFD."""
import json, os, sys, time
from pathlib import Path
t0 = time.time()
bad = sorted(k for k in os.environ if k.startswith(('OMP_', 'OPENBLAS_', 'MKL_', 'ADFLOW_')))
print('uid', os.getuid(), 'gid', os.getgid())
print('forbidden parent env:', bad)
from benchmark.common import load_tasks, TASK_DOCUMENTS
print('task documents:', TASK_DOCUMENTS)
tasks = load_tasks()
print('load_tasks ok:', {k: getattr(v, 'target_cl', None) for k, v in tasks.items()})
from rebuttal.adjoint.check_refined import control
row, refs = control()
print('control case:', row.get('original_evaluation'), [(r['mach'], r['cl'], r['cd']) for r in refs])
from benchmark.grade_local import LocalGrader
g = LocalGrader(tasks['nominal'], Path('/tmp/preflight/work'), run_id='uid1000-preflight')
print('grader constructed, child env keys:', sorted(g.child_env))
print('runtime_status:', json.dumps(g.runtime_status))
print('elapsed_s', round(time.time() - t0, 1))
# Diagnose the runtime probe report the grader wrote, if any.
for f in sorted(Path('/tmp/preflight/work').glob('runtime_fingerprint_*.json')):
    d = json.loads(f.read_text())
    print('probe report', f.name, 'status:', d.get('status'))
    def walk(o, p=''):
        if isinstance(o, dict):
            if o.get('error') or o.get('passed') is False:
                print('  FAIL', p, '->', str(o.get('error'))[:300], '| output tail:', str(o.get('output'))[-400:].replace('\n', ' / '))
            for k, v in o.items(): walk(v, p + '.' + k)
        elif isinstance(o, list):
            for i, v in enumerate(o): walk(v, p + f'[{i}]')
    walk(d)
