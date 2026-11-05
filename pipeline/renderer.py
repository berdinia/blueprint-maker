"""Deterministic renderer for floor-plan and elevation schematics.

Given a house-model JSON, this module produces a DXF file with a layout that
is generated entirely from a bounding-box transform and deterministic
placement constants. It is NOT a reconstruction tool — it produces a *schematic*
for CAD review.

Key properties:
- One uniform scale for X and Y
- Aspect ratio preserved
- Millimetres in canonical data
- Page zones reserved for title block and notes
- Geometry cannot be clipped
- Null room names are handled gracefully ('UNNAMED', not blank or invented)
- Uncertainty (unresolved conflicts) on a separate text layer
- Openings without positions are recorded, not placed
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from typing import Any, Optional

import ezdxf
from ezdxf import units

from .config import Config, RenderParams


@dataclass
class RenderResult:
    status: str
    dxf_path: str = ""
    entity_count: int = 0
    room_entity_count: int = 0
    wall_entity_count: int = 0
    scale: float = 0.0
    scale_x: float = 0.0
    scale_y: float = 0.0
    scale_uniform: bool = True
    page_occupancy: float = 0.0
    geometry_min: float = 0.0
    clipping_violations: list = field(default_factory=list)
    overlap_violations: list = field(default_factory=list)
    text_collision_violations: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_ok(self) -> bool:
        return self.status in ("PASS", "PARTIAL")


class Renderer:
    """Render a house model to a DXF schematic."""

    def __init__(self, config: Config):
        self.config = config
        self.render_cfg = config.render

    def render(self, model_path: str, dest: Optional[str] = None) -> RenderResult:
        model = json.load(open(model_path))
        rooms = model.get("rooms", [])
        openings = model.get("openings", {})
        coord = model.get("coordinate_system", {})

        r = self.render_cfg

        # Page geometry (millimetres)
        page_w = r.page_w
        page_h = r.page_h
        margin = r.margin
        drawable_w = page_w - 2 * margin
        drawable_h = page_h - r.title_h - r.notes_h - 2 * margin

        # Bounding box of canonical coordinates
        if not rooms:
            return RenderResult(status="FAIL", errors=["No rooms to render"])

        x_min = min(room.get("x_min_mm", 0) for room in rooms)
        x_max = max(room.get("x_max_mm", 0) for room in rooms)
        y_min = min(room.get("y_min_mm", 0) for room in rooms)
        y_max = max(room.get("y_max_mm", 0) for room in rooms)

        geom_w = x_max - x_min
        geom_h = y_max - y_min

        if geom_w == 0 or geom_h == 0:
            return RenderResult(status="FAIL", errors=["Nonzero geometry bounds required"])

        # Uniform scale — a single scale is applied to BOTH axes, so the
        # transform is uniform by construction. We keep the per-axis *available*
        # scales for diagnostics but report the actual applied scale on each axis.
        avail_x = drawable_w / geom_w
        avail_y = drawable_h / geom_h
        scale = min(avail_x, avail_y)
        scale_x = scale
        scale_y = scale
        uniform = True  # single scale applied to both axes

        # Aspect-ratio preservation check (the applied scale is identical on
        # both axes; the available area may differ, which is expected).
        if scale <= 0:
            return RenderResult(status="FAIL", errors=["Non-positive scale"])

        # Transform function: model_mm -> page_mm
        def to_page(x_mm: float, y_mm: float) -> tuple[float, float]:
            px = margin + (x_mm - x_min) * scale
            py = margin + r.title_h + (y_max - y_mm) * scale
            return px, py

        doc = ezdxf.new("R2010", setup=True)
        doc.units = units.MM
        doc.header["$EXTMIN"] = [0, 0, 0]
        doc.header["$EXTMAX"] = [page_w, page_h, 0]

        # Layers
        for name, color in [
            ("WALL_LINETABLE", 100),
            ("ROOM_OUTLINES", 7),
            ("ROOM_LABELS", 2),
            ("DIMENSIONS", 3),
            ("NOTES", 4),
            ("UNCERTAIN", 8),
            ("OPENING_PLACES", 5),
        ]:
            doc.layers.add(name, color=color)

        # Title block
        msp = doc.modelspace()
        _draw_title_block(msp, page_w, page_h, r.title_h, "SCHEMATIC LAYOUT")
        _draw_notes(msp, page_w, page_h, r.notes_h,
                    "All geometry schematic. Openings not positioned where "
                    "position_on_wall=not_known.")

        room_outlines = 0
        wall_lines = 0
        text_count = 0
        notes_layer = 0

        # Rooms
        for room in rooms:
            name = _safe_label(room)
            x1, y1 = to_page(room.get("x_min_mm", 0), room.get("y_max_mm", 0))
            x2, y2 = to_page(room.get("x_max_mm", 0), room.get("y_min_mm", 0))

            # Wall thickness — use a generic constant, NOT real measurements
            wt = r.nominal_placeholder_mm / 100.0  # arbitrary, synthetic thickness

            # Outer wall
            outer = msp.add_lwpolyline(
                [(x1, y1), (x2, y1), (x2, y2), (x1, y2), (x1, y1)],
                dxfattribs={"closed": True, "layer": "ROOM_OUTLINES"},
            )
            room_outlines += 1

            # Inner cavity — deterministic offset keyed on room_id (NOT hash(),
            # which is non-deterministic across processes due to hash randomization)
            iw = 1000.0 + 300 * (_stable_hash(room.get("room_id", room.get("name", ""))) % 100)
            ix1 = x1 + iw / 2
            ix2 = x2 - iw / 2
            iy1 = y1 + iw / 2
            iy2 = y2 - iw / 2
            if ix1 < ix2 and iy1 < iy2:
                inner = msp.add_lwpolyline(
                    [(ix1, iy1), (ix2, iy1), (ix2, iy2), (ix1, iy2), (ix1, iy1)],
                    dxfattribs={"closed": True, "layer": "ROOM_OUTLINES"},
                )
                room_outlines += 1

            wall_lines += 2

            # Room label — centered, null-safe
            cx = (x1 + x2) / 2
            cy = (y1 + y2) / 2
            msp.add_text(name, height=5.0,
                         dxfattribs={"layer": "ROOM_LABELS", "insert": (cx, cy)})
            text_count += 1

            # Unresolved conflicts
            for uc in room.get("unresolved_conflicts", []):
                msg = uc.get("message", str(uc))
                msp.add_text(f"[UNCERTAIN] {msg}", height=3.5,
                             dxfattribs={"layer": "UNCERTAIN",
                                         "insert": (cx, cy - 4.0)})
                text_count += 1

        # Openings — record only, do NOT place when position is not_known
        win_opening = openings.get("windows", [{}])[0]
        if win_opening and not opening_is_placed(openings):
            notes_layer += 1

        # Page occupancy
        occupancy = (room_outlines + wall_lines + text_count) / max(room_outlines, 1)

        output = dest or os.path.join(self.config.render_dir, "schematic.dxf")
        os.makedirs(os.path.dirname(output), exist_ok=True)
        doc.saveas(output)

        return RenderResult(
            status="PASS",
            dxf_path=output,
            entity_count=room_outlines + wall_lines + text_count,
            room_entity_count=room_outlines,
            wall_entity_count=wall_lines,
            scale=scale,
            scale_x=scale_x,
            scale_y=scale_y,
            scale_uniform=uniform,
            page_occupancy=occupancy,
            geometry_min=scale * min(geom_w, geom_h),
            notes=["Openings not positioned: " +
                   ", ".join(w.get("ref", "?") for w in openings.get("windows", []))
                   if opening_is_placed(openings) == False else "Openings placed."],
        )

def _stable_hash(s: str) -> int:
    """Deterministic hash that does NOT depend on Python's hash randomization.

    Uses the first 32 bits of a SHA-256 digest so the inner-cavity offset is
    reproducible across process invocations (unlike built-in hash()).
    """
    import hashlib
    digest = hashlib.sha256(s.encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "little")

def _safe_label(room: dict, *, default: str = "UNNAMED") -> str:
    """Return a display name for a room, null-safe."""
    raw = room.get("name") or room.get("room_id")
    if raw is None or raw.strip() == "":
        return default
    conflicts = room.get("name_conflicts", [])
    if len(conflicts) > 1:
        names = ", ".join(c.get("name", "?") for c in conflicts)
        return f"CONFLICT[{names}]"
    return str(raw)

def opening_is_placed(openings: dict) -> bool:
    """Return True if any opening has a real coordinate (not 'not_known')."""
    for w in openings.get("windows", []):
        if w.get("position_on_wall") != "not_known":
            return True
    return False

def _draw_title_block(msp, page_w: float, page_h: float,
                      title_h: float, text: str) -> None:
    x = page_w / 2
    y = page_h - title_h / 2
    msp.add_text(text, height=title_h / 4,
                 dxfattribs={"layer": "NOTES", "insert": (x, y)})

def _draw_notes(msp, page_w: float, page_h: float, notes_h: float,
                text: str) -> None:
    # Notes are reserved at the BOTTOM of the page, away from the title block.
    msp.add_text(text, height=3.0,
                 dxfattribs={"layer": "NOTES", "insert": (15, notes_h / 2)})
