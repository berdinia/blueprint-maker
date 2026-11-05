# Migration from the scaffold (0.1.0 → 0.2.0)

The previous `pipeline` package is removed. `import pipeline` from the
repository root now raises an `ImportError` pointing here. It was not wrapped
because its semantics were unsafe (see `docs/ENGINEERING-ASSESSMENT.md`
D-L1, D-L2, D-P1, D-E1): a compatible wrapper would have carried those defects
into existing scripts silently.

## API mapping

| Old | New |
|---|---|
| `pipeline.config.load_config()` | `blueprint_pipeline.config.load_settings(config_path, project_root=…)` |
| `Config.native_url()/proxy_url()` | `settings.inference.native / .primary` (`EndpointSettings`); `settings.require_inference("extract")` |
| `pipeline.process_lock.acquire_lock/release_lock/stale_lock` | `blueprint_pipeline.orchestration.lock.HeavyJobLock` (context manager), `inspect_lock`, `clear_stale_lock` |
| `SourceRegistry(path).load()` | `blueprint_pipeline.inventory.SourceRegister.load(path)` (fails on duplicates/missing file) |
| `SourceRegistry.resolve_source_id(requested, advisory)` | `blueprint_pipeline.evidence.enforce_source_identity(parsed, expected_id)` — the expected ID always wins |
| `SourceRegistry.families` | computed by `blueprint classify` → `work/evidence/classification.json` |
| `ExtractionRunner(config, registry).extract(path, source_id, …)` | `blueprint extract [--source ID]` or `extraction.ExtractionRunner(ctx).run(record)` |
| `Renderer(config).render(model_path, dest)` | `blueprint render` (SVG/PNG/PDF/DXF) or `rendering.build_scene` + `write_*` |
| `validate_render(dxf, svg, png)` | `blueprint validate` → `work/validation/report.json` |
| `scripts/generate_synthetic_dxf.py`, `generate_synthetic_svg.py` | `blueprint run-synthetic --keep DIR` (both were broken) |
| `scripts/run_synthetic_e2e.py` | still works (wrapper for `blueprint run-synthetic`); no longer overwrites `TEST-REPORT.md` |
| `tests/test_pipeline.py` | `tests/{unit,integration,end_to_end}/` |
| `schemas/*.schema.json` (draft-07, unused) | `src/blueprint_pipeline/schemas/*.schema.json` (draft 2020-12, enforced) |

## Environment variables

Legacy names are rejected with their replacement:

| Old | New |
|---|---|
| `BLUEPRINT_PROXY_ENDPOINT` | `BLUEPRINT_ENDPOINT_URL` (+ `BLUEPRINT_ENDPOINT_API`) |
| `BLUEPRINT_NATIVE_ENDPOINT` | `BLUEPRINT_NATIVE_ENDPOINT_URL` |
| `BLUEPRINT_OUTPUT_DIR` | `BLUEPRINT_RESULTS_DIR` |
| `BLUEPRINT_RENDER_DIR`, `BLUEPRINT_MODEL_DIR` | `BLUEPRINT_WORK_DIR` |
| `BLUEPRINT_REGISTRY` | fixed: `<project>/register/source-register.json` |
| `BLUEPRINT_LOCK_PATH` | fixed: `<work>/locks/heavy-job.lock` |
| `BLUEPRINT_*_MM`, `RENDER_*` | `render.*` keys in YAML |
| `config/example.env` | `.env.example`, `config/example.local.yaml` |

Model names are no longer defaulted; set `models.extract` and `models.review`.

## Data

1. **Source registry → source register.** Convert each entry to
   `{"source_id", "path", "sha256"?, "declared_fragment_class"?, "notes"?}` under
   `register/source-register.json` with `schema`/`schema_version`. Paths are
   relative to `input/`. Old `families`/`copy_of`/`is_copy` become
   `register/relationships.json` entries (`EXACT_FILE_DUPLICATE`,
   `ANNOTATED_COPY`, … with `asserted_by: HUMAN`).
2. **Extraction outputs.** Old `results/<basename>.json` files are not
   compatible (they never held a successful result because of D-E1). Re-run
   `blueprint extract`.
3. **House model.** Convert a legacy `house-model.json`:

```bash
blueprint migrate-model old/house-model.json migrated-model.json
```

   Migrated rooms are marked **hypothetical** (AMBIGUOUS, UNCONFIRMED) because
   the legacy format recorded no provenance; re-create their placements as
   `PLACE_ROOM` decisions once identity candidates exist.
4. **Renders.** Old DXFs were page-scaled and vertically mirrored (D-R1);
   regenerate them. New DXFs are 1:1 millimetres in model space.

## Recommended local procedure

1. Work on a copy or a new branch; back up the private project first.
2. `pip install -e ".[dev]"`, then `python scripts/audit_network_usage.py src`,
   `python -m pytest -q`, `blueprint run-synthetic`.
3. Create a new project directory with `blueprint --project NEW init`; copy
   (do not move) evidence into `NEW/input/`; write the register.
4. `blueprint --project NEW config --probe`, then `blueprint --project NEW resume`.
