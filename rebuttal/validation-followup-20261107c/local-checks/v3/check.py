import json, sys, time
from pathlib import Path
sys.path.insert(0, '/workspace/repo')
from rebuttal.adjoint.campaign_v3.common import HERE, verify_release, sha
out = Path('/out')
r = {}
for name, pin in [('initial_release.json', 'ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043'),
                  ('pilot_release.json', '97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4')]:
    try:
        verify_release(HERE / name, pin); r[name] = 'verified'
    except Exception as e:
        r[name] = f'FAILED {type(e).__name__}: {e}'
print('manifests:', r)
from rebuttal.adjoint.campaign_v1.run_initial_grade import preflight_runtime
rel = json.loads((HERE / 'initial_release.json').read_text())
try:
    runtime, _ = preflight_runtime(rel['runtime_expected_sha256'])
    print('scientific runtime hashes: all', len(rel['runtime_expected_sha256']), 'match')
except Exception as e:
    print('scientific runtime: FAILED', type(e).__name__, str(e)[:500]); runtime = None
from rebuttal.adjoint.campaign_v3.run import optimizer_runtime
opt = optimizer_runtime()
print('optimizer:', opt['cyipopt_version'], opt['ipopt_version'], 'numpy', opt['numpy_version'], 'threads', opt['thread_environment'], 'cpu', opt['cpu_model'])
(out / 'runtime-local.json').write_text(json.dumps(dict(manifests=r, scientific=runtime, optimizer=opt), indent=2) + '\n')
