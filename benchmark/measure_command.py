"""Measure a command in a dedicated Linux cgroup/container.

The caller provides an *exclusive* cgroup scope. This wrapper never reports a
shared machine's counters as optimizer usage. CPU covers descendants, plus
the small wrapper overhead when the wrapper shares the measured cgroup.
"""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import signal
import subprocess
import time
import uuid

from benchmark.resource_logging import ResourceLog, read_cgroup_cpu


def drain_cgroup(cgroup, grace_seconds=2):
    """Reap adopted children and allow killed processes to finish exiting."""
    deadline = time.monotonic() + grace_seconds
    own_pid = os.getpid()
    while True:
        if own_pid == 1:
            # Docker runs this wrapper as PID 1, which adopts orphaned solver
            # children. Popen.wait() only reaps the direct command.
            try:
                while os.waitpid(-1, os.WNOHANG)[0]:
                    pass
            except ChildProcessError:
                pass
        remaining = set()
        for path in Path(cgroup).rglob("cgroup.procs"):
            try:
                remaining.update(int(pid) for pid in path.read_text().split() if int(pid) != own_pid)
            except FileNotFoundError:
                continue  # An empty child cgroup may disappear during cleanup.
        if not remaining or time.monotonic() >= deadline:
            return sorted(remaining)
        time.sleep(0.05)


def run(command, output, cgroup, timeout, stage, episode_id=None):
    log = ResourceLog(output, episode_id)
    before = read_cgroup_cpu(cgroup)
    if before is None:
        raise RuntimeError("isolated cgroup CPU counters unavailable; refusing unmeasured pilot")
    if list(Path("/dev").glob("nvidia[0-9]*")) or list(Path("/dev/dri").glob("renderD*")):
        raise RuntimeError("GPU devices present; CPU-only pilot requires explicit GPU instrumentation")
    group_id = str(uuid.uuid4())
    log.event("command_start", group_id=group_id, stage=stage, cgroup=str(cgroup), cpu=before,
              cpu_scope="exclusive_cgroup_including_descendants_and_wrapper_overhead",
              local_gpu="none_exposed", model_server_compute="unavailable_from_client")
    started = time.monotonic_ns()
    process = subprocess.Popen(command, start_new_session=True)
    caught_signal = None
    def forward(sig, frame):
        nonlocal caught_signal
        caught_signal = sig
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            pass
    previous = {sig: signal.signal(sig, forward) for sig in (signal.SIGTERM, signal.SIGINT)}
    timed_out = False
    try:
        try:
            code = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            code = 124
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
        # Kill residual descendants in the original process group. Detached
        # descendants are reported below and make the pilot fail validation.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        remaining = drain_cgroup(cgroup)
        after = read_cgroup_cpu(cgroup)
        log.event("command_end", group_id=group_id, stage=stage,
                  wall_seconds=(time.monotonic_ns() - started) / 1e9,
                  returncode=process.returncode, timed_out=timed_out, caught_signal=caught_signal,
                  cpu=after, cpu_delta_usec={k: after[k]-before[k] for k in before} if after else None,
                  remaining_cgroup_pids=remaining,
                  accounting_complete=after is not None and not remaining)
    return code if not remaining and after is not None else 125


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--exclusive-cgroup", type=Path, required=True)
    p.add_argument("--timeout-seconds", type=int, required=True)
    p.add_argument("--stage", required=True)
    p.add_argument("--episode-id", default=None)
    p.add_argument("command", nargs=argparse.REMAINDER)
    a = p.parse_args()
    command = a.command[1:] if a.command[:1] == ["--"] else a.command
    if not command or a.timeout_seconds <= 0:
        p.error("a command and positive timeout are required")
    return run(command, a.output, a.exclusive_cgroup, a.timeout_seconds, a.stage, a.episode_id)


if __name__ == "__main__":
    raise SystemExit(main())
