#!/bin/bash
# Infrastructure wrapper, validation-only follow-up (jisen-validation-only/wb-validation-followup-20261107c).
# Runs David's explicit v3 commands in order, each gated on the previous result. It never retries a
# scientific step and never reuses a path. Exit 0 = all stages ran; exit 10 = stopped at a gate.
set -u
R=/results/wb-validation-followup-20261107c; I=$R/infra; C=/workspace/repo/rebuttal/adjoint/campaign_v3
E=(/bin/bash /workspace/repo/rebuttal/container_entry.sh)
ST=(env OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1)
SAMPLE=(python3 $I/resource_sampler.py --output)
EP=adjoint-v3-s01-low; P=$R/pipeline; mkdir -p "$P" "$R/resource_samples"
INIT=$R/initial/$EP/initialization.json; STAGE=$R/optimization/$EP/stage-04.json
ev() { printf '{"time": "%s", "stage": "%s", "event": "%s", "detail": "%s"}\n' "$(date -u +%FT%TZ)" "$1" "$2" "$3" | tee -a "$P/events.jsonl"; }
field() { [ -f "$1" ] && /usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]))' "$1" "$2" || echo missing; }
stop() { ev "$1" stop "$2"; exit 10; }

ev gate_v3_checks check "result=$(field $R/v3-checks/result.json passed)"
[ "$(field $R/v3-checks/result.json passed)" = True ] || stop gate_v3_checks "v3 preflight/tests did not pass"

deadline=$(( $(date +%s) + 36*3600 )); ev gate_controls wait ""
while :; do
  a=$(field $R/reference_a/verification_result.json passed); b=$(field $R/reference_b/verification_result.json passed)
  if [ "$a" = False ] || [ "$b" = False ]; then stop gate_controls "a control failed: reference_a=$a reference_b=$b"; fi
  if [ "$a" = True ] && [ "$b" = True ]; then ev gate_controls pass "both original-grader controls passed"; break; fi
  [ "$(date +%s)" -gt "$deadline" ] && stop gate_controls "no control verdict within 36 h: reference_a=$a reference_b=$b"
  sleep 60
done

ev initial start "free original-grader initialization, roster index 0"
"${E[@]}" "${SAMPLE[@]}" $R/resource_samples/v3-initial.jsonl -- python3 $C/initial.py --index 0 --out $R/initial \
  --release-sha256 ca3b67775868c400ed59403f8beb6fa95ca516fbcb79400cd635e20a9bb91043 >"$P/initial.log" 2>&1; rc=$?
d=$(field "$INIT" delivered); ev initial end "rc=$rc delivered=$d"
[ "$rc" = 0 ] && [ "$d" = True ] || stop initial "initialization.json does not confirm delivered=true"

ev optimization start "run.py --stage-limit 4, single-threaded BLAS"
"${E[@]}" "${SAMPLE[@]}" $R/resource_samples/v3-optimization.jsonl -- "${ST[@]}" python3 $C/run.py --initial "$INIT" \
  --out $R/optimization/$EP --release-sha256 97c2ec11bb7bd2134612e1470a5e6ffe76993078595cfba7cdbb5639a782eff4 \
  --stage-limit 4 >"$P/optimization.log" 2>&1; rc=$?
ph=$(field "$STAGE" phase); live=$(pgrep -fc 'campaign_v3/worker.py' || true)
ev optimization end "rc=$rc phase=$ph live_workers=${live:-0}"
{ [ "$ph" != missing ] && [ "$ph" != blocked ] && [ "${live:-0}" = 0 ]; } || stop optimization "no terminal non-blocked stage-04.json, or an optimizer process is still alive"

ev private start "separate original-grader process, clean parent environment"
"${E[@]}" "${SAMPLE[@]}" $R/resource_samples/v3-private.jsonl -- python3 $C/private.py --initial "$INIT" --stage "$STAGE" \
  --out $R/private/$EP/checkpoint-4 >"$P/private.log" 2>&1; rc=$?
ev private end "rc=$rc"
ev pipeline done "all stages ran; read private/$EP/checkpoint-4/result.json for the verdict"
exit 0
