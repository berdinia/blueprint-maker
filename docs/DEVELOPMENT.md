# Development

## Environment

```bash
python -m venv .venv && . .venv/bin/activate && pip install -e ".[dev]"
```

## Checks (same as CI)

```bash
ruff format --check src tests scripts
```

```bash
ruff check src tests scripts
```

```bash
mypy
```

```bash
python scripts/audit_network_usage.py src
```

```bash
python -m pytest -q
```

```bash
blueprint run-synthetic --report synthetic-report.md
```

```bash
python -m build
```

`pytest` treats `DeprecationWarning`s raised from `blueprint_pipeline` as
errors. Slow tests (multi-process locks, end-to-end) are marked `slow`:
`python -m pytest -m "not slow"` for a quick loop.

## Test layout

* `tests/unit/` — pure functions and single modules (no network, no subprocess).
* `tests/integration/` — real HTTP against the loopback fake server, real
  separate processes for locking, real files for the runner and stages, CLI.
* `tests/end_to_end/` — the full synthetic run with output verification, and
  a check that the committed fixture JSON matches the code.

The fake server (`blueprint_pipeline.testing.fake_server.FakeModelServer`)
binds to `127.0.0.1` only and can return content, empty bodies, HTTP errors,
truncated or malformed streams, `finish_reason: length`, server errors, or
sleep (for timeouts). Responses are keyed by the SHA-256 of the request image,
or taken from a FIFO queue.

## Creating synthetic fixtures

Rules: invent everything; never translate, scale, rotate or perturb anything
real; use `SYN-*` IDs and abstract labels; mark files as synthetic.

1. Edit `src/blueprint_pipeline/testing/synthetic.py`:
   `REGISTER`, `RELATIONSHIPS`, `DECISIONS`, canned `EXTRACTION_RESPONSES`
   (keyed by source ID) and `REVIEW_RESPONSES` (in order), and
   `generate_images()` for the evidence images (generated, never committed).
2. Add checks for the new behaviour in `testing/run.py::_verify`.
3. Regenerate the committed JSON copy:

```bash
blueprint run-synthetic --export-fixture fixtures/synthetic/project
```

4. Run `blueprint run-synthetic --keep /tmp/syn` and inspect the packet.

For a unit-level fixture, build canonical models with `tests/conftest.py`
helpers (`make_model`, `feature`, `rect`).

## Adding a stage or check

* Stages live in `orchestration/stages.py`; each returns a `StageResult` and
  writes outputs only through `ctx.store` (atomic, with metadata).
* Checks return `validation.checks.Check` items; use `warn_only=True` for
  advisory findings and `skip()` (never PASS) when a check cannot run.

## Schema changes

See [SCHEMAS.md](SCHEMAS.md#adding-a-schema-version).
