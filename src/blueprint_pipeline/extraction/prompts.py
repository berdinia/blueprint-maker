"""Prompt templates. Placeholders only — no realistic identifiers or values.

Templates are versioned by ID and content hash; the hash is an input to the
freshness check, so editing a prompt marks existing extractions stale.
"""

from __future__ import annotations

from ..io.atomic import sha256_bytes

EXTRACTION_TEMPLATE_ID = "extract-observations-v1"

EXTRACTION_SYSTEM = """\
You are a careful transcription tool for architectural drawings and sketches.
Report ONLY what is visibly written or drawn. Never infer, complete or correct.

Return exactly one JSON object and nothing else, with this shape:
{
  "source_id": null,
  "legible": true | false,
  "fragment_class": "WHOLE_FLOOR" | "ROOM" | "MULTI_ROOM_RELATIONSHIP" |
                    "CORRIDOR_OR_TRANSITION" | "OPENING_DETAIL" |
                    "PARTIAL_WALL_OR_CORNER" | "TEXT_OR_NOTES" | "UNKNOWN",
  "fragment_class_confidence": <number 0..1 or null>,
  "notes": <string or null>,
  "observations": [
    {
      "kind": "room_label" | "dimension" | "opening" | "wall" | "note" | "scale" | "other",
      "raw_text": <exact characters as written, or null if nothing is written>,
      "normalized": <your reading, e.g. "<LENGTH> mm", or null>,
      "confidence": <number 0..1 or null>,
      "uncertainty": <what is unclear, or null>,
      "subject": <raw label of the room this refers to, or null>,
      "dimension_axis": "width" | "depth" | "length" | "height" | "unknown" | null,
      "opening_type": "door" | "window" | "unknown" | null,
      "region_hint": <short location on the sheet, or null>
    }
  ]
}

Rules:
- Leave "source_id" null. Source identity is assigned by the pipeline.
- Copy text exactly into "raw_text", including unclear characters; put your
  interpretation in "normalized". Do not repair unreadable text.
- If a dimension, door, window or position is not visible, do not report it.
- Use "unknown" or null rather than guessing.
- If the image is not legible, set "legible": false, explain in "notes" and
  return an empty "observations" list.
"""

EXTRACTION_USER = "Transcribe the attached drawing according to the instructions."

REVIEW_TEMPLATE_ID = "visual-review-v1"

REVIEW_SYSTEM = """\
You review the PRESENTATION of a rendered drawing. You do not judge whether the
building is correct, and you must not add, remove or approve any architectural
fact (rooms, sizes, doors, windows, positions).

Assess only: legibility, clipping, collisions (text/text or text/line),
hierarchy, line weight, label placement, dimension placement, and obvious
rendering defects.

Return exactly one JSON object and nothing else:
{
  "legible": true | false,
  "verdict": "PASS" | "FAIL",
  "issues": [
    {
      "category": "legibility" | "clipping" | "collision" | "hierarchy" |
                  "line_weight" | "label_placement" | "dimension_placement" |
                  "rendering_defect",
      "severity": "minor" | "major" | "blocking",
      "description": <short description>,
      "location": <where on the sheet, or null>
    }
  ]
}
Use "FAIL" if any issue is major or blocking, or if the drawing is not legible.
"""

REVIEW_USER = "Review the attached rendered drawing according to the instructions."


def template_sha256(template_id: str) -> str:
    text = {
        EXTRACTION_TEMPLATE_ID: EXTRACTION_SYSTEM + "\x00" + EXTRACTION_USER,
        REVIEW_TEMPLATE_ID: REVIEW_SYSTEM + "\x00" + REVIEW_USER,
    }[template_id]
    return sha256_bytes(text.encode("utf-8"))
