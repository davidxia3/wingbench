"""Run the frozen CFD environment separately from the modern surrogate Python.

Each fresh grade gets an exclusive Docker cgroup. The controller's cgroup
excludes the solver containers; both scopes must be included in accounting.
This adapter changes process placement, not the scientific grading path.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import uuid

from benchmark.common import ROOT, geometry_sha256, json_ready, load_tasks
from benchmark.evaluators import CfdEvaluator, GradeResult


class ContainerEvaluator:
    def __init__(self, task, work_root, *, image, entry_script, run_id):
        if "@sha256:" not in image:
            raise ValueError("CFD image must be pinned by digest")
        self.task = task
        self.root = Path(work_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.image = image
        self.entry_script = Path(entry_script).resolve()
        self.run_id = run_id
        self.runtime_status = {"container_image": image, "probe_pending": True}

    def grade(self, geometry, label):
        # UUID avoids stale artifacts and permits separately charged repeat grades.
        attempt = self.root / str(uuid.uuid4())
        attempt.mkdir()
        request = attempt / "request.json"
        request.write_text(json.dumps({"task": self.task.name, "geometry": list(geometry),
                                       "label": label, "run_id": self.run_id}))
        name = "wingbench-grade-" + attempt.name
        command = ["docker", "run", "--rm", "--name", name,
                   "--user", "1000:1000", "--cpus", "16", "--memory", "60g",
                   "--memory-swap", "60g", "--shm-size", "2g",
                   "--mount", f"type=bind,src={ROOT},dst={ROOT},readonly",
                   "--mount", f"type=bind,src={self.root},dst={self.root}",
                   "--mount", f"type=bind,src={self.entry_script},dst=/wingbench-entry.sh,readonly",
                   "--entrypoint", "/bin/bash", self.image, "/wingbench-entry.sh",
                   "python3", "-m", "benchmark.measure_command", "--output", str(attempt / "cgroup.jsonl"),
                   "--exclusive-cgroup", "/sys/fs/cgroup", "--timeout-seconds", "24000",
                   "--stage", "CFD-container", "--episode-id", self.run_id, "--", "python3", "-m",
                   "benchmark.container_evaluator", "--request", str(request)]
        try:
            with (attempt / "container.log").open("w") as output:
                subprocess.run(command, stdout=output, stderr=subprocess.STDOUT,
                               timeout=24100, check=True)
        finally:
            # Also removes a solver left behind after controller interruption.
            subprocess.run(["docker", "rm", "-f", name], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=30, check=False)
        payload = json.loads((attempt / "grade.json").read_text())
        if payload["grade"]["wing"] != geometry_sha256(geometry):
            raise RuntimeError("container returned a different geometry")
        self.runtime_status = {"container_image": self.image, **payload["runtime"]}
        grade = payload["grade"]
        grade["geometry"] = tuple(grade["geometry"])
        grade["reason_codes"] = tuple(grade["reason_codes"])
        return GradeResult(**grade)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args()
    request = json.loads(args.request.read_text())
    evaluator = CfdEvaluator(load_tasks()[request["task"]], args.request.parent / "cfd",
                             run_id=request["run_id"])
    grade = evaluator.grade(request["geometry"], request["label"])
    (args.request.parent / "grade.json").write_text(json.dumps(json_ready({
        "grade": asdict(grade), "runtime": evaluator.runtime_status}), indent=2) + "\n")


if __name__ == "__main__":
    main()
