Infrastructure files for jisen-validation-only/wb-validation-followup-20261107c (validation only, no production).
resource_sampler.py   unchanged copy of the v2 package's rebuttal/compute/jisen_verify_v2/resource_sampler.py
                      (sha256 670227502d3b84f4e4b88b3cba840b6cf7a6e31715c0ba8a2fc1432d6afd4a5f); 15 s memory.current
                      samples are sampled lower bounds, not true peaks.
v3_runtime_record.py  manifest, runtime-pin, CPU-identity and read-only-source record; no CFD.
v3-checks.sh          runs v3 initial.py --preflight and test_core.py; no CFD.
v3-replica.sh         runs David's explicit v3 commands in order with gates; never retries a scientific step.
Frozen v3 source: ../v3-source/repo, extracted on the PVC from pilot-package.tar.gz
(sha256 86326a1c06566491cbadb7bd16f5b422ae14562a3365b9b760f0529c2fca9ba1), made read-only, mounted read-only at /workspace/repo.
