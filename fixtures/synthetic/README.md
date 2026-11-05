SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING.

All data in this directory is invented to exercise the pipeline. No original
scans, photographs, sketches, blueprints or derived data are included, and no
real measurements, topology, room names or source IDs are present.

- `project/` — JSON inputs of the synthetic end-to-end project (source
  register, declared relationships, human decisions, canned local-model
  responses). Generated from `src/blueprint_pipeline/testing/synthetic.py` by
  `blueprint run-synthetic --export-fixture fixtures/synthetic/project`;
  a test fails if they drift. Evidence images are generated at run time.
- `house-model.json`, `source-registry.json` — the previous scaffold's legacy
  fixtures, kept to test `blueprint migrate-model`.
