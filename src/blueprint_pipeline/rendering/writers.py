"""Scene writers: SVG, PNG, PDF (page space) and DXF (model space, 1:1 mm).

All writers are deterministic for a given scene and library version:
fixed element order, fixed numeric formatting, no timestamps, no random IDs.
"""

from __future__ import annotations

import contextlib
import io
import itertools
import json
import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape, quoteattr

from PIL import Image, ImageDraw, ImageFont
from PIL.PngImagePlugin import PngInfo

from .scene import LAYERS, Line, Poly, Scene, Text

COLORS = {
    "FRAME": (0, 0, 0),
    "WALLS": (0, 0, 0),
    "ROOMS": (150, 150, 150),
    "OPENINGS": (0, 0, 0),
    "ROOM_LABELS": (0, 0, 0),
    "DIMENSIONS": (60, 60, 60),
    "UNCERTAIN": (176, 80, 0),
    "ANNOTATION": (0, 0, 0),
}
DXF_COLORS = {
    "FRAME": 7,
    "WALLS": 7,
    "ROOMS": 8,
    "OPENINGS": 4,
    "ROOM_LABELS": 2,
    "DIMENSIONS": 3,
    "UNCERTAIN": 30,
    "ANNOTATION": 7,
}
FONT_STACK = "DejaVu Sans Mono, Menlo, Consolas, Liberation Mono, Courier New, monospace"


def fmt(v: float, nd: int = 3) -> str:
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _by_layer(scene: Scene) -> Iterator[tuple[str, list[Line], list[Poly], list[Text]]]:
    for layer in LAYERS:
        yield (
            layer,
            [x for x in scene.lines if x.layer == layer],
            [x for x in scene.polys if x.layer == layer],
            [x for x in scene.texts if x.layer == layer],
        )


# ----------------------------------------------------------------------------- SVG
def write_svg(scene: Scene, nd: int = 3) -> bytes:
    w, h = scene.page_w, scene.page_h
    meta = {
        "scene_sha256": scene.sha256(),
        "transform": None
        if scene.transform is None
        else {
            "scale": scene.transform.scale,
            "min_x": scene.transform.min_x,
            "min_y": scene.transform.min_y,
            "max_x": scene.transform.max_x,
            "max_y": scene.transform.max_y,
            "left": scene.transform.left,
            "top": scene.transform.top,
            "offset_x": scene.transform.offset_x,
            "offset_y": scene.transform.offset_y,
        },
        "regions": {
            name: getattr(scene.layout, name).as_list()
            for name in ("frame", "geometry", "dim_bottom", "dim_left", "side", "title")
        },
        "expected_counts": scene.expected_counts(),
        "model_id": scene.meta.get("model_id"),
    }
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="http://www.w3.org/2000/svg" version="1.1" width="{fmt(w)}mm" '
        f'height="{fmt(h)}mm" viewBox="0 0 {fmt(w)} {fmt(h)}">',
        f'<metadata id="blueprint-meta">{escape(json.dumps(meta, sort_keys=True))}</metadata>',
        f'<rect id="background" x="0" y="0" width="{fmt(w)}" height="{fmt(h)}" fill="#ffffff"/>',
    ]
    for layer, lines, polys, texts in _by_layer(scene):
        if not (lines or polys or texts):
            continue
        r, g, b = COLORS[layer]
        color = f"#{r:02x}{g:02x}{b:02x}"
        out.append(
            f'<g id="layer-{layer}" data-layer="{layer}" stroke="{color}" fill="none" '
            'stroke-linecap="round" stroke-linejoin="round">'
        )
        for ln in lines:
            dash = f' stroke-dasharray="{fmt(ln.dash[0])} {fmt(ln.dash[1])}"' if ln.dash else ""
            ref = f" data-ref={quoteattr(ln.ref)}" if ln.ref else ""
            out.append(
                f'<line x1="{fmt(ln.x1, nd)}" y1="{fmt(ln.y1, nd)}" x2="{fmt(ln.x2, nd)}" '
                f'y2="{fmt(ln.y2, nd)}" stroke-width="{fmt(ln.weight)}"{dash}{ref}/>'
            )
        for p in polys:
            pts = " ".join(f"{fmt(x, nd)},{fmt(y, nd)}" for x, y in p.points)
            tag = "polygon" if p.closed else "polyline"
            dash = f' stroke-dasharray="{fmt(p.dash[0])} {fmt(p.dash[1])}"' if p.dash else ""
            ref = f" data-ref={quoteattr(p.ref)}" if p.ref else ""
            out.append(f'<{tag} points="{pts}" stroke-width="{fmt(p.weight)}"{dash}{ref}/>')
        for t in texts:
            anchor = {"start": "start", "middle": "middle", "end": "end"}[t.anchor]
            ref = f" data-ref={quoteattr(t.ref)}" if t.ref else ""
            out.append(
                f'<text x="{fmt(t.x, nd)}" y="{fmt(t.y, nd)}" font-size="{fmt(t.size)}" '
                f'text-anchor="{anchor}" font-family="{FONT_STACK}" fill="{color}" stroke="none" '
                f'data-region="{t.region}"{ref}>{escape(t.text)}</text>'
            )
        out.append("</g>")
    out.append("</svg>")
    return ("\n".join(out) + "\n").encode("utf-8")


# ----------------------------------------------------------------------------- PNG
_MONO_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/dejavu/DejaVuSansMono.ttf",
    "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "/Library/Fonts/Courier New.ttf",
    "C:\\Windows\\Fonts\\consola.ttf",
    "C:\\Windows\\Fonts\\cour.ttf",
)


def _font_path() -> str | None:
    for p in _MONO_FONTS:
        if Path(p).is_file():
            return p
    return None


@contextlib.contextmanager
def _nullctx() -> Iterator[None]:
    yield None


def write_png(scene: Scene, dpi: int, *, source_svg_sha256: str) -> tuple[bytes, dict[str, Any]]:
    k = dpi / 25.4
    W, H = round(scene.page_w * k), round(scene.page_h * k)
    img = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(img)
    fp = _font_path()
    fonts: dict[int, Any] = {}

    def font(px: int) -> Any:
        if px not in fonts:
            fonts[px] = ImageFont.truetype(fp, px) if fp else ImageFont.load_default(px)
        return fonts[px]

    def P(x: float, y: float) -> tuple[float, float]:
        return (x * k, y * k)

    def seg(
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        color: Any,
        wpx: int,
        dash: tuple[float, float] | None,
    ) -> None:
        if not dash:
            d.line([P(x1, y1), P(x2, y2)], fill=color, width=wpx)
            return
        length = math.hypot(x2 - x1, y2 - y1)
        if length == 0:
            return
        on, off = dash
        pos = 0.0
        while pos < length:
            e = min(pos + on, length)
            d.line(
                [
                    P(x1 + (x2 - x1) * pos / length, y1 + (y2 - y1) * pos / length),
                    P(x1 + (x2 - x1) * e / length, y1 + (y2 - y1) * e / length),
                ],
                fill=color,
                width=wpx,
            )
            pos = e + off

    for layer, lines, polys, texts in _by_layer(scene):
        color = COLORS[layer]
        for ln in lines:
            seg(ln.x1, ln.y1, ln.x2, ln.y2, color, max(1, round(ln.weight * k)), ln.dash)
        for p in polys:
            pts = p.points + ([p.points[0]] if p.closed else [])
            for (x1, y1), (x2, y2) in itertools.pairwise(pts):
                seg(x1, y1, x2, y2, color, max(1, round(p.weight * k)), p.dash)
        for t in texts:
            px = max(6, round(t.size * k))
            anchor = {"start": "ls", "middle": "ms", "end": "rs"}[t.anchor]
            d.text(P(t.x, t.y), t.text, fill=color, font=font(px), anchor=anchor)
    info = PngInfo()
    info.add_text("blueprint:source_svg_sha256", source_svg_sha256)
    info.add_text("blueprint:scene_sha256", scene.sha256())
    info.add_text("blueprint:font", Path(fp).name if fp else "pillow-default")
    buf = io.BytesIO()
    img.save(buf, format="PNG", pnginfo=info, optimize=False, compress_level=6)
    return buf.getvalue(), {
        "width_px": W,
        "height_px": H,
        "dpi": dpi,
        "font": Path(fp).name if fp else "pillow-default",
    }


# ----------------------------------------------------------------------------- PDF
def _pdf_str(s: str) -> str:
    b = s.encode("cp1252", errors="replace").decode("latin-1")
    return b.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def write_pdf(scene: Scene) -> bytes:
    """Single-page vector PDF (PDF 1.4) using the base-14 Courier font."""
    k = 72.0 / 25.4
    W, H = scene.page_w * k, scene.page_h * k

    def X(x: float) -> str:
        return fmt(x * k, 3)

    def Y(y: float) -> str:
        return fmt(H - y * k, 3)

    ops: list[str] = ["1 J 1 j"]
    for layer, lines, polys, texts in _by_layer(scene):
        r, g, b = (c / 255 for c in COLORS[layer])
        stroke = f"{fmt(r)} {fmt(g)} {fmt(b)} RG"
        fill = f"{fmt(r)} {fmt(g)} {fmt(b)} rg"
        for ln in lines:
            dash = f"[{fmt(ln.dash[0] * k)} {fmt(ln.dash[1] * k)}] 0 d" if ln.dash else "[] 0 d"
            ops.append(
                f"{stroke} {fmt(ln.weight * k)} w {dash} {X(ln.x1)} {Y(ln.y1)} m {X(ln.x2)} {Y(ln.y2)} l S"
            )
        for p in polys:
            dash = f"[{fmt(p.dash[0] * k)} {fmt(p.dash[1] * k)}] 0 d" if p.dash else "[] 0 d"
            path = f"{X(p.points[0][0])} {Y(p.points[0][1])} m " + " ".join(
                f"{X(x)} {Y(y)} l" for x, y in p.points[1:]
            )
            ops.append(f"{stroke} {fmt(p.weight * k)} w {dash} {path} {'s' if p.closed else 'S'}")
        for t in texts:
            size = t.size * k
            width = len(t.text) * 0.6 * size
            x = t.x * k - (width / 2 if t.anchor == "middle" else width if t.anchor == "end" else 0)
            ops.append(
                f"BT {fill} /F1 {fmt(size)} Tf {fmt(x)} {Y(t.y)} Td ({_pdf_str(t.text)}) Tj ET"
            )
    content = ("\n".join(ops) + "\n").encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {fmt(W)} {fmt(H)}] "
            "/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ).encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier /Encoding /WinAnsiEncoding >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for i, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n"
    ).encode()
    return bytes(out)


# ----------------------------------------------------------------------------- DXF
@contextlib.contextmanager
def _fixed_dxf_metadata() -> Iterator[None]:
    import ezdxf

    prev = ezdxf.options.write_fixed_meta_data_for_testing
    ezdxf.options.write_fixed_meta_data_for_testing = True
    try:
        yield
    finally:
        ezdxf.options.write_fixed_meta_data_for_testing = prev


def write_dxf(scene: Scene, nd: int = 3) -> bytes:
    """Editable DXF in model space at 1:1 millimetres.

    Every scene element is mapped back through the inverse transform, so walls,
    rooms and openings sit at their exact model coordinates and annotation is
    arranged around them as on the sheet (scaled to model units).
    """
    tr = scene.transform
    if tr is not None:
        inv = tr.to_model
        unit = 1.0 / tr.scale
    else:

        def inv(px: float, py: float) -> tuple[float, float]:
            return px, scene.page_h - py

        unit = 1.0

    def M(x: float, y: float) -> tuple[float, float]:
        mx, my = inv(x, y)
        return (round(mx, nd) + 0.0, round(my, nd) + 0.0)

    with _fixed_dxf_metadata():
        return _write_dxf_doc(scene, nd, M, unit)


def _write_dxf_doc(scene: Scene, nd: int, M: Any, unit: float) -> bytes:
    import ezdxf
    from ezdxf.enums import TextEntityAlignment

    doc = ezdxf.new("R2010", setup=True)
    doc.units = 4  # millimetres
    doc.header["$MEASUREMENT"] = 1
    doc.header["$LTSCALE"] = round(unit, 6)
    for layer in LAYERS:
        doc.layers.add(layer, color=DXF_COLORS[layer])
    msp = doc.modelspace()
    xs: list[float] = []
    ys: list[float] = []

    def track(p: tuple[float, float]) -> tuple[float, float]:
        xs.append(p[0])
        ys.append(p[1])
        return p

    for layer, lines, polys, texts in _by_layer(scene):
        for ln in lines:
            attrs: dict[str, Any] = {"layer": layer}
            if ln.dash:
                attrs["linetype"] = "DASHED"
            msp.add_line(track(M(ln.x1, ln.y1)), track(M(ln.x2, ln.y2)), dxfattribs=attrs)
        for p in polys:
            attrs = {"layer": layer}
            if p.dash:
                attrs["linetype"] = "DASHED"
            msp.add_lwpolyline(
                [track(M(x, y)) for x, y in p.points], close=p.closed, dxfattribs=attrs
            )
        for t in texts:
            align = {
                "start": TextEntityAlignment.LEFT,
                "middle": TextEntityAlignment.CENTER,
                "end": TextEntityAlignment.RIGHT,
            }[t.anchor]
            pos = track(M(t.x, t.y))
            msp.add_text(
                t.text, height=round(t.size * unit, 3), dxfattribs={"layer": layer}
            ).set_placement(pos, align=align)
    if xs:
        doc.header["$EXTMIN"] = (min(xs), min(ys), 0.0)
        doc.header["$EXTMAX"] = (max(xs), max(ys), 0.0)
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")
