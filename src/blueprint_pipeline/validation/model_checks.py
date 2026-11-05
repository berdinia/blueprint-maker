"""Mechanical validation of the canonical model (schema, provenance, geometry)."""

from __future__ import annotations

import math
from typing import Any

from ..canonical.geometry import (
    bbox,
    finite_point,
    polygon_problems,
    polygons_overlap,
    seg_length,
    segments_properly_cross,
)
from ..evidence.measurements import values_agree
from ..schemas import schema_errors
from .checks import Check, CheckList

PLACED = {"ANCHORED", "LIKELY", "AMBIGUOUS"}
NOT_PLACED = {"UNPLACED", "DETAIL_ONLY"}


def _all_features(m: dict[str, Any]) -> list[tuple[str, str, dict[str, Any]]]:
    out = []
    for coll, key in (
        ("rooms", "room_id"),
        ("walls", "wall_id"),
        ("openings", "opening_id"),
        ("adjacency", "adjacency_id"),
    ):
        for f in m.get(coll, []):
            out.append((coll, f.get(key, "?"), f))
    return out


def check_model(
    m: dict[str, Any],
    *,
    known_source_ids: set[str] | None = None,
    known_origins: set[str] | None = None,
) -> list[Check]:
    cl = CheckList("canonical_model")
    errs = schema_errors(m, "canonical-model")
    cl.add("schema_valid", not errs, "schema valid" if not errs else "; ".join(errs[:8]))
    if errs:
        return cl.items  # later checks assume the schema shape

    cl.add("units_mm", m["units"] == "mm", f"units={m['units']!r}")

    # unique ids across every identified collection
    ids: dict[str, str] = {}
    dups: list[str] = []
    for coll, fid, _ in _all_features(m):
        if fid in ids:
            dups.append(f"{fid} ({ids[fid]}, {coll})")
        ids[fid] = coll
    for coll, key in (("measurements", "measurement_id"), ("conflicts", "conflict_id")):
        for x in m[coll]:
            if x[key] in ids:
                dups.append(f"{x[key]} ({ids[x[key]]}, {coll})")
            ids[x[key]] = coll
    cl.add("unique_ids", not dups, "all IDs unique" if not dups else f"duplicates: {dups[:10]}")

    # finite coordinates
    bad: list[str] = []
    for r in m["rooms"]:
        if r["polygon_mm"] and not all(finite_point(p) for p in r["polygon_mm"]):
            bad.append(r["room_id"])
    for w in m["walls"]:
        if not (finite_point(w["start_mm"]) and finite_point(w["end_mm"])):
            bad.append(w["wall_id"])
    cl.add("finite_coordinates", not bad, "all coordinates finite" if not bad else f"{bad}")

    zero = [w["wall_id"] for w in m["walls"] if seg_length(w["start_mm"], w["end_mm"]) < 1.0]
    cl.add("nonzero_wall_lengths", not zero, f"{len(m['walls'])} walls" if not zero else f"{zero}")

    # provenance coverage
    missing_prov = []
    unknown_src = []
    unknown_origin = []
    for _coll, fid, f in _all_features(m):
        if f["hypothetical"]:
            continue
        if not f["source_ids"] or not f["evidence_origin_ids"]:
            missing_prov.append(fid)
        if known_source_ids is not None:
            unknown_src += [f"{fid}:{s}" for s in f["source_ids"] if s not in known_source_ids]
        if known_origins is not None:
            unknown_origin += [
                f"{fid}:{o}" for o in f["evidence_origin_ids"] if o not in known_origins
            ]
    cl.add(
        "provenance_coverage",
        not missing_prov,
        "every non-hypothetical feature has sources and origins"
        if not missing_prov
        else f"no provenance: {missing_prov[:10]}",
    )
    if known_source_ids is not None:
        cl.add(
            "provenance_sources_registered",
            not unknown_src,
            "all source IDs registered" if not unknown_src else f"unregistered: {unknown_src[:10]}",
        )
    if known_origins is not None:
        cl.add(
            "provenance_origins_known",
            not unknown_origin,
            "all origin IDs known" if not unknown_origin else f"unknown: {unknown_origin[:10]}",
        )

    # unsupported features / placement consistency
    unsupported = []
    inconsistent = []
    for r in m["rooms"]:
        has_geo = r["polygon_mm"] is not None
        if has_geo and r["placement_state"] in NOT_PLACED:
            inconsistent.append(f"{r['room_id']}: geometry but {r['placement_state']}")
        if not has_geo and r["placement_state"] in PLACED:
            inconsistent.append(f"{r['room_id']}: {r['placement_state']} but no geometry")
        if has_geo and not r["hypothetical"] and not r["decision_ids"]:
            unsupported.append(f"{r['room_id']}: geometry without a placement decision")
        if r["hypothetical"] and not r.get("uncertainty"):
            unsupported.append(f"{r['room_id']}: hypothetical without stated uncertainty")
    for o in m["openings"]:
        placed = o["wall_id"] is not None
        if placed and o["placement_state"] in NOT_PLACED:
            inconsistent.append(f"{o['opening_id']}: on a wall but {o['placement_state']}")
        if not placed and o["placement_state"] in PLACED:
            inconsistent.append(f"{o['opening_id']}: {o['placement_state']} but no wall")
        if placed and o["offset_mm"] is None:
            inconsistent.append(f"{o['opening_id']}: on a wall without an offset")
        if placed and not o["hypothetical"] and not o["decision_ids"]:
            unsupported.append(f"{o['opening_id']}: placement without a decision")
    cl.add(
        "unsupported_features",
        not unsupported,
        "no unsupported geometry" if not unsupported else "; ".join(unsupported[:10]),
    )
    cl.add(
        "placement_state_consistency",
        not inconsistent,
        "placement states consistent" if not inconsistent else "; ".join(inconsistent[:10]),
    )

    # conflict references
    conflict_ids = {c["conflict_id"] for c in m["conflicts"]}
    dangling = [
        f"{fid}->{c}"
        for _, fid, f in _all_features(m)
        for c in f["conflict_refs"]
        if c not in conflict_ids
    ]
    cl.add(
        "conflict_references",
        not dangling,
        f"{len(conflict_ids)} conflicts, refs resolve" if not dangling else f"dangling {dangling}",
    )
    open_unreferenced = [
        c["conflict_id"]
        for c in m["conflicts"]
        if c["status"] == "OPEN"
        and c.get("affects")
        and not any(c["conflict_id"] in r["conflict_refs"] for r in m["rooms"])
    ]
    cl.add(
        "open_conflicts_attached",
        not open_unreferenced,
        "open conflicts are attached to affected features"
        if not open_unreferenced
        else f"open conflicts not referenced: {open_unreferenced}",
    )
    return cl.items


def check_geometry(m: dict[str, Any], *, tol_mm: float = 1.0) -> list[Check]:
    cl = CheckList("geometry")
    placed = [r for r in m["rooms"] if r["polygon_mm"]]
    if not placed:
        cl.skip("geometry_present", "no placed rooms; geometry checks not applicable")
        return cl.items
    probs = {r["room_id"]: polygon_problems(r["polygon_mm"]) for r in placed}
    bad = {k: v for k, v in probs.items() if v}
    cl.add(
        "polygon_validity",
        not bad,
        f"{len(placed)} valid polygons" if not bad else f"{bad}",
        refs=sorted(bad),
    )
    pts = [p for r in placed for p in r["polygon_mm"]] + [
        p for w in m["walls"] for p in (w["start_mm"], w["end_mm"])
    ]
    b = bbox(pts)
    w, h = b[2] - b[0], b[3] - b[1]
    cl.add(
        "geometry_bounds",
        w > 0 and h > 0 and all(math.isfinite(v) for v in b),
        f"bounds {w:.0f} x {h:.0f} mm",
        data={"bounds_mm": [round(v, 3) for v in b]},
    )
    overlaps = []
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            if bad.get(placed[i]["room_id"]) or bad.get(placed[j]["room_id"]):
                continue
            if polygons_overlap(placed[i]["polygon_mm"], placed[j]["polygon_mm"]):
                overlaps.append(f"{placed[i]['room_id']}/{placed[j]['room_id']}")
    cl.add("room_overlap", not overlaps, "no overlapping rooms" if not overlaps else f"{overlaps}")

    crossings = []
    walls = m["walls"]
    for i in range(len(walls)):
        for j in range(i + 1, len(walls)):
            a, bb = walls[i], walls[j]
            if segments_properly_cross(a["start_mm"], a["end_mm"], bb["start_mm"], bb["end_mm"]):
                crossings.append(f"{a['wall_id']}x{bb['wall_id']}")
    cl.add(
        "wall_intersections",
        not crossings,
        "no crossing walls" if not crossings else f"{crossings}",
    )

    wall_by_id = {w["wall_id"]: w for w in walls}
    att = []
    for o in m["openings"]:
        if o["wall_id"] is None:
            continue
        wl = wall_by_id.get(o["wall_id"])
        if wl is None:
            att.append(f"{o['opening_id']}: unknown wall {o['wall_id']}")
            continue
        length = seg_length(wl["start_mm"], wl["end_mm"])
        if o["offset_mm"] is None or o["width_mm"] is None:
            att.append(f"{o['opening_id']}: missing offset/width")
        elif o["offset_mm"] < -tol_mm or o["offset_mm"] + o["width_mm"] > length + tol_mm:
            att.append(f"{o['opening_id']}: span exceeds wall ({length:.0f} mm)")
    n_placed = sum(1 for o in m["openings"] if o["wall_id"])
    cl.add(
        "opening_attachment",
        not att,
        f"{n_placed} placed openings attached" if not att else "; ".join(att),
    )

    adj_bad = []
    for a in m["adjacency"]:
        if a["relation"] != "SHARES_WALL":
            continue
        if not any(set(a["room_ids"]) <= set(wl["room_ids"]) for wl in walls):
            adj_bad.append(a["adjacency_id"])
    cl.add(
        "adjacency_consistency",
        not adj_bad,
        "adjacency matches shared walls" if not adj_bad else f"{adj_bad}",
    )

    # measurement agreement: width = X extent, depth = Y extent (documented convention)
    meas_by_room: dict[str, list[dict[str, Any]]] = {}
    for me in m["measurements"]:
        meas_by_room.setdefault(me["subject_id"], []).append(me)
    disagree_unflagged: list[str] = []
    disagree_flagged: list[str] = []
    aspect: list[str] = []
    for r in placed:
        if len(r["polygon_mm"]) != 4:
            continue
        bx = bbox(r["polygon_mm"])
        ext = {"width": bx[2] - bx[0], "depth": bx[3] - bx[1]}
        got: dict[str, list[float]] = {}
        for me in meas_by_room.get(r["room_id"], []):
            if me["quantity"] in ext and me["value_mm"] is not None:
                got.setdefault(me["quantity"], []).append(me["value_mm"])
                if not values_agree(me["value_mm"], ext[me["quantity"]]):
                    msg = f"{r['room_id']} {me['quantity']} {me['value_mm']:g} vs model {ext[me['quantity']]:g}"
                    (disagree_flagged if r["conflict_refs"] else disagree_unflagged).append(msg)
        if "width" in got and "depth" in got and ext["depth"] > 0:
            measured = min(got["width"]) / min(got["depth"]) if min(got["depth"]) else 0
            model_ratio = ext["width"] / ext["depth"]
            if measured and not math.isclose(measured, model_ratio, rel_tol=0.05):
                (aspect if not r["conflict_refs"] else disagree_flagged).append(
                    f"{r['room_id']} aspect measured {measured:.3f} vs model {model_ratio:.3f}"
                )
    cl.add(
        "dimension_model_agreement",
        not disagree_unflagged,
        "measurements agree with placed geometry"
        if not disagree_unflagged
        else "; ".join(disagree_unflagged[:8]),
    )
    cl.add(
        "dimension_disagreement_with_open_conflict",
        not disagree_flagged,
        "none"
        if not disagree_flagged
        else "; ".join(disagree_flagged[:8]) + " (conflict recorded)",
        warn_only=True,
    )
    cl.add(
        "measured_aspect_ratio",
        not aspect,
        "aspect ratios agree" if not aspect else "; ".join(aspect),
    )
    seen: dict[tuple[Any, ...], str] = {}
    dup = []
    for me in m["measurements"]:
        k = (me["subject_id"], me["quantity"], me["value_mm"], me.get("origin_id"), me["source_id"])
        if k in seen:
            dup.append(f"{me['measurement_id']}={seen[k]}")
        seen[k] = me["measurement_id"]
    cl.add(
        "duplicate_dimensions",
        not dup,
        "no duplicate dimension records" if not dup else f"{dup}",
        warn_only=True,
    )
    return cl.items
