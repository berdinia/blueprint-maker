"""Build the canonical evidence model.

Inputs: the evidence document (relationships, origins, fragments, identity
candidates, conflicts), the current extraction records, the source register
and the human decisions file.

Geometry enters the model **only** through decisions (PLACE_ROOM,
PLACE_OPENING, ADD_HYPOTHETICAL_ROOM). Rooms, openings and fragments without a
placement decision are kept, but with ``polygon_mm = null`` / ``wall_id = null``
and an UNPLACED or DETAIL_ONLY state; they are listed for human review instead
of being forced onto the plan.
"""

from __future__ import annotations

import copy
from collections import defaultdict
from typing import Any

from .. import __version__
from ..io.atomic import canonical_json, sha256_bytes
from .geometry import seg_length, split_collinear_edges

PLACEMENT_RANK = {"ANCHORED": 0, "LIKELY": 1, "AMBIGUOUS": 2, "UNPLACED": 3, "DETAIL_ONLY": 4}


def _r(v: float, nd: int = 3) -> float:
    out = round(float(v), nd)
    return 0.0 if out == 0 else out


def _weakest(states: list[str]) -> str:
    return max(states, key=lambda s: PLACEMENT_RANK[s]) if states else "UNPLACED"


def _mean_conf(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return round(sum(vs) / len(vs), 3) if vs else None


def _feature(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "source_ids": [],
        "evidence_origin_ids": [],
        "raw_observation_ids": [],
        "confidence": None,
        "placement_state": "UNPLACED",
        "revision_state": "UNKNOWN",
        "human_confirmation": "UNCONFIRMED",
        "conflict_refs": [],
        "hypothetical": False,
        "uncertainty": None,
        "decision_ids": [],
    }
    base.update(kw)
    return base


def build_canonical_model(
    *,
    evidence: dict[str, Any],
    extractions: dict[str, dict[str, Any]],
    decisions: dict[str, Any] | None,
    synthetic: bool,
    input_hashes: dict[str, str],
) -> dict[str, Any]:
    ev = copy.deepcopy(evidence)
    decs = (decisions or {}).get("decisions", [])
    applied: list[dict[str, Any]] = []
    candidates = {c["candidate_id"]: c for c in ev["identity_candidates"]}
    conflicts = {c["conflict_id"]: c for c in ev["conflicts"]}
    questions = list(ev["unresolved_questions"])
    obs_index = {
        o["observation_id"]: o for rec in extractions.values() for o in rec.get("observations", [])
    }
    origin_of = {s: o["origin_id"] for o in ev["evidence_origins"] for s in o["source_ids"]}

    def note(d: dict[str, Any], ok: bool, detail: str) -> None:
        applied.append(
            {"decision_id": d["decision_id"], "type": d["type"], "applied": ok, "detail": detail}
        )

    # ---- 1. merges ----------------------------------------------------------
    for d in (d for d in decs if d["type"] == "MERGE_CANDIDATES"):
        missing = [c for c in d["candidate_ids"] if c not in candidates]
        if missing:
            note(d, False, f"unknown candidate(s): {missing}")
            continue
        target_id = d["into"]
        merged = {
            "candidate_id": target_id,
            "label_normalized": candidates[d["candidate_ids"][0]]["label_normalized"],
            "label_raw": [],
            "observation_ids": [],
            "source_ids": [],
            "origin_ids": [],
            "measurements": defaultdict(list),
            "conflict_ids": [],
            "revision_state": "CURRENT",
            "status": "CANDIDATE",
        }
        for cid in d["candidate_ids"]:
            c = candidates.pop(cid)
            for k in ("label_raw", "observation_ids", "source_ids", "origin_ids", "conflict_ids"):
                merged[k] = sorted(set(merged[k]) | set(c[k]), key=str)
            for axis, ms in c.get("measurements", {}).items():
                merged["measurements"][axis].extend(ms)
            if c["revision_state"] != "CURRENT":
                merged["revision_state"] = c["revision_state"]
        merged["measurements"] = dict(merged["measurements"])
        merged["independent_origin_count"] = len(merged["origin_ids"])
        candidates[target_id] = merged
        for cf in conflicts.values():
            if cf["subject"] in d["candidate_ids"]:
                cf["subject"] = target_id
        note(d, True, f"merged {len(d['candidate_ids'])} candidates into {target_id}")

    # ---- 2. revision acceptance ---------------------------------------------
    superseded_sources: set[str] = set()
    revisions: list[dict[str, Any]] = []
    for d in (d for d in decs if d["type"] == "ACCEPT_REVISION"):
        c = candidates.get(d["candidate_id"])
        if c is None:
            note(d, False, f"unknown candidate {d['candidate_id']}")
            continue
        current = set(d["current_source_ids"])
        superseded = set(d.get("superseded_source_ids", []))
        superseded_sources |= superseded
        for cf_id in c["conflict_ids"]:
            cf = conflicts[cf_id]
            if cf["kind"] == "REVISION":
                cf["status"] = "RESOLVED_BY_DECISION"
                cf["resolution"] = f"{d['decision_id']}: current={sorted(current)}"
            elif cf["kind"] == "MEASUREMENT":
                remaining = [
                    obs_index[o]["value_mm"]
                    for o in cf.get("observation_ids", [])
                    if o in obs_index and obs_index[o]["source_id"] not in superseded
                ]
                if remaining and max(remaining) - min(remaining) <= max(
                    50.0, 0.02 * max(remaining)
                ):
                    cf["status"] = "RESOLVED_BY_DECISION"
                    cf["resolution"] = f"{d['decision_id']}: superseded readings excluded"
        c["revision_state"] = "CURRENT"
        revisions.append(
            {
                "revision_id": f"REV-{d['decision_id']}",
                "candidate_id": c["candidate_id"],
                "source_ids": sorted(current),
                "state": "CURRENT",
                "decision_id": d["decision_id"],
            }
        )
        if superseded:
            revisions.append(
                {
                    "revision_id": f"REV-{d['decision_id']}-SUPERSEDED",
                    "candidate_id": c["candidate_id"],
                    "source_ids": sorted(superseded),
                    "state": "SUPERSEDED",
                    "decision_id": d["decision_id"],
                }
            )
        note(d, True, f"current={sorted(current)} superseded={sorted(superseded)}")
    for c in candidates.values():
        if c["revision_state"] in ("POSSIBLE_REVISION", "CONFLICTING_VERSION"):
            revisions.append(
                {
                    "revision_id": f"REV-OPEN-{c['candidate_id']}",
                    "candidate_id": c["candidate_id"],
                    "source_ids": c["source_ids"],
                    "state": c["revision_state"],
                    "decision_id": None,
                }
            )

    # ---- 3. rooms -------------------------------------------------------------
    placements = {d["candidate_id"]: d for d in decs if d["type"] == "PLACE_ROOM"}
    rooms: list[dict[str, Any]] = []
    room_of_candidate: dict[str, str] = {}
    measurements: list[dict[str, Any]] = []
    unplaced: list[dict[str, Any]] = []
    for cid in sorted(placements):
        if cid not in candidates:
            note(placements[cid], False, f"unknown candidate {cid}")
    for cid in sorted(candidates):
        c = candidates[cid]
        room_id = f"ROOM-{cid[3:]}"
        d = placements.get(cid)
        if d is not None:
            room_id = d.get("room_id", room_id)
        open_conf = [
            x for x in c["conflict_ids"] if x in conflicts and conflicts[x]["status"] == "OPEN"
        ]
        obs_conf = [obs_index[o].get("confidence") for o in c["observation_ids"] if o in obs_index]
        live_sources = [s for s in c["source_ids"] if s not in superseded_sources]
        room = _feature(
            room_id=room_id,
            display_name=c["label_normalized"],
            name_candidates=sorted({x for x in c.get("label_raw", []) if x is not None}),
            identity_candidate_id=cid,
            polygon_mm=None,
            source_ids=live_sources or c["source_ids"],
            evidence_origin_ids=sorted(
                {origin_of.get(s, s) for s in (live_sources or c["source_ids"])}
            ),
            raw_observation_ids=c["observation_ids"],
            confidence=_mean_conf(obs_conf),
            revision_state=c["revision_state"],
            conflict_refs=sorted(open_conf),
            measurement_ids=[],
        )
        if d is not None and cid in placements:
            room["polygon_mm"] = [[_r(x), _r(y)] for x, y in d["polygon_mm"]]
            room["placement_state"] = d["placement_state"]
            room["human_confirmation"] = d.get("human_confirmation", "CONFIRMED")
            room["decision_ids"] = [d["decision_id"]]
            if d.get("display_name") is not None:
                room["display_name"] = d["display_name"]
            note(d, True, f"placed {room_id}")
        else:
            room["uncertainty"] = "no placement decision; position unknown"
            unplaced.append(
                {
                    "subject_id": room_id,
                    "kind": "room",
                    "fragment_class": None,
                    "placement_state": "UNPLACED",
                    "source_ids": room["source_ids"],
                    "reason": "no placement decision; position unknown",
                }
            )
            questions.append(
                {
                    "question_id": f"Q-PLACE-{room_id}",
                    "text": f"Where is {room_id} ({c['label_normalized'] or 'unlabeled'})?",
                    "refs": [room_id, cid],
                }
            )
        for axis, ms in sorted(c.get("measurements", {}).items()):
            for m in ms:
                mid = f"M-{m['observation_id'].replace('/', '-')}"
                measurements.append(
                    {
                        "measurement_id": mid,
                        "subject_id": room_id,
                        "quantity": axis,
                        "value_mm": m["value_mm"],
                        "raw_text": m["raw_text"],
                        "observation_id": m["observation_id"],
                        "source_id": m["source_id"],
                        "origin_id": m["origin_id"],
                        "confidence": m.get("confidence"),
                        "uncertainty": obs_index.get(m["observation_id"], {}).get(
                            "value_uncertainty"
                        ),
                    }
                )
                room["measurement_ids"].append(mid)
        room_of_candidate[cid] = room_id
        rooms.append(room)

    for d in (d for d in decs if d["type"] == "ADD_HYPOTHETICAL_ROOM"):
        rooms.append(
            _feature(
                room_id=d["room_id"],
                display_name=d.get("display_name"),
                name_candidates=[],
                identity_candidate_id=None,
                polygon_mm=[[_r(x), _r(y)] for x, y in d["polygon_mm"]],
                placement_state="AMBIGUOUS",
                hypothetical=True,
                uncertainty=f"hypothetical: {d['rationale']}",
                decision_ids=[d["decision_id"]],
                measurement_ids=[],
            )
        )
        note(d, True, f"added hypothetical room {d['room_id']}")
    rooms.sort(key=lambda r: r["room_id"])
    room_by_id = {r["room_id"]: r for r in rooms}

    # ---- 4. walls (derived from placed room edges) -----------------------------
    edges = []
    for r in rooms:
        poly = r["polygon_mm"]
        if not poly:
            continue
        for i in range(len(poly)):
            a, b = poly[i], poly[(i + 1) % len(poly)]
            edges.append(((a[0], a[1]), (b[0], b[1]), r["room_id"]))
    segs = split_collinear_edges(edges, tol=1e-3)
    segs.sort(
        key=lambda s: (
            round(min(s[0], s[1])[0], 3),
            round(min(s[0], s[1])[1], 3),
            round(max(s[0], s[1])[0], 3),
            round(max(s[0], s[1])[1], 3),
        )
    )
    walls: list[dict[str, Any]] = []
    for i, (p0, p1, owners) in enumerate(segs, start=1):
        a, b = sorted([(_r(p0[0]), _r(p0[1])), (_r(p1[0]), _r(p1[1]))])
        owner_rooms = [room_by_id[o] for o in owners]
        hyp = all(o["hypothetical"] for o in owner_rooms)
        walls.append(
            _feature(
                wall_id=f"W-{i:03d}",
                start_mm=[a[0], a[1]],
                end_mm=[b[0], b[1]],
                room_ids=owners,
                thickness_mm=None,
                source_ids=sorted({s for o in owner_rooms for s in o["source_ids"]}),
                evidence_origin_ids=sorted(
                    {s for o in owner_rooms for s in o["evidence_origin_ids"]}
                ),
                placement_state=_weakest([o["placement_state"] for o in owner_rooms]),
                revision_state="CURRENT"
                if all(o["revision_state"] == "CURRENT" for o in owner_rooms)
                else "UNKNOWN",
                human_confirmation="CONFIRMED"
                if all(o["human_confirmation"] == "CONFIRMED" for o in owner_rooms)
                else "UNCONFIRMED",
                hypothetical=hyp,
                uncertainty="derived from hypothetical room(s)" if hyp else None,
                confidence=None,
            )
        )

    def walls_between(room_ids: list[str]) -> list[dict[str, Any]]:
        return [w for w in walls if set(room_ids) <= set(w["room_ids"])]

    # ---- 5. adjacency -----------------------------------------------------------
    adjacency: list[dict[str, Any]] = []
    pairs: dict[tuple[str, str], list[str]] = defaultdict(list)
    for w in walls:
        if len(w["room_ids"]) == 2:
            pairs[(w["room_ids"][0], w["room_ids"][1])].append(w["wall_id"])

    # ---- 6. openings ------------------------------------------------------------
    openings: list[dict[str, Any]] = []
    placed_obs: set[str] = set()
    for d in (d for d in decs if d["type"] == "PLACE_OPENING"):
        rids = [room_of_candidate.get(c) for c in d["room_candidates"]]
        if any(r is None or room_by_id[r]["polygon_mm"] is None for r in rids):
            note(d, False, "referenced room is unknown or unplaced")
            continue
        rid_list = [r for r in rids if r is not None]
        wall = None
        offset = float(d["offset_mm"])
        if len(rid_list) == 2:
            cands = walls_between(rid_list)
            if cands:
                wall = max(
                    cands, key=lambda w: (seg_length(w["start_mm"], w["end_mm"]), w["wall_id"])
                )
        else:
            poly = room_by_id[rid_list[0]]["polygon_mm"]
            ei = d.get("edge_index")
            if ei is not None and ei < len(poly):
                ea, eb = poly[ei], poly[(ei + 1) % len(poly)]
                # wall segment on that edge which contains the whole opening span
                for w in walls:
                    if rid_list[0] not in w["room_ids"]:
                        continue
                    sa = seg_length(ea, w["start_mm"])
                    sb = seg_length(ea, w["end_mm"])
                    edge_len = seg_length(ea, eb)
                    on_edge = (
                        abs(sa + seg_length(w["start_mm"], eb) - edge_len) < 1e-3
                        and abs(sb + seg_length(w["end_mm"], eb) - edge_len) < 1e-3
                    )
                    if not on_edge:
                        continue
                    lo, hi = sorted((sa, sb))
                    if lo - 1e-3 <= offset and offset + d["width_mm"] <= hi + 1e-3:
                        wall = w
                        offset = offset - lo
                        break
        if wall is None:
            note(d, False, "no wall found for the referenced room(s)/edge")
            continue
        wlen = seg_length(wall["start_mm"], wall["end_mm"])
        if offset + d["width_mm"] > wlen + 1e-3:
            note(d, False, f"opening span {offset}+{d['width_mm']} exceeds wall length {wlen:.1f}")
            continue
        obs_ids = d.get("observation_ids", [])
        placed_obs |= set(obs_ids)
        srcs = sorted({obs_index[o]["source_id"] for o in obs_ids if o in obs_index})
        openings.append(
            _feature(
                opening_id=d["opening_id"],
                kind=d["kind"],
                wall_id=wall["wall_id"],
                offset_mm=_r(offset),
                width_mm=_r(d["width_mm"]),
                room_ids=rid_list,
                source_ids=srcs or wall["source_ids"],
                evidence_origin_ids=sorted(
                    {origin_of.get(s, s) for s in (srcs or wall["source_ids"])}
                ),
                raw_observation_ids=obs_ids,
                placement_state=d.get("placement_state", "ANCHORED"),
                revision_state="CURRENT",
                human_confirmation=d.get("human_confirmation", "CONFIRMED"),
                decision_ids=[d["decision_id"]],
                confidence=_mean_conf(
                    [obs_index[o].get("confidence") for o in obs_ids if o in obs_index]
                ),
            )
        )
        if len(rid_list) == 2 and d["kind"] == "door":
            pairs.setdefault((min(rid_list), max(rid_list)), [])
        note(d, True, f"placed {d['opening_id']} on {wall['wall_id']}")

    # Unplaced opening observations. Readings of the same opening from the same
    # evidence origin (duplicate files, photos of one sheet) collapse into one
    # feature; anything equivalent to a placed opening is not repeated.
    def okey(o: dict[str, Any]) -> tuple[Any, ...]:
        return (
            origin_of.get(o["source_id"], o["source_id"]),
            o.get("opening_type") or "unknown",
            (o.get("raw_text") or "").strip().upper(),
            (o.get("subject") or "").strip().upper(),
        )

    placed_keys = {okey(obs_index[o]) for o in placed_obs if o in obs_index}
    groups: dict[tuple[Any, ...], list[str]] = defaultdict(list)
    for oid in sorted(obs_index):
        o = obs_index[oid]
        if o["kind"] == "opening" and oid not in placed_obs and okey(o) not in placed_keys:
            groups[okey(o)].append(oid)
    fragment_state = {f["source_id"]: f for f in ev["fragments"]}
    for _key, oids in sorted(groups.items(), key=lambda kv: kv[1][0]):
        o = obs_index[oids[0]]
        srcs = sorted({obs_index[x]["source_id"] for x in oids})
        frag_states = {fragment_state.get(s, {}).get("placement_state") for s in srcs}
        state = "DETAIL_ONLY" if frag_states == {"DETAIL_ONLY"} else "UNPLACED"
        frag = fragment_state.get(o["source_id"], {})
        open_id = "OPEN-" + oids[0].replace("/", "-")
        subj = o.get("subject")
        openings.append(
            _feature(
                opening_id=open_id,
                kind=o.get("opening_type") or "unknown",
                wall_id=None,
                offset_mm=None,
                width_mm=o.get("value_mm"),
                room_ids=[],
                source_ids=srcs,
                evidence_origin_ids=[origin_of.get(o["source_id"], o["source_id"])],
                raw_observation_ids=oids,
                placement_state=state,
                revision_state="CURRENT",
                confidence=_mean_conf([obs_index[x].get("confidence") for x in oids]),
                uncertainty="position on wall unknown",
            )
        )
        unplaced.append(
            {
                "subject_id": open_id,
                "kind": f"opening:{o.get('opening_type') or 'unknown'}",
                "fragment_class": frag.get("fragment_class"),
                "placement_state": state,
                "source_ids": srcs,
                "reason": f"position unknown{f' (subject {subj!r})' if subj else ''}",
            }
        )
    openings.sort(key=lambda o: o["opening_id"])

    for (ra, rb), wall_ids in sorted(pairs.items()):
        a, b = sorted((ra, rb))
        rel = "SHARES_WALL" if wall_ids else "CONNECTED_BY_OPENING"
        for o in openings:
            if sorted(o.get("room_ids", [])) == [a, b] and o["kind"] == "door":
                rel = "CONNECTED_BY_OPENING"
        rr = [room_by_id[a], room_by_id[b]]
        adjacency.append(
            _feature(
                adjacency_id=f"ADJ-{a}-{b}",
                room_ids=[a, b],
                relation=rel,
                source_ids=sorted({s for r in rr for s in r["source_ids"]}),
                evidence_origin_ids=sorted({s for r in rr for s in r["evidence_origin_ids"]}),
                placement_state=_weakest([r["placement_state"] for r in rr]),
                revision_state="CURRENT",
                hypothetical=any(r["hypothetical"] for r in rr),
                uncertainty="involves a hypothetical room"
                if any(r["hypothetical"] for r in rr)
                else None,
            )
        )

    for f in ev["fragments"]:
        if f["placement_state"] in ("UNPLACED", "DETAIL_ONLY") and f["fragment_class"] not in (
            "WHOLE_FLOOR",
        ):
            unplaced.append(
                {
                    "subject_id": f"FRAGMENT-{f['source_id']}",
                    "kind": "source_fragment",
                    "fragment_class": f["fragment_class"],
                    "placement_state": f["placement_state"],
                    "source_ids": [f["source_id"]],
                    "reason": f["placement_basis"],
                }
            )
    unplaced.sort(key=lambda u: u["subject_id"])

    # ---- 7. conflicts with affected features ---------------------------------
    out_conflicts = []
    for cf in sorted(conflicts.values(), key=lambda c: c["conflict_id"]):
        cf = dict(cf)
        cf["affects"] = sorted(
            r["room_id"] for r in rooms if r.get("identity_candidate_id") == cf["subject"]
        )
        out_conflicts.append(cf)
    open_ids = {c["conflict_id"] for c in out_conflicts if c["status"] == "OPEN"}
    questions = [
        q
        for q in questions
        if not any(ref in conflicts and ref not in open_ids for ref in q.get("refs", []))
    ]
    seen_q: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for q in questions:
        if q["question_id"] not in seen_q:
            seen_q.add(q["question_id"])
            deduped.append(q)
    questions = deduped

    anchored = any(r["placement_state"] == "ANCHORED" for r in rooms)
    model: dict[str, Any] = {
        "schema": "blueprint.canonical-model",
        "schema_version": "1.0.0",
        "model_id": "",
        "synthetic": synthetic,
        "units": "mm",
        "coordinate_system": {
            "y_axis": "up",
            "origin": "defined by placement decisions",
            "status": "anchored" if anchored else "provisional",
        },
        "rooms": rooms,
        "walls": walls,
        "openings": openings,
        "adjacency": adjacency,
        "measurements": sorted(measurements, key=lambda m: m["measurement_id"]),
        "conflicts": out_conflicts,
        "revisions": sorted(revisions, key=lambda r: r["revision_id"]),
        "decisions": sorted(applied, key=lambda a: a["decision_id"]),
        "unresolved_questions": sorted(questions, key=lambda q: q["question_id"]),
        "fragments_unplaced": unplaced,
        "provenance": {
            "inputs": dict(sorted(input_hashes.items())),
            "builder": f"blueprint-pipeline {__version__}",
            "migrated_from": None,
        },
    }
    body = dict(model)
    model["model_id"] = "MODEL-" + sha256_bytes(canonical_json(body).encode())[:16]
    return model
