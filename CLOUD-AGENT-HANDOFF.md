> **Historical document.** This is the handoff that accompanied the sanitized
> scaffold export (v0.1.0). The work it requested is complete; see
> `docs/ENGINEERING-ASSESSMENT.md`, `FINAL-VERIFICATION.md` and `KNOWN-ISSUES.md`.

# Cloud Agent Handoff

**You have been given only sanitized scaffolding.** There is no real
architectural evidence in this codebase.

## What to do

Your task is **software engineering and pipeline improvement**. Work on the
code, tests, schemas, and documentation. Generated tests must remain
synthetic. Refactoring is encouraged.

## What NOT to do

- Do **NOT** ask for actual house plans, blueprints, or real measurements.
  They are not available and will not be provided.
- Do **NOT** infer that the synthetic fixtures represent a real building.
  They do not. The identifiers `SYN-*`, `SYN-ROOM-*`, `SYN-OPEN-*` are
  intentionally non-descriptive.
- Do **NOT** attempt to reconstruct a real building from this codebase.
- Do **NOT** upload, upload, or connect this to any cloud OCR or vision service.
- Do **NOT** call a cloud model. All model endpoints are local.

## Synthetic test data

All fixtures in `fixtures/synthetic/` are clearly marked:

```
SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING.
```

These fixtures exercise:
- source inventory
- duplicate grouping (`SYN-FAM-B`)
- source-family relationships (`SYN-FAM-A`, annotated copy)
- fragment classification
- canonical-model creation
- deterministic rendering
- DXF generation
- mechanical validation
- visual-review report parsing (stubbed)
- atomic error handling

## Prioritized engineering tasks

1. **Stabilize process locking and job lifecycle** — ensure the lock
   handles all edge cases (stale detection, PID reclamation, cross-platform).
2. **Harden authoritative source-ID handling** — strengthen the pattern where
   the caller supplies `source_id` and the model's self-reported `source_id`
   is advisory only.
3. **Improve deterministic fit-to-page rendering** — refine the bounding-box
   transform to handle edge cases (single room, empty geometry, aspect ratio
   mismatches, very large page sizes).
4. **Strengthen mechanical validation** — add more checks (e.g., room overlap
   detection, text collision by extent, DXF entity type validation).
5. **Separate transport, parsing, semantic, and review verdicts** — ensure
   each stage has a distinct pass/fail that is not conflated.
   See `extraction.schema.json` for the four-verdict design.
6. **Improve tests and reproducibility** — add property-based tests for
   the rendering transform. Ensure tests run in a clean env.
7. **Simplify duplicated scripts and stale workflow paths** — remove
   ad-hoc root-level scripts that are not needed.
8. **Make endpoint configuration portable** — use environment variables for
   all endpoints, model names, and paths. No hardcoded values.

## Architecture

See `ARCHITECTURE.md` for the generic architecture.
See `KNOWN-ISSUES.md` for tracked defects.
