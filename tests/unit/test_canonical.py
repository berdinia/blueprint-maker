"""Canonical model building, migration and model/geometry validation."""

from __future__ import annotations

import json

from blueprint_pipeline.canonical import build_canonical_model, migrate_legacy_house_model
from blueprint_pipeline.schemas import schema_errors
from blueprint_pipeline.validation import check_geometry, check_model
from conftest import REPO, feature, make_model, rect


def _status(checks, cid):
    return next(c.status for c in checks if c.check_id == cid)


def evidence(cands, conflicts=(), fragments=()):
    return {
        "schema": "blueprint.evidence",
        "schema_version": "1.0.0",
        "relationships": [],
        "evidence_origins": [
            {"origin_id": "ORIGIN-SYN-1", "source_ids": ["SYN-1"]},
            {"origin_id": "ORIGIN-SYN-2", "source_ids": ["SYN-2"]},
        ],
        "families": [],
        "fragments": list(fragments),
        "identity_candidates": cands,
        "conflicts": list(conflicts),
        "unresolved_questions": [],
    }


def cand(cid, label, sources=("SYN-1",), **kw):
    d = {
        "candidate_id": cid,
        "label_normalized": label,
        "label_raw": [label],
        "observation_ids": [],
        "source_ids": list(sources),
        "origin_ids": [f"ORIGIN-{s}" for s in sources],
        "independent_origin_count": len(sources),
        "status": "CANDIDATE",
        "revision_state": "CURRENT",
        "measurements": {},
        "conflict_ids": [],
    }
    d.update(kw)
    return d


def decisions(*ds):
    return {"schema": "blueprint.decisions", "schema_version": "1.0.0", "decisions": list(ds)}


def place(did, cid, poly, state="ANCHORED"):
    return {
        "decision_id": did,
        "type": "PLACE_ROOM",
        "candidate_id": cid,
        "polygon_mm": poly,
        "placement_state": state,
    }


def build(ev, decs=None, extractions=None):
    return build_canonical_model(
        evidence=ev,
        extractions=extractions or {},
        decisions=decs,
        synthetic=True,
        input_hashes={"evidence": "0" * 64},
    )


def test_rooms_without_decisions_stay_unplaced_not_one_per_file():
    m = build(evidence([cand("ID-ALPHA", "ALPHA", ("SYN-1", "SYN-2"))]))
    assert len(m["rooms"]) == 1  # one room from two files
    r = m["rooms"][0]
    assert r["polygon_mm"] is None and r["placement_state"] == "UNPLACED"
    assert r["evidence_origin_ids"] == ["ORIGIN-SYN-1", "ORIGIN-SYN-2"]
    assert any(u["subject_id"] == r["room_id"] for u in m["fragments_unplaced"])
    assert not schema_errors(m, "canonical-model")


def test_walls_split_on_partial_shared_edges_and_door_placement():
    ev = evidence([cand("ID-A", "A"), cand("ID-B", "B"), cand("ID-C", "C")])
    d = decisions(
        place("D1", "ID-A", rect(0, 0, 4000, 3000)),
        place("D2", "ID-B", rect(4000, 0, 7000, 3000)),
        place("D3", "ID-C", rect(0, 3000, 5000, 5000)),
        {
            "decision_id": "D4",
            "type": "PLACE_OPENING",
            "opening_id": "OPEN-1",
            "kind": "door",
            "room_candidates": ["ID-A", "ID-B"],
            "offset_mm": 500,
            "width_mm": 900,
        },
        {
            "decision_id": "D5",
            "type": "PLACE_OPENING",
            "opening_id": "OPEN-2",
            "kind": "door",
            "room_candidates": ["ID-A", "ID-B"],
            "offset_mm": 2500,
            "width_mm": 900,
        },
    )
    m = build(ev, d)
    shared = [w for w in m["walls"] if len(w["room_ids"]) == 2]
    pairs = sorted(tuple(w["room_ids"]) for w in shared)
    assert pairs == [("ROOM-A", "ROOM-B"), ("ROOM-A", "ROOM-C"), ("ROOM-B", "ROOM-C")]
    bc = next(w for w in shared if w["room_ids"] == ["ROOM-B", "ROOM-C"])
    assert bc["start_mm"] == [4000.0, 3000.0] and bc["end_mm"] == [5000.0, 3000.0]
    applied = {x["decision_id"]: x["applied"] for x in m["decisions"]}
    assert applied["D4"] is True and applied["D5"] is False  # 2500+900 > 3000 wall
    door = next(o for o in m["openings"] if o["opening_id"] == "OPEN-1")
    assert door["wall_id"] is not None
    assert {a["relation"] for a in m["adjacency"]} >= {"SHARES_WALL", "CONNECTED_BY_OPENING"}
    checks = check_model(m) + check_geometry(m)
    assert all(c.status != "FAIL" for c in checks), [c for c in checks if c.status == "FAIL"]


def test_accept_revision_resolves_only_with_decision():
    ext = {
        "SYN-1": {
            "observations": [
                {
                    "observation_id": "SYN-1/OBS-0001",
                    "source_id": "SYN-1",
                    "value_mm": 2500.0,
                    "kind": "dimension",
                    "confidence": 0.9,
                }
            ]
        },
        "SYN-2": {
            "observations": [
                {
                    "observation_id": "SYN-2/OBS-0001",
                    "source_id": "SYN-2",
                    "value_mm": 2800.0,
                    "kind": "dimension",
                    "confidence": 0.9,
                }
            ]
        },
    }
    conflicts = [
        {
            "conflict_id": "CF-R",
            "kind": "REVISION",
            "subject": "ID-C",
            "detail": "x",
            "status": "OPEN",
            "resolution": None,
            "source_ids": ["SYN-1", "SYN-2"],
            "observation_ids": [],
        },
        {
            "conflict_id": "CF-M",
            "kind": "MEASUREMENT",
            "subject": "ID-C",
            "detail": "x",
            "status": "OPEN",
            "resolution": None,
            "observation_ids": ["SYN-1/OBS-0001", "SYN-2/OBS-0001"],
            "values": [2500, 2800],
        },
    ]
    c = cand(
        "ID-C",
        "C",
        ("SYN-1", "SYN-2"),
        conflict_ids=["CF-R", "CF-M"],
        revision_state="POSSIBLE_REVISION",
    )
    open_model = build(evidence([c], conflicts), extractions=ext)
    assert {x["status"] for x in open_model["conflicts"]} == {"OPEN"}
    assert open_model["rooms"][0]["conflict_refs"] == ["CF-M", "CF-R"]
    d = decisions(
        {
            "decision_id": "D9",
            "type": "ACCEPT_REVISION",
            "candidate_id": "ID-C",
            "current_source_ids": ["SYN-2"],
            "superseded_source_ids": ["SYN-1"],
        }
    )
    m = build(evidence([c], conflicts), d, ext)
    assert {x["status"] for x in m["conflicts"]} == {"RESOLVED_BY_DECISION"}
    assert {r["state"] for r in m["revisions"]} == {"CURRENT", "SUPERSEDED"}


def test_hypothetical_room_is_marked_and_uncertain():
    m = build(
        evidence([]),
        decisions(
            {
                "decision_id": "D1",
                "type": "ADD_HYPOTHETICAL_ROOM",
                "room_id": "ROOM-H",
                "polygon_mm": rect(0, 0, 1000, 1000),
                "rationale": "test",
            }
        ),
    )
    r = m["rooms"][0]
    assert r["hypothetical"] and r["uncertainty"].startswith("hypothetical") and not r["source_ids"]
    assert all(c.status != "FAIL" for c in check_model(m))


def test_model_rejects_unsupported_geometry():
    m = make_model()
    m["rooms"][0]["decision_ids"] = []  # geometry with no decision behind it
    m["rooms"][1]["placement_state"] = "UNPLACED"  # geometry but "unplaced"
    checks = check_model(m)
    assert _status(checks, "unsupported_features") == "FAIL"
    assert _status(checks, "placement_state_consistency") == "FAIL"


def test_model_rejects_missing_provenance_duplicates_and_dangling_conflicts():
    m = make_model()
    m["rooms"][0]["source_ids"] = []
    m["rooms"][1]["room_id"] = "ROOM-A"
    m["rooms"][0]["conflict_refs"] = ["CF-NOPE"]
    checks = check_model(m)
    assert _status(checks, "provenance_coverage") == "FAIL"
    assert _status(checks, "unique_ids") == "FAIL"
    assert _status(checks, "conflict_references") == "FAIL"


def test_mixed_units_rejected_by_schema():
    m = make_model(units="cm")
    assert _status(check_model(m), "schema_valid") == "FAIL"


def test_geometry_checks_detect_overlap_zero_area_and_bad_opening():
    m = make_model(
        [
            feature(
                room_id="ROOM-A",
                display_name="A",
                polygon_mm=rect(0, 0, 3000, 2000),
                measurement_ids=[],
            ),
            feature(
                room_id="ROOM-B",
                display_name="B",
                polygon_mm=rect(2000, 0, 5000, 2000),
                measurement_ids=[],
            ),
            feature(
                room_id="ROOM-Z",
                display_name=None,
                polygon_mm=[[0, 0], [10, 0], [20, 0]],
                measurement_ids=[],
            ),
        ]
    )
    m["openings"] = [
        feature(
            opening_id="OPEN-X",
            kind="door",
            wall_id=m["walls"][0]["wall_id"],
            offset_mm=0,
            width_mm=99999,
            room_ids=[],
        )
    ]
    g = check_geometry(m)
    assert _status(g, "polygon_validity") == "FAIL"
    assert _status(g, "room_overlap") == "FAIL"
    assert _status(g, "opening_attachment") == "FAIL"


def test_measurement_disagreement_without_conflict_fails():
    m = make_model()
    m["measurements"] = [
        {
            "measurement_id": "M-1",
            "subject_id": "ROOM-A",
            "quantity": "width",
            "value_mm": 3500.0,
            "raw_text": "3500 mm",
            "observation_id": "SYN-A/OBS-0001",
            "source_id": "SYN-A",
        }
    ]
    assert _status(check_geometry(m), "dimension_model_agreement") == "FAIL"
    m["conflicts"] = [
        {
            "conflict_id": "CF-1",
            "kind": "MEASUREMENT",
            "subject": "x",
            "detail": "d",
            "status": "OPEN",
        }
    ]
    m["rooms"][0]["conflict_refs"] = ["CF-1"]
    g = check_geometry(m)
    assert _status(g, "dimension_model_agreement") == "PASS"
    assert _status(g, "dimension_disagreement_with_open_conflict") == "WARN"


def test_legacy_migration_preserves_null_names_and_marks_hypothetical():
    legacy = json.loads((REPO / "fixtures/synthetic/house-model.json").read_text())
    m = migrate_legacy_house_model(legacy)
    assert not schema_errors(m, "canonical-model")
    rooms = {r["room_id"]: r for r in m["rooms"]}
    assert rooms["SYN-ROOM-C"]["display_name"] is None
    assert all(r["hypothetical"] and r["placement_state"] == "AMBIGUOUS" for r in rooms.values())
    assert rooms["SYN-ROOM-C"]["conflict_refs"]
    assert {o["placement_state"] for o in m["openings"]} == {"UNPLACED"}
    assert m["provenance"]["migrated_from"] == "legacy-house-model@0"
