import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from benchmark.resource_report import model_summary, audit

from benchmark.resource_logging import ResourceLog, measured_model_call, normalized_usage, read_cgroup_cpu


class ResourceTests(unittest.TestCase):
    def test_missing_is_not_zero_and_explicit_zero_is_preserved(self):
        self.assertIsNone(normalized_usage(None)["input_tokens"])
        self.assertIsNone(normalized_usage({"prompt_tokens": -1})["input_tokens"])
        self.assertEqual(normalized_usage({"prompt_tokens": 0})["input_tokens"], 0)
        self.assertIsNone(normalized_usage({"prompt_tokens": True})["input_tokens"])
        self.assertEqual(normalized_usage({"prompt_cache_hit_tokens": 17})["cached_input_tokens"], 17)

    def test_requests_include_failures_and_do_not_expose_secrets(self):
        rows = []
        def failure(*a):
            raise TimeoutError("secret-provider-key")
        with self.assertRaises(TimeoutError):
            measured_model_call(failure, rows.append, "secret-request-body")
        response = {"usage": {"prompt_tokens": 20, "completion_tokens": 10,
                              "prompt_tokens_details": {"cached_tokens": 5},
                              "completion_tokens_details": {"reasoning_tokens": 6}},
                    "choices": [{"text": "private-response"}]}
        self.assertIs(measured_model_call(lambda: response, rows.append), response)
        measured_model_call(lambda: {}, rows.append)
        ends = [r for r in rows if r["kind"] == "model_request_end"]
        self.assertEqual(len(ends), 3)
        self.assertEqual(len({r["request_id"] for r in ends}), 3)
        self.assertEqual([r["usage_available"] for r in ends], [False, True, False])
        self.assertEqual(ends[1]["usage"]["reasoning_tokens"], 6)
        self.assertNotIn("secret", json.dumps(rows))
        self.assertNotIn("private-response", json.dumps(rows))
        report = model_summary(rows)
        self.assertEqual(report["request_attempts"], 3)
        self.assertEqual(report["tokens"]["input_tokens"]["known_total"], 20)
        self.assertEqual(report["tokens"]["input_tokens"]["requests_missing_value"], 2)
        self.assertIn("successful_responses_missing_input_or_output_tokens", report["issues"])
        self.assertIn("unclosed_or_orphaned_model_requests", model_summary(rows[:-1])["issues"])

    def test_cpu_includes_reaped_subprocess_and_error_span_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "events.jsonl"
            log = ResourceLog(path, "test")
            with log.span("decision"):
                subprocess.run([sys.executable, "-c", "sum(i*i for i in range(1500000))"], check=True)
            with self.assertRaises(ValueError):
                with log.span("surrogate", charged_candidates=2):
                    raise ValueError("deliberate")
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertGreater(rows[1]["cpu"]["reaped_children_user_seconds"], 0)
            self.assertEqual(rows[-1]["status"], "error")
            self.assertEqual(rows[-1]["charged_candidates"], 2)
            self.assertEqual(rows[-1]["span_id"], rows[-2]["span_id"])

    def test_missing_cgroup_is_unavailable(self):
        self.assertIsNone(read_cgroup_cpu("/nonexistent-wingbench-test"))

    def test_four_query_prefix_keeps_original_budget(self):
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as folder:
            subprocess.run([sys.executable, "-m", "benchmark.run_episode", "--optimizer",
                            "benchmark.examples.retrieval_recorded:RetrievalRecorded", "--task", "nominal",
                            "--seed", "1", "--evaluator", "recorded", "--out", folder,
                            "--stop-after-checkpoint", "4"], cwd=root, check=True, capture_output=True, text=True)
            episode = Path(folder) / "U000"
            summary = json.loads((episode / "summary.json").read_text())
            self.assertEqual(summary["status"], "checkpoint_complete")
            self.assertEqual(summary["original_online_budget"], 32)
            self.assertEqual(summary["online_queries"], 4)
            rows = [json.loads(line) for line in (episode / "resources.jsonl").read_text().splitlines()]
            self.assertEqual([r["checkpoint"] for r in rows if r["kind"] == "checkpoint_nomination"], [0, 1, 2, 4])
            starts = {r["span_id"] for r in rows if r["kind"] == "span_start"}
            ends = {r["span_id"] for r in rows if r["kind"] == "span_end"}
            self.assertEqual(starts, ends)
            self.assertTrue(all(r["measurement"] is not None for r in rows if r["kind"] == "optimizer_cpu"))
            self.assertTrue(any(r["kind"] == "evaluation_reused" for r in rows))
            report = audit(episode)
            self.assertFalse(report["live_validation_passed"])
            self.assertIn("recorded_evaluator_is_not_live_CFD_validation", report["issues"])


if __name__ == "__main__":
    unittest.main()
