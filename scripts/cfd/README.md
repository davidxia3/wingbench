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
- The grader calls SuperWing's mesh generator and ADflow runner. They come
  with SuperWing and are not included here. The protocol records their
  fingerprints.
- `derive_seed_aoa.py` and `prepare_superwing_cases.py` derive the
  angle-of-attack seeds and the case tables from the dataset records.

The remaining files are the publication and manifest utilities the grader
imports. Running the grader needs the ADflow environment, which is external.
