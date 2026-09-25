#!/usr/bin/env python3
"""Capture the solver-runtime versions required by an official CFD fingerprint."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping


MPI_SMOKE_SCHEMA_VERSION = "superwing-mpi-smoke-v1"
MPI_SMOKE_MARKER = "SUPERWING_MPI_SMOKE_V1="
THREAD_ENVIRONMENT_NAMES = frozenset({
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
})
SAFE_INHERITED_ENVIRONMENT_NAMES = frozenset({
    "AFLR_ARCH", "CAPS_GLYPH", "CASARCH", "CASREV", "CASROOT",
    "CGNS_HOME", "COMPILERS", "COMPLEXIFY_DIR", "EFCOMP", "ESP_ARCH",
    "ESP_ROOT", "HOME", "HOMEDIR", "IPOPT_DIR", "LANG", "LC_ALL",
    "LC_CTYPE", "LD_LIBRARY_PATH", "MDOLAB_JSON_DIR", "MDOLAB_PACKAGES_DIR",
    "MDOLAB_REPO_DIR", "MPI_DIR", "OMPI_MCA_btl_vader_single_copy_mechanism",
    "PATH", "PETSC_ARCH", "PETSC_ARCH_COMPLEX", "PETSC_ARCH_REAL",
    "PETSC_DIR", "PKG_CONFIG_PATH", "PYENV_ROOT", "PYTHONINC", "PYTHONLIB",
    "PYTHONNOUSERSITE", "PYTHONPATH", "TMPDIR", "UDUNITS2_XML_PATH", "USER",
})
SCIENTIFIC_IMPORT_PROGRAM = """\
import adflow
import baseclasses
import cgnsutilities
import cst_modeling
import mpi4py
import numpy
import petsc4py
import pyhyp
import scipy
print('SUPERWING_FILTERED_IMPORTS_OK', flush=True)
"""
MPI_SMOKE_PROGRAM = r'''import json
import os
import sys

from mpi4py import MPI

expected = json.loads(sys.argv[1])
comm = MPI.COMM_WORLD
names = sorted(
    name for name in os.environ
    if name.startswith("ADFLOW_")
    or name in {"OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"}
)
record = {
    "rank": int(comm.Get_rank()),
    "world_size": int(comm.Get_size()),
    "load_bearing_environment": {name: os.environ[name] for name in names},
}
records = comm.gather(record, root=0)
passed = None
if comm.Get_rank() == 0:
    records = sorted(records, key=lambda row: row["rank"])
    payload = {
        "schema_version": "superwing-mpi-smoke-v1",
        "requested_ranks": int(sys.argv[2]),
        "rank_records": records,
    }
    print("SUPERWING_MPI_SMOKE_V1=" + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), allow_nan=False
    ), flush=True)
    requested = int(sys.argv[2])
    passed = (
        requested == 16
        and [row["rank"] for row in records] == list(range(requested))
        and all(row["world_size"] == requested for row in records)
        and all(row["load_bearing_environment"] == expected for row in records)
    )
passed = comm.bcast(passed, root=0)
raise SystemExit(0 if passed else 17)
'''


def _distribution_version(*names: str) -> str | None:
    for name in names:
        try:
            return importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    return None


def _distribution_direct_url(*names: str) -> dict[str, Any] | None:
    for name in names:
        try:
            distribution = importlib.metadata.distribution(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        text = distribution.read_text("direct_url.json")
        if text:
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None


def _file_sha256(path: str | None) -> str | None:
    if not path or not Path(path).is_file():
        return None
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git_commit_near(path: str | None) -> str | None:
    if not path:
        return None
    start = Path(path).resolve().parent
    try:
        result = subprocess.run(
            ["git", "-C", str(start), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
            check=True,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


def _module_record(module_name: str, *distribution_names: str) -> dict[str, Any]:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # the exact import failure is evidence for preflight
        return {
            "available": False,
            "version": _distribution_version(*distribution_names),
            "module_path": None,
            "error": f"{type(exc).__name__}: {exc}",
        }
    module_path = getattr(module, "__file__", None)
    direct_url = _distribution_direct_url(*distribution_names)
    vcs_info = (direct_url or {}).get("vcs_info") or {}
    return {
        "available": True,
        "version": (
            getattr(module, "__version__", None)
            or _distribution_version(*distribution_names)
        ),
        "module_path": module_path,
        "module_file_sha256": _file_sha256(module_path),
        "direct_url": direct_url,
        "vcs_commit_id": vcs_info.get("commit_id") or _git_commit_near(module_path),
        "error": None,
    }


def _executable_record(name: str) -> dict[str, Any]:
    path = shutil.which(name)
    version_output = None
    error = None
    if path:
        try:
            result = subprocess.run(
                [path, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=20,
            )
            version_output = result.stdout.strip()
            if result.returncode != 0:
                error = f"returncode={result.returncode}"
        except (OSError, subprocess.SubprocessError) as exc:
            error = f"{type(exc).__name__}: {exc}"
    return {
        "path": path,
        "sha256": _file_sha256(path),
        "version_output": version_output,
        "error": error,
    }


def _shared_library_report(executable: str) -> dict[str, Any]:
    command = ["ldd", executable] if platform.system() == "Linux" else ["otool", "-L", executable]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"command": command, "returncode": None, "output": None,
                "error": f"{type(exc).__name__}: {exc}"}
    # Linux ldd appends process-specific ASLR load addresses such as
    # ``(0x00007f...)``.  They are not library identities and would make an
    # otherwise identical immutable image hash differently on every host.
    normalized_output = re.sub(
        r"\(0x[0-9a-fA-F]+\)", "(0xADDR)", result.stdout
    )
    return {
        "command": command,
        "returncode": result.returncode,
        "output": normalized_output,
        "error": None if result.returncode == 0 else f"returncode={result.returncode}",
    }


def _load_bearing_environment(environ: Mapping[str, str]) -> dict[str, str]:
    return {
        name: environ[name]
        for name in sorted(environ)
        if name.startswith("ADFLOW_") or name in THREAD_ENVIRONMENT_NAMES
    }


def _effective_probe_environment(environ: Mapping[str, str]) -> dict[str, str]:
    allowed = SAFE_INHERITED_ENVIRONMENT_NAMES | THREAD_ENVIRONMENT_NAMES
    return {
        name: environ[name]
        for name in sorted(environ)
        if name in allowed or name.startswith("ADFLOW_")
    }


def _run_filtered_scientific_import_smoke(
    environ: Mapping[str, str], timeout_seconds: int = 120
) -> dict[str, Any]:
    """Prove imports survive the exact allowlist used by CFD subprocesses."""
    effective = _effective_probe_environment(environ)
    command = [sys.executable, "-c", SCIENTIFIC_IMPORT_PROGRAM]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            env=effective,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "passed": False,
            "returncode": None,
            "output": None,
            "error": f"{type(exc).__name__}: {exc}",
            "effective_environment": effective,
        }
    return {
        "passed": result.returncode == 0
        and "SUPERWING_FILTERED_IMPORTS_OK" in result.stdout,
        "returncode": result.returncode,
        "output": result.stdout,
        "error": None if result.returncode == 0 else f"returncode={result.returncode}",
        "effective_environment": effective,
    }


def _mpi_marker_passes(
    marker: Any,
    *,
    mpi_ranks: int,
    required_child_environment: Mapping[str, str],
) -> bool:
    if not isinstance(marker, dict):
        return False
    if marker.get("schema_version") != MPI_SMOKE_SCHEMA_VERSION:
        return False
    if marker.get("requested_ranks") != mpi_ranks:
        return False
    records = marker.get("rank_records")
    if not isinstance(records, list) or len(records) != mpi_ranks:
        return False
    if any(not isinstance(row, dict) for row in records):
        return False
    if any(
        type(row.get("rank")) is not int or type(row.get("world_size")) is not int
        for row in records
    ):
        return False
    ranks = [row.get("rank") for row in records]
    if ranks != list(range(mpi_ranks)):
        return False
    expected_environment = dict(sorted(required_child_environment.items()))
    return all(
        row.get("world_size") == mpi_ranks
        and row.get("load_bearing_environment") == expected_environment
        for row in records
    )


def _run_mpi_smoke(
    mpirun_path: str | None,
    *,
    mpi_ranks: int,
    required_child_environment: Mapping[str, str],
    environ: Mapping[str, str] | None = None,
    timeout_seconds: int = 120,
) -> dict[str, Any]:
    """Launch the real MPI runtime and prove all 16 ranks see the exact solver env."""
    expected_environment = dict(sorted(required_child_environment.items()))
    probe_environment = dict(os.environ if environ is None else environ)
    observed_probe_environment = _load_bearing_environment(probe_environment)
    base = {
        "schema_version": MPI_SMOKE_SCHEMA_VERSION,
        "launcher_path": mpirun_path,
        "python_executable": sys.executable,
        "program_sha256": hashlib.sha256(MPI_SMOKE_PROGRAM.encode("utf-8")).hexdigest(),
        "requested_ranks": mpi_ranks,
        "required_child_environment": expected_environment,
        "probe_process_load_bearing_environment": observed_probe_environment,
    }
    if mpi_ranks != 16:
        return {
            **base,
            "returncode": None,
            "marker_payload": None,
            "passed": False,
            "error": "the frozen runtime preflight requires exactly 16 MPI ranks",
        }
    if not mpirun_path:
        return {
            **base,
            "returncode": None,
            "marker_payload": None,
            "passed": False,
            "error": "mpirun is unavailable",
        }
    if observed_probe_environment != expected_environment:
        return {
            **base,
            "returncode": None,
            "marker_payload": None,
            "passed": False,
            "error": "probe process load-bearing environment differs from the protocol",
        }
    expected_json = json.dumps(
        expected_environment, sort_keys=True, separators=(",", ":"), allow_nan=False,
    )
    command = [
        mpirun_path,
        "-np",
        str(mpi_ranks),
        sys.executable,
        "-c",
        MPI_SMOKE_PROGRAM,
        expected_json,
        str(mpi_ranks),
    ]
    try:
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=timeout_seconds,
            env=probe_environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            **base,
            "returncode": None,
            "marker_payload": None,
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    marker_lines = [
        line[len(MPI_SMOKE_MARKER):]
        for line in result.stdout.splitlines()
        if line.startswith(MPI_SMOKE_MARKER)
    ]
    marker_payload = None
    marker_error = None
    if len(marker_lines) != 1:
        marker_error = f"expected exactly one MPI marker, observed {len(marker_lines)}"
    else:
        try:
            marker_payload = json.loads(marker_lines[0])
        except json.JSONDecodeError as exc:
            marker_error = f"malformed MPI marker: {exc}"
    passed = (
        result.returncode == 0
        and marker_error is None
        and _mpi_marker_passes(
            marker_payload,
            mpi_ranks=mpi_ranks,
            required_child_environment=expected_environment,
        )
    )
    if not passed and marker_error is None:
        marker_error = (
            f"MPI smoke contract failed with returncode={result.returncode}"
        )
    report = {
        **base,
        "returncode": result.returncode,
        "marker_payload": marker_payload,
        "passed": passed,
        "error": marker_error,
    }
    if not passed:
        report["failure_output_tail"] = result.stdout[-4000:]
    return report


def collect(
    *,
    mpi_ranks: int,
    required_child_environment: Mapping[str, str],
) -> dict[str, Any]:
    modules = {
        "adflow": _module_record("adflow", "adflow"),
        "baseclasses": _module_record("baseclasses", "mdolab-baseclasses", "baseclasses"),
        "cgnsutilities": _module_record("cgnsutilities", "cgnsutilities"),
        "cst_modeling": _module_record("cst_modeling", "cst-modeling3d"),
        "mpi4py": _module_record("mpi4py", "mpi4py"),
        "numpy": _module_record("numpy", "numpy"),
        "petsc4py": _module_record("petsc4py", "petsc4py"),
        "pyhyp": _module_record("pyhyp", "pyhyp"),
        "scipy": _module_record("scipy", "scipy"),
    }
    mpi_library = None
    petsc_version = None
    if modules["mpi4py"]["available"]:
        try:
            from mpi4py import MPI

            mpi_library = MPI.Get_library_version().strip()
        except Exception as exc:
            modules["mpi4py"]["runtime_error"] = f"{type(exc).__name__}: {exc}"
    if modules["petsc4py"]["available"]:
        try:
            from petsc4py import PETSc

            petsc_version = list(PETSc.Sys.getVersion())
        except Exception as exc:
            modules["petsc4py"]["runtime_error"] = f"{type(exc).__name__}: {exc}"
    required = (
        "adflow", "baseclasses", "cgnsutilities", "cst_modeling", "mpi4py",
        "numpy", "petsc4py", "pyhyp", "scipy",
    )
    mpirun = _executable_record("mpirun")
    mpi_smoke = _run_mpi_smoke(
        mpirun["path"],
        mpi_ranks=mpi_ranks,
        required_child_environment=required_child_environment,
    )
    filtered_import_smoke = _run_filtered_scientific_import_smoke(os.environ)
    python_executable = {
        "path": sys.executable,
        "sha256": _file_sha256(sys.executable),
        "shared_libraries": _shared_library_report(sys.executable),
    }
    cst_commit = modules["cst_modeling"].get("vcs_commit_id")
    complete = (
        all(
            modules[name]["available"]
            and modules[name].get("module_file_sha256")
            and not modules[name].get("runtime_error")
            for name in required
        )
        and bool(mpi_library)
        and isinstance(petsc_version, list)
        and bool(cst_commit)
        and bool(mpirun["path"] and mpirun["sha256"] and mpirun["version_output"])
        and mpirun["error"] is None
        and mpi_smoke["passed"] is True
        and filtered_import_smoke["passed"] is True
        and bool(python_executable["sha256"])
        and python_executable["shared_libraries"]["error"] is None
    )
    return {
        "schema_version": "superwing-runtime-fingerprint-v2",
        "status": "complete" if complete else "incomplete",
        "configured_mpi_ranks": mpi_ranks,
        "required_child_environment": dict(sorted(required_child_environment.items())),
        "effective_probe_environment": _effective_probe_environment(os.environ),
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": sys.executable,
        },
        "platform": platform.platform(),
        "machine": platform.machine(),
        "modules": modules,
        "mpi_library_version": mpi_library,
        "petsc_version": petsc_version,
        "mpirun": mpirun,
        "mpi_smoke_test": mpi_smoke,
        "filtered_scientific_import_smoke": filtered_import_smoke,
        "python_executable": python_executable,
        "cst_modeling3d_commit_observed": cst_commit,
        "process_identity": {
            "uid": os.getuid() if hasattr(os, "getuid") else None,
            "euid": os.geteuid() if hasattr(os, "geteuid") else None,
            "gid": os.getgid() if hasattr(os, "getgid") else None,
        },
        "inherited_environment": {
            name: os.environ.get(name)
            for name in sorted(SAFE_INHERITED_ENVIRONMENT_NAMES)
        },
    }


def write_payload_atomic_no_overwrite(path: Path, payload: Mapping[str, Any]) -> None:
    """Publish complete probe evidence atomically and never replace prior evidence."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = (
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        # A same-filesystem hard link publishes the already-complete inode in a
        # single operation and, unlike os.replace(), fails if evidence exists.
        os.link(temporary_path, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        temporary_path.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--mpi-ranks", required=True, type=int)
    parser.add_argument("--required-child-environment-json", required=True)
    args = parser.parse_args()
    try:
        required_child_environment = json.loads(args.required_child_environment_json)
    except json.JSONDecodeError as exc:
        parser.error(f"--required-child-environment-json is invalid JSON: {exc}")
    if (
        not isinstance(required_child_environment, dict)
        or not required_child_environment
        or any(
            not isinstance(name, str) or not isinstance(value, str)
            for name, value in required_child_environment.items()
        )
    ):
        parser.error("--required-child-environment-json must be a non-empty string map")
    payload = collect(
        mpi_ranks=args.mpi_ranks,
        required_child_environment=required_child_environment,
    )
    try:
        write_payload_atomic_no_overwrite(Path(args.output), payload)
    except FileExistsError:
        print(f"refusing to overwrite existing runtime evidence: {args.output}", file=sys.stderr)
        return 3
    return 0 if payload["status"] == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())
