"""Source families, evidence origins, fragments and identity resolution."""

from __future__ import annotations

from blueprint_pipeline.evidence.families import (
    Relationship,
    classify_fragments,
    computed_relationships,
    evidence_origins,
    families,
    resolve_relationships,
)
from blueprint_pipeline.evidence.identity import resolve_identities

IDS = ["SYN-1", "SYN-2", "SYN-3", "SYN-4", "SYN-5"]


def rel(a, b, kind, by="HUMAN"):
    return Relationship(a, b, kind, by, 0.9, "test")


def obs(sid, i, kind, raw, **kw):
    d = {
        "observation_id": f"{sid}/OBS-{i:04d}",
        "source_id": sid,
        "kind": kind,
        "raw_text": raw,
        "normalized": raw,
        "confidence": 0.9,
        "subject": None,
        "dimension_axis": None,
        "value_mm": None,
    }
    d.update(kw)
    return d


def test_exact_duplicates_and_photo_variants_share_one_origin():
    sources = [
        {"source_id": s, "sha256": ("a" * 64 if s in ("SYN-1", "SYN-2") else s.ljust(64, "0"))}
        for s in IDS
    ]
    dh = {
        "SYN-1": "ffff0000ffff0000",
        "SYN-2": "ffff0000ffff0000",
        "SYN-3": "ffff0000ffff0001",
        "SYN-4": "0000ffff0000ffff",
        "SYN-5": "00ff00ff00ff00ff",
    }
    rels, _ = resolve_relationships([], computed_relationships(sources, dh), set(IDS))
    kinds = {(r.a, r.b): r.kind for r in rels}
    assert kinds[("SYN-1", "SYN-2")] == "EXACT_FILE_DUPLICATE"
    assert kinds[("SYN-1", "SYN-3")] == "SAME_SCAN_OR_PHOTO_VARIANT"
    origin_of, origins = evidence_origins(IDS, rels)
    assert origin_of["SYN-1"] == origin_of["SYN-2"] == origin_of["SYN-3"]
    assert len(origins) == 3  # repeated scans are one origin, not three


def test_independent_redraw_and_revision_are_separate_origins_not_merged():
    rels = [rel("SYN-1", "SYN-4", "REDRAWN_SAME_ROOM"), rel("SYN-1", "SYN-5", "POSSIBLE_REVISION")]
    origin_of, _ = evidence_origins(IDS, rels)
    assert len({origin_of[s] for s in ("SYN-1", "SYN-4", "SYN-5")}) == 3
    fams = families(IDS, rels, origin_of)
    fam1 = next(f for f in fams if "SYN-1" in f["source_ids"])
    assert set(fam1["source_ids"]) == {"SYN-1", "SYN-4", "SYN-5"}
    assert fam1["revision_groups"] == [["SYN-1", "SYN-5"]]


def test_human_assertion_overrides_heuristic_variant():
    heur = rel("SYN-1", "SYN-5", "SAME_SCAN_OR_PHOTO_VARIANT", "HEURISTIC")
    human = rel("SYN-1", "SYN-5", "CONFLICTING_VERSION", "HUMAN")
    rels, findings = resolve_relationships([human], [heur], set(IDS))
    assert [r.kind for r in rels] == ["CONFLICTING_VERSION"]
    assert findings[0]["code"] == "RELATIONSHIP_OVERRIDDEN"
    origin_of, _ = evidence_origins(IDS, rels)
    assert origin_of["SYN-1"] != origin_of["SYN-5"]  # a revision is never folded into one origin


def test_unknown_and_self_relationships_reported():
    rels, findings = resolve_relationships(
        [rel("SYN-1", "SYN-404", "UNRELATED"), rel("SYN-2", "SYN-2", "UNKNOWN")], [], set(IDS)
    )
    assert rels == []
    assert {f["code"] for f in findings} == {"RELATIONSHIP_UNKNOWN_SOURCE", "RELATIONSHIP_SELF"}


def test_fragment_classes_and_placement_states():
    extraction = {
        "SYN-1": {"fragment": {"fragment_class": "WHOLE_FLOOR", "confidence": 0.8}},
        "SYN-2": {"fragment": {"fragment_class": "ROOM", "confidence": 0.8}},
        "SYN-3": {"fragment": {"fragment_class": "ROOM", "confidence": 0.8}},
        "SYN-4": None,
        "SYN-5": {"fragment": {"fragment_class": "ROOM", "confidence": 0.5}},
    }
    labels = {
        "SYN-1": {"ALPHA", "BRAVO"},
        "SYN-2": {"ALPHA"},
        "SYN-3": {"ZULU"},
        "SYN-5": {"ALPHA", "ZULU"},
    }
    origin_of = {s: f"ORIGIN-{s}" for s in IDS}
    frags = {
        f["source_id"]: f
        for f in classify_fragments(IDS, {"SYN-4": "OPENING_DETAIL"}, extraction, origin_of, labels)
    }
    assert frags["SYN-1"]["placement_state"] == "ANCHORED"
    assert frags["SYN-2"]["placement_state"] == "LIKELY"
    assert (
        frags["SYN-3"]["placement_state"] == "UNPLACED"
    )  # unplaced detail is preserved, not forced
    assert frags["SYN-4"]["placement_state"] == "DETAIL_ONLY"
    assert frags["SYN-4"]["class_asserted_by"] == "HUMAN"
    assert frags["SYN-5"]["placement_state"] == "AMBIGUOUS"


def test_identity_counts_origins_not_files_and_keeps_conflicts():
    ext = {
        "SYN-1": {
            "observations": [
                obs("SYN-1", 1, "room_label", "ALPHA"),
                obs(
                    "SYN-1",
                    2,
                    "dimension",
                    "2500 mm",
                    subject="ALPHA",
                    dimension_axis="depth",
                    value_mm=2500.0,
                ),
            ]
        },
        "SYN-2": {"observations": [obs("SYN-2", 1, "room_label", "Alpha")]},  # photo of SYN-1
        "SYN-4": {
            "observations": [
                obs("SYN-4", 1, "room_label", "ALPHA"),
                obs(
                    "SYN-4",
                    2,
                    "dimension",
                    "2800 mm",
                    subject="alpha",
                    dimension_axis="depth",
                    value_mm=2800.0,
                ),
            ]
        },
        "SYN-5": {"observations": [obs("SYN-5", 1, "room_label", None, normalized=None)]},
    }
    origin_of = {"SYN-1": "O1", "SYN-2": "O1", "SYN-4": "O4", "SYN-5": "O5"}
    cands, conflicts, questions = resolve_identities(ext, origin_of, [])
    by = {c["candidate_id"]: c for c in cands}
    alpha = by["ID-ALPHA"]
    assert alpha["independent_origin_count"] == 2 and len(alpha["source_ids"]) == 3
    assert alpha["status"] == "CONFLICTED"
    meas = next(c for c in conflicts if c["kind"] == "MEASUREMENT")
    assert sorted(meas["values"]) == [2500.0, 2800.0]  # both kept, nothing averaged
    unl = [c for c in cands if c["label_normalized"] is None]
    assert len(unl) == 1 and unl[0]["status"] == "UNRESOLVED"
    assert any("Unlabeled" in q["text"] for q in questions)


def test_corroboration_and_revision_state():
    ext = {
        "SYN-1": {
            "observations": [
                obs("SYN-1", 1, "room_label", "BRAVO"),
                obs("SYN-1", 2, "room_label", "CHARLIE"),
            ]
        },
        "SYN-4": {"observations": [obs("SYN-4", 1, "room_label", "BRAVO")]},
        "SYN-5": {"observations": [obs("SYN-5", 1, "room_label", "CHARLIE")]},
    }
    origin_of = {s: f"O-{s}" for s in ext}
    cands, conflicts, _ = resolve_identities(
        ext, origin_of, [rel("SYN-1", "SYN-5", "POSSIBLE_REVISION")]
    )
    by = {c["candidate_id"]: c for c in cands}
    assert by["ID-BRAVO"]["status"] == "CORROBORATED"
    assert by["ID-CHARLIE"]["revision_state"] == "POSSIBLE_REVISION"
    assert by["ID-CHARLIE"]["status"] == "CONFLICTED"
    assert [c["kind"] for c in conflicts] == ["REVISION"]
