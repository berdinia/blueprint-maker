# Synthetic End-to-End Test Report

Run: 2026-11-05T01:41:53Z

Fixture: synthetic (`fixtures/synthetic/`, self-declared synthetic).
No private source, no `/Users` path, no source images were accessed.

| # | Check | Result | Detail |
|---|-------|--------|--------|
| 1 | source_id_authoritative | PASS | advisory 'SYN-MODEL-ERROR' overridden by registry 'SYN-SRC-001'; mismatch recorded |
| 2 | process_lock_duplicate_blocked | PASS | first acquired, second blocked by pid 9895, stale_flag=False, released on exit |
| 3 | atomic_error_handling | PASS | valid output preserved=True; .error.json written=False (failure never overwrites last valid JSON) |
| 4 | dxf_render_and_reopen | PASS | reopened 9 entities; uniform scale x=0.032727 y=0.032727 aspect_ok=True |
| 5 | svg_generation_and_parse | PASS | parsed OK, 5 tag types |
| 6 | png_visible_geometry | PASS | 87 non-zero compressed bytes; 28x28 non-background block |
| 7 | mechanical_render_validation | PASS | 0 failing / 8 checks (finite coords, nonzero bounds, entity count, text collision, SVG parse) |
| 8 | visual_review_semantic | PASS | verdict=PARTIAL (3 questions, 2 yes / 1 no) |
| 9 | no_source_root_access | PASS | no home/project absolute paths written into generated outputs |

**ALL PASS**
Passed: 9/9
