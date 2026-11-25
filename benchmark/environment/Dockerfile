# Software environment of the benchmark's CFD evaluator.
#
# Base image. The MDO Lab public ADflow image from the u20-gcc-ompi-stable
# line, pinned to the exact linux/amd64 digest that graded the recorded
# results. It carries Ubuntu 20.04, gcc, Open MPI, PETSc, CGNS, Python 3.9.12,
# numpy, scipy, mpi4py, petsc4py, ADflow, pyHyp, cgnsUtilities, baseclasses
# and the other MDO Lab packages. ENVIRONMENT.md lists what you must add.
FROM mdolab/public@sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd

ARG CST_MODELING3D_URL=https://github.com/YangYunjia/cst-modeling3d.git
ARG CST_MODELING3D_COMMIT=9500b19a732463c26b32f6860ac6c517c7d212a5
ARG CST_MODELING3D_DIR=/home/mdolabuser/repos/cst-modeling3d

LABEL org.opencontainers.image.base.digest="sha256:fb6d20250ad192b6034c3e61286e6179f4854eb58e3fdb25d98dd5844fcb42fd" \
      org.opencontainers.image.base.name="docker.io/mdolab/public:u20-gcc-ompi-stable" \
      wingbench.cst_modeling3d.commit="9500b19a732463c26b32f6860ac6c517c7d212a5"

# The base image runs as its own account, uid 1000. The grader refuses root,
# so the image keeps that account.
USER mdolabuser
WORKDIR /home/mdolabuser/repos

# cst-modeling3d at the pinned commit. The build stops unless the checkout is
# exactly that commit. The .git directory is kept on purpose. The grader's
# runtime probe runs git next to the imported module and compares the commit
# with the protocol.
RUN git clone "$CST_MODELING3D_URL" "$CST_MODELING3D_DIR" \
 && git -C "$CST_MODELING3D_DIR" -c advice.detachedHead=false checkout "$CST_MODELING3D_COMMIT" \
 && test "$(git -C "$CST_MODELING3D_DIR" rev-parse --verify HEAD)" = "$CST_MODELING3D_COMMIT" \
 && test -f "$CST_MODELING3D_DIR/cst_modeling/__init__.py"

# The library is imported from PYTHONPATH. It is not pip installed, because
# the recorded runs did not install it either.
ENV PYTHONPATH=$CST_MODELING3D_DIR

USER root
# 1. git refuses a repository owned by another account. This lets the runtime
#    probe read the commit even when the container runs as a different uid.
# 2. The grader takes a lock file under /workspace. The recorded runs mounted
#    the repository at /workspace/repo and their work area at /workspace/out.
# 3. Solver settings the protocol requires in every ADflow child process.
#    They are stored in a file and NOT exported here. The grader refuses to
#    start when these names are already set in its own environment, and it
#    passes them to mpirun itself. Load the file only to run the SuperWing
#    mesh or solver scripts by hand.
# 4. The base image sets its compiler, MPI, PETSc and Python paths in a shell
#    file that only interactive shells read. The entry script reads it for
#    every command, then makes sure cst-modeling3d is on PYTHONPATH.
RUN git config --system --add safe.directory "$CST_MODELING3D_DIR" \
 && mkdir -p /workspace/.wingbench-locks /etc/wingbench \
 && chown -R 1000:1000 /workspace \
 && printf '%s\n' \
      '# Solver settings required by the grading protocol.' \
      '# Do not load this file before scripts/cfd/grade_benchmark.py. The grader' \
      '# refuses to start when these names are set, and it exports them itself.' \
      '# Load it only when you run the mesh or solver scripts by hand.' \
      'export ADFLOW_USE_ANK=1' \
      'export ADFLOW_USE_NK=1' \
      'export ADFLOW_NK_SWITCH=1e-8' \
      'export OMP_NUM_THREADS=1' \
      'export OPENBLAS_NUM_THREADS=1' \
      'export MKL_NUM_THREADS=1' \
      > /etc/wingbench/solver.env \
 && printf '%s\n' \
      '#!/bin/bash' \
      '# Reads the base image toolchain paths, adds cst-modeling3d to PYTHONPATH,' \
      '# then runs the requested command.' \
      'if [ -z "${HOME:-}" ] || [ "$HOME" = "/" ]; then export HOME=/home/mdolabuser; fi' \
      'if [ -f "${BASHRC_MDOLAB:-/home/mdolabuser/.bashrc_mdolab}" ]; then' \
      '  source "${BASHRC_MDOLAB:-/home/mdolabuser/.bashrc_mdolab}"' \
      'else' \
      '  echo "wingbench-entrypoint could not find the base image shell file" >&2' \
      'fi' \
      'cst='"$CST_MODELING3D_DIR" \
      'case ":${PYTHONPATH:-}:" in' \
      '  *":$cst:"*) ;;' \
      '  *) export PYTHONPATH="$cst${PYTHONPATH:+:$PYTHONPATH}" ;;' \
      'esac' \
      'exec "$@"' \
      > /usr/local/bin/wingbench-entrypoint \
 && chmod 0755 /usr/local/bin/wingbench-entrypoint
USER mdolabuser
WORKDIR /home/mdolabuser

# Build check. Python 3.9 from the base image, cst_modeling importable,
# numpy and scipy present, mpirun found, and the pinned commit checked out.
RUN /usr/local/bin/wingbench-entrypoint bash -c \
    'python3 -c "import sys, cst_modeling, numpy, scipy; assert sys.version_info[:2] == (3, 9), sys.version; print(\"python\", sys.version.split()[0], \"cst_modeling\", cst_modeling.__version__)" \
     && command -v mpirun \
     && test "$(git -C '"$CST_MODELING3D_DIR"' rev-parse --verify HEAD)" = '"$CST_MODELING3D_COMMIT"

ENTRYPOINT ["/usr/local/bin/wingbench-entrypoint"]
CMD ["/bin/bash"]
