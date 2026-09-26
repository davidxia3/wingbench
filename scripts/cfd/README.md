# CFD grader

The code that graded every wing. The grading protocol
(`configs/cfd/final_protocol_candidate_v2.json`) records the fingerprint of
each file here, and the grader checks them before it runs.

- `grade_benchmark.py` grades one wing: builds the mesh, runs the three flow
  conditions in series at fixed lift, and writes the result document.
- `grading_protocol.py` loads the frozen protocol and refuses to run if any
  pinned file has changed.
- `task_protocols.py` derives the low-load and
  high-load protocols from the frozen base, changing only the lift target and
  its angle-of-attack seed.
- The grader calls SuperWing's mesh generator and ADflow runner. The SuperWing
  authors publish them in the public AeroTransformer repository, and the
  protocol records their fingerprints. Run
  `python3 scripts/cfd/fetch_superwing_scripts.py` once. It downloads the four
  files at the pinned commit into `aerotransformer/`, applies
  `run-adflow.wingbench.patch` to the ADflow runner, and checks every file
  against the protocol. The patch sets the solver options the paper describes
  and records the lift-matching result in the output. After this step
  `grading_protocol.py` loads.
- `derive_seed_aoa.py` and `prepare_superwing_cases.py` derive the
  angle-of-attack seeds and the case tables from the dataset records.

The remaining files are the publication and manifest utilities the grader
imports. Running the grader needs the ADflow environment, which is external.
