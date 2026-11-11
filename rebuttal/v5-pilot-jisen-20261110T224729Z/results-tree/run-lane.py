"""Orchestration only: exact declared pair, no retries; frozen scientific code."""
import os,sys,json,subprocess,hashlib
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,'/workspace/repo')
from rebuttal.adjoint.campaign_v5.common import verify_release,require_approval
RELEASE='0d501cb205d5d9cfd98a2bb863981f1cf1be716b54ddd39ecd51bc69e5c455ca'
def write(p,d):
    with p.open('x') as f:json.dump(d,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def now():return datetime.now(timezone.utc).isoformat()
manifest=json.loads((ROOT/'launch-plan.json').read_text())
for name,digest in json.loads((ROOT/'control-hashes.json').read_text()).items():
    if hashlib.sha256((ROOT/name).read_bytes()).hexdigest()!=digest:raise RuntimeError('Launch control changed: '+name)
lane=manifest['lanes'][int(os.environ['WINGBENCH_LANE'])]
if os.environ['WINGBENCH_NODE_NAME']!=lane['hostname']:raise RuntimeError('Wrong execution node')
verify_release('/workspace/repo/rebuttal/adjoint/campaign_v5/release.json',RELEASE)
require_approval(ROOT/'authorization.json',RELEASE)
# Certificates bind both exact runtime and reviewed evidence; pipeline rechecks runtime.
cert=Path(lane['certificate'])
if hashlib.sha256(cert.read_bytes()).hexdigest()!=lane['certificate_sha256']:raise RuntimeError('Certificate changed')
claims=ROOT/'claims';claims.mkdir(exist_ok=True)
(claims/('lane-'+str(lane['lane']))).mkdir() # exclusive, never auto-retried
statuses=[]
for index in lane['indices']:
    (claims/('index-'+str(index))).mkdir()
    write(claims/('index-'+str(index))/'reservation.json',dict(index=index,started_at=now(),lane=lane['lane']))
    command=[sys.executable,'/workspace/repo/rebuttal/adjoint/campaign_v5/pipeline.py',
        '--index',str(index),'--out',str(ROOT/'results'/('index-'+str(index))),
        '--runtime-check',str(cert),'--approval',str(ROOT/'authorization.json'),'--release-sha256',RELEASE]
    log=ROOT/'logs';log.mkdir(exist_ok=True)
    with (log/('index-'+str(index)+'.log')).open('x') as stream:
        code=subprocess.run(command,stdout=stream,stderr=subprocess.STDOUT).returncode
    status=dict(index=index,returncode=code,finished_at=now(),automatic_retry=False)
    write(claims/('index-'+str(index))/'process-status.json',status);statuses.append(status)
    # A failed episode is retained. The next independently authorized episode is distinct.
write(ROOT/('lane-'+str(lane['lane'])+'-status.json'),dict(episodes=statuses,finished_at=now()))
sys.exit(0 if all(s['returncode'] in (0,2) for s in statuses) else 1)
