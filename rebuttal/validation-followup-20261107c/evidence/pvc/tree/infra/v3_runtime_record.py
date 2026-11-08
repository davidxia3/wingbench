"""Infrastructure-only record for the v3 validation replica (no CFD).
Verifies both frozen v3 release manifests, the pinned scientific runtime and the optimizer pins,
records CPU identity and confirms /workspace/repo is read-only. Writes one JSON file exclusively."""
import json, os, platform, socket, sys
from pathlib import Path
sys.path.insert(0, '/workspace/repo')
from rebuttal.adjoint.campaign_v3.common import HERE, verify_release
out = Path(sys.argv[1])
rec = {'schema': 'jisen-v3-runtime-record-1', 'manifests': {}, 'host': socket.gethostname(),
       'node': os.environ.get('NODE_NAME'), 'platform': platform.platform()}
pins = {'initial_release.json': 'ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043',
        'pilot_release.json': '97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4'}
for name, pin in pins.items():
    try:
        verify_release(HERE / name, pin); rec['manifests'][name] = 'verified'
    except Exception as exc:
        rec['manifests'][name] = 'FAILED %s: %s' % (type(exc).__name__, exc)
try:
    probe = Path('/workspace/repo/.write-probe'); probe.write_text('x'); probe.unlink()
    rec['repo_read_only'] = False
except OSError:
    rec['repo_read_only'] = True
from rebuttal.adjoint.campaign_v1.run_initial_grade import preflight_runtime
release = json.loads((HERE / 'initial_release.json').read_text())
try:
    runtime, _ = preflight_runtime(release['runtime_expected_sha256'])
    rec['scientific_runtime'] = runtime; scientific_ok = True
except Exception as exc:
    rec['scientific_runtime_error'] = '%s: %s' % (type(exc).__name__, str(exc)[:4000]); scientific_ok = False
from rebuttal.adjoint.campaign_v3.run import optimizer_runtime
opt = optimizer_runtime(); rec['optimizer_runtime'] = opt
optimizer_ok = (opt['cyipopt_version'] == '1.4.1' and opt['ipopt_version'] == [3, 13, 4]
                and all(v == '1' for v in opt['thread_environment'].values()))
def read(p):
    try: return Path(p).read_text().strip()
    except OSError: return None
rec['cpu'] = {'model': opt['cpu_model'], 'affinity_count': len(os.sched_getaffinity(0)), 'os_cpu_count': os.cpu_count(),
              'cgroup_cpu_max': read('/sys/fs/cgroup/cpu.max'), 'cgroup_memory_max': read('/sys/fs/cgroup/memory.max')}
rec['checks'] = {'manifests_verified': all(v == 'verified' for v in rec['manifests'].values()),
                 'scientific_runtime_hashes_match': scientific_ok, 'optimizer_pins_match': optimizer_ok,
                 'repo_read_only': rec['repo_read_only']}
rec['passed'] = all(rec['checks'].values())
out.parent.mkdir(parents=True, exist_ok=True)
with out.open('x') as f: f.write(json.dumps(rec, indent=2) + '\n')
print(json.dumps({'passed': rec['passed'], 'checks': rec['checks'], 'cpu': rec['cpu']['model']}))
sys.exit(0 if rec['passed'] else 1)
