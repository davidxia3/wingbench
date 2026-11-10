"""Observe pod cgroup memory every15s while preserving child exit status.

Sampled memory.current maxima are lower bounds, not true peaks or process RSS.
Optional kernel peaks are pod/cgroup-lifetime values, not per-task peaks.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time


def cgroup_paths():
    paths=[Path('/sys/fs/cgroup'),Path('/sys/fs/cgroup/memory')]
    try:
        for line in Path('/proc/self/cgroup').read_text().splitlines():
            _,controllers,location=line.split(':',2)
            if not controllers:
                paths.append(Path('/sys/fs/cgroup')/location.lstrip('/'))
            elif 'memory' in controllers.split(','):
                paths.append(Path('/sys/fs/cgroup/memory')/location.lstrip('/'))
    except (OSError,ValueError):
        pass
    return sorted(set(paths))


def snapshot(paths=None):
    rows=[]
    for path in (cgroup_paths() if paths is None else paths):
        values={}
        for name in ('memory.current','memory.peak','memory.events','memory.events.local',
                     'memory.max','memory.usage_in_bytes','memory.max_usage_in_bytes','memory.failcnt',
                     'memory.oom_control','cpu.stat'):
            try:values[name]=(path/name).read_text().strip()
            except OSError:pass
        if values:rows.append({'path':str(path),'values':values})
    return {'timestamp_utc':datetime.now(timezone.utc).isoformat(),'monotonic_seconds':time.monotonic(),'cgroups':rows}


def summarize(samples):
    maxima={}
    for sample in samples:
        for row in sample['cgroups']:
            values=row['values']
            raw=values.get('memory.current',values.get('memory.usage_in_bytes'))
            if raw is None:continue
            try:n=int(raw)
            except ValueError:continue
            if n<0:continue
            maxima[row['path']]=max(maxima.get(row['path'],0),n)
    return {'sample_count':len(samples),'max_sampled_memory_current_bytes_by_cgroup':maxima,
            'memory_samples_available':bool(maxima),
            'scope':'15-second samples: lower bounds on each visible cgroup peak, not true peak or process RSS. Kernel peak counters, when present, cover cgroup lifetime. Ancestor or namespaced scopes must not be added together.',
            'absence_means':'unavailable, never zero; consult raw memory.events and Kubernetes OOM/events evidence'}


def run(output,command,interval=15.0):
    if interval!=15.0:raise ValueError('This infrastructure version freezes15-second sampling')
    output=Path(output);output.parent.mkdir(parents=True,exist_ok=True)
    samples=[];forwarded=[]
    # Exclusive creation: no overwrite or merge across repeated workers.
    with output.open('x') as stream:
        child=subprocess.Popen(command)
        def forward(sig,frame):
            forwarded.append(sig)
            if child.poll() is None:child.send_signal(sig)
        old={sig:signal.signal(sig,forward) for sig in (signal.SIGINT,signal.SIGTERM)}
        try:
            while True:
                sample=snapshot();samples.append(sample)
                stream.write(json.dumps(sample,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())
                try:
                    code=child.wait(timeout=interval)
                    # Preserve final state/counters even for a short task.
                    sample=snapshot();samples.append(sample)
                    stream.write(json.dumps(sample,allow_nan=False)+'\n');stream.flush();os.fsync(stream.fileno())
                    break
                except subprocess.TimeoutExpired:pass
        finally:
            for sig,handler in old.items():signal.signal(sig,handler)
    result=summarize(samples);result.update(command=command,child_return_code=code,forwarded_signals=forwarded)
    with output.with_suffix('.summary.json').open('x') as f:f.write(json.dumps(result,indent=2)+'\n')
    return code if code>=0 else 128-code


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output',type=Path,required=True)
    p.add_argument('command',nargs=argparse.REMAINDER);a=p.parse_args()
    command=a.command[1:] if a.command[:1]==['--'] else a.command
    if not command:p.error('A child command is required after --')
    return run(a.output,command)
if __name__=='__main__':sys.exit(main())
