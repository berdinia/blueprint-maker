# blueprint-pipeline

A local-only framework for reconstructing floor plans from heterogeneous
evidence (scans, photographs, sketches) into a reviewable, versioned canonical
model and deterministic CAD output.

> This repository ships **synthetic test data only**
> (`SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING`). Private evidence
> never belongs here; it lives in a separate local project directory.

```
source inventory → normalization → local extraction → structured observations
→ source-family analysis → fragment classification → room-identity resolution
→ canonical evidence model → deterministic SVG/DXF/PNG/PDF → mechanical validation
→ independent local visual review → bounded repair → human-review package
```

The canonical model — never a model response or a rendered image — is the
factual authority. All inference goes to endpoints you configure on your own
machine or private network; there is no cloud provider and no implicit
fallback.

## Install

Python 3.10–3.13 (tested on Linux; CI also runs macOS).

```bash
python -m venv .venv
```

```bash
. .venv/bin/activate
```

```bash
pip install -e ".[dev]"
```

## Run the synthetic end-to-end test

```bash
blueprint run-synthetic --keep /tmp/blueprint-synthetic
```

This builds a throw-away project, starts a loopback fake model server, runs
every stage through the real code paths and verifies the outputs (22 content
checks). Open `/tmp/blueprint-synthetic/results/human-review-packet/` to see
the drawing, the validation report and the review record.

```bash
python -m pytest -q
```

## Use it on a local project

```bash
blueprint --project ~/plans/project-x init
```

1. Put evidence files under `input/` (they are only ever read).
2. List them in `register/source-register.json` — the register, not filenames,
   decides source identity.
3. Set `inference.primary.url`, `models.extract` and `models.review` in
   `blueprint.yaml` (see `config/example.local.yaml`).
4. Check the configuration and probe the endpoint:

```bash
blueprint --project ~/plans/project-x config --probe
```

5. Run or resume:

```bash
blueprint --project ~/plans/project-x resume
```

```bash
blueprint --project ~/plans/project-x status
```

Geometry enters the model only through `register/decisions.json` (human
placements); see [docs/PIPELINE.md](docs/PIPELINE.md).

## Documentation

| Topic | File |
|---|---|
| Architecture and design decisions | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Stages, inputs and outputs | [docs/PIPELINE.md](docs/PIPELINE.md) |
| Configuration | [docs/CONFIGURATION.md](docs/CONFIGURATION.md) |
| Local models | [docs/LOCAL-MODELS.md](docs/LOCAL-MODELS.md) |
| Schemas and versioning | [docs/SCHEMAS.md](docs/SCHEMAS.md) |
| Validation | [docs/VALIDATION.md](docs/VALIDATION.md) |
| Operations (status, resume, locks, failures) | [docs/OPERATIONS.md](docs/OPERATIONS.md) |
| Privacy | [docs/PRIVACY.md](docs/PRIVACY.md) |
| Development and fixtures | [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) |
| Migrating from the scaffold | [docs/MIGRATION.md](docs/MIGRATION.md) |
| Assessment of the received scaffold | [docs/ENGINEERING-ASSESSMENT.md](docs/ENGINEERING-ASSESSMENT.md) |
| Verification results | [FINAL-VERIFICATION.md](FINAL-VERIFICATION.md) |
| Known issues | [KNOWN-ISSUES.md](KNOWN-ISSUES.md) |
