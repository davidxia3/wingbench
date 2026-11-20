"""Local HTTP integration check of the actual instrumented agent transport.

This uses a fake loopback provider and no real credentials or model calls.
It tests successful usage, missing usage, HTTP failure, and separate processes
like those used for planning branches. It does not validate the live provider.
"""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import sys
import threading

from benchmark.resource_report import model_summary


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers["Content-Length"]))
        if self.path == "/failure":
            self.send_response(503)
            result = {"error": "deliberate local transport test"}
        elif self.path == "/missing":
            self.send_response(200)
            result = {"model": "test-only"}
        else:
            self.send_response(200)
            result = {"model": "test-only", "usage": {"prompt_tokens": 12, "completion_tokens": 7,
                      "prompt_tokens_details": {"cached_tokens": 4},
                      "completion_tokens_details": {"reasoning_tokens": 3}}}
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(result).encode())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    code = '''import importlib.util,sys
spec=importlib.util.spec_from_file_location("agent",sys.argv[1])
agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(agent)
for path in ("success", "failure", "success", "missing"):
    try: agent.api_call(sys.argv[2]+"/"+path,"fake-local-test-token",{"model":"test-only"},5)
    except agent.AgentError: pass
'''
    processes = [subprocess.Popen([sys.executable, "-I", "-c", code,
                                  str(args.controller.resolve() / "openai_chat_agent.py"),
                                  f"http://127.0.0.1:{server.server_port}"],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    rows = []
    try:
        for index, process in enumerate(processes):
            out, err = process.communicate(timeout=20)
            if process.returncode:
                raise RuntimeError(err)
            (args.out / f"branch-{index}.jsonl").write_text(out)
            rows.extend(json.loads(line) for line in out.splitlines())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    report = model_summary(rows)
    assert report["request_attempts"] == 8, report
    assert report["failed_attempts"] == 2, report
    assert report["tokens"]["input_tokens"] == {"known_total": 48, "requests_with_value": 4, "requests_missing_value": 4}, report
    assert report["tokens"]["reasoning_tokens"]["known_total"] == 12, report
    assert report["issues"] == ["successful_responses_missing_input_or_output_tokens"], report
    assert "fake-local-test-token" not in json.dumps(rows)
    report.update(test_passed=True, evidence_kind="fake_loopback_provider", live_provider_validated=False)
    (args.out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"test_passed": True, "requests": report["request_attempts"], "live_provider_validated": False}))


if __name__ == "__main__":
    main()
