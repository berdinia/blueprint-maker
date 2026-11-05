"""Schema migrations.

``migrate_legacy_house_model`` converts the previous scaffold's
``house-model.json`` (schema "0") into canonical-model 1.0.0.

Legacy rooms carried axis-aligned rectangles without provenance. They are
migrated as **hypothetical** rooms (placement AMBIGUOUS, UNCONFIRMED), because
the legacy format cannot say which evidence supports them. A human must
re-confirm them through decisions before they count as evidence-backed.
Legacy openings with ``position_on_wall = not_known`` become UNPLACED openings.
"""

from __future__ import annotations

from typing import Any

from .. import __version__
from ..errors import SchemaValidationError
from ..io.atomic import canonical_json, sha256_bytes
from ..schemas import schema_errors
from .builder import _feature


def migrate_legacy_house_model(doc: dict[str, Any]) -> dict[str, Any]:
    errs = schema_errors(doc, "legacy-house-model")
    if errs:
        raise SchemaValidationError("legacy-house-model", errs)
    rooms = []
    questions = []
    conflicts = []
    unplaced = []
    for r in doc["rooms"]:
        rid = r["room_id"]
        keys = ("x_min_mm", "x_max_mm", "y_min_mm", "y_max_mm")
        poly = None
        if (
            all(k in r for k in keys)
            and r["x_max_mm"] > r["x_min_mm"]
            and r["y_max_mm"] > r["y_min_mm"]
        ):
            x0, x1, y0, y1 = (float(r[k]) for k in keys)
            poly = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        names = [c.get("name") for c in r.get("name_conflicts", []) if isinstance(c, dict)]
        crefs = []
        for i, uc in enumerate(r.get("unresolved_conflicts", []), start=1):
            cid = f"CONFLICT-LEGACY-{rid}-{i}"
            crefs.append(cid)
            conflicts.append(
                {
                    "conflict_id": cid,
                    "kind": "NAME" if "name" in str(uc.get("type", "")) else "IDENTITY",
                    "subject": rid,
                    "detail": str(uc.get("message", uc))[:500],
                    "status": "OPEN",
                    "resolution": None,
                    "affects": [rid],
                }
            )
        room = _feature(
            room_id=rid,
            display_name=r.get("name"),
            name_candidates=[n for n in names if n is not None],
            identity_candidate_id=None,
            polygon_mm=poly,
            placement_state="AMBIGUOUS" if poly else "UNPLACED",
            hypothetical=poly is not None,
            uncertainty="migrated from legacy model without provenance; re-confirm via decisions",
            conflict_refs=crefs,
            measurement_ids=[],
        )
        if poly is None:
            room["hypothetical"] = False
            unplaced.append(
                {
                    "subject_id": rid,
                    "kind": "room",
                    "fragment_class": None,
                    "placement_state": "UNPLACED",
                    "source_ids": [],
                    "reason": "legacy room without valid coordinates",
                }
            )
        rooms.append(room)
        questions.append(
            {
                "question_id": f"Q-LEGACY-{rid}",
                "text": f"Confirm legacy room {rid} against evidence (no provenance was recorded).",
                "refs": [rid],
            }
        )
    openings: list[dict[str, Any]] = []
    legacy_openings = doc.get("openings") or {}
    for kind_key, kind in (("windows", "window"), ("doors", "door")):
        for o in legacy_openings.get(kind_key, []) or []:
            ref = str(o.get("ref") or f"LEGACY-{kind}-{len(openings) + 1}")
            openings.append(
                _feature(
                    opening_id=ref,
                    kind=kind,
                    wall_id=None,
                    offset_mm=None,
                    width_mm=o.get("measured_width_mm"),
                    room_ids=[],
                    placement_state="UNPLACED",
                    uncertainty="legacy opening; position on wall not known",
                )
            )
            unplaced.append(
                {
                    "subject_id": ref,
                    "kind": f"opening:{kind}",
                    "fragment_class": None,
                    "placement_state": "UNPLACED",
                    "source_ids": [],
                    "reason": "legacy opening without wall position",
                }
            )
    model: dict[str, Any] = {
        "schema": "blueprint.canonical-model",
        "schema_version": "1.0.0",
        "model_id": "",
        "synthetic": "SYNTHETIC" in canonical_json(doc).upper(),
        "units": "mm",
        "coordinate_system": {"y_axis": "up", "origin": "legacy", "status": "provisional"},
        "rooms": sorted(rooms, key=lambda r: r["room_id"]),
        "walls": [],
        "openings": sorted(openings, key=lambda o: o["opening_id"]),
        "adjacency": [],
        "measurements": [],
        "conflicts": conflicts,
        "revisions": [],
        "decisions": [],
        "unresolved_questions": questions,
        "fragments_unplaced": unplaced,
        "provenance": {
            "inputs": {"legacy_model": sha256_bytes(canonical_json(doc).encode())},
            "builder": f"blueprint-pipeline {__version__}",
            "migrated_from": "legacy-house-model@0",
        },
    }
    model["model_id"] = "MODEL-" + sha256_bytes(canonical_json(model).encode())[:16]
    return model


MIGRATIONS = {("legacy-house-model", "0"): migrate_legacy_house_model}
