# Final verification

All results below were produced by actually running the listed commands on
2026-11-05 in fresh virtual environments (Linux x86-64). Nothing here is
inferred or copied from an earlier report. Data: synthetic only.

## Environments

| Environment | Python | Install | Purpose |
|---|---|---|---|
| dev | 3.13.18 | `uv pip install -e ".[dev]"` (fresh venv) | lint, types, tests, synthetic run, build |
| wheel | 3.11 | `uv pip install dist/blueprint_pipeline-0.2.0-py3-none-any.whl` (fresh venv) | package data, installed CLI, synthetic run from an unrelated directory |
| CI | 3.10, 3.13 × ubuntu-latest, macos-latest | `.github/workflows/ci.yml` | see "CI" below |

Resolved runtime dependencies (dev): ezdxf 1.4.5, Pillow 13.0.0, numpy 2.5.4,
jsonschema 4.26.0, PyYAML 6.0.3. Tools: pytest 9.2.0, hypothesis 6.170.14,
ruff 0.17.3, mypy 1.20.2.

## Results

| Check | Result | Command | Notes |
|---|---|---|---|
| Clean install | PASS | `uv pip install -e ".[dev]"`; wheel install in a second venv | both fresh venvs |
| Formatting | PASS | `ruff format --check src tests scripts` | 69 files |
| Lint | PASS | `ruff check src tests scripts` | rules E,F,W,I,B,UP,S,SIM,RUF |
| Type checking | PASS | `mypy` | 51 source files, `disallow_untyped_defs` |
| Network audit | PASS | `python scripts/audit_network_usage.py src` | 0 findings; negative case tested |
| Unit tests | PASS | `python -m pytest -q` | 105 passed |
| Integration tests | PASS | `python -m pytest -q` | 30 passed (real HTTP on loopback, separate processes) |
| End-to-end tests | PASS | `python -m pytest -q` | 2 passed |
| Synthetic end-to-end | PASS | `blueprint run-synthetic --report …` | 22/22 output checks (dev) and 22/22 from the installed wheel |
| Schema validation | PASS | `test_canonical.py`, e2e | every stored document validated against its schema before write; mixed units rejected |
| Provenance enforcement | PASS | `test_model_generated_wrong_source_id_is_overridden_and_reported`, `test_known_but_different_model_id_never_becomes_authority`, e2e `provenance_enforced` | wrong, missing, non-string, duplicate IDs, missing manifest entry, filename disagreement |
| Process locking | PASS | `test_only_one_process_holds_the_lock` (6 spawned interpreters), `test_blocked_job_never_removes_the_holders_lock`, `test_crashed_holder_leaves_stale_lock_requiring_review` | SIGKILL'd holder → stale-requires-review; clear refuses live owner |
| Atomic writes | PASS | `test_failed_validation_never_replaces_valid_file`, `test_success_then_failure_never_overwrites_valid` | forced EMPTY_RESPONSE after SUCCESS leaves `current.json` byte-identical |
| Freshness / hash mismatch | PASS | `test_store_freshness_states`, `test_output_input_hash_mismatch_detected`, `test_status_and_resume_follow_artifact_hashes` | STALE / CORRUPT / NO_METADATA; evidence change blocks normalize |
| Inference categories | PASS | `test_failure_categories`, `test_http_200_with_semantic_failure_is_not_success`, `test_timeout_after_send_is_not_retried` | all 9 categories; timeout sends exactly 1 request |
| Local-only controls | PASS | `test_config.py`, `test_proxy_environment_is_ignored`, `test_no_fallback_unless_explicitly_enabled` | public hosts rejected; proxy env ignored; fallback only when enabled |
| DXF reopen | PASS | `test_dxf_reopens_in_millimetres_with_exact_polygons`, e2e | `$INSUNITS=4`, per-layer counts, exact model coordinates |
| SVG validation | PASS | `test_svg_check_detects_non_uniform_scaling_and_stale_scene`, e2e | XML, counts, viewBox, in-file uniform scale (tampered SVG fails) |
| PNG visibility | PASS | `test_png_checks_detect_blank_image_and_stale_hash`, e2e | blank and squashed images fail; SVG-hash binding |
| PDF structure | PASS | `test_pdf_structure` | xref/offsets/MediaBox |
| Fit-to-page transform | PASS | `test_transform_formula_uniform_and_y_up`, `test_property_geometry_always_inside_region_with_uniform_scale` (Hypothesis, 150 cases), `test_degenerate_geometry_is_safe` | |
| Layout (text) | PASS | `test_text_collision_and_clipping_detected`, `test_label_never_crosses_walls_tiny_room_omitted` | |
| Review-hash validation | PASS | `test_stale_review_detected`, `test_status_and_resume_follow_artifact_hashes`, e2e `review_bound_to_png` | |
| Review semantics | PASS | `test_legible_false_is_a_failed_review_even_if_model_says_pass`, `test_http_200_and_parsed_json_are_not_review_success`, `test_reviewer_cannot_judge_facts` | |
| Bounded repair | PASS | `test_repair_is_bounded_and_presentation_only`, e2e `review_bounded_repair` | e2e: cycle 0 `legible:false` → 1 repair → PASS |
| Determinism | PASS | `test_deterministic_outputs`, e2e `deterministic_render` | SVG/PDF/DXF byte-identical |
| Package/build | PASS | `python -m build` | sdist + wheel; schemas present in the installed wheel |
| Privacy scan of repository | PASS | `git ls-files | xargs grep …`; `scripts/check_local_only.py .` | no absolute paths, usernames, hosts or personal data in tracked files |

## Test totals

- Passed: 137
- Failed: 0
- Skipped: 0

## Synthetic end-to-end stage outcome

| Stage | Status | Why |
|---|---|---|
| inventory | PARTIAL | by design: one registered file missing, one unregistered file, filename disagreement |
| normalize | PASS | 7/7 |
| extract | PARTIAL | by design: SYN-SRC-007 returns HTTP 200 with empty content → `EMPTY_RESPONSE` |
| classify / reconcile | PARTIAL | by design: one source without extraction; CHARLIE revision + measurement conflicts |
| build-model | PARTIAL | open conflicts and unplaced items retained |
| review | PARTIAL | local-model review PASS after 1 repair cycle; mechanical PARTIAL (1 WARN: recorded conflict) |
| package | PARTIAL | packet built; "ready for sign-off: NO" because a conflict is open — correct |

## CI (GitHub Actions, private repository)

Run of commit `3727a52`, workflow `.github/workflows/ci.yml`: every step
(format, lint, mypy, network audit, pytest, synthetic run, build) passed on
all four jobs.

| Job | Runner | Tests | Synthetic run | Build |
|---|---|---|---|---|
| ubuntu-latest, 3.10 | x86-64 | 137 passed | PASS | PASS |
| ubuntu-latest, 3.13 | x86-64 | 137 passed | PASS | PASS |
| macos-latest, 3.10 | Apple Silicon (arm64) | 137 passed | PASS | PASS |
| macos-latest, 3.13 | Apple Silicon (arm64) | 137 passed | PASS | PASS |

The 3.10 jobs resolved numpy 2.2.6 and Pillow 12.3.0, exercising the lower
part of the dependency bounds; the local 3.13 run resolved Pillow 13.0.0.

## Not verified

- **A real local vision model.** No model server is available in this
  environment and cloud models are out of scope by design; every inference
  path was exercised against the loopback fake server instead. The first
  local step should be `blueprint config --probe` followed by a single
  `blueprint extract --source <one synthetic-like test image>`.
- **Windows** (lock fallback path) — not a target platform; untested.
