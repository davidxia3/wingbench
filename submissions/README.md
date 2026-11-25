# Submissions

Each folder here is one competitor's submission, added by a pull request as
`benchmark/README.md` describes. A submission holds the episode folders the
runner wrote, the optimizer's source code or a link to a tagged release with a
matching hash, and a short note on the method, the machine, and the seeds.
Every episode must pass `benchmark/verify.py`.

The dry run in `benchmark/README.md` writes to `submissions/example/`. That
folder is ignored by git, so it never enters a pull request by accident. The
maintainers add one line to `.gitignore` for each accepted submission folder.
