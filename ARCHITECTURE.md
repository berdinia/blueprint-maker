# Architecture

## Package layout

```
src/blueprint_pipeline/
  config/         typed, validated settings; endpoint privacy policy
  io/             atomic writes, hashing, artifact store with metadata sidecars
  inventory/      source register (identity authority) and disk inventory
  normalization/  metadata-free, size-limited copies + perceptual hash
  inference/      the single local inference client, parsing, outcome evaluation
  extraction/     prompt templates and the extraction runner
  evidence/       provenance, relationships, origins, fragments, identity, lengths
  canonical/      canonical-model builder, geometry helpers, migrations
  rendering/      layout/transform, scene, SVG/PNG/PDF/DXF writers
  validation/     model, geometry, layout and per-file checks; reports
  review/         visual-review interpretation, bounded repair, review packet
  orchestration/  context, logging, heavy-job lock, stages, status, resume
  schemas/        versioned JSON Schemas (package data)
  testing/        loopback fake model server, synthetic fixture and runner
  cli.py          the `blueprint` command
```

## Data flow and authority

| Stage | Reads | Writes (under `work/`) | Authority |
|---|---|---|---|
| inventory | register, `input/` | `inventory/inventory.json` | register |
| normalize | inventory, evidence | `normalized/<id>.png/.json` | input checksum |
| extract | normalized copy | `extraction/<id>/current.json`, `attempts/…` | register for identity; model for observations only |
| classify | inventory, normalized, extractions, `relationships.json` | `evidence/classification.json` | HUMAN > HEURISTIC > MODEL |
| reconcile | classification, extractions | `evidence/evidence.json` | code (deterministic) |
| build-model | evidence, extractions, `decisions.json`, register | `canonical/model.json` | human decisions for geometry |
| render | model, render settings, overrides | `render/plan.{svg,png,pdf,dxf}`, `scene.json` | canonical model |
| validate | model, renders | `validation/report.{json,md}` | code |
| review | PNG | `review/review.json`, `history/`, `repair-log.json` | presentation only |
| package | all of the above | `results/human-review-packet/` | — |

Each stage records `state/stages/<stage>.json` with input and output hashes.

## Key design decisions

1. **Identity comes from the register.** Model-echoed source IDs are recorded
   and compared, then overwritten before anything else reads the payload.
2. **Evidence origins, not files, are counted.** Exact duplicates, photo/scan
   variants, rescans and annotated copies collapse into one origin; independent
   redraws and supplementary details count separately; possible revisions are
   kept apart and flagged.
3. **Conflicts are retained.** Disagreeing measurements stay side by side as
   conflicts; nothing is averaged. Only a human decision (`ACCEPT_REVISION`)
   resolves them.
4. **Geometry only via decisions.** The builder never invents positions.
   Unplaced rooms/openings/fragments stay in the model with `polygon_mm: null`
   or `wall_id: null` and are listed, not drawn. Hypothetical geometry must be
   declared and is drawn dashed on the `UNCERTAIN` layer.
5. **One scene, four writers.** The page layout is computed once; SVG, PNG and
   PDF draw it in page space and DXF maps it back to model space at 1:1 mm, so
   the DXF is measurable and editable.
6. **One uniform transform.** `scale = min(avail_w / geom_w, avail_h / geom_h)`,
   centred, Y-up model to Y-down page with no mirroring; degenerate geometry
   is handled explicitly.
7. **Validation re-reads files.** Validators parse the SVG/DXF/PNG/PDF that were
   written and compare them with a freshly built scene; stale renders and
   reviews are detected by hash.
8. **Transport ≠ success.** Nine inference outcome categories; HTTP 200 with
   empty content is `EMPTY_RESPONSE`, parsed JSON that fails the schema is
   `SCHEMA_FAILED`, etc. Review verdicts are separate from transport.
9. **One heavy job at a time, across processes.** `fcntl.flock` lock with an
   owner record; crashes leave a record that requires operator review because
   the model server may still be busy.
10. **Freshness by hash, never mtime.** Artifact metadata stores input, config,
    model and schema hashes.
11. **Bounded repair.** At most two automated, presentation-only repair cycles;
    then a human.
12. **No ambient network.** The inference client uses `http.client` directly,
    so `HTTP(S)_PROXY` variables cannot reroute evidence; endpoints are
    re-validated before every connection; `scripts/audit_network_usage.py`
    fails on any new network code.
