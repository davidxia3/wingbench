#!/bin/bash
# Operator-side kubectl shim for the WingBench v2 launcher. Infrastructure only.
# It changes no numerics, Job specs, solver settings, package source or hashes.
# It only affects how the operator's kubectl answers two kinds of read-only `get` call.
#
# 1) Empty --ignore-not-found JSON.
#    launch.py checks for name collisions with
#      kubectl get jobs NAME... --ignore-not-found -o json
#    and parses stdout as JSON. When no named object exists, real kubectl (v1.27.6 and
#    v1.36.1, both verified 2026-11-07) prints NOTHING and exits 0. json.loads('') then
#    raises, which stopped run wb-adjoint-v2-20261107a before any Job was created.
#    In exactly that case the shim prints an empty v1 List, which is the launcher's own
#    meaning of "no existing Jobs".
#
# 2) Transient API errors on read-only `get`.
#    launch.py polls `get jobs` every 30 s with check=True, so one API blip during a
#    16-34 h run would kill the launcher. A `get` that fails with a non-permanent error
#    is retried up to 3 times. Each attempt is bounded by coreutils `timeout -k 2s 14s`
#    and attempts are 5 s apart, so the worst case is about 58 s, inside the launcher's
#    60 s call timeout. NotFound, Forbidden, Unauthorized and invalid-argument errors are
#    never retried.
#    Do NOT use kubectl --request-timeout for this: any client override makes client-go
#    skip in-cluster config and dial localhost:8080 (verified in-pod 2026-11-07).
#
# Every non-get call (create, logs, top, ...) is exec'd unchanged.
set -u
REAL="${WB_REAL_KUBECTL:-/tools/kubectl-real}"
verb="" ignore=0 json=0 prev=""
for a in "$@"; do
  if [ -z "$verb" ]; then
    case "$a" in get|create|apply|delete|logs|top|exec|cp|version|describe|patch) verb="$a" ;; esac
  fi
  case "$a" in
    --ignore-not-found|--ignore-not-found=true) ignore=1 ;;
    -ojson|--output=json) json=1 ;;
    json) if [ "$prev" = "-o" ] || [ "$prev" = "--output" ]; then json=1; fi ;;
  esac
  prev="$a"
done
[ "$verb" = get ] || exec "$REAL" "$@"

bound=()
command -v timeout >/dev/null 2>&1 && bound=(timeout -k 2s 14s)
tmp="$(mktemp -d "${TMPDIR:-/tmp}/kshim.XXXXXX")" || exec "$REAL" "$@"
trap 'rm -rf "$tmp"' EXIT
rc=1
for attempt in 1 2 3; do
  ${bound[@]+"${bound[@]}"} "$REAL" "$@" >"$tmp/out" 2>"$tmp/err"
  rc=$?
  [ "$rc" -eq 0 ] && break
  if grep -qiE 'notfound|not found|forbidden|unauthorized|invalid|unknown (flag|shorthand)|doesn.t have a resource type' "$tmp/err"; then
    break
  fi
  if [ "$attempt" -lt 3 ]; then
    echo "kubectl-shim: transient get failure (rc=$rc, attempt $attempt/3), retrying in 5 s" >&2
    sleep 5
  fi
done
cat "$tmp/err" >&2
if [ "$rc" -eq 0 ] && [ "$ignore" = 1 ] && [ "$json" = 1 ] && [ -z "$(tr -d '[:space:]' <"$tmp/out")" ]; then
  printf '%s\n' '{"apiVersion":"v1","kind":"List","items":[],"metadata":{"resourceVersion":""}}'
  echo "kubectl-shim: empty --ignore-not-found JSON output replaced by an empty List" >&2
else
  cat "$tmp/out"
fi
exit "$rc"
