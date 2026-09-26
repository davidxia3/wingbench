#!/usr/bin/env python3
"""Fetch the four SuperWing simulation files the grading protocol pins.

The grader meshes and solves a wing with the mesh and ADflow scripts that the
SuperWing authors published in the public AeroTransformer repository. Three
of them are used unchanged. The fourth, run-adflow.py, is used with a small
WingBench patch that sets the solver options the paper describes. This script
downloads the four files at the pinned commit, applies the patch, and checks
every resulting file against the SHA-256 fingerprint pinned in
configs/cfd/final_protocol_candidate_v2.json. A fingerprint is a short code
computed from a file's contents. If one letter changes, the code changes.

Usage:
    python3 scripts/cfd/fetch_superwing_scripts.py

The files are written to scripts/cfd/aerotransformer/. Needs the `patch`
command, which macOS and Linux provide.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "scripts/cfd/aerotransformer"
PROTOCOL = ROOT / "configs/cfd/final_protocol_candidate_v2.json"
PATCH = ROOT / "scripts/cfd/run-adflow.wingbench.patch"
REPO = "tum-pbs/AeroTransformer"
COMMIT = "3dc350ff69e354d1451eae468c686a368bd6151f"
FILES = ["gen-mesh.superwing.py", "run-adflow.py", "single-point.py", "original_tip.xyz"]


def pinned_hashes() -> dict[str, str]:
    """Map each pinned aerotransformer file name to its SHA-256 from the protocol."""
    found: dict[str, str] = {}

    def walk(node):
        if isinstance(node, dict):
            path = node.get("path")
            digest = node.get("file_sha256") or node.get("sha256")
            if isinstance(path, str) and "aerotransformer/" in path and isinstance(digest, str):
                found[Path(path).name] = digest
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(json.loads(PROTOCOL.read_text()))
    missing = [name for name in FILES if name not in found]
    if missing:
        sys.exit(f"the protocol does not pin {missing}")
    return found


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    pins = pinned_hashes()
    DEST.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        url = f"https://raw.githubusercontent.com/{REPO}/{COMMIT}/simulation/{name}"
        print(f"downloading {name}")
        with urllib.request.urlopen(url) as response:
            (DEST / name).write_bytes(response.read())
    print("applying the WingBench patch to run-adflow.py")
    subprocess.run(["patch", "-s", str(DEST / "run-adflow.py"), str(PATCH)], check=True)
    bad = []
    for name in FILES:
        digest = sha256(DEST / name)
        status = "ok" if digest == pins[name] else "MISMATCH"
        print(f"{name:24s} {digest[:16]}  {status}")
        if status != "ok":
            bad.append(name)
    if bad:
        sys.exit(f"fingerprint mismatch for {bad}. The upstream files or the patch changed.")
    print(f"all four files match the protocol. They are in {DEST.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
