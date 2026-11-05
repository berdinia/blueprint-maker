# Changelog

## 0.2.0 — framework hardening

### Fixed (defects confirmed in the received scaffold)
- Lock: blocked jobs deleted the holder's lock; check-then-create race allowed
  two holders; stale locks never reclaimed (D-L1–D-L4).
- Provenance: a model-echoed source ID could become authoritative; duplicate
  and missing register entries were silent (D-P1–D-P4).
- Extraction: every result was written as an error (`NameError`); HTTP 200 with
  empty content counted as success; proxy environment variables could reroute
  requests (D-E1–D-E7).
- Rendering: vertically mirrored output, phantom walls, crash on empty window
  list, rooms without coordinates collapsed to the origin (D-R1–D-R6).
- Validation: PNG validator always failed on real images; missing artifacts
  counted as PASS (D-V1–D-V4).
- Configuration: explicit directories gained an extra `/input`; example env
  names were never read (D-C1–D-C3).
- Tests: several end-to-end checks were vacuous (D-T1–D-T5).

### Added
- `src/blueprint_pipeline` package and the `blueprint` CLI (inventory,
  normalize, extract, classify, reconcile, build-model, render, validate,
  review, package, status, resume, lock, config, migrate-model, init,
  run-synthetic).
- Typed YAML/env configuration with local-only privacy policy.
- Atomic artifact store with hash-based freshness and separate error artifacts.
- Cross-process heavy-job lock with stale-requires-review semantics.
- Local inference client (OpenAI-compatible + Ollama, streaming, raw capture,
  nine outcome categories, safe retry/fallback rules).
- Source relationships, evidence origins, families, fragments, identity
  candidates, conflict retention; human decisions file.
- Versioned JSON Schemas (draft 2020-12) and legacy model migration.
- Deterministic SVG/PNG/PDF/DXF from one scene; DXF 1:1 mm in model space.
- Mechanical validation that re-parses every artifact; JSON + Markdown reports.
- Visual review records bound to PNG hashes, bounded repair, review packet.
- Loopback fake model server, synthetic fixture, end-to-end verification.
- Network-usage audit script; CI on Linux and macOS.

### Removed
- Legacy `pipeline` package (import now fails with a migration message),
  `generate_synthetic_dxf.py`, `generate_synthetic_svg.py`, unused draft-07
  schemas, stale `manifest.sha256`, auto-overwritten `TEST-REPORT.md`,
  `config/example.env`.

## 0.1.0 — sanitized scaffold (as received)
