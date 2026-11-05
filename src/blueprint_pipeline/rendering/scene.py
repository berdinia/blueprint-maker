"""Deterministic scene construction.

The scene is the single intermediate representation consumed by the SVG, PNG
and PDF writers (page space) and, via the inverse transform, the DXF writer
(model space). Building it is pure: the same model and settings always yield
the same scene, in the same order.

Text metrics use a monospace model: advance width 0.6 em, ascent 0.75 em,
descent 0.25 em. All writers use monospace fonts (Courier in PDF), so the
layout's collision checks hold for every output.
"""

from __future__ import annotations

import math
import textwrap
from dataclasses import asdict, dataclass, field
from typing import Any

from ..canonical.geometry import bbox, point_in_polygon, seg_length, segments_intersect
from ..config.settings import RenderSettings
from ..io.atomic import canonical_json, sha256_bytes
from .layout import PageLayout, Rect, Transform, compute_layout, fit_transform

CHAR_W = 0.6
ASCENT = 0.75
DESCENT = 0.25

LAYERS = (
    "FRAME",
    "WALLS",
    "ROOMS",
    "OPENINGS",
    "ROOM_LABELS",
    "DIMENSIONS",
    "UNCERTAIN",
    "ANNOTATION",
)

WEIGHT = {"ANCHORED": 0.50, "LIKELY": 0.35, "AMBIGUOUS": 0.25}
EXTENSION_GAP_MM = 2.0


@dataclass(frozen=True)
class RenderOverrides:
    """Presentation-only adjustments made by bounded repair. Never facts."""

    label_scale: float = 1.0
    prefer_tags: bool = False
    line_weight_scale: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Line:
    x1: float
    y1: float
    x2: float
    y2: float
    layer: str
    weight: float
    dash: tuple[float, float] | None = None
    ref: str | None = None


@dataclass
class Poly:
    points: list[tuple[float, float]]
    layer: str
    weight: float
    closed: bool = True
    dash: tuple[float, float] | None = None
    ref: str | None = None


@dataclass
class Text:
    x: float
    y: float
    text: str
    size: float
    layer: str
    region: str  # geometry | dimensions | side | title
    anchor: str = "start"  # start | middle | end
    ref: str | None = None

    def box(self) -> Rect:
        return text_box(self.x, self.y, self.text, self.size, self.anchor)


def text_width(text: str, size: float) -> float:
    return len(text) * size * CHAR_W


def text_box(x: float, y: float, text: str, size: float, anchor: str = "start") -> Rect:
    w = text_width(text, size)
    x0 = x - (w / 2 if anchor == "middle" else w if anchor == "end" else 0.0)
    return Rect(x0, y - ASCENT * size, x0 + w, y + DESCENT * size)


@dataclass
class Scene:
    page_w: float
    page_h: float
    layout: PageLayout
    transform: Transform | None
    lines: list[Line] = field(default_factory=list)
    polys: list[Poly] = field(default_factory=list)
    texts: list[Text] = field(default_factory=list)
    room_tags: dict[str, str] = field(default_factory=dict)
    label_omitted: list[str] = field(default_factory=list)
    geometry_page_bbox: Rect | None = None
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        def r(v: Any) -> Any:
            if isinstance(v, float):
                return round(v, 4)
            if isinstance(v, (list, tuple)):
                return [r(x) for x in v]
            if isinstance(v, dict):
                return {k: r(x) for k, x in v.items()}
            return v

        return r(
            {
                "page": [self.page_w, self.page_h],
                "layout": {
                    name: getattr(self.layout, name).as_list()
                    for name in (
                        "page",
                        "frame",
                        "geometry",
                        "dim_bottom",
                        "dim_left",
                        "side",
                        "title",
                    )
                },
                "transform": asdict(self.transform) if self.transform else None,
                "lines": [asdict(x) for x in self.lines],
                "polys": [asdict(x) for x in self.polys],
                "texts": [asdict(x) for x in self.texts],
                "room_tags": self.room_tags,
                "label_omitted": self.label_omitted,
                "geometry_page_bbox": self.geometry_page_bbox.as_list()
                if self.geometry_page_bbox
                else None,
                "meta": self.meta,
            }
        )

    def sha256(self) -> str:
        return sha256_bytes(canonical_json(self.to_dict()).encode())

    def expected_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        items: list[Line | Poly | Text] = [*self.lines, *self.polys, *self.texts]
        for item in items:
            counts[item.layer] = counts.get(item.layer, 0) + 1
        return dict(sorted(counts.items()))


def _ascii_safe(s: str) -> str:
    return "".join(ch if 32 <= ord(ch) < 127 or ord(ch) > 159 else "?" for ch in s)


def _box_inside_polygon(box: Rect, poly: list[tuple[float, float]], inset: float = 0.4) -> bool:
    corners = [
        (box.x0 - inset, box.y0 - inset),
        (box.x1 + inset, box.y0 - inset),
        (box.x1 + inset, box.y1 + inset),
        (box.x0 - inset, box.y1 + inset),
    ]
    if not all(point_in_polygon(c, poly) for c in corners):
        return False
    edges = [(corners[i], corners[(i + 1) % 4]) for i in range(4)]
    for i in range(len(poly)):
        a, b = poly[i], poly[(i + 1) % len(poly)]
        for c, d in edges:
            if segments_intersect(a, b, c, d):
                return False
    return True


def _box_hits_line(box: Rect, ln: Line, pad: float = 0.3) -> bool:
    x0, y0, x1, y1 = box.x0 - pad, box.y0 - pad, box.x1 + pad, box.y1 + pad
    if (
        max(ln.x1, ln.x2) < x0
        or min(ln.x1, ln.x2) > x1
        or max(ln.y1, ln.y2) < y0
        or min(ln.y1, ln.y2) > y1
    ):
        return False
    p, q = (ln.x1, ln.y1), (ln.x2, ln.y2)
    if x0 <= p[0] <= x1 and y0 <= p[1] <= y1:
        return True
    corners = [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return any(segments_intersect(p, q, corners[i], corners[(i + 1) % 4]) for i in range(4))


class _Placer:
    """Collision-aware text placement shared by all regions."""

    def __init__(self, scene: Scene) -> None:
        self.scene = scene
        self.boxes: list[Rect] = []

    def free(self, box: Rect, *, check_walls: bool, pad: float = 0.6) -> bool:
        if any(box.intersects(b, pad) for b in self.boxes):
            return False
        if check_walls:
            for ln in self.scene.lines:
                if ln.layer in ("WALLS", "OPENINGS", "UNCERTAIN", "DIMENSIONS") and _box_hits_line(
                    box, ln
                ):
                    return False
        return True

    def put(self, t: Text) -> Text:
        self.boxes.append(t.box())
        self.scene.texts.append(t)
        return t


def _measure_summary(model: dict[str, Any], room_id: str) -> str:
    vals: dict[str, list[float]] = {}
    for m in model["measurements"]:
        if (
            m["subject_id"] == room_id
            and m["value_mm"] is not None
            and m["quantity"] in ("width", "depth")
        ):
            vals.setdefault(m["quantity"], [])
            if m["value_mm"] not in vals[m["quantity"]]:
                vals[m["quantity"]].append(m["value_mm"])
    parts = []
    for q, label in (("width", "W"), ("depth", "D")):
        if q in vals:
            vs = sorted(vals[q])
            parts.append(f"{label} {'/'.join(f'{v:g}' for v in vs)}{'?' if len(vs) > 1 else ''}")
    return " ".join(parts) if parts else "no measurements"


def build_scene(
    model: dict[str, Any],
    r: RenderSettings,
    overrides: RenderOverrides | None = None,
    *,
    title: str = "SCHEMATIC PLAN",
) -> Scene:
    ov = overrides or RenderOverrides()
    layout = compute_layout(r)
    rooms = sorted(model["rooms"], key=lambda x: x["room_id"])
    placed = [x for x in rooms if x["polygon_mm"]]
    walls = sorted(model["walls"], key=lambda w: w["wall_id"])
    pts = [tuple(p) for x in placed for p in x["polygon_mm"]] + [
        tuple(p) for w in walls for p in (w["start_mm"], w["end_mm"])
    ]
    transform = fit_transform(bbox(pts), layout.geometry) if pts else None
    scene = Scene(r.page_w_mm, r.page_h_mm, layout, transform)
    lw = ov.line_weight_scale
    placer = _Placer(scene)

    # Frame and region borders (ANNOTATION hierarchy: thin).
    f = layout.frame
    scene.polys.append(
        Poly([(f.x0, f.y0), (f.x1, f.y0), (f.x1, f.y1), (f.x0, f.y1)], "FRAME", 0.35 * lw)
    )
    t = layout.title
    scene.lines.append(Line(t.x0, t.y0, t.x1, t.y0, "FRAME", 0.25 * lw))
    s = layout.side
    scene.lines.append(Line(s.x0, s.y0, s.x0, s.y1, "FRAME", 0.25 * lw))

    tags = {room["room_id"]: f"R{i}" for i, room in enumerate(rooms, start=1)}
    scene.room_tags = tags

    if transform is not None:
        tp = transform.to_page
        # -- walls, split around placed openings --------------------------------
        open_by_wall: dict[str, list[dict[str, Any]]] = {}
        for o in model["openings"]:
            if o["wall_id"]:
                open_by_wall.setdefault(o["wall_id"], []).append(o)
        for w in walls:
            a, b = w["start_mm"], w["end_mm"]
            length = seg_length(a, b)
            ux, uy = (b[0] - a[0]) / length, (b[1] - a[1]) / length
            state = w["placement_state"]
            uncertain = w["hypothetical"] or state == "AMBIGUOUS"
            layer = "UNCERTAIN" if uncertain else "WALLS"
            weight = WEIGHT.get(state, 0.25) * lw
            dash = (1.5, 1.0) if uncertain else None
            cuts = sorted(
                (o["offset_mm"], o["offset_mm"] + o["width_mm"], o)
                for o in open_by_wall.get(w["wall_id"], [])
            )
            pos = 0.0
            pieces = []
            for c0, c1, o in cuts:
                if c0 > pos:
                    pieces.append((pos, c0))
                pos = max(pos, c1)
                # opening symbol
                p0 = tp(a[0] + ux * c0, a[1] + uy * c0)
                p1 = tp(a[0] + ux * c1, a[1] + uy * c1)
                if o["kind"] == "door":
                    # leaf perpendicular at the start of the gap + swing arc
                    nx, ny = -(p1[1] - p0[1]), (p1[0] - p0[0])
                    leaf = (p0[0] + nx, p0[1] + ny)
                    scene.lines.append(
                        Line(
                            p0[0],
                            p0[1],
                            leaf[0],
                            leaf[1],
                            "OPENINGS",
                            0.18 * lw,
                            ref=o["opening_id"],
                        )
                    )
                    radius = math.hypot(nx, ny)
                    start = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
                    end = math.atan2(ny, nx)
                    if end - start > math.pi:
                        end -= 2 * math.pi
                    if start - end > math.pi:
                        end += 2 * math.pi
                    arc = [
                        (
                            p0[0] + radius * math.cos(start + (end - start) * k / 8),
                            p0[1] + radius * math.sin(start + (end - start) * k / 8),
                        )
                        for k in range(9)
                    ]
                    scene.polys.append(
                        Poly(arc, "OPENINGS", 0.13 * lw, closed=False, ref=o["opening_id"])
                    )
                else:
                    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
                    norm = math.hypot(dx, dy) or 1.0
                    ox, oy = -dy / norm * 0.6, dx / norm * 0.6
                    for sgn in (-1, 1):
                        scene.lines.append(
                            Line(
                                p0[0] + sgn * ox,
                                p0[1] + sgn * oy,
                                p1[0] + sgn * ox,
                                p1[1] + sgn * oy,
                                "OPENINGS",
                                0.18 * lw,
                                ref=o["opening_id"],
                            )
                        )
            if pos < length:
                pieces.append((pos, length))
            for c0, c1 in pieces:
                q0 = tp(a[0] + ux * c0, a[1] + uy * c0)
                q1 = tp(a[0] + ux * c1, a[1] + uy * c1)
                scene.lines.append(
                    Line(q0[0], q0[1], q1[0], q1[1], layer, weight, dash, ref=w["wall_id"])
                )

        # -- room outlines -------------------------------------------------------
        page_polys: dict[str, list[tuple[float, float]]] = {}
        for room in placed:
            pp = [tp(x, y) for x, y in room["polygon_mm"]]
            page_polys[room["room_id"]] = pp
            uncertain = room["hypothetical"] or room["placement_state"] == "AMBIGUOUS"
            scene.polys.append(
                Poly(
                    pp,
                    "UNCERTAIN" if uncertain else "ROOMS",
                    0.10 * lw,
                    dash=(1.5, 1.0) if uncertain else None,
                    ref=room["room_id"],
                )
            )
        gb = bbox(
            [p for pp in page_polys.values() for p in pp]
            + [(ln.x1, ln.y1) for ln in scene.lines if ln.layer in ("WALLS", "UNCERTAIN")]
            + [(ln.x2, ln.y2) for ln in scene.lines if ln.layer in ("WALLS", "UNCERTAIN")]
        )
        scene.geometry_page_bbox = Rect(*gb)

        # -- overall dimensions (model extents, not measurements) -----------------
        g = scene.geometry_page_bbox
        db, dl = layout.dim_bottom, layout.dim_left
        yd = db.y0 + db.h * 0.45
        xd = dl.x1 - dl.w * 0.45
        dim_sz = max(r.min_label_h_mm, min(r.note_h_mm, db.h * 0.3))
        gap = EXTENSION_GAP_MM  # drafting gap between geometry and extension lines
        scene.lines += [
            Line(g.x0, g.y1 + gap, g.x0, yd + 1.2, "DIMENSIONS", 0.13 * lw, ref="DIM-X"),
            Line(g.x1, g.y1 + gap, g.x1, yd + 1.2, "DIMENSIONS", 0.13 * lw, ref="DIM-X"),
            Line(g.x0, yd, g.x1, yd, "DIMENSIONS", 0.18 * lw, ref="DIM-X"),
            Line(g.x0 - gap, g.y0, xd - 1.2, g.y0, "DIMENSIONS", 0.13 * lw, ref="DIM-Y"),
            Line(g.x0 - gap, g.y1, xd - 1.2, g.y1, "DIMENSIONS", 0.13 * lw, ref="DIM-Y"),
            Line(xd, g.y0, xd, g.y1, "DIMENSIONS", 0.18 * lw, ref="DIM-Y"),
        ]
        for x in (g.x0, g.x1):
            scene.lines.append(
                Line(x - 0.8, yd + 0.8, x + 0.8, yd - 0.8, "DIMENSIONS", 0.18 * lw, ref="DIM-X")
            )
        for y in (g.y0, g.y1):
            scene.lines.append(
                Line(xd - 0.8, y + 0.8, xd + 0.8, y - 0.8, "DIMENSIONS", 0.18 * lw, ref="DIM-Y")
            )
        model_w = transform.max_x - transform.min_x
        model_h = transform.max_y - transform.min_y
        placer.put(
            Text(
                (g.x0 + g.x1) / 2,
                yd + DESCENT * dim_sz + dim_sz * 1.25,
                f"{model_w:.0f}",
                dim_sz,
                "DIMENSIONS",
                "dimensions",
                "middle",
                ref="DIM-X",
            )
        )
        # The vertical dimension's text is written horizontally, centred on the
        # dimension line just above its upper tick (inside the top band), so it
        # never needs rotation and cannot run off the left edge of the sheet.
        placer.put(
            Text(
                xd,
                g.y0 - 1.6 - DESCENT * dim_sz,
                f"{model_h:.0f}",
                dim_sz,
                "DIMENSIONS",
                "dimensions",
                "middle",
                ref="DIM-Y",
            )
        )

        # -- room labels -----------------------------------------------------------
        base = r.label_h_mm * ov.label_scale
        sizes = []
        sz = base
        while sz >= r.min_label_h_mm - 1e-9:
            sizes.append(round(sz, 3))
            sz *= 0.85
        if not sizes or sizes[-1] > r.min_label_h_mm:
            sizes.append(r.min_label_h_mm)
        for room in placed:
            rid = room["room_id"]
            pp = page_polys[rid]
            bx = bbox(pp)
            cx, cy = (bx[0] + bx[2]) / 2, (bx[1] + bx[3]) / 2
            name = room["display_name"] or "UNNAMED"
            texts = [tags[rid]] if ov.prefer_tags else [_ascii_safe(name), tags[rid]]
            offsets = [(0.0, 0.0)] + [
                (fx * (bx[2] - bx[0]), fy * (bx[3] - bx[1]))
                for fx, fy in (
                    (0, -0.2),
                    (0, 0.2),
                    (-0.2, 0),
                    (0.2, 0),
                    (0, -0.33),
                    (0, 0.33),
                    (-0.25, -0.25),
                    (0.25, -0.25),
                    (-0.25, 0.25),
                    (0.25, 0.25),
                )
            ]
            placed_label: Text | None = None
            for txt in texts:
                for size in sizes:
                    for dx, dy in offsets:
                        cand = Text(
                            cx + dx,
                            cy + dy + size * (ASCENT - DESCENT) / 2,
                            txt,
                            size,
                            "ROOM_LABELS",
                            "geometry",
                            "middle",
                            ref=rid,
                        )
                        b = cand.box()
                        if _box_inside_polygon(b, pp) and placer.free(b, check_walls=True):
                            placed_label = placer.put(cand)
                            break
                    if placed_label:
                        break
                if placed_label:
                    break
            if placed_label is None:
                scene.label_omitted.append(rid)
                continue
            flags = []
            if room["hypothetical"]:
                flags.append("HYPOTHETICAL")
            elif room["placement_state"] != "ANCHORED":
                flags.append(room["placement_state"])
            if room["conflict_refs"]:
                flags.append(f"{len(room['conflict_refs'])} CONFLICT")
            if flags:
                fs = max(r.min_label_h_mm, placed_label.size * 0.7)
                ftxt = " ".join(flags)
                cand = Text(
                    placed_label.x,
                    placed_label.y + placed_label.size * DESCENT + fs * (ASCENT + 0.35),
                    ftxt,
                    fs,
                    "UNCERTAIN",
                    "geometry",
                    "middle",
                    ref=rid,
                )
                if _box_inside_polygon(cand.box(), pp) and placer.free(
                    cand.box(), check_walls=True
                ):
                    placer.put(cand)

    # -- side column: notes and schedules -----------------------------------------
    nh = r.note_h_mm
    line_h = nh * 1.45
    cols = max(10, int((s.w - 4) / (nh * CHAR_W)))
    y = s.y0 + 3 + nh
    bottom = s.y1 - 2
    lines_out: list[tuple[str, bool]] = []

    def section(head: str, body: list[str]) -> None:
        lines_out.append((head, True))
        for b in body:
            for wl in textwrap.wrap(b, cols, subsequent_indent="  ") or [""]:
                lines_out.append((wl, False))
        lines_out.append(("", False))

    notes = [
        "Generated from the canonical model; geometry is schematic, not a survey.",
        "Units: millimetres. Overall dimensions are model extents.",
        "Dashed = ambiguous or hypothetical. Unplaced items are listed, not drawn.",
    ]
    if model.get("synthetic"):
        notes.insert(0, "SYNTHETIC TEST DATA - NOT DERIVED FROM A REAL BUILDING.")
    section("NOTES", notes)
    sched = []
    for room in rooms:
        rid = room["room_id"]
        state = "HYPOTHETICAL" if room["hypothetical"] else room["placement_state"]
        nm = _ascii_safe(room["display_name"] or "UNNAMED")
        extra = " (label omitted)" if rid in scene.label_omitted else ""
        sched.append(f"{tags[rid]} {nm}: {_measure_summary(model, rid)}; {state}{extra}")
    section("ROOM SCHEDULE", sched or ["(no rooms)"])
    section(
        "NOT PLACED / DETAIL ONLY",
        [
            f"{u['subject_id']} [{u['placement_state']}] {u['reason']}"
            for u in model["fragments_unplaced"]
        ]
        or ["(none)"],
    )
    section(
        "OPEN CONFLICTS",
        [f"{c['conflict_id']}: {c['detail']}" for c in model["conflicts"] if c["status"] == "OPEN"]
        or ["(none)"],
    )
    capacity = max(0, int((bottom - y) / line_h) + 1)
    if len(lines_out) > capacity:
        hidden = len(lines_out) - capacity + 1
        lines_out = [
            *lines_out[: capacity - 1],
            (f"... {hidden} more lines in review packet", False),
        ]
    for txt, head in lines_out:
        if txt:
            placer.put(
                Text(
                    s.x0 + 2, y, _ascii_safe(txt), nh * (1.1 if head else 1.0), "ANNOTATION", "side"
                )
            )
        y += line_h

    # -- title block --------------------------------------------------------------
    th = r.title_h_mm
    big = min(th * 0.28, 6.0)
    small = min(th * 0.16, 3.2)
    heading = title + (" - SYNTHETIC TEST DATA" if model.get("synthetic") else "")
    max_chars = int((t.w - 8) / (big * CHAR_W))
    placer.put(
        Text(t.x0 + 4, t.y0 + 3 + big * ASCENT, heading[:max_chars], big, "ANNOTATION", "title")
    )
    info = [
        f"model {model.get('model_id', '?')}",
        f"schema canonical-model {model.get('schema_version', '?')}",
        f"scale {transform.scale_ratio() if transform else 'n/a'} (fit to page)  units mm",
        f"rooms placed {len(placed)}/{len(rooms)}  walls {len(walls)}  "
        f"openings placed {sum(1 for o in model['openings'] if o['wall_id'])}/{len(model['openings'])}",
    ]
    yy = t.y0 + 3 + big + small * 1.6
    for i, txt in enumerate(info):
        col_x = t.x0 + 4 + (i % 2) * (t.w / 2)
        row_y = yy + (i // 2) * small * 1.6
        placer.put(
            Text(
                col_x,
                row_y,
                txt[: int((t.w / 2 - 6) / (small * CHAR_W))],
                small,
                "ANNOTATION",
                "title",
            )
        )

    scene.meta = {
        "renderer": "blueprint_pipeline.rendering",
        "model_id": model.get("model_id"),
        "overrides": ov.to_dict(),
        "scale": transform.scale if transform else None,
        "degenerate": transform.degenerate if transform else "no_geometry",
    }
    return scene
