# Validation

Mechanical validation always runs before visual review. Results go to
`work/validation/report.json` (schema `validation-report`) and
`work/validation/report.md`.

Each check is `PASS`, `WARN`, `FAIL` or `SKIP`. Overall status: `FAIL` if any
required check failed; `PARTIAL` if any required check was skipped or any
check warned; otherwise `PASS`. **SKIP never counts as PASS.**

## Checks

| Category | Checks |
|---|---|
| canonical_model | schema, units mm, unique IDs, finite coordinates, nonzero wall lengths, provenance coverage, sources registered, origins known, unsupported features, placement-state consistency, conflict references, open conflicts attached |
| geometry | polygon validity (≥ 3 vertices, nonzero area, simple), bounds, room overlap (intersection area), wall crossings, opening attachment, adjacency vs shared walls, dimension-to-model agreement (FAIL if unflagged, WARN if a conflict is recorded), measured aspect ratio, duplicate dimensions |
| layout | transform present, uniform scale per room, aspect ratio, geometry inside viewport, fit-to-page (limiting axis ≥ 98 % filled), collapsed / tiny rooms, every wall drawn, text clipping per region, text–text and text–wall collisions, title-block and side-column separation, page-edge clipping, labels placed, each dimension once |
| svg | XML parse, viewBox = page, scene binding (stale render detection), per-layer element counts, points inside viewBox, text inside sheet, text collisions recomputed from the file, uniform scale of room polygons in the file |
| dxf | reopen, `$INSUNITS = 4` (mm), required layers, per-layer entity counts, finite coordinates, walls/rooms inside model bounds, room polygons at exact model coordinates |
| png | expected pixel size, hash binding to the current SVG and scene, ink near the geometry, ink bounding box matches the expected geometry box, spatial distribution (every grid cell crossed by a wall contains ink), minimum visible geometry size |
| pdf | header, xref offset, object offsets, MediaBox = page, one page, text-run count |

The PNG check deliberately does **not** use a page-wide white/non-white
percentage: it crops to the expected geometry box (plus a tolerance smaller
than the dimension-line gap) so that annotation cannot mask missing geometry.

## Running

```bash
blueprint --project <dir> validate
```

```bash
cat <dir>/work/validation/report.md
```

## Visual review semantics

Stored in `work/review/review.json` (schema `review-record`):

```json
{"transport_status": "SUCCESS", "parse_status": "SUCCESS", "schema_status": "SUCCESS",
 "review_verdict": "FAIL", "legible": false,
 "reviewed_artifact": "render/plan.png", "reviewed_artifact_sha256": "…", "issues": []}
```

* HTTP 200 or parsed JSON is not review success.
* `legible: false` → `FAIL`; any major/blocking in-scope issue → `FAIL`.
* Issues outside legibility, clipping, collision, hierarchy, line weight,
  label placement, dimension placement and rendering defects are recorded in
  `out_of_scope_items` and ignored — the reviewer may not judge facts.
* `blueprint status` reports a review `STALE` when the PNG hash differs.
