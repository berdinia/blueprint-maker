# Blueprint Reconstruction Scaffold

**Cloud-safe software scaffolding for a local-first floor-plan reconstruction
pipeline.**

This is the *engineering system* and *synthetic test project* — not a house
plan, not real measurements, and not real building data.

> **SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING.**
> All fixtures in `fixtures/synthetic/` are invented for pipeline testing.
> No original scans, photographs, blueprints, or derived architectural data is
> included in this export.

## What this system does

The pipeline ingests floor-plan images via a local vision model, extracts
structured facts, reconciles multiple sources into a coherent model, and
renders deterministic CAD output (DXF/SVG/PNG). The final step is an
independent local visual review followed by human sign-off.

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

## Included components

| Component | Description |
|---|---|
| `pipeline/` | Python packages: config, process-lock, source-registry, extraction runner, renderer, validator |
| `schemas/` | JSON schemas for extraction output and house model canvas |
| `fixtures/synthetic/` | Synthetic test fixtures (independent, invented) |
| `tests/` | End-to-end and unit tests (synthetic only) |
| `scripts/` | Utility scripts: `generate_synthetic_dxf.py`, `generate_synthetic_svg.py`, `check_local_only.py` |
| `config/example.env` | Example configuration file — copy and adjust |

## Installation

```bash
pip install -e .
# or:
pip install ezdxf Pillow numpy
```

## Local test commands

```bash
# Run all tests
python tests/test_pipeline.py

# Or with pytest
python -m pytest tests/test_pipeline.py -v
```

## Expected outputs

- `schematic.dxf` — deterministic DXF output
- `schematic.svg` — SVG output (generated separately)
- `render_report.json` — machine-readable validation report
- `*.error.json` — on failure, the last valid result is preserved

## Project status

**In active development.** The pipeline is functional end-to-end using the
synthetic fixture. See `KNOWN-ISSUES.md` for tracked defects.

## Known software problems

See `KNOWN-ISSUES.md`. Do NOT include real examples in fixes.

## Privacy design

This export was built from a strict allowlist. No private evidence, derived
data, or real measurements are included. See `PRIVACY-STATEMENT.md` and
`CLOUD-AGENT-HANDOFF.md` for full details.

## Endpoint configuration

All model endpoints are configurable via environment variables. No endpoint is
embedded in the code. See `config/example.env`:

```bash
export BLUEPRINT_NATIVE_ENDPOINT=http://127.0.0.1:11434
export BLUEPRINT_PROXY_ENDPOINT=http://127.0.0.1:11435
export BLUEPRINT_EXTRACT_MODEL=qwen3.8:27b
export BLUEPRINT_REVIEW_MODEL=gemma4:31b
```

Do NOT fall back to a hosted/cloud endpoint if a local probe times out.
All model access is local.
