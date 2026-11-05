# Privacy Statement

## Scope

This export contains **no original sources** and **no data derived from them**.

## Guarantees

| Category | Status |
|---|---|
| Original source images (PNG, PDF, scans, photographs) | **NOT INCLUDED** |
| Converted or resized images | **NOT INCLUDED** |
| Real room measurements (widths, depths, areas) | **NOT INCLUDED** |
| Real topology, adjacency, or opening positions | **NOT INCLUDED** |
| Real room names | **NOT INCLUDED** |
| Real source filenames | **NOT INCLUDED** |
| Real source IDs | **NOT INCLUDED** (synthetic IDs: `SYN-001`, `SYN-002`, etc.) |
| Real source hashes | **NOT INCLUDED** |
| Real addresses or geographical references | **NOT INCLUDED** |
| Extracted OCR output | **NOT INCLUDED** |
| Raw or parsed vision-model output | **NOT INCLUDED** |
| Canonical house models (real) | **NOT INCLUDED** (only synthetic model present) |
| Renders, DXF/SVG/PDF/PNG outputs (real) | **NOT INCLUDED** |
| Review responses and logs (real) | **NOT INCLUDED** |
| Local usernames and home-directory paths | **NOT INCLUDED** |
| Backup, previous-version, and stale files | **NOT INCLUDED** |
| Git history | **NOT INCLUDED** |
| Hermes session files | **NOT INCLUDED** |
| Databases | **NOT INCLUDED** |
| Caches | **NOT INCLUDED** |

## Synthetic fixtures

The files in `fixtures/synthetic/` are:

- **Invented from scratch.** They are not a translated, scaled, rotated, or
  perturbed version of any real building.
- **Clearly marked** as synthetic.
- **Not based on** any real room name, measurement, topology, adjacency
  relationship, or opening position.

## Local validation

The export passed local privacy validation:

- [x] No private filenames
- [x] No private paths
- [x] No real room labels
- [x] No real measurements or areas
- [x] No OCR/model excerpts
- [x] No EXIF/PDF metadata
- [x] No Git/history
- [x] No databases or archives
- [x] No symlinks resolving outside the export

Unresolved findings: 0

## Human review

Local validation cannot replace final human review. Before uploading this
export to any cloud service, a human should:

1. Open `README.md` — confirm the pipeline description is generic.
2. Open `CLOUD-AGENT-HANDOFF.md` — confirm the handoff instructions.
3. Open `fixtures/synthetic/house-model.json` — confirm the geometry is
   obviously synthetic.
4. Open `SANITIZATION-ATTESTATION.md` — confirm all PASS.
5. Inspect the archive file listing one final time.

## Validation cannot replace final human review

The local privacy scans use a sensitive-term dictionary built from the private
source register. Exact numeric matches can produce false positives. Every
numeric hit must be classified locally as `PRIVATE_MATCH`,
`GENERIC_SOFTWARE_CONSTANT`, `SYNTHETIC_FIXTURE_VALUE`, or `REVIEW_REQUIRED`.

This export has zero `PRIVATE_MATCH` and zero `REVIEW_REQUIRED` items.
