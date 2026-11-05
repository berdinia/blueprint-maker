"""Mechanical validation of the page layout and of each written artifact.

Layout checks inspect the scene. Artifact checks re-parse the files that were
actually written (SVG, DXF, PNG, PDF) and compare them with the scene, so a
writer bug cannot hide behind a correct scene.
"""

from __future__ import annotations

import io
import json
import math
import re
import xml.etree.ElementTree as ET
from typing import Any

import numpy as np
from PIL import Image

from ..canonical.geometry import bbox
from ..io.atomic import sha256_bytes
from ..rendering.layout import Rect
from ..rendering.scene import LAYERS, Scene, text_box
from .checks import Check, CheckList

SVG_NS = "{http://www.w3.org/2000/svg}"
GEOM_LAYERS = ("WALLS", "UNCERTAIN", "ROOMS", "OPENINGS")


def _region_rects(scene: Scene) -> dict[str, Rect]:
    L = scene.layout
    body = Rect(L.frame.x0, L.frame.y0, L.side.x0, L.title.y0)
    return {"geometry": L.geometry, "dimensions": body, "side": L.side, "title": L.title}


def _collisions(boxes: list[tuple[str, Rect]], pad: float = 0.0) -> list[str]:
    hits = []
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if boxes[i][1].intersects(boxes[j][1], -pad):
                hits.append(f"{boxes[i][0]!r} / {boxes[j][0]!r}")
    return hits


def _box_line_hit(b: Rect, x1: float, y1: float, x2: float, y2: float) -> bool:
    from ..rendering.scene import Line, _box_hits_line

    return _box_hits_line(b, Line(x1, y1, x2, y2, "", 0.0), pad=0.0)


# ---------------------------------------------------------------------------- layout
def check_layout(scene: Scene, model: dict[str, Any]) -> list[Check]:
    cl = CheckList("layout")
    placed = [r for r in model["rooms"] if r["polygon_mm"]]
    tr = scene.transform
    if not placed:
        cl.skip("transform", "no placed geometry; fit-to-page not applicable", required=False)
    else:
        cl.add(
            "transform", tr is not None, "uniform transform computed" if tr else "missing transform"
        )
    if tr is not None and placed:
        bad = []
        for r in placed:
            mb = bbox(r["polygon_mm"])
            pb = bbox([tr.to_page(x, y) for x, y in r["polygon_mm"]])
            sx = (pb[2] - pb[0]) / (mb[2] - mb[0]) if mb[2] > mb[0] else None
            sy = (pb[3] - pb[1]) / (mb[3] - mb[1]) if mb[3] > mb[1] else None
            for s in (sx, sy):
                if s is not None and not math.isclose(s, tr.scale, rel_tol=1e-9):
                    bad.append(r["room_id"])
        cl.add(
            "uniform_scale",
            not bad,
            f"scale {tr.scale:.6g} on both axes" if not bad else f"{bad}",
            data={"scale": tr.scale},
        )
        g = scene.geometry_page_bbox
        mw, mh = tr.max_x - tr.min_x, tr.max_y - tr.min_y
        if g and mw > 0 and mh > 0:
            ok = math.isclose((g.x1 - g.x0) / (g.y1 - g.y0), mw / mh, rel_tol=1e-6)
            cl.add(
                "aspect_ratio_preserved",
                ok,
                f"page {g.x1 - g.x0:.2f}x{g.y1 - g.y0:.2f} vs model {mw:.0f}x{mh:.0f}",
            )
        region = scene.layout.geometry
        cl.add(
            "geometry_inside_viewport",
            g is not None and region.contains(g, tol=1e-6),
            f"geometry {g.as_list() if g else None} within {region.as_list()}",
        )
        if g is not None and tr.degenerate is None:
            fill = max((g.x1 - g.x0) / region.w, (g.y1 - g.y0) / region.h)
            cl.add("fit_to_page", fill >= 0.98, f"geometry fills {fill:.1%} of the limiting axis")
        elif tr.degenerate:
            cl.add(
                "fit_to_page",
                True,
                f"degenerate geometry ({tr.degenerate}); centred",
                warn_only=True,
            )
        collapsed = []
        small = []
        for r in placed:
            pb = bbox([tr.to_page(x, y) for x, y in r["polygon_mm"]])
            side = min(pb[2] - pb[0], pb[3] - pb[1])
            if side < 0.5:
                collapsed.append(r["room_id"])
            elif side < 3.0:
                small.append(r["room_id"])
        cl.add(
            "no_collapsed_rooms",
            not collapsed,
            "no collapsed rooms" if not collapsed else f"{collapsed}",
        )
        cl.add(
            "room_minimum_size",
            not small,
            "rooms >= 3 mm on page" if not small else f"small: {small}",
            warn_only=True,
        )
        drawn = {ln.ref for ln in scene.lines if ln.layer in ("WALLS", "UNCERTAIN")}
        fully_open = {
            w["wall_id"]
            for w in model["walls"]
            if sum(o["width_mm"] or 0 for o in model["openings"] if o["wall_id"] == w["wall_id"])
            >= math.hypot(w["end_mm"][0] - w["start_mm"][0], w["end_mm"][1] - w["start_mm"][1])
            - 1e-6
        }
        missing = sorted({w["wall_id"] for w in model["walls"]} - drawn - fully_open)
        cl.add(
            "walls_drawn",
            not missing,
            f"{len(model['walls'])} walls drawn" if not missing else f"missing {missing}",
        )

    regions = _region_rects(scene)
    frame = scene.layout.frame
    clipped = []
    for t in scene.texts:
        b = t.box()
        if not frame.contains(b) or not regions[t.region].contains(b):
            clipped.append(f"{t.text[:30]!r} ({t.region})")
    cl.add(
        "text_clipping",
        not clipped,
        "all text inside its region" if not clipped else "; ".join(clipped[:8]),
    )
    boxes = [(t.text[:24], t.box()) for t in scene.texts]
    hits = _collisions(boxes)
    cl.add(
        "text_text_collisions", not hits, "no text collisions" if not hits else "; ".join(hits[:8])
    )
    wall_hits = []
    for t in scene.texts:
        if t.region != "geometry":
            continue
        b = t.box()
        for ln in scene.lines:
            if ln.layer in ("WALLS", "UNCERTAIN", "OPENINGS") and _box_line_hit(
                b, ln.x1, ln.y1, ln.x2, ln.y2
            ):
                wall_hits.append(f"{t.text[:24]!r}~{ln.ref}")
                break
    cl.add(
        "text_wall_collisions",
        not wall_hits,
        "labels clear of walls" if not wall_hits else "; ".join(wall_hits[:8]),
    )
    title = scene.layout.title
    intrusions = [
        t.text[:24] for t in scene.texts if t.region != "title" and t.box().intersects(title)
    ]
    intrusions += [
        str(ln.ref)
        for ln in scene.lines
        if ln.layer not in ("FRAME",)
        and Rect(
            min(ln.x1, ln.x2), min(ln.y1, ln.y2), max(ln.x1, ln.x2), max(ln.y1, ln.y2)
        ).intersects(title)
    ]
    if scene.geometry_page_bbox is not None and (
        scene.geometry_page_bbox.intersects(title)
        or scene.geometry_page_bbox.intersects(scene.layout.side)
    ):
        intrusions.append("geometry")
    cl.add(
        "title_block_separation",
        not intrusions,
        "title block and side column clear" if not intrusions else f"{intrusions[:8]}",
    )
    outside = []
    for ln in scene.lines:
        for x, y in ((ln.x1, ln.y1), (ln.x2, ln.y2)):
            if not (
                frame.x0 - 1e-6 <= x <= frame.x1 + 1e-6 and frame.y0 - 1e-6 <= y <= frame.y1 + 1e-6
            ):
                outside.append(str(ln.ref or ln.layer))
    for p in scene.polys:
        for x, y in p.points:
            if not (
                frame.x0 - 1e-6 <= x <= frame.x1 + 1e-6 and frame.y0 - 1e-6 <= y <= frame.y1 + 1e-6
            ):
                outside.append(str(p.ref or p.layer))
    cl.add(
        "page_edge_clipping",
        not outside,
        "all strokes inside the frame" if not outside else f"{sorted(set(outside))[:8]}",
    )
    if placed:
        omitted = scene.label_omitted
        cl.add(
            "room_labels_placed",
            not omitted,
            f"{len(placed)} rooms labelled"
            if not omitted
            else f"labels omitted (too small): {omitted}",
            warn_only=True,
        )
        dim_refs = [t.ref for t in scene.texts if t.layer == "DIMENSIONS"]
        dups = sorted({str(r) for r in dim_refs if dim_refs.count(r) > 1})
        cl.add(
            "duplicate_dimensions", not dups, "each dimension drawn once" if not dups else f"{dups}"
        )
    return cl.items


# ---------------------------------------------------------------------------- SVG
def _num(s: str | None) -> float:
    return float(s) if s is not None else float("nan")


def check_svg(data: bytes, scene: Scene, model: dict[str, Any]) -> list[Check]:
    cl = CheckList("svg")
    try:
        root = ET.fromstring(data)  # noqa: S314 - SVG produced by this program, not external input
    except ET.ParseError as exc:
        cl.add("xml_valid", False, f"XML parse error: {exc}")
        return cl.items
    cl.add("xml_valid", True, "well-formed XML")
    ok_root = root.tag == f"{SVG_NS}svg"
    vb = [float(v) for v in (root.get("viewBox") or "0 0 0 0").split()]
    cl.add(
        "viewbox",
        ok_root
        and vb == [0.0, 0.0, scene.page_w, scene.page_h]
        and root.get("width") == f"{scene.page_w:g}mm",
        f"viewBox {vb}",
    )
    meta_el = root.find(f"{SVG_NS}metadata")
    meta: dict[str, Any] = {}
    if meta_el is not None and meta_el.text:
        try:
            meta = json.loads(meta_el.text)
        except json.JSONDecodeError:
            meta = {}
    cl.add(
        "scene_binding",
        meta.get("scene_sha256") == scene.sha256(),
        "SVG generated from the current scene"
        if meta.get("scene_sha256") == scene.sha256()
        else "SVG does not match the current scene (stale render)",
    )
    counts: dict[str, int] = {}
    pts: list[tuple[float, float]] = []
    texts: list[tuple[str, Rect]] = []
    room_polys: dict[str, list[tuple[float, float]]] = {}
    for g in root.findall(f"{SVG_NS}g"):
        layer = g.get("data-layer", "")
        for el in g:
            tag = el.tag.replace(SVG_NS, "")
            counts[layer] = counts.get(layer, 0) + 1
            if tag == "line":
                pts += [
                    (_num(el.get("x1")), _num(el.get("y1"))),
                    (_num(el.get("x2")), _num(el.get("y2"))),
                ]
            elif tag in ("polygon", "polyline"):
                pp: list[tuple[float, float]] = []
                for pair in (el.get("points") or "").split():
                    xs_, ys_ = pair.split(",")
                    pp.append((float(xs_), float(ys_)))
                pts += pp
                if tag == "polygon" and layer in ("ROOMS", "UNCERTAIN") and el.get("data-ref"):
                    room_polys[el.get("data-ref", "")] = pp
            elif tag == "text":
                txt = el.text or ""
                texts.append(
                    (
                        txt,
                        text_box(
                            _num(el.get("x")),
                            _num(el.get("y")),
                            txt,
                            _num(el.get("font-size")),
                            el.get("text-anchor", "start"),
                        ),
                    )
                )
    exp = scene.expected_counts()
    cl.add(
        "entity_counts",
        counts == exp,
        f"{sum(counts.values())} elements match scene"
        if counts == exp
        else f"svg {counts} != scene {exp}",
    )
    W, H = scene.page_w, scene.page_h
    bad = [
        p
        for p in pts
        if not (math.isfinite(p[0]) and math.isfinite(p[1]) and 0 <= p[0] <= W and 0 <= p[1] <= H)
    ]
    cl.add(
        "geometry_inside_viewbox",
        not bad,
        f"{len(pts)} points inside viewBox" if not bad else f"{len(bad)} outside",
    )
    page = Rect(0, 0, W, H)
    clipped = [t for t, b in texts if not page.contains(b)]
    cl.add(
        "text_inside_viewbox",
        not clipped,
        "all text inside the sheet" if not clipped else f"{clipped[:5]}",
    )
    hits = _collisions(texts)
    cl.add(
        "text_collisions_in_file",
        not hits,
        "no overlapping text in the file" if not hits else "; ".join(hits[:5]),
    )
    tr = meta.get("transform")
    mismatched = []
    if tr:
        for r in model["rooms"]:
            rp = room_polys.get(r["room_id"])
            if not r["polygon_mm"] or not rp:
                continue
            mb, pb = bbox(r["polygon_mm"]), bbox(rp)
            for m_ext, p_ext in ((mb[2] - mb[0], pb[2] - pb[0]), (mb[3] - mb[1], pb[3] - pb[1])):
                if m_ext > 0 and abs(p_ext / m_ext - tr["scale"]) > 2e-3 / m_ext + 1e-9:
                    mismatched.append(r["room_id"])
        cl.add(
            "uniform_scale_in_file",
            not mismatched,
            "room polygons scaled uniformly" if not mismatched else f"{sorted(set(mismatched))}",
        )
    return cl.items


# ---------------------------------------------------------------------------- DXF
def check_dxf(data: bytes, scene: Scene, model: dict[str, Any]) -> list[Check]:
    import ezdxf

    cl = CheckList("dxf")
    try:
        doc = ezdxf.read(io.StringIO(data.decode("utf-8")))
    except Exception as exc:  # ezdxf raises several unrelated types
        cl.add("dxf_reopen", False, f"cannot reopen: {type(exc).__name__}: {exc}")
        return cl.items
    msp = doc.modelspace()
    ents = list(msp)
    cl.add("dxf_reopen", True, f"reopened, {len(ents)} entities")
    cl.add(
        "units_declared",
        doc.header.get("$INSUNITS") == 4,
        f"$INSUNITS={doc.header.get('$INSUNITS')} (4 = mm)",
    )
    exp = scene.expected_counts()
    present = {layer.dxf.name for layer in doc.layers}
    missing = sorted(set(exp) - present)
    cl.add(
        "required_layers",
        not missing,
        f"layers {sorted(exp)}" if not missing else f"missing {missing}",
    )
    counts: dict[str, int] = {}
    for e in ents:
        counts[e.dxf.layer] = counts.get(e.dxf.layer, 0) + 1
    cl.add(
        "entity_counts",
        counts == exp,
        "entity counts match scene"
        if counts == exp
        else f"dxf {dict(sorted(counts.items()))} != scene {exp}",
    )
    coords: list[tuple[float, float]] = []
    geom: list[tuple[float, float]] = []
    polys: dict[int, list[tuple[float, float]]] = {}
    for e in ents:
        t = e.dxftype()
        if t == "LINE":
            p = [(e.dxf.start.x, e.dxf.start.y), (e.dxf.end.x, e.dxf.end.y)]
        elif t == "LWPOLYLINE":
            p = [(v[0], v[1]) for v in e.get_points("xy")]  # type: ignore[attr-defined]
            if e.dxf.layer in ("ROOMS", "UNCERTAIN") and e.closed:  # type: ignore[attr-defined]
                polys[len(polys)] = p
        elif t == "TEXT":
            p = (
                [(e.dxf.align_point.x, e.dxf.align_point.y)]
                if e.dxf.hasattr("align_point")
                else [(e.dxf.insert.x, e.dxf.insert.y)]
            )
        else:
            continue
        coords += p
        if e.dxf.layer in ("WALLS", "ROOMS"):
            geom += p
    bad = [c for c in coords if not (math.isfinite(c[0]) and math.isfinite(c[1]))]
    cl.add(
        "finite_coordinates",
        not bad,
        f"{len(coords)} coordinates finite" if not bad else f"{len(bad)} non-finite",
    )
    placed = [r for r in model["rooms"] if r["polygon_mm"]]
    if placed and geom:
        mb = bbox(
            [tuple(p) for r in placed for p in r["polygon_mm"]]
            + [tuple(p) for w in model["walls"] for p in (w["start_mm"], w["end_mm"])]
        )
        out = [
            g
            for g in geom
            if not (mb[0] - 1e-3 <= g[0] <= mb[2] + 1e-3 and mb[1] - 1e-3 <= g[1] <= mb[3] + 1e-3)
        ]
        cl.add(
            "geometry_within_model_bounds",
            not out,
            "walls/rooms within model bounds (1:1 mm)" if not out else f"{len(out)} points outside",
        )
        dxf_sets = [sorted((round(x, 3), round(y, 3)) for x, y in p) for p in polys.values()]
        missing_rooms = [
            r["room_id"]
            for r in placed
            if sorted((round(x, 3), round(y, 3)) for x, y in r["polygon_mm"]) not in dxf_sets
        ]
        cl.add(
            "room_polygons_exact",
            not missing_rooms,
            "room polygons at exact model coordinates"
            if not missing_rooms
            else f"not exact: {missing_rooms}",
        )
    elif placed:
        cl.add("geometry_within_model_bounds", False, "no wall/room entities found")
    return cl.items


# ---------------------------------------------------------------------------- PNG
def check_png(data: bytes, scene: Scene, *, dpi: int, svg_sha256: str) -> list[Check]:
    cl = CheckList("png")
    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # Pillow raises several types
        cl.add("png_decode", False, f"cannot decode: {exc}")
        return cl.items
    k = dpi / 25.4
    W, H = round(scene.page_w * k), round(scene.page_h * k)
    cl.add("dimensions", img.size == (W, H), f"{img.size[0]}x{img.size[1]} px (expected {W}x{H})")
    info = getattr(img, "text", {}) or {}
    cl.add(
        "svg_hash_binding",
        info.get("blueprint:source_svg_sha256") == svg_sha256,
        "PNG rendered from the current SVG"
        if info.get("blueprint:source_svg_sha256") == svg_sha256
        else "PNG is not bound to the current SVG (stale)",
    )
    cl.add(
        "scene_binding",
        info.get("blueprint:scene_sha256") == scene.sha256(),
        "PNG rendered from the current scene",
    )
    arr = np.asarray(img.convert("L"))
    ink = arr < 200
    total_ink = int(ink.sum())
    g = scene.geometry_page_bbox
    if g is None:
        cl.add("non_background_pixels", total_ink > 0, f"{total_ink} ink pixels (no geometry)")
        return cl.items
    # Crop to the expected geometry bbox plus a tolerance smaller than the
    # drafting gap, so annotation and dimension strokes cannot mask missing or
    # misplaced geometry.
    tol = max(2, round(1.2 * k))
    rx0, ry0 = max(0, math.floor(g.x0 * k) - tol), max(0, math.floor(g.y0 * k) - tol)
    rx1, ry1 = (
        min(W, math.ceil(g.x1 * k) + tol + 1),
        min(H, math.ceil(g.y1 * k) + tol + 1),
    )
    region_ink = ink[ry0:ry1, rx0:rx1]
    n = int(region_ink.sum())
    exp_w, exp_h = (g.x1 - g.x0) * k, (g.y1 - g.y0) * k
    cl.add(
        "non_background_pixels",
        n >= max(50, 0.5 * (exp_w + exp_h)),
        f"{n} ink pixels around the geometry",
    )
    if n == 0:
        cl.add("non_background_bbox", False, "no ink where geometry is expected")
        return cl.items
    ys, xs = np.nonzero(region_ink)
    bx0, bx1 = xs.min() + rx0, xs.max() + rx0
    by0, by1 = ys.min() + ry0, ys.max() + ry0
    ok_bbox = (
        abs(bx0 - g.x0 * k) <= tol
        and abs(bx1 - g.x1 * k) <= tol
        and abs(by0 - g.y0 * k) <= tol
        and abs(by1 - g.y1 * k) <= tol
    )
    cl.add(
        "non_background_bbox",
        ok_bbox,
        f"ink bbox [{bx0},{by0},{bx1},{by1}] vs geometry [{g.x0 * k:.0f},{g.y0 * k:.0f},{g.x1 * k:.0f},{g.y1 * k:.0f}] px (tol {tol})",
    )
    # Spatial distribution: every grid cell crossed by a geometry stroke must contain ink.
    gx, gy = 8, 8
    cw, ch = (g.x1 - g.x0) / gx or 1, (g.y1 - g.y0) / gy or 1
    expected: set[tuple[int, int]] = set()

    def mark(x1: float, y1: float, x2: float, y2: float) -> None:
        steps = max(2, int(math.hypot(x2 - x1, y2 - y1) / (min(cw, ch) / 4)))
        for i in range(steps + 1):
            x = x1 + (x2 - x1) * i / steps
            y = y1 + (y2 - y1) * i / steps
            cx = min(gx - 1, max(0, int((x - g.x0) / cw)))
            cy = min(gy - 1, max(0, int((y - g.y0) / ch)))
            expected.add((cx, cy))

    for ln in scene.lines:
        if ln.layer in ("WALLS", "UNCERTAIN") and not ln.dash:
            mark(ln.x1, ln.y1, ln.x2, ln.y2)
    inked = 0
    for cx, cy in expected:
        x0 = int((g.x0 + cx * cw) * k) - 1
        y0 = int((g.y0 + cy * ch) * k) - 1
        x1 = int((g.x0 + (cx + 1) * cw) * k) + 2
        y1 = int((g.y0 + (cy + 1) * ch) * k) + 2
        if ink[max(0, y0) : y1, max(0, x0) : x1].any():
            inked += 1
    frac = inked / len(expected) if expected else 1.0
    cl.add(
        "spatial_distribution",
        frac >= 0.95,
        f"{inked}/{len(expected)} expected grid cells contain ink",
    )
    min_px = 50
    cl.add(
        "minimum_visible_geometry",
        (bx1 - bx0) >= min_px or (by1 - by0) >= min_px,
        f"visible geometry {bx1 - bx0}x{by1 - by0} px (min {min_px})",
    )
    return cl.items


# ---------------------------------------------------------------------------- PDF
def check_pdf(data: bytes, scene: Scene) -> list[Check]:
    cl = CheckList("pdf")
    cl.add("header", data.startswith(b"%PDF-1."), "PDF header present")
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    xref_ok = bool(m) and data[int(m.group(1)) : int(m.group(1)) + 4] == b"xref" if m else False
    cl.add("xref", xref_ok, "startxref points at the xref table" if xref_ok else "broken xref")
    offsets_ok = True
    if xref_ok and m:
        tbl = data[int(m.group(1)) :]
        entries = re.findall(rb"(\d{10}) 00000 n ", tbl)
        for i, off in enumerate(entries, start=1):
            if not data[int(off) :].startswith(f"{i} 0 obj".encode()):
                offsets_ok = False
    cl.add(
        "object_offsets",
        offsets_ok,
        "object offsets valid" if offsets_ok else "object offsets invalid",
    )
    k = 72 / 25.4
    mb = re.search(rb"/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]", data)
    size_ok = (
        bool(mb)
        and math.isclose(float(mb.group(1)), scene.page_w * k, abs_tol=0.01)
        and math.isclose(float(mb.group(2)), scene.page_h * k, abs_tol=0.01)
        if mb
        else False
    )
    cl.add(
        "page_size",
        size_ok,
        f"MediaBox {mb.group(1).decode() if mb else '?'} x {mb.group(2).decode() if mb else '?'} pt",
    )
    pages = len(re.findall(rb"/Type /Page\b", data))
    cl.add("page_count", pages == 1, f"{pages} page(s)")
    tj = len(re.findall(rb"\) Tj ET", data))
    cl.add("text_count", tj == len(scene.texts), f"{tj} text runs (scene {len(scene.texts)})")
    return cl.items


def svg_sha(data: bytes) -> str:
    return sha256_bytes(data)


__all__ = ["LAYERS", "check_dxf", "check_layout", "check_pdf", "check_png", "check_svg", "svg_sha"]
