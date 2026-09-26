# CFD evaluator environment

This folder describes the software that graded every wing in the benchmark. It lets you rebuild that software as a container. It also lists the files you must add yourself, the machine the recorded results came from, and how to check that your copy matches.

The folder has three files.

| File | Purpose |
| --- | --- |
| `Dockerfile` | Builds the container image with Docker or Podman. |
| `apptainer.def` | Builds the same image with Apptainer, for clusters without Docker. |
| `ENVIRONMENT.md` | This document. |

## 1. What the container provides

### The base image

The CFD workers ran inside the public ADflow image published by the MDO Lab. The image is `mdolab/public` on Docker Hub, from the `u20-gcc-ompi-stable` line. Both build files name the image by digest, so the tag can move without changing what you get.

| Item | Value |
| --- | --- |
| Image for linux/amd64 | `mdolab/public@sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd` |
| Platform list the tag pointed to | `sha256:68393a976907d6306f8b2d87ba95365a8720c3b2a5e55ae431f3cb05d5198223` |
| Download size | about 2.7 GB in 79 compressed layers |

Both digests are written in the grading protocol, `configs/cfd/final_protocol_candidate_v2.json`, in its `environment` block. The platform list holds only the one amd64 image. On 2026-09-25 the tag `u20-gcc-ompi-stable` still pointed to that list, and both digests still resolved on Docker Hub. The image exists only for amd64. It does not run on Arm machines.

The base image was built in June 2025. It contains Ubuntu 20.04, gcc, Open MPI 4.1.6, PETSc, CGNS 4.2.0, Python 3.9.12 installed through pyenv, numpy, scipy, mpi4py, petsc4py, and the MDO Lab packages ADflow, pyHyp, cgnsUtilities, baseclasses, pyGeo, IDWarp and pySpline. The build history of the image shows numpy 1.21.5, scipy 1.7.3 and mpi4py 3.1.5 being installed. The MDO Lab packages were taken from their main branches at build time, and the digest pins their exact versions. The image has one account of its own, named `mdolabuser`, with user id 1000. Every command runs as that account. The grader refuses to run as root.

### `cst-modeling3d`

The SuperWing mesh generator builds the wing surface with the library `cst-modeling3d`. The recorded runs did not install it as a package. They placed a git checkout on `PYTHONPATH`. The container does the same.

| Item | Value |
| --- | --- |
| Source | `https://github.com/YangYunjia/cst-modeling3d.git` |
| Commit | `9500b19a732463c26b32f6860ac6c517c7d212a5` |
| Location in the container | `/home/mdolabuser/repos/cst-modeling3d` |
| `PYTHONPATH` | `/home/mdolabuser/repos/cst-modeling3d` |

The build stops if the checkout is not exactly this commit. The `.git` directory stays in the image on purpose. The grader's runtime probe, `scripts/cfd/runtime_probe.py`, runs `git rev-parse HEAD` next to the imported module and compares the answer with the `cst_modeling3d_commit` field of the protocol. Without `.git` the probe reports no commit and the grader stops. The library imports numpy, scipy and matplotlib when it loads. The recorded runs added no Python packages to the base image, so the base image must already provide them. Step 1 of the verification recipe checks this.

### Solver settings

The protocol requires six environment variables in every ADflow process. The protocol lists them in `environment.required_child_environment`. Their values are these.

| Variable | Value | Meaning |
| --- | --- | --- |
| `ADFLOW_USE_ANK` | `1` | Turn on ADflow's approximate Newton Krylov solver for the early part of the solve. |
| `ADFLOW_USE_NK` | `1` | Turn on ADflow's Newton Krylov solver for the late part of the solve. |
| `ADFLOW_NK_SWITCH` | `1e-8` | Relative residual at which ADflow switches to the Newton Krylov solver. |
| `OMP_NUM_THREADS` | `1` | One OpenMP thread per MPI rank. |
| `OPENBLAS_NUM_THREADS` | `1` | One OpenBLAS thread per MPI rank. |
| `MKL_NUM_THREADS` | `1` | One MKL thread per MPI rank. |

The released grader treats these names in a special way. It refuses to start when any variable that begins with `ADFLOW_`, or any of the three thread variables, is set in its own environment. It then adds the six values itself, from the protocol, to the environment of every `mpirun` child, and passes only a fixed list of other variables through. This is why the container does not export them. They are written to `/etc/wingbench/solver.env` instead. Load that file only when you run the SuperWing mesh or solver scripts by hand, with `source /etc/wingbench/solver.env`. Never load it in a shell that will run `scripts/cfd/grade_benchmark.py`.

### How the container starts

The base image writes its compiler, MPI, PETSc and Python paths into a shell file, `/home/mdolabuser/.bashrc_mdolab`, that only interactive shells read. A plain `docker run image python3` would not find the right Python. The Docker image therefore has an entry script, `/usr/local/bin/wingbench-entrypoint`. It reads that file, puts `cst-modeling3d` on `PYTHONPATH`, and then runs your command. The Apptainer image reads the same file once at build time and stores every variable it sets in the container environment, so every command sees them.

### Paths inside the container

The recorded runs mounted the repository at `/workspace/repo` and their work area at `/workspace/out`. The grader takes a lock file at `/workspace/.wingbench-locks/np16.lock`, the `execution.host_lock_path` field of the protocol. The lock allows one 16 rank solve per machine. The grader finds its own repository root from the location of its script, so the repository may live elsewhere, but `/workspace` must exist and be writable.

## 2. Files you must add

### 2.1 The SuperWing scripts

The grader calls the mesh generator and the ADflow runner written by the SuperWing authors. The files are not stored in this release. The authors publish them in the public AeroTransformer code repository, `https://github.com/tum-pbs/AeroTransformer`, in its `simulation` folder. The release uses them at commit `3dc350ff69e354d1451eae468c686a368bd6151f`, the same commit that section 2.2 names for the model code. Three of the files are used unchanged. The fourth, `run-adflow.py`, is used with the release's patch `scripts/cfd/run-adflow.wingbench.patch`. The patch sets solver options and records the lift matching result, as the release's `scripts/cfd/README.md` describes. Run this once in the repository root.

```bash
python3 scripts/cfd/fetch_superwing_scripts.py
```

It downloads the four files at that commit into `scripts/cfd/aerotransformer/`, applies the patch, and checks every file against the protocol. The protocol pins these four files. Three are scripts and one is the wing tip geometry they read.

| Path in the repository | sha256 |
| --- | --- |
| `scripts/cfd/aerotransformer/gen-mesh.superwing.py` | `7daf656cdb23c6de32fba1585e22bb850d1b7e86c2ad7d031239989f2d1f73b4` |
| `scripts/cfd/aerotransformer/run-adflow.py` | `c4f2bb3c04aa21a5a07b5c51a3612e4cd04f7bcc108894e953dd2c6506a86ad6` |
| `scripts/cfd/aerotransformer/single-point.py` | `3682aad55ebc051121e0aa373305d0341bbde1a57642fc3f8981b84ffbd15f27` |
| `scripts/cfd/aerotransformer/original_tip.xyz` | `c93c1e2c74da73c9610d5c77e81b2f1d9bc0af53df04e1d608aaa61cda3e7db1` |

The grader checks these hashes before it runs anything and refuses to run if one differs. It then copies the scripts into each case directory under adapted names and checks those copies too, against the `generated_case_file_sha256` block of the protocol. The files must therefore match byte for byte. If your machine cannot download, obtain the same four files from that repository at that commit by another route, apply the patch with `patch scripts/cfd/aerotransformer/run-adflow.py scripts/cfd/run-adflow.wingbench.patch`, and run step 2 of section 5.

### 2.2 The surrogate model

The cheap model is the L size surface flow AeroTransformer, called `ATsurf_L`. It does not run inside this container. It runs on the optimizer side, on the CPU, in float32. It is listed here so that the whole benchmark can be rebuilt from one place.

| Item | Value |
| --- | --- |
| Weights repository | `https://huggingface.co/thuerey-group/AeroTransformer` |
| Repository revision | `698d70095a00d6f4a25f870e8de0772fc12b68f7` |
| Weights file | `ATsurf_L/best_model_weights`, 58,218,804 bytes, sha256 `4fa8fb4e3986cdb7a4a24f1f74410c54412beb5191857c76fdd4c4f0c5dc8bfa` |
| Model config file | `ATsurf_L/model_config`, 310 bytes, sha256 `a6cedb0a3dba73f99c41bcfdf3913d37a912c6b0558d5f906e6633aebdca8ef2` |
| Model code | `https://github.com/tum-pbs/AeroTransformer` at commit `3dc350ff69e354d1451eae468c686a368bd6151f` |

The benchmark protocol `configs/benchmark/multifidelity_benchmark_protocol_v0.json` names the model `ATsurf_L` and pins a binding file, `configs/benchmark/aerotransformer_l_v1.json`, by its sha256 `c18b1c9e5535ec0bf38ec1178aa16d46ab9b599cbaa71f6ce5c7eb395e6220d4`. The binding file is not in this release. The values above come from a copy of it whose sha256 matches that pin. On 2026-09-25 both file hashes matched the files on HuggingFace at that revision, and that revision was the head of the model repository.

## 3. Reference hardware and time limits

The recorded results come from AWS `m7a.4xlarge` machines. Each machine has 16 vCPUs and 64 GB of memory. One wing was graded per machine at a time. Each CFD job used all 16 cores as 16 MPI ranks with one thread each, and it had the full 64 GB. The grader enforces one job per machine with the lock file described above. Give each rank a core of its own if you can. When two ranks share one core the solves take longer.

| Step | Limit | Protocol field |
| --- | --- | --- |
| Mesh generation | 1800 seconds, which is 30 minutes | `execution.mesh_timeout_seconds` |
| Each of the three flow conditions | 7200 seconds, which is 2 hours | `execution.solve_timeout_seconds_per_attempt` |
| Retries after an infrastructure failure | none | `execution.infrastructure_retries` |

The three conditions run one after another, so one wing can take up to six and a half hours. The limits are wall clock times measured on the machine above.

The limits matter for the results. A job that reaches a limit is stopped. The protocol records it as inconclusive. It gets no score, and the wing does not count as delivered. On a slower machine, on a machine where two ranks share one core, or with less memory so that the solver swaps, the same wing takes longer. More wings then reach the limits, and the delivery counts come out lower than the recorded ones for reasons that have nothing to do with the optimizer. On a faster machine the opposite happens. Results are only comparable to the recorded ones when the hardware is at least as fast, and any report should state the hardware and the limits it used.

## 4. Build and run

### Docker

```bash
docker build -t wingbench-cfd -f benchmark/environment/Dockerfile benchmark/environment
```

Prepare a work directory on the host with the repository inside it. Add the four SuperWing files under `work/repo/scripts/cfd/aerotransformer/`.

```bash
mkdir -p work/out
git clone <this repository> work/repo
docker run --rm -it --mount "type=bind,src=$PWD/work,dst=/workspace" wingbench-cfd
```

Inside the container the repository is at `/workspace/repo`. The container account has user id 1000. If your host account has a different id, either run `chown -R 1000:1000 work` on the host first, or add `--user "$(id -u):$(id -g)"` to the run command.

### Apptainer

```bash
apptainer build wingbench-cfd.sif benchmark/environment/apptainer.def
apptainer exec --cleanenv --bind "$PWD/work:/workspace" wingbench-cfd.sif bash
```

Apptainer can build this without root on most systems. If the build fails for that reason, add `--fakeroot`. Use `--cleanenv` when you run the image. It keeps host variables out of the container. This matters twice. The grader refuses to start when `ADFLOW_` or thread variables come in from the host, and a host `PYTHONPATH` could shadow the container's. Apptainer runs the container as you, so the rule against root is met on its own.

## 5. Verification recipe

### Step 1. The container

Run this inside the container. It must print `OK`.

```bash
python3 - <<'PY'
import subprocess, sys
assert sys.version_info[:2] == (3, 9), sys.version
import numpy, scipy, cst_modeling, cst_modeling.surface
import adflow, baseclasses, cgnsutilities, mpi4py, petsc4py, pyhyp
commit = subprocess.check_output(
    ["git", "-C", "/home/mdolabuser/repos/cst-modeling3d", "rev-parse", "HEAD"], text=True
).strip()
assert commit == "9500b19a732463c26b32f6860ac6c517c7d212a5", commit
print("OK python", sys.version.split()[0], "cst_modeling", cst_modeling.__version__)
PY
command -v mpirun
env | grep -E '^(ADFLOW_|OMP_NUM_THREADS|OPENBLAS_NUM_THREADS|MKL_NUM_THREADS)' || echo "no solver variables in the shell, good"
```

The last line must report that no solver variables are set.

### Step 2. The pinned grader files

Run this in the repository root, on the host or in the container. It hashes every file the protocol pins and compares the result with the protocol.

```bash
python3 - <<'PY'
import hashlib, json
from pathlib import Path
protocol = json.load(open("configs/cfd/final_protocol_candidate_v2.json"))
problems = 0
for entry in protocol["scientific_files"]:
    path = Path(entry["path"])
    observed = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else "missing"
    status = "ok" if observed == entry["sha256"] else "FAIL"
    problems += status == "FAIL"
    print(status, entry["path"])
print("files with a problem", problems)
PY
```

After the fetch step of section 2.1, all 24 lines must say `ok`. Before it, exactly the four files of section 2.1 say `FAIL`. All 24 files were checked this way on 2026-09-25 and matched.

### Step 3. The surrogate files

```bash
sha256sum ATsurf_L/best_model_weights ATsurf_L/model_config
```

The two hashes must equal the values in section 2.2.

### Step 4. The base image

If Docker is installed, this must succeed and print a manifest.

```bash
docker manifest inspect mdolab/public@sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd
```

Without Docker, `skopeo inspect --raw docker://mdolab/public@sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd` does the same.

## 6. A rebuild that was tested

On 2026-09-25 the Docker image was built from this folder on a new `m7a.4xlarge`, the four SuperWing files were fetched, and `benchmark/grade_local.py` graded one recorded wing. All three conditions returned lift, drag, angle of attack, and lift-to-drag ratio identical to the recorded values, and the score equaled the recorded 33.401823323619524. The grade took 2 hours 13 minutes. The runtime probe ran and its fingerprint differed from the campaign's, as section 7 explains, so the result document carries `grading_mode` `local`. The folder `rebuilt_test/` holds that result document, the grader's log, the fetch log, and the image digest of the rebuilt container.

## 7. What this rebuild does not reproduce

1. The exact image that graded the wings. The protocol records it as `final_child_image_linux_amd64_digest`, `sha256:53a214a5c6ea52287b08656da5e370411323aca64b60e7ab0b13992eb21f8c44`. That image lived in a private registry and is not public. The build files rebuild the same content from the public base image, but the new image has a different digest. The agent study ran later and its result documents record a newer build of the same recipe, `sha256:426f5b1e8758bb48be843231a7040e539ad8a8e0ae5acd6928fbc06a26fa067f`, with the same base image, library commit, and solver settings.

2. The runtime fingerprint. The protocol field `expected_runtime_fingerprint_sha256` holds `60263209c65d73810a768021b8043ced6038a9c55b0c7feaf37c82168e73314d`. It is the sha256 of the complete report written by `scripts/cfd/runtime_probe.py`. That report includes the version string of the host kernel, the file path of every module, the account ids, and the values of `PATH` and similar variables. A rebuilt environment on another machine produces a different report, so the released grader stops with a runtime fingerprint mismatch error. To grade with a rebuilt environment you must run the probe, write its hash into a copy of the protocol, and treat that copy as a new protocol revision, as the protocol's change rule requires. Results graded under it are your own and are not the recorded results.

3. The grader's other inputs. `scripts/cfd/grade_benchmark.py` also requires an authorization file, a runtime attestation file and a publication journal on its command line. The tools that produced the first two ran on the recorded machines and are not in the release. The environment described here is necessary for a rerun, but it is not sufficient on its own.

4. The surrogate. Section 2.2 lists its files, but the model runs outside this container.
