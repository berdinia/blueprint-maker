"""Fit-to-page transform, scene layout, writers and render validators."""

from __future__ import annotations

import io
import math
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from PIL import Image

from blueprint_pipeline.config.settings import RenderSettings
from blueprint_pipeline.io.atomic import sha256_bytes
from blueprint_pipeline.rendering import (
    Rect,
    build_scene,
    fit_transform,
    write_dxf,
    write_pdf,
    write_png,
    write_svg,
)
from blueprint_pipeline.rendering.scene import Text
from blueprint_pipeline.validation import check_dxf, check_layout, check_pdf, check_png, check_svg
from conftest import feature, make_model, rect

R = RenderSettings()


def status(checks, cid):
    return next(c.status for c in checks if c.check_id == cid)


# ------------------------------------------------------------------ transform
def test_transform_formula_uniform_and_y_up():
    region = Rect(10, 20, 210, 120)  # 200 x 100
    t = fit_transform((0, 0, 4000, 1000), region)
    assert t.scale == pytest.approx(min(200 / 4000, 100 / 1000))
    assert t.to_page(0, 1000) == pytest.approx((10, 20 + t.offset_y))  # max_y at the top
    assert t.to_page(4000, 0)[0] == pytest.approx(210)
    x, y = t.to_page(1234, 567)
    assert t.to_model(x, y) == pytest.approx((1234, 567))


@pytest.mark.parametrize(
    "bounds,kind",
    [((5, 5, 5, 5), "point"), ((0, 0, 0, 3000), "zero_width"), ((0, 7, 3000, 7), "zero_height")],
)
def test_degenerate_geometry_is_safe(bounds, kind):
    region = Rect(0, 0, 100, 50)
    t = fit_transform(bounds, region)
    assert t.degenerate == kind and t.scale > 0
    x, y = t.to_page(bounds[0], bounds[1])
    assert 0 <= x <= 100 and 0 <= y <= 50


@settings(max_examples=150, deadline=None)
@given(
    st.lists(
        st.tuples(st.floats(-1e6, 1e6), st.floats(-1e6, 1e6), st.floats(1, 5e4), st.floats(1, 5e4)),
        min_size=1,
        max_size=8,
    ),
    st.floats(30, 400),
    st.floats(30, 400),
)
def test_property_geometry_always_inside_region_with_uniform_scale(rects, rw, rh):
    pts = [(x, y) for x, y, w, h in rects for (x, y) in ((x, y), (x + w, y + h))]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    region = Rect(5, 7, 5 + rw, 7 + rh)
    t = fit_transform((min(xs), min(ys), max(xs), max(ys)), region)
    for x, y in pts:
        px, py = t.to_page(x, y)
        assert region.x0 - 1e-6 <= px <= region.x1 + 1e-6
        assert region.y0 - 1e-6 <= py <= region.y1 + 1e-6
    # the limiting axis is filled, the other is centred
    gw, gh = (max(xs) - min(xs)) * t.scale, (max(ys) - min(ys)) * t.scale
    assert math.isclose(max(gw / region.w, gh / region.h), 1.0, rel_tol=1e-9)
    assert t.offset_x == pytest.approx((region.w - gw) / 2) and t.offset_y == pytest.approx(
        (region.h - gh) / 2
    )


# ------------------------------------------------------------------ scene
def test_null_room_name_renders_unnamed_and_no_mirroring():
    m = make_model(
        [
            feature(
                room_id="ROOM-LOW",
                display_name=None,
                polygon_mm=rect(0, 0, 3000, 2000),
                measurement_ids=[],
            ),
            feature(
                room_id="ROOM-HIGH",
                display_name="HIGH",
                polygon_mm=rect(0, 2000, 3000, 4000),
                measurement_ids=[],
            ),
        ]
    )
    sc = build_scene(m, R)
    labels = {t.ref: t for t in sc.texts if t.layer == "ROOM_LABELS"}
    assert labels["ROOM-LOW"].text == "UNNAMED"
    # Regression for D-R1: higher model Y must be higher on the page (smaller page y).
    assert labels["ROOM-HIGH"].y < labels["ROOM-LOW"].y


def test_layout_checks_pass_on_clean_model_and_unplaced_items_not_drawn():
    m = make_model()
    m["openings"] = [
        feature(
            opening_id="OPEN-U",
            kind="window",
            wall_id=None,
            offset_mm=None,
            width_mm=900,
            room_ids=[],
            placement_state="UNPLACED",
            decision_ids=[],
        )
    ]
    sc = build_scene(m, R)
    assert not [x for x in sc.lines if x.ref == "OPEN-U"]
    checks = check_layout(sc, m)
    assert all(c.status == "PASS" for c in checks), [c for c in checks if c.status != "PASS"]


def test_text_collision_and_clipping_detected():
    m = make_model()
    sc = build_scene(m, R)
    first = sc.texts[0]
    sc.texts.append(Text(first.x, first.y, first.text, first.size, first.layer, first.region))
    sc.texts.append(Text(R.page_w_mm - 2, 20, "OFF THE EDGE", 3, "ANNOTATION", "side"))
    checks = check_layout(sc, m)
    assert status(checks, "text_text_collisions") == "FAIL"
    assert status(checks, "text_clipping") == "FAIL"


def test_label_never_crosses_walls_tiny_room_omitted():
    m = make_model(
        [
            feature(
                room_id="ROOM-BIG",
                display_name="BIG",
                polygon_mm=rect(0, 0, 100000, 50000),
                measurement_ids=[],
            ),
            feature(
                room_id="ROOM-TINY",
                display_name="A VERY LONG NAME",
                polygon_mm=rect(100000, 0, 100300, 300),
                measurement_ids=[],
            ),
        ]
    )
    sc = build_scene(m, R)
    assert "ROOM-TINY" in sc.label_omitted
    checks = check_layout(sc, m)
    assert status(checks, "text_wall_collisions") == "PASS"
    assert status(checks, "room_labels_placed") == "WARN"


def test_deterministic_outputs():
    m = make_model()
    a, b = build_scene(m, R), build_scene(m, R)
    assert a.sha256() == b.sha256()
    assert (
        write_svg(a) == write_svg(b)
        and write_pdf(a) == write_pdf(b)
        and write_dxf(a) == write_dxf(b)
    )


# ------------------------------------------------------------------ files
def test_svg_check_detects_non_uniform_scaling_and_stale_scene():
    m = make_model()
    sc = build_scene(m, R)
    svg = write_svg(sc)
    assert all(c.status == "PASS" for c in check_svg(svg, sc, m))
    # stretch one room polygon horizontally only
    text = svg.decode()
    poly = re.search(r'<polygon points="([^"]+)"[^>]*data-ref="ROOM-A"', text).group(1)
    stretched = " ".join(
        f"{float(p.split(',')[0]) * 1.1:.3f},{p.split(',')[1]}" for p in poly.split()
    )
    bad = text.replace(poly, stretched).encode()
    assert status(check_svg(bad, sc, m), "uniform_scale_in_file") == "FAIL"
    other = build_scene(make_model(model_id="MODEL-OTHER"), R)
    assert status(check_svg(svg, other, m), "scene_binding") == "FAIL"
    assert status(check_svg(b"<svg", sc, m), "xml_valid") == "FAIL"


def test_dxf_reopens_in_millimetres_with_exact_polygons():
    m = make_model()
    sc = build_scene(m, R)
    checks = check_dxf(write_dxf(sc), sc, m)
    assert all(c.status == "PASS" for c in checks), [c for c in checks if c.status != "PASS"]
    assert status(check_dxf(b"not a dxf", sc, m), "dxf_reopen") == "FAIL"


def test_png_checks_detect_blank_image_and_stale_hash():
    m = make_model()
    sc = build_scene(m, R)
    svg = write_svg(sc)
    png, _ = write_png(sc, 100, source_svg_sha256=sha256_bytes(svg))
    good = check_png(png, sc, dpi=100, svg_sha256=sha256_bytes(svg))
    assert all(c.status == "PASS" for c in good), [c for c in good if c.status != "PASS"]
    assert status(check_png(png, sc, dpi=100, svg_sha256="0" * 64), "svg_hash_binding") == "FAIL"
    buf = io.BytesIO()
    Image.open(io.BytesIO(png)).convert("RGB").point(lambda _: 255).save(buf, "PNG")
    blank = check_png(buf.getvalue(), sc, dpi=100, svg_sha256=sha256_bytes(svg))
    assert status(blank, "non_background_pixels") == "FAIL"
    # geometry drawn but squashed into one corner: bbox and distribution fail
    img = Image.new("RGB", Image.open(io.BytesIO(png)).size, "white")
    img.paste(Image.open(io.BytesIO(png)).resize((200, 140)), (0, 0))
    buf = io.BytesIO()
    img.save(buf, "PNG")
    squashed = check_png(buf.getvalue(), sc, dpi=100, svg_sha256=sha256_bytes(svg))
    assert status(squashed, "non_background_bbox") == "FAIL"


def test_pdf_structure():
    sc = build_scene(make_model(), R)
    pdf = write_pdf(sc)
    assert all(c.status == "PASS" for c in check_pdf(pdf, sc))
    assert status(check_pdf(pdf.replace(b"startxref\n", b"startxref\n9"), sc), "xref") == "FAIL"
