#!/bin/bash
# Infrastructure wrapper, validation-only follow-up (jisen-validation-only/wb-validation-followup-20261107c).
# Runs David's v3 initialization --preflight and the real-optimizer synthetic tests. No CFD.
set -u
R=/results/wb-validation-followup-20261107c; I=$R/infra; C=/workspace/repo/rebuttal/adjoint/campaign_v3
E=(/bin/bash /workspace/repo/rebuttal/container_entry.sh)
ST=(env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1)
O=$R/v3-checks; mkdir -p "$O"
ts() { date -u +%FT%TZ; }
echo "$(ts) runtime record";  "${E[@]}" "${ST[@]}" python3 $I/v3_runtime_record.py $O/runtime_record.json >$O/runtime_record.log 2>&1; rc1=$?
echo "$(ts) initial preflight rc1=$rc1"; "${E[@]}" python3 $C/initial.py --index 0 --preflight --out $R/preflight \
  --release-sha256 ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043 >$O/initial_preflight.log 2>&1; rc2=$?
echo "$(ts) test_core rc2=$rc2"; "${E[@]}" "${ST[@]}" python3 $C/test_core.py $R/optimizer-tests >$O/test_core.log 2>&1; rc3=$?
echo "$(ts) done rc3=$rc3"
/usr/bin/python3 - "$O" "$R" "$rc1" "$rc2" "$rc3" <<'PY'
import json, sys
from pathlib import Path
o, r, rc1, rc2, rc3 = Path(sys.argv[1]), Path(sys.argv[2]), *map(int, sys.argv[3:])
def load(p):
    try: return json.loads(Path(p).read_text())
    except Exception: return None
rec = load(o / 'runtime_record.json') or {}
pf = load(r / 'preflight/preflight-adjoint-v3-s01-low/complete.json') or {}
tl = [l for l in (o / 'test_core.log').read_text().splitlines() if l.startswith('TEST_RESULT=')]
tc = json.loads(tl[-1][len('TEST_RESULT='):]) if tl else {}
res = {'runtime_record_rc': rc1, 'runtime_record_passed': rec.get('passed') is True,
       'initial_preflight_rc': rc2, 'initial_preflight_passed': pf.get('passed') is True and pf.get('CFD_calls') == 0,
       'test_core_rc': rc3, 'test_core_passed': tc.get('passed') is True}
res['passed'] = all(res[k] for k in ('runtime_record_passed', 'initial_preflight_passed', 'test_core_passed')) and rc1 == rc2 == rc3 == 0
(o / 'result.json').open('x').write(json.dumps(res, indent=2) + '\n'); print(json.dumps(res))
sys.exit(0 if res['passed'] else 1)
PY
