#!/usr/bin/env python3
"""Campaign interface for the wing-design task.

This is a thin client. It talks to the campaign service over a local socket;
the service does the work and keeps the authoritative records. You can read this
file, and you can write your own client if you prefer -- the accounting, the
budget and the handoff record live on the service side either way.

  campaign.py task                            the objective and flight conditions
  campaign.py schema                          the fields an evaluation returns
  campaign.py budget                          what is left
  campaign.py validate --geom FILE            domain check, canonical hash
  campaign.py surrogate --geom F [--geom F..] official surrogate, counted
  campaign.py surrogate --batch FILE [--client-batch-id ID]
  campaign.py surrogate-batches               recent batch ids and states
  campaign.py surrogate-status --batch-id ID  compact finished ranges, running chunk and state
  campaign.py surrogate-wait --batch-id ID [--timeout-seconds N]
                                                wait for a terminal batch state without polling
  campaign.py surrogate-fetch --batch-id ID   retrieve committed rows, never reevaluate or charge
  campaign.py surrogate-cancel --batch-id ID  stop before the next chunk; a running chunk finishes/accounted
  campaign.py hand-off --geom FILE --reason T [--notes F] [--recommend FILE]
  campaign.py nominate --geom FILE --reason T [--notes F]
  campaign.py log-compute --seconds N --label T

A geometry file is json: either a bare list of 38 physical values, or an object
with a "geometry_38d" field holding that list.
"""
from __future__ import annotations
import argparse, json, os, socket, sys
from pathlib import Path

SOCK = os.environ.get("EPISODE_SOCKET",
                      str(Path(os.environ["EPISODE_ROOT"]) / "ipc/episode.sock"))
EP = Path(os.environ["EPISODE_ROOT"]).resolve()


def call(op: str, **kw) -> dict:
    ctx_p = EP / "source/turn_context.json"
    if ctx_p.is_file():
        kw.setdefault("turn", json.loads(ctx_p.read_text()).get("turn"))
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(7200)
    try:
        s.connect(SOCK)
    except (ConnectionRefusedError, FileNotFoundError):
        raise RuntimeError("the campaign service is not accepting connections right now; "
                           "nothing was sent")
    s.sendall((json.dumps({"op": op, **kw}) + "\n").encode())
    buf = b""
    while not buf.endswith(b"\n"):
        chunk = s.recv(1 << 20)
        if not chunk:
            break
        buf += chunk
    s.close()
    if not buf.endswith(b"\n"):
        raise RuntimeError("the campaign service closed the connection before replying. The "
                           "service records whether any of this call's work was performed or "
                           "charged; `campaign.py budget` shows the recovered state once the "
                           "service answers again")
    r = json.loads(buf)
    if not r.get("ok"):
        raise RuntimeError(r.get("error", "call failed"))
    return r["result"]


def _load(p):
    return json.loads(Path(p).read_text())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("task", "schema", "budget"):
        sub.add_parser(n)
    v = sub.add_parser("validate"); v.add_argument("--geom", required=True)
    s = sub.add_parser("surrogate")
    s.add_argument("--geom", action="append", default=[])
    s.add_argument("--batch", default=None)
    s.add_argument("--client-batch-id", default=None)
    sub.add_parser("surrogate-batches")
    for cmd in ("surrogate-status", "surrogate-fetch"):
        x = sub.add_parser(cmd); x.add_argument("--batch-id", required=True)
    x = sub.add_parser("surrogate-wait"); x.add_argument("--batch-id", required=True)
    x.add_argument("--timeout-seconds", type=float, default=300.0)
    x = sub.add_parser("surrogate-cancel"); x.add_argument("--batch-id", required=True)
    x.add_argument("--reason", default="agent requested cancellation")
    h = sub.add_parser("hand-off")
    h.add_argument("--geom", required=True); h.add_argument("--reason", required=True)
    h.add_argument("--notes", default=None); h.add_argument("--recommend", default=None)
    n = sub.add_parser("nominate")
    n.add_argument("--geom", required=True); n.add_argument("--reason", required=True)
    n.add_argument("--notes", default=None)
    lc = sub.add_parser("log-compute")
    lc.add_argument("--seconds", type=float, required=True)
    lc.add_argument("--label", default="")
    a = ap.parse_args()
    try:
        if a.cmd in ("task", "schema", "budget"):
            out = call(a.cmd)
        elif a.cmd == "validate":
            out = call("validate", geometry=_load(a.geom))
        elif a.cmd == "surrogate":
            geoms = [_load(p) for p in a.geom]
            if a.batch:
                b = _load(a.batch)
                geoms += b if isinstance(b, list) else b["geometries"]
            if not geoms:
                raise SystemExit("give --geom or --batch")
            out = call("surrogate", geometries=geoms, client_batch_id=a.client_batch_id)
        elif a.cmd == "surrogate-batches":
            out = call("surrogate_batch_list")
        elif a.cmd == "surrogate-status":
            out = call("surrogate_batch_status", batch_id=a.batch_id)
        elif a.cmd == "surrogate-wait":
            out = call("surrogate_batch_wait", batch_id=a.batch_id,
                       timeout_seconds=a.timeout_seconds)
        elif a.cmd == "surrogate-fetch":
            out = call("surrogate_batch_fetch", batch_id=a.batch_id)
        elif a.cmd == "surrogate-cancel":
            out = call("surrogate_batch_cancel", batch_id=a.batch_id, reason=a.reason)
        elif a.cmd == "hand-off":
            out = call("hand_off_wing", geometry_path=a.geom, reason=a.reason,
                       notes_path=a.notes, recommendation_path=a.recommend)
        elif a.cmd == "nominate":
            out = call("nominate_wing", geometry_path=a.geom, reason=a.reason,
                       notes_path=a.notes)
        else:
            out = call("log_local_compute", seconds=a.seconds, label=a.label)
        print(json.dumps(out, indent=1))
        return 0
    except Exception as exc:
        print(json.dumps({"error": str(exc)}, indent=1))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
