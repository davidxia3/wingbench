# Configs

The settings files the benchmark ran with.

- `cfd/final_protocol_candidate_v2.json` is the grading protocol. It pins the
  grader's source files under `scripts/cfd/` by content.
- `cfd/seed_aoa_selection_*.json` record how the angle-of-attack seed of each
  lift target was chosen from the dataset.
- `benchmark/wingbench_v2_candidate_protocol_v1.json` is the episode protocol
  the optimizers ran under. It verifies the lineage files beside it.
- `benchmark/superwing_physical_normalization_v1.json` maps the 38
  standardized design variables to physical values.
- `benchmark/undefended_50_wing_corpus_v1.json` and the three `corpus50_*`
  files are the fixed warm-start context every surrogate-assisted episode
  begins from.
- `optimizer/wingbench_v2_phase3_method_panel_v1.json` fixes the four
  surrogate-assisted baselines and their settings.

The files record each other's fingerprints, and the code checks those
fingerprints when it loads them. A few of the records they point to are
development files that are not included here.
