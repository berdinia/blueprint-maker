# Engineering Assessment

Scope: the sanitized export `Blueprint-Reconstruction-Scaffold-Clean` as received
(commit `3bb6b20` on `main`). All observations below were made against that
baseline using synthetic data only. Defects marked **confirmed** were reproduced
by an executable probe (see "Baseline evidence"); nothing here is inferred from
documentation alone.

## 1. Safety gate

| Item | Finding |
|---|---|
| `Unresolved privacy findings: 0` | Present in `SANITIZATION-ATTESTATION.md`. |
| `Classification: PASS` | **No literal line.** Every reported category is PASS and the conclusion reports 0 unresolved findings; treated as a formatting gap, not a privacy finding. |
| Manifest integrity | 28 of 32 manifest entries verify. The 4 missing entries are generated outputs (`rendered/synthetic/*` ×3, `results/house-model.json.json.error.json`). The archive contains *less* than attested, never more. The attestation's "Total files exported: 32 / True binary files: 3" therefore describes a pre-pruning tree. |
| Content review | Fixtures use `SYN-*` identifiers, abstract geometry, and declare themselves synthetic. No absolute user paths, hostnames or credentials were found. |

Recommendation for the exporter: emit an explicit `Classification:` line and
regenerate `manifest.sha256` *after* pruning generated outputs.

## 2. Current architecture (baseline)

```
pipeline/            flat package named "pipeline" (generic, collision-prone)
  config.py          env-var loader, dataclasses, no validation
  process_lock.py    JSON lock file, exists()+rename
  source_registry.py registry load/save + resolve_source_id
  extraction_runner.py urllib call to an OpenAI-style endpoint, parse, write
  renderer.py        DXF-only renderer (page-scaled geometry in DXF)
  validate_render.py DXF/SVG/PNG checks
scripts/             4 scripts (2 broken, 1 hand-rolls SVG/PNG)
tests/test_pipeline.py 12 tests
schemas/             extraction + house-model JSON Schemas (draft-07, unused at runtime)
fixtures/synthetic/  house-model.json, source-registry.json
```

* Entry points: `python tests/test_pipeline.py`, `scripts/run_synthetic_e2e.py`,
  `scripts/generate_synthetic_dxf.py`, `scripts/generate_synthetic_svg.py`,
  `scripts/check_local_only.py`. No console script / CLI.
* Configuration: environment variables only. `config/example.env` uses variable
  names the code does not read (see D-C2).
* Schemas: present but never loaded or enforced by any code path.
* Prompt templates: one inline system prompt string in `extraction_runner.py`.
* Inference adapters: one inline `urllib` call; no streaming, no raw capture.
* Orchestration: none — stages are not represented; no status or resume.
* Dependencies: `ezdxf>=1.3`, `Pillow>=9.0`, `numpy>=1.24` (unbounded above);
  dev `pytest`, `ruff`, `mypy` (unpinned). `requires-python >=3.10`.

### Pipeline stages: documented vs implemented

| Documented stage | Baseline implementation |
|---|---|
| source inventory | Registry load only; no file scan, no checksums |
| normalization | **absent** |
| local extraction | Present but every outcome is written as an error (D-E1) |
| source-family analysis | Static `families` list in registry; no analysis |
| fragment classification | **absent** (`fragment_type` free text) |
| room-identity resolution | **absent** |
| canonical model | Hand-written fixture only; schema not enforced |
| deterministic CAD | DXF only; SVG/PNG/PDF **absent** |
| mechanical validation | Partial; several checks vacuous (D-V*) |
| visual review | **absent** (e2e "review" check passes any verdict) |
| human review | **absent** |

## 3. Baseline test results (exact)

Environment: Linux, CPython 3.11.x, clean venv, `uv pip install -e ".[dev]"`
(resolved ezdxf 1.4.5, Pillow 13.0.0, numpy 2.4.6).

| Command | Result |
|---|---|
| `python -m pytest tests/test_pipeline.py -q` | 12 passed, 9 warnings (ezdxf `closed` deprecation) |
| `python tests/test_pipeline.py` | 12 passed / 0 failed |
| `python scripts/run_synthetic_e2e.py` | 9/9 PASS (but see D-T1..D-T4: several checks are vacuous) |
| `python scripts/generate_synthetic_dxf.py` | **crash**: `AttributeError: 'RenderResult' object has no attribute 'page_w'` |
| `python scripts/generate_synthetic_svg.py` | exits 0 but writes **DXF text into `synthetic-schematic.svg`** |
| `ruff check .` | 108 findings |
| `ruff format --check .` | 11 files would be reformatted |
| `mypy pipeline` | 4 errors incl. `Name "_atomic_write" is not defined` |
| `python scripts/check_local_only.py .` | 0 hits (no terms dictionary supplied → no-op by design) |

Note: the attestation reports "Unit test suite: 10/10"; the suite contains 12 tests.

## 4. Confirmed defects

Severity: **C** critical (wrong data, privacy or safety), **H** high, **M** medium.

### Provenance

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-P1 | C | `resolve_source_id` matches on `model_advisory == sid`; when the caller's ID is not in the registry but the model's guess is, **the model's ID becomes authoritative** and no mismatch is recorded. | `resolve_source_id("SYN-UNKNOWN-999", "SYN-002")` → `authoritative: SYN-002, mismatch: False` |
| D-P2 | H | Missing registry file loads silently as empty. | `SourceRegistry("/nonexistent").load()` → 0 sources, no error |
| D-P3 | H | Duplicate `source_id` entries silently overwrite each other (last wins). | 2 entries → 1 source, notes `'b'` |
| D-P4 | M | Missing manifest entries fall through to `resolved_from: "caller"`; nothing marks the result unverifiable. | code path |
| D-P5 | M | Fixture inconsistencies: `SYN-FAM-A.copy_of` points at the copy, not the original; `name_conflicts.sources` use lowercase `syn-src-001` which matches no registered ID; tests use `SYN-SRC-001`, which is not in the registry. | fixture review |

### Process lock

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-L1 | C | A **blocked** job calls `release_lock()` and deletes the *holder's* lock file. A third job can then start while the first is still running. | holder acquires; blocked runner returns `BLOCKED`; lock file no longer exists |
| D-L2 | C | `exists()`-then-`rename()` is not atomic; all contenders share one `.tmp` name. Under contention **two processes acquired the lock simultaneously**, and others crashed with `FileNotFoundError`. | 6 processes × 20 trials: max 2 simultaneous holders |
| D-L3 | H | A detected stale lock is never reclaimed (`pass`), so a crashed job blocks forever; a *corrupt* lock is reclaimed silently. | dead PID + age 0 → `stale=True`, `acquire=False` |
| D-L4 | M | `release_lock` removes the file regardless of owner. Records lack hostname, operation, model. | code path |

### Extraction / inference

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-E1 | C | `_write_result` calls `_atomic_write` as a bare name (it is a static method) → `NameError`. **Every non-failed extraction, including a valid one, ends as `FAILED` with an `.error.json`;** no successful result can ever be written. | fake local server returning valid JSON → `status FAILED, err NameError` |
| D-E2 | C | Transport failure or an HTTP 200 with empty content yields `semantic_status = PARTIAL_RESULT`, and `is_ok` treats `PARTIAL_RESULT` as success. (Masked today by D-E1.) | code path |
| D-E3 | H | Uses `urllib.request`, which honours `HTTP(S)_PROXY` environment variables. In an environment with a proxy configured and no `NO_PROXY` for the endpoint host, **evidence images could be routed through a proxy**. | `urllib` semantics |
| D-E4 | H | No raw response preservation, no request IDs, no streaming support, no error categories; any exception becomes `transport="PASS", json_parse="FAIL"`. | code path |
| D-E5 | M | Ollama-only `options.num_predict` sent to an OpenAI-compatible route (ignored); image always labelled `image/png`. `extraction_id = extract-<unix seconds>` collides. | code path |
| D-E6 | M | Runner output does not conform to `extraction.schema.json` (`additionalProperties: false` vs extra fields; `extracted_at` vs `extraction_timestamp`; no `content`). | schema vs code |
| D-E7 | M | Output path is `<input basename>.json`: two sources with the same basename in different folders overwrite each other; results are not keyed by source ID. | code path |

### Rendering

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-R1 | C | **Vertical mirror.** `to_page` computes a y-down page coordinate but writes it into y-up DXF space; rooms with larger model Y appear *lower*. | room at y=4000–5000 labelled at DXF y=60.5; room at y=0–1000 at y=216.5 |
| D-R2 | H | "Inner cavity" offset is `1000 + 300·(hash % 100)` *page millimetres* on an A4 page, so it is never drawn; `wall_entity_count` reports walls that do not exist; wall thickness `nominal_placeholder_mm / 100` is an unexplained divisor. | 2 rooms → 2 polylines; `wall_entity_count=4` |
| D-R3 | H | `openings.get("windows", [{}])[0]` → `IndexError` when `windows` is an empty list. Doors are ignored. | probe |
| D-R4 | H | Rooms missing coordinates default to `0` and are drawn collapsed at the origin with status PASS. | probe |
| D-R5 | M | `page_occupancy` is an entity ratio (value 4.0), not an occupancy. `$EXTMIN/$EXTMAX` set to page size, not geometry. DXF geometry is page-scaled, so the "editable DXF" cannot be measured in real units. | probe |
| D-R6 | M | No SVG, PNG or PDF writer exists despite documentation. | code |

### Validation

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-V1 | C | PNG validator always fails on a real RGB image (`arr.reshape(1, h, w)` on an `h×w×3` array). | `cannot reshape array of size 360000 into shape (1,300,400)` |
| D-V2 | H | Missing SVG/PNG are recorded as **PASS** ("skipped"). `room_count` failure is only recorded on failure. | probe |
| D-V3 | H | Not implemented despite the docstring: geometry-inside-page, uniform-scale check, room overlap, clipping, min entity size, text-to-wall collision. | code |
| D-V4 | M | PNG background taken from pixel (0,0); hash variable shadows image height. | code |

### Configuration

| ID | Sev | Defect | Evidence |
|---|---|---|---|
| D-C1 | H | Explicit `BLUEPRINT_INPUT_DIR=/data/evidence` resolves to `/data/evidence/input` (same for output/render/model dirs). | probe |
| D-C2 | H | `config/example.env` uses `RENDER_PAGE_W_MM`, `PROJECT_ROOT`, `INPUT_DIR`… which the code never reads (`BLUEPRINT_PAGE_W_MM`, …). | probe: `RENDER_PAGE_W_MM=999` → 210.0 |
| D-C3 | M | No validation: bad floats raise raw `ValueError`; endpoints are not checked for scheme, loopback or private range; no privacy mode; model names hard-coded as defaults despite docs claiming none are embedded. | probe |

### Tests and reporting

| ID | Sev | Defect |
|---|---|---|
| D-T1 | H | e2e "atomic_error_handling" checks `SYN-INPUT-A.result.json`, a path the runner never writes — it passes vacuously. |
| D-T2 | H | e2e SVG and PNG are hand-written by the test, not produced by the renderer; the PNG check counts *compressed bytes*. |
| D-T3 | H | e2e "visual_review_semantic" passes for every verdict including `FAIL`. |
| D-T4 | M | `test_no_cloud_fallback` never exercises the runner; `test_process_lock` does not assert its stale result; `test_renderer_handles_null_names` does not check for `UNNAMED`. |
| D-T5 | M | e2e overwrites the tracked `TEST-REPORT.md` in the repository root on every run. |

### Documentation drift

* `KNOWN-ISSUES.md` K1 references `extraction.py: _invoke_model` (file is `extraction_runner.py`) and describes a silent fallback the code does not contain — the real defects (D-E1/D-E2/D-E3) are not listed.
* `ARCHITECTURE.md` claims "Geometry cannot be clipped" and lists 14 validation checks, of which 6 are not implemented.
* README "Expected outputs" lists `schematic.svg` "generated separately" — no generator exists.

## 5. Technical debt

* Package named `pipeline` (collides with common module names; not namespaced).
* Ad-hoc `sys.path.insert` in every script and test.
* No stage model, no artifact metadata, freshness by nothing (not even mtime).
* `open()` without context managers in several places.
* Duplicate scripts (`generate_synthetic_dxf.py`, `generate_synthetic_svg.py`) with diverging, broken behaviour.

## 6. Changes made (summary — details in CHANGELOG.md and MIGRATION.md)

| Area | Change |
|---|---|
| Layout | `src/blueprint_pipeline/` with `config, io, inventory, normalization, inference, extraction, evidence, canonical, rendering, validation, review, orchestration` subpackages; tests split into `unit/integration/end_to_end`. |
| Config | Typed, validated settings from YAML + `BLUEPRINT_*` env; loopback-only default; private-network mode; no implicit fallback; secrets referenced by env-var name and redacted. |
| Provenance | Caller/manifest authoritative; model ID recorded and compared; duplicate, missing and filename-disagreement detection. |
| Lock | `fcntl.flock`-based cross-process lock with owner record, bounded wait, stale-requires-review, operator inspect/clear. |
| Writes | Unique temp file + fsync + validate + `os.replace`; error artifacts separate; hash-based freshness. |
| Inference | `http.client` (ignores proxy env), OpenAI-compatible + Ollama native, streaming assembly, raw capture, 9 outcome categories, retry only before work starts. |
| Evidence | Source relationships (11 kinds), evidence-origin counting, fragment classes, placement states, identity candidates, conflict retention. |
| Canonical | Versioned JSON Schemas (draft 2020-12) + migration from legacy house-model. |
| Rendering | One scene → DXF (1:1 model mm), SVG, PNG, PDF; uniform fit-to-page; no y-mirror; deterministic. |
| Validation | Model, geometry, DXF, SVG, PNG checks; SKIP never counts as PASS; JSON + Markdown reports. |
| Review | Transport/parse/schema/verdict separated; artifact hash binding; stale detection; ≤2 bounded repair cycles; human-review packet. |
| Orchestration | Single `blueprint` CLI with per-stage status derived from artifact metadata; resume. |

## 7. Compatibility concerns

* The `pipeline` import package is removed. Its lock and provenance semantics
  were unsafe (D-L1, D-L2, D-P1); a silently compatible shim would carry those
  defects forward. Importing it now fails loudly with a pointer to `docs/MIGRATION.md`.
* The legacy `house-model.json` format is still readable via
  `blueprint migrate-model` (legacy → canonical 1.0.0).
* DXF output is now 1:1 in model millimetres (editable/measurable) instead of
  page-scaled; page layout lives in SVG/PNG/PDF.
* Environment variable names are normalised to `BLUEPRINT_*`; legacy names are
  rejected with a message listing the replacement rather than silently ignored.

## Baseline evidence

The probe script (synthetic inputs only, local loopback server) and its output
were captured during assessment; the key lines are reproduced in the tables above.
The regression tests that now cover each defect are listed in `FINAL-VERIFICATION.md`.
