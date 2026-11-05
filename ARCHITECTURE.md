# Architecture

## Generic pipeline

```
source inventory
  → normalization
  → local extraction
  → source-family analysis
  → fragment classification
  → canonical model
  → deterministic CAD generation
  → mechanical validation
  → independent visual review
  → human review
```

## Modules

### `pipeline.config`
Loads configuration from environment variables. No default paths.
Endpoints are configurable. Render parameters are configurable.

### `pipeline.process_lock`
Advisory file-based process lock. Prevents concurrent extraction/review
jobs. Detects stale locks and reclaims them safely.

### `pipeline.source_registry`
Canonical list of source documents. Source identity is authoritative from
the registry. Model self-report is advisory only.

### `pipeline.extraction_runner`
Calls the local vision model, parses JSON, resolves source ID from registry,
writes result atomically. On failure, writes `*.error.json` and preserves
the last valid result. No silent cloud fallback.

### `pipeline.renderer`
Deterministic fit-to-page DXF rendering. Bounding-box transform with one
uniform scale for X and Y. Null room names → "UNNAMED". Openings without
positions → recorded, not placed. Uncertainty on a separate layer.

### `pipeline.validate_render`
Mechanical checks: finite coords, nonzero geometry, entity count, inside
drawable page, uniform scale, min entity size, clipping, overlap, text
collision, DXF reopening, SVG/XML validity, page occupancy, pixel
distribution, hash matching.

## Schemas

### `schemas/extraction.schema.json`
Schema for a single extraction result. Distinct verdicts:
- `transport` — success of the local model endpoint
- `json_parse` — success of JSON parsing
- `semantic_status` — success of semantic validation
- `source_id_advisory`/`source_id_mismatch` — audit only, never identity

### `schemas/house-model.schema.json`
Schematic layout schema. All coordinate systems are provisional. Rooms have
`room_id`, null-safe `name`, optional `linear_dims_mm`. Openings with
`position_on_wall=not_known` are records, not placed geometry.

## Key design decisions

1. **Source ID is authoritative from the registry or caller.** The model
   NEVER determines it. Model self-report is audit only.

2. **Atomic writes.** Every result is written to a tmp file, then renamed.
   Failed results never overwrite a valid JSON.

3. **Uniform scale rendering.** One scale for X and Y, preserving aspect
   ratio. Geometry cannot be clipped.

4. **No cloud fallback.** If a local proxy probe times out, the job is
   blocked cleanly — not silently redirected to a cloud endpoint.

5. **Synthetic fixtures.** All test data is invented from scratch and
   clearly marked. No real measurements, topology, or identifiers.
