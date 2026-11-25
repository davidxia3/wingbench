"""Build an instrumented copy of the archived controller; never edit archives.

Copies only Python source. Runtime credentials/configuration/episode data are
not copied. The resource stream is authoritative for new usage accounting;
legacy compatibility counters are retained but must not be used for the audit.
"""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError(f"controller source mismatch at {old[:70]!r}")
    return text.replace(old, new, 1)


def build(source, destination):
    source, destination = Path(source), Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = {"schema": "wingbench-controller-instrumentation-v1", "files": {},
                "authoritative_model_usage": "resource model_request_start/end events",
                "legacy_usage_totals": "compatibility only; not used for resource analysis",
                "deployment_validated": False}
    for src in sorted(source.glob("*.py")):
        shutil.copy2(src, destination / src.name)
        manifest["files"][src.name] = {"original_sha256": hashlib.sha256(src.read_bytes()).hexdigest()}
    telemetry = Path(__file__).with_name("resource_logging.py")
    shutil.copy2(telemetry, destination / "wingbench_resources.py")
    agent = destination / "openai_chat_agent.py"
    text = agent.read_text()
    text = replace_once(text, "def api_call(url: str, token: str, payload: dict, timeout: int) -> dict:",
        "import importlib.util as _resource_import\n"
        "_resource_spec = _resource_import.spec_from_file_location('wingbench_resources', Path(__file__).with_name('wingbench_resources.py'))\n"
        "_resource_module = _resource_import.module_from_spec(_resource_spec)\n"
        "_resource_spec.loader.exec_module(_resource_module)\n"
        "measured_model_call = _resource_module.measured_model_call\n\n"
        "def api_call(url: str, token: str, payload: dict, timeout: int) -> dict:\n"
        "    return measured_model_call(_api_call_unmeasured, emit, url, token, payload, timeout)\n\n"
        "def _api_call_unmeasured(url: str, token: str, payload: dict, timeout: int) -> dict:")
    agent.write_text(text)
    execution = destination / "execution.py"
    text = execution.read_text()
    # Log from manager-owned code before deletion of any launch cgroup. The
    # kernel retains CPU of exited children until the cgroup is removed.
    helper = '''def _resource_cgroup_snapshot(cg, phase):
    import json
    import time
    from wingbench_resources import ResourceLog, read_cgroup_cpu
    folder = os.environ.get("WINGBENCH_RESOURCE_DIR")
    if not folder:
        raise RuntimeError("instrumented controller requires WINGBENCH_RESOURCE_DIR")
    populated = None
    try:
        populated = dict(line.split() for line in (cg / "cgroup.events").read_text().splitlines()).get("populated")
    except OSError:
        pass
    ResourceLog(Path(folder) / ("cgroup-" + cg.name + ".jsonl")).event(
        "launch_cgroup_" + phase, cgroup=str(cg), cpu=read_cgroup_cpu(cg),
        populated=populated, scope="launch_including_all_descendants; do_not_add_child_scopes")


'''
    text = replace_once(text, "def prepare_launch_cgroup(launch_id: str, memory_max: str | None) -> Path:",
                        helper + "def prepare_launch_cgroup(launch_id: str, memory_max: str | None) -> Path:")
    text = replace_once(text, "    cg.mkdir(parents=True, exist_ok=True)\n", 
                        "    cg.mkdir(parents=True, exist_ok=True)\n    _resource_cgroup_snapshot(cg, 'start')\n")
    text = replace_once(text, "def remove_empty_cgroups(cg: Path) -> None:\n",
                        "def remove_empty_cgroups(cg: Path) -> None:\n    _resource_cgroup_snapshot(cg, 'end')\n")
    execution.write_text(text)
    service = destination / "service.py"
    text = service.read_text()
    text = replace_once(text,
        '            out = _oracle().evaluate([[float(v) for v in by_hash[h]] for h in chunk])',
        '            from wingbench_resources import ResourceLog\n'
        '            with ResourceLog(PRIVATE / "surrogate_resources.jsonl").span(\n'
        '                    "surrogate", batch_id=batch_id, chunk_index=ci, requested_candidates=len(chunk),\n'
        '                    charge_source="surrogate_ledger.jsonl chunk_committed"):\n'
        '                out = _oracle().evaluate([[float(v) for v in by_hash[h]] for h in chunk])')
    service.write_text(text)
    manager = destination / "manager.py"
    text = manager.read_text()
    text = replace_once(text, 'def audit(event: str, **kw):\n',
        'def audit(event: str, **kw):\n'
        '    from wingbench_resources import ResourceLog\n'
        '    fields = {k: v for k, v in kw.items() if k in ("launch_id", "decision_id", "kind",\n'
        '              "request_id", "checkpoint_id", "checkpoint_after_query", "after_query",\n'
        '              "nomination_id", "provenance", "returncode", "open", "clean")}\n'
        '    ResourceLog(PRIVATE / "manager_resources.jsonl", EID).event(\n'
        '        "manager_event", event_name=event, metadata=fields)\n')
    manager.write_text(text)
    maker = destination / "make_episode.py"
    text = maker.read_text()
    text = replace_once(text, 'def make(eid: str, shared: Path, template: Path) -> dict:\n',
        'def make(eid: str, shared: Path, template: Path) -> dict:\n'
        '    from wingbench_resources import ResourceLog, stamp\n'
        '    resource_started = stamp()\n')
    text = replace_once(text, '    priv.mkdir(parents=True)\n',
        '    priv.mkdir(parents=True)\n'
        '    ResourceLog(priv / "manager_resources.jsonl", eid).event(\n'
        '        "episode_setup_start", **resource_started)\n')
    text = replace_once(text, '    return {"episode": eid, "path": str(ep), "private": str(priv),\n',
        '    ResourceLog(priv / "manager_resources.jsonl", eid).event("episode_setup_end")\n'
        '    return {"episode": eid, "path": str(ep), "private": str(priv),\n')
    maker.write_text(text)
    for path in destination.glob("*.py"):
        compile(path.read_text(), str(path), "exec")
        manifest["files"].setdefault(path.name, {})["instrumented_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (destination / "instrumentation_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    manifest = build(args.source, args.destination)
    print(json.dumps({"files": len(manifest["files"]), "destination": str(args.destination),
                      "deployment_validated": False}))


if __name__ == "__main__":
    main()
