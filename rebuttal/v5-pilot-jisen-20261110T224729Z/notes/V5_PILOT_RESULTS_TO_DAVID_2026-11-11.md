Hi David,

Launch `jisen-20261110T224729Z` has ended. No episode reached a scientific outcome, for two separate reasons.

Results and evidence are on branch `jisen/v5-pilot-jisen-20261110T224729Z` (commit `982ffe2`): https://github.com/davidxia3/wingbench/blob/jisen/v5-pilot-jisen-20261110T224729Z/rebuttal/v5-pilot-jisen-20261110T224729Z/README.md

**1. Protocol defect: indices 0, 2, 3 and 5 blocked**
- All four initializations delivered. Their starting scores were 18.9344, 31.8733, 16.5083 and 31.7930.
- Each then blocked at the first proposal-zero call with `ValueError: Initial benchmark protocol changed`.
- Cause: `campaign_v5/live_initial.py`, lines 34–36, calls `load_protocol()` with the default path, which loads the nominal protocol (`403410fc…`). It compares that hash with the task protocol from the initialization: low is `7ba78aef…` and high is `99f5ff27…`. The next check, against `seed_alpha0`, would also use the nominal seed.
- Each episode returns its start with 0 search attempts.

**2. Eviction: index 1 interrupted, index 4 never started**
- At 16:07:01Z an SRE cluster-downsize drain of node 090 (SRE-10111) evicted lane 1 through the Eviction API. Thirteen nodes were cordoned for that downsize.
- At that point index 1 had completed:
  - the live proposal-zero trim, plus all 7 gradient requests, which is 14 native CL/CD adjoints accepted on the first attempt at 2.5e-11 to 5.9e-11 against the 1.1e-9 gate;
  - attempts 1–3 as scientific Mach 0 forward failures (2.3e-10 to 6.9e-10 against the 1e-10 gate);
  - attempt 4 accepted, with native score 28.9805 against a start of 28.9341.
- Attempt 5 was in its Mach 1 gradient when the pod was killed. There is no `stage-08`, no pipeline status and no private grade.
- Index 4 has no claim.

**What we did not do.** Nothing was retried, recreated, resumed or rerolled. Under the launch rules, index 1 cannot be cold-restarted. A follow-up for index 1 and 4, or a corrected release for the low and high tasks, needs a new authorization from you.

**Resources**
- 339.3 allocated core-hours in total.
- Sampled maximum `memory.current`, which is a lower bound: 59.1, 257.6 and 58.2 GiB for lanes 0, 1 and 2. Lane 1's peak came during a three-Mach adjoint.
- No OOM.

**Storage**
- The full raw tree (493 files, 4.2 GB) and a tar archive of it remain on our PVC (archive sha256 `f3ecfa8b…a8`).
- The branch has the compact tree (433 files, hashes computed on the cluster), the incident notes, the Kubernetes records and the cgroup samples.

Note: node 090 is no longer available to us after the downsize. Node 059 is unaffected.

Thanks,
Jisen
