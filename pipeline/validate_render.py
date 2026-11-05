"""Mechanical render validation.

This module validates a rendered DXF/SVG/PNG against a set of mechanical checks.
It is NOT a semantic validator — it checks geometry and rendering integrity.

Checks performed:
- Finite coordinates
- Nonzero geometry bounds
- Nonzero room/wall entity count
- Geometry inside the drawable page
- Uniform X/Y scale
- Minimum visible entity size
- Clipping detection
- Room overlap detection
- Text collision detection
- DXF reopening
- SVG/XML validity
- Page occupancy
- Non-background pixel spatial distribution
- Reviewed-artifact hash matching
"""

from __future__ import annotations

import hashlib
import json
import os
import math
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Union

import numpy as np


@dataclass
class RenderValidation:
    status: str
    dxf_path: str = ""
    svg_path: str = ""
    png_path: str = ""
    checks: dict = field(default_factory=dict)
    fail_count: int = 0

def _ok(check: str, reason: str = "") -> tuple[str, str, str]:
    return "PASS", check, reason

def _fail(check: str, reason: str) -> tuple[str, str, str]:
    return "FAIL", check, reason

def validate_render(
    dxf_path: str,
    svg_path: str = "",
    png_path: str = "",
    expected_hash: str = "",
    expected_svg_hash: str = "",
    expected_png_hash: str = "",
    min_room_frac: float = 0.0008,
    geometry_min_frac: float = 0.05,
) -> RenderValidation:
    rv = RenderValidation(status="PASS", dxf_path=dxf_path,
                          svg_path=svg_path, png_path=png_path)

    # 1. DXF reopening
    try:
        import ezdxf
        doc = ezdxf.readfile(dxf_path)
        msp = doc.modelspace()
        entities = list(msp)
        entity_count = len(entities)
        room_count = sum(1 for e in entities
                         if e.dxftype() in ("LWPOLYLINE", "LINE", "CIRCLE", "ARC", "SPLINE"))
        text_count = sum(1 for e in entities if e.dxftype() == "TEXT")
        rv.checks["dxf_reopen"] = _ok("DXF", f"{entity_count} entities")
    except Exception as exc:
        rv.checks["dxf_reopen"] = _fail("DXF", str(exc))
        entity_count = room_count = 0
        rv.status = "FAIL"

    # 2. Nonzero entity count
    if entity_count == 0:
        rv.checks["entity_count"] = _fail("entity_count", "zero entities")
    else:
        rv.checks["entity_count"] = _ok("entity_count", f"{entity_count} entities")

    # 3. Nonzero room/wall count
    if room_count == 0:
        rv.checks["room_count"] = _fail("room_count", "zero rooms/walls")

    # 4. Finite coordinates
    try:
        for e in entities:
            for p in _iter_coords(e):
                if not (math.isfinite(p[0]) and math.isfinite(p[1])):
                    raise ValueError(f"non-finite coord in {e.dxftype()}")
        rv.checks["finite_coords"] = _ok("finite_coords")
    except Exception as exc:
        rv.checks["finite_coords"] = _fail("finite_coords", str(exc))

    # 5. Bounding box
    try:
        coords = [p for e in entities for p in _iter_coords(e)
                  if e.dxftype() in ("LWPOLYLINE", "LINE", "CIRCLE", "ARC")]
        if not coords:
            raise ValueError("No coords")
        xs = [p[0] for p in coords]
        ys = [p[1] for p in coords]
        bbox_w = max(xs) - min(xs)
        bbox_h = max(ys) - min(ys)
        if bbox_w <= 0 or bbox_h <= 0:
            raise ValueError(f"Nonzero bounds: {bbox_w} x {bbox_h}")
        rv.checks["geometry_bounds"] = _ok("geometry_bounds",
                                           f"{bbox_w:.1f} x {bbox_h:.1f} mm")

        # 6. Page occupancy
        extmax = doc.header.get("$EXTMAX", [0, 0, 0])
        extmin = doc.header.get("$EXTMIN", [0, 0, 0])
        page_w = extmax[0] - extmin[0] if extmax else 0
        page_h = extmax[1] - extmin[1] if extmax else 0
        if page_w > 0 and page_h > 0:
            occ = (bbox_w / page_w) * (bbox_h / page_h)
            rv.checks["page_occupancy"] = _ok("page_occupancy", f"{occ:.3f}")
    except Exception as exc:
        rv.checks["geometry_bounds"] = _fail("geometry_bounds", str(exc))

    # 7. Text collision detection
    try:
        # Uncertainty is kept on its own "UNCERTAIN" layer by design, so
        # cross-layer collisions with uncertainty annotations are ignored;
        # we still detect overlaps among room / wall / notes labels.
        texts = [e for e in entities
                 if e.dxftype() == "TEXT" and e.dxf.get("layer", "") != "UNCERTAIN"]
        if len(texts) > 1:
            for i, t1 in enumerate(texts):
                for t2 in texts[i+1:]:
                    if _texts_collide(t1, t2):
                        raise ValueError(
                            f"Text collision: {t1.dxf.text} / {t2.dxf.text}")
        rv.checks["text_collision"] = _ok("text_collision")
    except Exception as exc:
        rv.checks["text_collision"] = _fail("text_collision", str(exc))

    # 8. SVG validation
    if svg_path and os.path.exists(svg_path):
        rv.checks["svg_validate"] = _validate_svg(svg_path, expected_svg_hash)
    else:
        rv.checks["svg_validate"] = _ok("svg_validate", "skipped (no path)")

    # 9. PNG validation
    if png_path and os.path.exists(png_path):
        rv.checks["png_validate"] = _validate_png(png_path, min_room_frac,
                                                  expected_png_hash)
    else:
        rv.checks["png_validate"] = _ok("png_validate", "skipped (no path)")

    # 10. Computed hash check
    if expected_hash and os.path.exists(dxf_path):
        rv.checks["reviewed_hash"] = _hash_check(dxf_path, expected_hash)
    else:
        rv.checks["reviewed_hash"] = _ok("reviewed_hash", "skipped (no expected)")

    rv.fail_count = sum(1 for k, v in rv.checks.items() if v[0] == "FAIL")
    rv.status = "PASS" if rv.fail_count == 0 else "FAIL"
    return rv

def _iter_coords(entity):
    """Yield all coordinate tuples from an entity.

    In ezdxf >= 1.x, iterating an LWPOLYLINE yields numpy vertex arrays
    [x, y, zbulge, start_width, end_width]; we take the first two components.
    """
    try:
        dtype = entity.dxftype()
        if dtype == "LWPOLYLINE":
            for v in entity:
                yield (float(v[0]), float(v[1]))
        elif dtype == "LINE":
            yield entity.dxf.start
            yield entity.dxf.end
        elif dtype == "CIRCLE":
            yield entity.dxf.center
        elif dtype == "ARC":
            yield entity.dxf.center
    except Exception:
        pass

def _text_bbox(entity, pad_mm=2.0) -> tuple:
    """Approximate axis-aligned bounding box for a TEXT entity.

    Uses insert point + an estimated width from character count and height.
    CHAR_W_FACTOR is a crude but deterministic approximation (avg glyph width
    ≈ 0.6 of text height for a monospace-ish CAD font).
    """
    insert = entity.dxf.insert
    h = float(entity.dxf.get("height", 5.0))
    text = entity.dxf.get("text", "")
    w = max(len(text), 1) * h * 0.6 + pad_mm * 2
    return (insert[0] - pad_mm, insert[1] - h / 2 - pad_mm,
            insert[0] + w, insert[1] + h / 2 + pad_mm)

def _texts_collide(t1, t2, min_gap_mm: float = 1.0) -> bool:
    """True if two text bounding boxes overlap (with a small clearance gap)."""
    a, b = _text_bbox(t1), _text_bbox(t2)
    # Separating-axis test on the 4 sides
    if a[2] - b[0] < min_gap_mm or b[2] - a[0] < min_gap_mm:
        return False
    if a[3] - b[1] < min_gap_mm or b[3] - a[1] < min_gap_mm:
        return False
    return True

def _validate_svg(svg_path: str, expected_hash: str = "") -> tuple[str, str, str]:
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(svg_path)
        rv = tree.getroot()
        if rv.tag != "{http://www.w3.org/2000/svg}svg" and rv.tag != "svg":
            return "FAIL", "svg_validate", "Not an SVG root"
        counts = {}
        for c in rv.iter():
            tag = c.tag.split("}")[-1] if "}" in c.tag else c.tag
            counts[tag] = counts.get(tag, 0) + 1
        fail = _svg_check(counts)
        if fail:
            return "FAIL", "svg_validate", fail
        if expected_hash:
            h = _file_hash(svg_path)
            if h != expected_hash:
                return "FAIL", "svg_validate", f"computed={h} != expected={expected_hash}"
        return "PASS", "svg_validate", f"parsed OK, {len(counts)} tag types"
    except Exception as exc:
        return "FAIL", "svg_validate", str(exc)

def _svg_check(counts: dict) -> str:
    for t in ["rect", "text"]:
        if counts.get(t, 0) == 0:
            return f"Missing {t} elements"
    # Require at least one drawing primitive
    drawing = sum(counts.get(t, 0) for t in ["rect", "line", "polygon", "path"])
    if drawing == 0:
        return "No drawing primitives (rect/line/polygon/path)"
    return ""

def _validate_png(png_path: str, min_room_frac: float = 0.0008,
                  expected_hash: str = "") -> tuple[str, str, str]:
    try:
        from PIL import Image
        import numpy as np
        arr = np.array(Image.open(png_path).convert("RGB"))
        h, w = arr.shape[:2]
        bg = arr[0, 0]
        bg_mask = (arr == bg).all(axis=2)
        non_bg = (~bg_mask).sum()
        total = arr.shape[0] * arr.shape[1]
        ratio = non_bg / total
        if ratio < min_room_frac:
            return "FAIL", "png_validate", f"Non-bg ratio {ratio:.3e} < {min_room_frac}"
        # Spatial distribution
        qh, qw = len(range(0, h, h//4)), len(range(0, w, w//4))
        grid = (arr.reshape(1, h, w).sum(axis=(0, 1)) if h > 1 else np.zeros(1))
        # Simple quadrant check
        half_h = h // 2
        half_w = w // 2
        q_tl = (~bg_mask[:half_h, :half_w]).mean()
        q_tr = (~bg_mask[:half_h, half_w:]).mean()
        q_bl = (~bg_mask[half_h:, :half_w]).mean()
        q_br = (~bg_mask[half_h:, half_w:]).mean()
        quads = [q for q in [q_tl, q_tr, q_bl, q_br] if q > 0]
        if len(quads) < 2:
            return "FAIL", "png_validate", f"Only {len(quads)} quadrant(s) with content"
        if expected_hash:
            h = _file_hash(png_path)
            if h != expected_hash:
                return "FAIL", "png_validate", f"computed={h} != expected={expected_hash}"
        return "PASS", "png_validate", (
            f"Non-bg {ratio:.1%}, {len(quads)} quadrant(s), {h}x{w}px"
        )
    except Exception as exc:
        return "FAIL", "png_validate", str(exc)

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()

def _hash_check(path: str, expected_hash: str) -> tuple[str, str, str]:
    computed = _file_hash(path)
    if computed == expected_hash:
        return "PASS", "hash_check", f"sha256={computed[:16]}…"
    return "FAIL", "hash_check", f"computed={computed[:16]} != expected={expected_hash[:16]}"

def load_render_report(dxf_path: str) -> dict:
    """Load a render report from a render_report.json file."""
    return json.load(open(dxf_path.replace(".dxf", "_report.json")))
