"""Synthetic project: fixture definition, image generation and end-to-end run.

SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING.

The fixture is defined here as the single source of truth; the JSON copies in
``fixtures/synthetic/project/`` are produced by
``blueprint run-synthetic --export-fixture`` and a test keeps them in sync.
Images are generated deterministically, so no binary evidence is committed.

The fixture deliberately exercises:

* exact duplicate file (SYN-SRC-002 ≡ SYN-SRC-001) with a misleading filename
* photo variant of the same sheet (SYN-SRC-003, perceptual-hash match) whose
  model response reports a wrong source ID
* independently redrawn room sketch that corroborates BRAVO (SYN-SRC-004)
* possible revision of CHARLIE with a conflicting depth and a new room DELTA
  that has no placement (SYN-SRC-005)
* opening detail that stays DETAIL_ONLY (SYN-SRC-006)
* HTTP 200 with empty content (SYN-SRC-007)
* registered but missing file (SYN-SRC-008) and an unregistered file
* a hypothetical room, a placed door, an unplaced window
* a first visual review that returns ``legible: false`` plus an out-of-scope
  "fact" comment, followed by one bounded repair cycle
"""

from __future__ import annotations

import io
import json
import shutil
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageEnhance, ImageFont

from ..io.atomic import canonical_json

MARK = "SYNTHETIC TEST DATA - NOT A REAL BUILDING"

REGISTER: dict[str, Any] = {
    "schema": "blueprint.source-register",
    "schema_version": "1.0.0",
    "synthetic": True,
    "sources": [
        {
            "source_id": "SYN-SRC-001",
            "path": "plans/syn-sheet-a.png",
            "sha256": None,
            "notes": "Synthetic whole-floor sheet.",
        },
        {
            "source_id": "SYN-SRC-002",
            "path": "plans/copy-of-syn-src-001.png",
            "sha256": None,
            "notes": "Byte-identical copy; filename mentions another source ID.",
        },
        {
            "source_id": "SYN-SRC-003",
            "path": "photos/syn-photo-a.jpg",
            "sha256": None,
            "notes": "Photograph-like variant of sheet A.",
        },
        {
            "source_id": "SYN-SRC-004",
            "path": "sketches/syn-sketch-bravo.png",
            "sha256": None,
            "declared_fragment_class": "ROOM",
            "notes": "Independent redraw of BRAVO.",
        },
        {
            "source_id": "SYN-SRC-005",
            "path": "sketches/syn-sketch-charlie-rev.png",
            "sha256": None,
            "notes": "Possible revision of CHARLIE.",
        },
        {
            "source_id": "SYN-SRC-006",
            "path": "details/syn-door-detail.png",
            "sha256": None,
            "declared_fragment_class": "OPENING_DETAIL",
            "notes": "Opening detail.",
        },
        {
            "source_id": "SYN-SRC-007",
            "path": "notes/syn-notes.png",
            "sha256": None,
            "notes": "Model returns empty content for this source.",
        },
        {
            "source_id": "SYN-SRC-008",
            "path": "missing/syn-not-present.png",
            "sha256": None,
            "notes": "Registered but absent.",
        },
    ],
}

RELATIONSHIPS: dict[str, Any] = {
    "schema": "blueprint.relationships",
    "schema_version": "1.0.0",
    "synthetic": True,
    "relationships": [
        {
            "a": "SYN-SRC-001",
            "b": "SYN-SRC-004",
            "kind": "REDRAWN_SAME_ROOM",
            "asserted_by": "HUMAN",
            "confidence": 0.9,
            "subject": "BRAVO",
            "note": "independent redraw",
        },
        {
            "a": "SYN-SRC-001",
            "b": "SYN-SRC-005",
            "kind": "POSSIBLE_REVISION",
            "asserted_by": "HUMAN",
            "confidence": 0.6,
            "subject": "CHARLIE",
            "note": "depth differs",
        },
        {
            "a": "SYN-SRC-001",
            "b": "SYN-SRC-006",
            "kind": "SUPPLEMENTARY_DETAIL",
            "asserted_by": "HUMAN",
            "confidence": 0.7,
            "subject": None,
            "note": "door detail",
        },
    ],
}

ROOMS_MM = {
    "ALPHA": [[0, 0], [4000, 0], [4000, 3000], [0, 3000]],
    "BRAVO": [[4000, 0], [7000, 0], [7000, 3000], [4000, 3000]],
    "CHARLIE": [[0, 3000], [5000, 3000], [5000, 5500], [0, 5500]],
}

DECISIONS: dict[str, Any] = {
    "schema": "blueprint.decisions",
    "schema_version": "1.0.0",
    "synthetic": True,
    "decisions": [
        {
            "decision_id": "DEC-001",
            "type": "PLACE_ROOM",
            "candidate_id": "ID-ALPHA",
            "polygon_mm": ROOMS_MM["ALPHA"],
            "placement_state": "ANCHORED",
            "confirmed_by": "synthetic-fixture",
            "rationale": "dimensions from SYN-SRC-001",
        },
        {
            "decision_id": "DEC-002",
            "type": "PLACE_ROOM",
            "candidate_id": "ID-BRAVO",
            "polygon_mm": ROOMS_MM["BRAVO"],
            "placement_state": "ANCHORED",
            "confirmed_by": "synthetic-fixture",
            "rationale": "corroborated by SYN-SRC-004",
        },
        {
            "decision_id": "DEC-003",
            "type": "PLACE_ROOM",
            "candidate_id": "ID-CHARLIE",
            "polygon_mm": ROOMS_MM["CHARLIE"],
            "placement_state": "LIKELY",
            "confirmed_by": "synthetic-fixture",
            "rationale": "placed from SYN-SRC-001; revision conflict left open on purpose",
        },
        {
            "decision_id": "DEC-004",
            "type": "PLACE_OPENING",
            "opening_id": "OPEN-DOOR-1",
            "kind": "door",
            "room_candidates": ["ID-ALPHA", "ID-BRAVO"],
            "offset_mm": 1000,
            "width_mm": 900,
            "observation_ids": ["SYN-SRC-001/OBS-0009"],
            "placement_state": "ANCHORED",
            "confirmed_by": "synthetic-fixture",
            "rationale": "door symbol on shared wall",
        },
        {
            "decision_id": "DEC-005",
            "type": "ADD_HYPOTHETICAL_ROOM",
            "room_id": "ROOM-HYP-ANNEX",
            "display_name": "ANNEX?",
            "polygon_mm": [[7000, 0], [8500, 0], [8500, 1500], [7000, 1500]],
            "rationale": "synthetic hypothesis to exercise the uncertainty layer",
        },
    ],
}


def _obs(kind: str, raw: str | None, **kw: Any) -> dict[str, Any]:
    d = {
        "kind": kind,
        "raw_text": raw,
        "normalized": kw.pop("normalized", raw),
        "confidence": kw.pop("confidence", 0.9),
        "uncertainty": None,
        "subject": None,
        "dimension_axis": None,
        "opening_type": None,
        "region_hint": None,
    }
    d.update(kw)
    return d


SHEET_A = {
    "source_id": None,
    "legible": True,
    "fragment_class": "WHOLE_FLOOR",
    "fragment_class_confidence": 0.85,
    "notes": None,
    "observations": [
        _obs("room_label", "ALPHA"),
        _obs("room_label", "BRAVO"),
        _obs("room_label", "CHARLIE"),
        _obs("dimension", "4000 mm", subject="ALPHA", dimension_axis="width"),
        _obs("dimension", "3000 mm", subject="ALPHA", dimension_axis="depth"),
        _obs("dimension", "3000 mm", subject="BRAVO", dimension_axis="width"),
        _obs("dimension", "3000 mm", subject="BRAVO", dimension_axis="depth"),
        _obs("dimension", "5000 mm", subject="CHARLIE", dimension_axis="width"),
        _obs(
            "opening",
            "D1",
            opening_type="door",
            subject="ALPHA",
            region_hint="between ALPHA and BRAVO",
        ),
        _obs(
            "opening",
            "W1",
            opening_type="window",
            subject="ALPHA",
            uncertainty="position not drawn",
        ),
        _obs("dimension", "2500 mm", subject="CHARLIE", dimension_axis="depth"),
        _obs("note", "SYNTHETIC TEST DATA", confidence=0.99),
    ],
}

PHOTO_A = json.loads(json.dumps(SHEET_A))
PHOTO_A["source_id"] = "SYN-SRC-999"  # model echoes a wrong ID → must be overridden
PHOTO_A["observations"][5]["raw_text"] = "3008 mm"  # OCR jitter within tolerance
PHOTO_A["observations"][5]["normalized"] = "3008 mm"

SKETCH_BRAVO = {
    "source_id": "SYN-SRC-004",
    "legible": True,
    "fragment_class": "ROOM",
    "fragment_class_confidence": 0.8,
    "notes": None,
    "observations": [
        _obs("room_label", "Bravo"),
        _obs("dimension", "3.0 m", normalized="3.0 m", subject="BRAVO", dimension_axis="width"),
        _obs(
            "dimension",
            "3000",
            normalized=None,
            subject="BRAVO",
            dimension_axis="depth",
            uncertainty="unit not written",
        ),
    ],
}

SKETCH_CHARLIE_REV = {
    "source_id": None,
    "legible": True,
    "fragment_class": "MULTI_ROOM_RELATIONSHIP",
    "fragment_class_confidence": 0.6,
    "notes": None,
    "observations": [
        _obs("room_label", "CHARLIE"),
        _obs("room_label", "DELTA"),
        _obs("dimension", "2800 mm", subject="CHARLIE", dimension_axis="depth"),
        _obs("dimension", "2.2 m", subject="DELTA", dimension_axis="width"),
    ],
}

DOOR_DETAIL = {
    "source_id": None,
    "legible": True,
    "fragment_class": "OPENING_DETAIL",
    "fragment_class_confidence": 0.9,
    "notes": None,
    "observations": [
        _obs("opening", "900 mm", opening_type="door", dimension_axis="width"),
    ],
}

EXTRACTION_RESPONSES: dict[str, dict[str, Any]] = {
    "SYN-SRC-001": {"mode": "content", "content": json.dumps(SHEET_A)},
    "SYN-SRC-003": {"mode": "content", "content": "```json\n" + json.dumps(PHOTO_A) + "\n```"},
    "SYN-SRC-004": {"mode": "content", "content": json.dumps(SKETCH_BRAVO)},
    "SYN-SRC-005": {
        "mode": "content",
        "content": "<think>checking</think>" + json.dumps(SKETCH_CHARLIE_REV),
    },
    "SYN-SRC-006": {"mode": "content", "content": json.dumps(DOOR_DETAIL)},
    "SYN-SRC-007": {"mode": "empty"},
}

REVIEW_RESPONSES: list[dict[str, Any]] = [
    {
        "mode": "content",
        "content": json.dumps(
            {
                "legible": False,
                "verdict": "PASS",
                "issues": [
                    {
                        "category": "legibility",
                        "severity": "major",
                        "description": "room labels too small at this scale",
                        "location": "plan",
                    },
                    {
                        "category": "room_size",
                        "severity": "major",
                        "description": "ALPHA should be larger",
                        "location": None,
                    },
                ],
            }
        ),
    },
    {"mode": "content", "content": json.dumps({"legible": True, "verdict": "PASS", "issues": []})},
]


# ---------------------------------------------------------------------------- images
def _font(size: int) -> Any:
    from ..rendering.writers import _font_path

    fp = _font_path()
    return ImageFont.truetype(fp, size) if fp else ImageFont.load_default(size)


def _sheet(
    rooms: dict[str, list[list[int]]], title: str, size: tuple[int, int] = (1200, 900)
) -> Image.Image:
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    xs = [p[0] for poly in rooms.values() for p in poly]
    ys = [p[1] for poly in rooms.values() for p in poly]
    s = min((size[0] - 200) / (max(xs) - min(xs)), (size[1] - 260) / (max(ys) - min(ys)))
    for name, poly in rooms.items():
        pts = [(100 + (x - min(xs)) * s, 160 + (max(ys) - y) * s) for x, y in poly]
        d.polygon(pts, outline="black", width=6)
        cx = sum(p[0] for p in pts) / len(pts)
        cy = sum(p[1] for p in pts) / len(pts)
        d.text((cx, cy), name, fill="black", font=_font(36), anchor="mm")
    d.text((20, 20), MARK, fill="black", font=_font(28))
    d.text((20, 70), title, fill="black", font=_font(28))
    return img


def generate_images(input_dir: Path) -> dict[str, Path]:
    """Write deterministic synthetic evidence images. Returns path per source."""
    out: dict[str, Path] = {}

    def save(rel: str, img: Image.Image, fmt: str = "PNG", **kw: Any) -> Path:
        p = input_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        buf = io.BytesIO()
        img.save(buf, format=fmt, **kw)
        p.write_bytes(buf.getvalue())
        return p

    sheet_a = _sheet(ROOMS_MM, "SHEET A")
    out["SYN-SRC-001"] = save("plans/syn-sheet-a.png", sheet_a)
    out["SYN-SRC-002"] = input_dir / "plans/copy-of-syn-src-001.png"
    shutil.copyfile(out["SYN-SRC-001"], out["SYN-SRC-002"])
    photo = ImageEnhance.Brightness(sheet_a).enhance(0.93)
    out["SYN-SRC-003"] = save("photos/syn-photo-a.jpg", photo, "JPEG", quality=82)
    out["SYN-SRC-004"] = save(
        "sketches/syn-sketch-bravo.png",
        _sheet({"BRAVO": [[0, 0], [3000, 0], [3000, 3000], [0, 3000]]}, "SKETCH B", (800, 800)),
    )
    out["SYN-SRC-005"] = save(
        "sketches/syn-sketch-charlie-rev.png",
        _sheet(
            {
                "CHARLIE": [[0, 0], [5000, 0], [5000, 2800], [0, 2800]],
                "DELTA": [[5000, 0], [7200, 0], [7200, 2800], [5000, 2800]],
            },
            "SKETCH C REV?",
            (1000, 700),
        ),
    )
    det = Image.new("RGB", (600, 600), "white")
    d = ImageDraw.Draw(det)
    d.line([(100, 400), (500, 400)], fill="black", width=8)
    d.arc([(100, 0), (500, 400)], 180, 270, fill="black", width=4)
    d.text((20, 20), MARK, fill="black", font=_font(18))
    d.text((200, 450), "900 mm", fill="black", font=_font(30))
    out["SYN-SRC-006"] = save("details/syn-door-detail.png", det)
    notes = Image.new("RGB", (700, 500), "white")
    d = ImageDraw.Draw(notes)
    d.text((20, 20), MARK, fill="black", font=_font(20))
    for i in range(6):
        d.line([(40, 100 + i * 50), (640, 100 + i * 50)], fill=(120, 120, 120), width=3)
    out["SYN-SRC-007"] = save("notes/syn-notes.png", notes)
    save("inbox/unregistered-scan.png", Image.new("RGB", (64, 64), "white"))
    return out


def write_fixture_files(dest: Path) -> list[Path]:
    """Write the JSON fixture files (register, relationships, decisions, responses)."""
    files = {
        "register/source-register.json": REGISTER,
        "register/relationships.json": RELATIONSHIPS,
        "register/decisions.json": DECISIONS,
        "model-responses.json": {
            "synthetic": True,
            "note": "Canned local-model responses used by the loopback fake server.",
            "extraction": EXTRACTION_RESPONSES,
            "review": REVIEW_RESPONSES,
        },
    }
    written = []
    for rel, obj in files.items():
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(canonical_json(obj), encoding="utf-8")
        written.append(p)
    return written


def write_project_config(project: Path, endpoint_url: str) -> Path:
    cfg = {
        "schema_version": 1,
        "paths": {
            "project_root": ".",
            "input_dir": "input",
            "work_dir": "work",
            "results_dir": "results",
        },
        "privacy": {"mode": "loopback"},
        "inference": {
            "primary": {"url": endpoint_url, "api": "openai"},
            "stream": True,
            "request_timeout_s": 30,
            "connect_timeout_s": 2,
        },
        "models": {"extract": "synthetic-extractor", "review": "synthetic-reviewer"},
        "retry": {"max_attempts": 1},
        "review": {"max_repair_cycles": 2},
    }
    p = project / "blueprint.yaml"
    import yaml

    p.write_text(yaml.safe_dump(cfg, sort_keys=True), encoding="utf-8")
    return p
