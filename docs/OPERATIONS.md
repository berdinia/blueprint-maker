# Operations

All commands take `--project DIR` (and optionally `--config FILE`, `--json`).
Exit codes: `0` PASS, `1` FAIL, `2` usage/configuration error, `3` BLOCKED,
`4` PARTIAL.

## What is the state of the project?

```bash
blueprint --project DIR status
```

State is computed from disk, never from logs or notifications:

* each stage's recorded status (`state/stages/<stage>.json`);
* `RECORDED` vs `STALE`: outputs are re-hashed and compared with the record,
  the configuration hash is compared, and staleness propagates downstream;
* the visual review is `CURRENT` only if its `reviewed_artifact_sha256`
  matches the PNG on disk;
* the heavy-job lock state and owner record.

Structured logs are appended to `work/logs/pipeline.jsonl` (one JSON object per
event; secret-looking keys redacted). They are for diagnosis, not state.

## Resuming interrupted work

```bash
blueprint --project DIR resume
```

Runs from the first stage that is not recorded-and-current, in order, and stops
at the first FAIL/BLOCKED (a failed visual review still produces the packet).
Inside `extract`, each source is skipped if its current extraction matches the
normalized input hash, prompt hash, configuration hash and model; only missing,
stale or failed sources are sent to the model. Re-running is idempotent.

Run a single stage, or a single source:

```bash
blueprint --project DIR extract --source SYN-SRC-004
```

```bash
blueprint --project DIR extract --source SYN-SRC-004 --force
```

## The heavy-job lock

```bash
blueprint --project DIR lock inspect
```

| State | Meaning | Action |
|---|---|---|
| `FREE` | no job, released cleanly | none |
| `HELD` | a live process holds it (PID, host, operation, model, source, start time shown) | wait, or set `lock.wait_s` |
| `STALE_REQUIRES_REVIEW` | the owner exited without releasing (crash, kill, power loss) | confirm the local model server is idle, then clear |
| `CORRUPT_RECORD` | unreadable owner record | inspect the file, then clear |

```bash
blueprint --project DIR lock clear --stale
```

`clear` refuses while a live process holds the lock, refuses if the recorded
PID is still alive on this host, and refuses a record from another host unless
you add `--allow-other-host`. It never deletes the lock file; it rewrites the
record (keeping the previous one under `previous`).

## Failed artifacts

* A failed extraction writes `work/extraction/<id>/current.json.error.json`
  (status, per-phase status, error, attempt directory). The previous valid
  `current.json` is untouched; `last_valid_preserved` says whether one exists.
* Every attempt keeps `request.json` (metadata; images by hash only),
  `raw-response.bin`, `assembled.txt`, `parsed.json` (if any) and
  `outcome.json` under `attempts/<attempt_id>/`.
* A rejected canonical model writes `canonical/model.json.error.json` with the
  failed checks.
* Errors are cleared automatically by the next successful write.

Interpreting extraction statuses: see the table in
[LOCAL-MODELS.md](LOCAL-MODELS.md). `TRANSPORT_FAILED` with
`not_retried_work_may_be_running` means the request timed out after it was
sent — check the model server before forcing a re-run.

## Validating outputs

```bash
blueprint --project DIR validate
```

Then read `work/validation/report.md`. The packet README in
`results/human-review-packet/` lists blocking items, open conflicts,
unresolved questions and unplaced items; `MANIFEST.sha256` verifies the packet:

```bash
cd DIR/results/human-review-packet && shasum -a 256 -c MANIFEST.sha256
```

## Reviewing the drawing

* Fix facts in `register/decisions.json` (placements, merges, revision
  acceptance) and re-run `resume`; never edit `work/canonical/model.json` by hand
  (the hash check will report it as changed).
* Presentation-only repairs from the bounded repair loop are stored in
  `work/review/overrides.json`; delete it to return to default presentation.
