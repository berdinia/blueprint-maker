# Pipeline

Run any stage with `blueprint --project <dir> <stage>`, or `blueprint resume`
to run every stage that is not current, in order. Each stage ends with a
status: **PASS**, **PARTIAL** (usable output with warnings or some sources
missing), **FAIL**, or **BLOCKED** (a prerequisite is missing or stale, the
configuration is incomplete, or the heavy-job lock is busy).

## Project layout

```
<project>/
  blueprint.yaml
  input/                      original evidence (read only)
  register/
    source-register.json      required: source IDs → relative paths (+ optional sha256)
    relationships.json        optional: declared source relationships
    decisions.json            optional: human placements, merges, revision acceptance
  work/                       all derived artifacts (+ .meta.json sidecars)
  results/human-review-packet/
```

## Stages

### 1. inventory
Compares the register with `input/`. Findings: `MISSING_FILE`,
`CHECKSUM_MISMATCH` (blocks: evidence must be immutable — register a new
source instead), `CHECKSUM_RECORDED`, `UNREGISTERED_FILE`,
`FILENAME_REGISTER_DISAGREEMENT` (filename mentions another source ID;
ignored for identity), `EXTENSION_MISMATCH`, `UNSUPPORTED_MEDIA`
(PDF/HEIC — convert locally first), `EXACT_DUPLICATE`.

### 2. normalize
Re-hashes each file against the inventory, then writes a PNG with EXIF
orientation applied, all metadata removed, transparency flattened and size
limited by `max_image_px` / `max_image_bytes`, plus a record with the input
checksum and a 64-bit dHash.

### 3. extract (heavy job)
For each usable source: validate configuration → skip if current (normalized
hash, prompt hash, config hash, model) → take the heavy-job lock → call the
local model → store `request.json`, `raw-response.bin`, `assembled.txt`,
`parsed.json`, `outcome.json` under `extraction/<id>/attempts/<attempt>/` →
enforce the register's source ID → schema → semantic checks. Only `SUCCESS`
replaces `current.json`; anything else writes `current.json.error.json`.

Raw text, the model's normalized reading, confidence and uncertainty are kept
separately; lengths are converted to mm only when the unit is explicit
(`3200` with no unit stays unconverted, with a reason).

### 4. classify
Relationships (declared + computed: identical checksum → `EXACT_FILE_DUPLICATE`,
dHash distance ≤ 6/64 → `SAME_SCAN_OR_PHOTO_VARIANT`), evidence origins,
families, revision groups, fragment class and placement state per source.
Pairs with no relationship are `UNKNOWN`.

Fragment classes: `WHOLE_FLOOR, ROOM, MULTI_ROOM_RELATIONSHIP,
CORRIDOR_OR_TRANSITION, OPENING_DETAIL, PARTIAL_WALL_OR_CORNER, TEXT_OR_NOTES,
UNKNOWN` (register-declared beats model-proposed).
Placement states: `ANCHORED, LIKELY, AMBIGUOUS, UNPLACED, DETAIL_ONLY`.

### 5. reconcile
Room-identity candidates across all sources by normalized label, with origin
counts (`CORROBORATED` needs ≥ 2 independent origins), `MEASUREMENT` and
`REVISION` conflicts, and unresolved questions. Unlabeled observations become
separate `UNRESOLVED` candidates.

### 6. build-model
Applies `decisions.json`:

| Decision | Effect |
|---|---|
| `PLACE_ROOM` | gives a candidate a polygon (mm, Y up) and placement state |
| `MERGE_CANDIDATES` | merges identity candidates the label grouping kept apart |
| `ACCEPT_REVISION` | marks current/superseded sources; resolves the revision conflict and measurement conflicts explained by it |
| `PLACE_OPENING` | puts a door/window on the wall shared by two rooms, or on a room edge, at an offset |
| `ADD_HYPOTHETICAL_ROOM` | explicit hypothesis, drawn dashed, excluded from provenance requirements |

Walls are derived from placed room edges (partially shared edges are split).
The model is checked before it is written; unsupported geometry, missing
provenance or inconsistent placement states reject it (`FAIL`).

Measurement convention: `width` = extent along model X, `depth` = along Y.

### 7. render
Deterministic `plan.svg`, `plan.png`, `plan.pdf` (page) and `plan.dxf` (model
space, 1:1 mm). Page regions: geometry (with dimension bands), side column
(notes, room schedule, unplaced items, open conflicts), title block.

### 8. validate
See [VALIDATION.md](VALIDATION.md).

### 9. review (heavy job)
Render → validate → review → (repair → render → validate → review) at most
`review.max_repair_cycles` (≤ 2) times. Blocked if mechanical validation
failed on model/geometry/DXF/PDF (facts cannot be repaired by presentation).
`--simulated` uses a deterministic, clearly labelled stand-in reviewer for
synthetic work; the packet never counts it as independent.

### 10. package
Assembles the human-review packet with a SHA-256 manifest and a README that
states whether the drawing is ready for sign-off and why not.
