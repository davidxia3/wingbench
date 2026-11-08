# Original-grader control image for wb-validation-followup-20261107c.
# Base: the exact v2 verification image, by full digest. The only change is the
# task-document file that the v2 package omitted, supplied by David on 2026-11-07
# (sha256 396865584f718036b5bc9c3a9e500364b582eb8be59473d80e4ecd898e24a6e0).
# No other file, package, environment variable or entrypoint changes.
FROM ghcr.io/mtilyxuegao/wingbench-adjoint-verify@sha256:268ddf9d46457f9c09cfb670e98256369af633319804cceae666ef9fe754286e
COPY --chown=1000:1000 task_documents.json /workspace/repo/agents/tasks/task_documents.json
