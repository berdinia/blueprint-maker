# Known issues

Generic software limitations. No private examples are included. The defects
listed in the previous version of this file (K1–K10) are addressed; see
`docs/ENGINEERING-ASSESSMENT.md` and `CHANGELOG.md`.

## Not verified in this environment

* **No real local model was exercised.** Every inference path was tested
  against the loopback fake server (both protocols, streaming and not, all
  failure modes). Real vision models may need prompt tuning, a larger
  `request_timeout_s`, or a smaller `max_image_px`.
* **macOS** is covered by the CI workflow (`macos-latest`); local runs here
  were Linux only. `fcntl.flock` semantics are the same on macOS.
* **Windows**: the lock falls back to exclusive file creation plus PID checks;
  untested, and a crash always needs `lock clear --stale`.

## Functional limitations

* **PDF/HEIC evidence** is not rasterised locally (would need an extra
  dependency); such sources are `UNSUPPORTED_MEDIA` and blocked. Convert them
  to PNG with a local tool and register the converted file.
* **Identity grouping** uses normalized labels only. Abbreviations, synonyms
  and misspellings are not merged automatically; use `MERGE_CANDIDATES`.
* **Photo-variant detection** (dHash ≤ 6/64) was tuned on synthetic images.
  Strong perspective or cropping defeats it (false negative: the photo counts
  as its own origin); a near-identical revision could match (mitigated: human
  relationships override heuristics, and same-origin measurement disagreement
  is reported as a conflict). Review `classify` findings.
* **Walls are centre lines**; thickness is not modelled (`thickness_mm: null`).
* **Opening placement** on a single room edge must fit inside one wall
  segment; an opening spanning a split point is reported as not applied.
* **Dimension/aspect agreement** is checked for four-vertex rooms only.
* **Labels** have no leader lines. A label that cannot fit inside its room is
  omitted from the plan (WARN) and listed in the schedule.
* **Side column** truncates with “… N more lines” when there are many unplaced
  items or conflicts; the full lists are in the packet's `attention.json`.
* **Text metrics** assume a monospace font (0.6 em). PNG text uses the first
  monospace font found (DejaVu Sans Mono, Menlo, Consolas, Courier New, …);
  with none installed, Pillow's default font is used and widths may differ.
* **PNG bytes** depend on the installed font and Pillow/FreeType versions; PNG
  output is bound to its SVG by hash rather than byte-identical across
  machines. SVG, PDF and DXF are byte-deterministic for a given library set.
* **Single page, single floor**: no multi-sheet or multi-level layouts yet.

## Process notes

* `blueprint resume` re-runs everything downstream of a changed input; with a
  slow local model, prefer `blueprint extract --source ID` for targeted work.
* The network audit is allowlist-based on module paths; review changes to
  `inference/client.py` by hand.
