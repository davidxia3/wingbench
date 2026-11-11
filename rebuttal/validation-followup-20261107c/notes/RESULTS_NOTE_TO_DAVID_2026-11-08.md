Hi David, the validation follow-up has finished. The results and compact evidence are on branch `jisen/validation-followup-20261107c` of the wingbench repo:
https://github.com/davidxia3/wingbench/blob/jisen/validation-followup-20261107c/rebuttal/validation-followup-20261107c/README.md

Separate verdicts:

a. **Old numerical run `wb-adjoint-v2-20261107b`.** Unchanged. Its strict certificate remains failed. The evidence package is in `run-b-evidence/`.

b. **Original-grader controls.** `reference_a` (node 059) and `reference_b` (node 090) both passed and delivered, with score 32.052252. The per-Mach CL/CD relative error was 0.0 at all three Mach numbers. They ran in a new control image: the v2 digest plus `task_documents.json` only (`sha256:7c1fb226…7587`).

c. **v3 runtime and integration.** Passed on AMD EPYC 9534:
   - the v2 image already contains cyipopt 1.4.1, IPOPT 3.13.4 and MUMPS, so nothing was installed;
   - the frozen v3 source was extracted from the pinned archive and mounted read-only;
   - both manifests verify and all 9 runtime hashes match;
   - the preflight made no CFD calls, and `test_core.py` passed.

d. **v3 replica** (roster index 0, `--stage-limit 4`). It ended in `pilot_paused` after 4 attempts.
   - Attempts 1–3 qualified. Forward residuals were at most 7.64e-13, and all 24 adjoints qualified on attempt 1, at 1.4e-11 to 2.2e-11.
   - Attempt 4's forward failed v2 qualification at Mach index 1 (3.27e-11) and was counted as a scientific failure. It was not retried.
   - The nominee is the changed wing from attempt 3, with native score 12.2608 (start 11.5488).

e. **Independent grade.** Delivered, score **12.2514**, **+6.08%** over the start. The native and original grades differ by 0.076%, so `native_original_agreement_passed=true`.

**Resources** (sampled lower bounds): the optimization peaked at 209.0 GiB, matching AWS's roughly 208 GiB. Each grading run was about 57 GiB. There were no OOMs or restarts. The whole run took 11 h wall and about 199 core-hours.

**One flag.** Every original-grader run here records the runtime fingerprint `matches_campaign=false`: we observed `ce490678…`, while the campaign expects `60263209…`. The fingerprint includes the host kernel string (ours is `Linux-5.15.0-176-generic`). It is identical on both of our nodes. The full fingerprint JSONs are in the evidence. If you send the AWS report, we can diff them.

Bulk meshes and solutions (2.9 GB) stay on our PVC, listed with sha256. Per the handoff, we have stopped here.

Jisen
