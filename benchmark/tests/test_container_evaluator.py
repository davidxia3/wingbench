"""Transport failures must not become valid grades or leave a solver running."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from benchmark.common import geometry_sha256, load_tasks
from benchmark.container_evaluator import ContainerEvaluator


class ContainerTests(unittest.TestCase):
    def test_round_trip_and_cleanup(self):
        geometry = [0.0] * 38
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            def run(command, **kwargs):
                calls.append(command)
                if command[:2] == ["docker", "run"]:
                    request_path = Path(command[-1])
                    request = json.loads(request_path.read_text())
                    self.assertEqual(request["geometry"], geometry)
                    payload = {"grade": {"geometry": geometry, "wing": geometry_sha256(geometry),
                               "delivered": False, "ld": None, "outcome": "conclusive_failure",
                               "reason_codes": ["mesh_failure"], "source": "cfd:test"},
                               "runtime": {"matches_campaign": False}}
                    (request_path.parent / "grade.json").write_text(json.dumps(payload))
                return subprocess.CompletedProcess(command, 0)
            with patch("benchmark.container_evaluator.subprocess.run", side_effect=run):
                evaluator = ContainerEvaluator(load_tasks()["nominal"], folder,
                                               image="repo@sha256:" + "1"*64,
                                               entry_script=Path(folder)/"entry.sh", run_id="U100")
                result = evaluator.grade(geometry, "q01")
            self.assertEqual(result.geometry, tuple(geometry))
            self.assertFalse(result.delivered)
            self.assertEqual(calls[-1][:3], ["docker", "rm", "-f"])
            self.assertIn("--episode-id", calls[0])

    def test_failed_container_is_removed_and_does_not_return_grade(self):
        calls = []
        def run(command, **kwargs):
            calls.append(command)
            if command[:2] == ["docker", "run"]:
                raise subprocess.CalledProcessError(125, command)
            return subprocess.CompletedProcess(command, 0)
        with tempfile.TemporaryDirectory() as folder:
            evaluator = ContainerEvaluator(load_tasks()["nominal"], folder,
                                           image="repo@sha256:" + "1"*64,
                                           entry_script=Path(folder)/"entry.sh", run_id="U100")
            with patch("benchmark.container_evaluator.subprocess.run", side_effect=run):
                with self.assertRaises(subprocess.CalledProcessError):
                    evaluator.grade([0.0]*38, "q01")
        self.assertEqual(calls[-1][:3], ["docker", "rm", "-f"])


if __name__ == "__main__":
    unittest.main()
