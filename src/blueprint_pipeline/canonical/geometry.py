"""Small, dependency-free planar geometry helpers (millimetres, Y up)."""

from __future__ import annotations

import math
from collections.abc import Sequence

Point = tuple[float, float]
EPS = 1e-6


def finite_point(p: Sequence[float]) -> bool:
    return len(p) == 2 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in p)


def signed_area(poly: Sequence[Sequence[float]]) -> float:
    a = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def bbox(points: Sequence[Sequence[float]]) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def centroid(poly: Sequence[Sequence[float]]) -> Point:
    a = signed_area(poly)
    if abs(a) < EPS:
        xs = [p[0] for p in poly]
        ys = [p[1] for p in poly]
        return sum(xs) / len(xs), sum(ys) / len(ys)
    cx = cy = 0.0
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        f = x1 * y2 - x2 * y1
        cx += (x1 + x2) * f
        cy += (y1 + y2) * f
    return cx / (6 * a), cy / (6 * a)


def _orient(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a: Sequence[float], b: Sequence[float], p: Sequence[float], tol: float) -> bool:
    return (
        min(a[0], b[0]) - tol <= p[0] <= max(a[0], b[0]) + tol
        and min(a[1], b[1]) - tol <= p[1] <= max(a[1], b[1]) + tol
        and abs(_orient(a, b, p)) <= tol * max(1.0, seg_length(a, b))
    )


def seg_length(a: Sequence[float], b: Sequence[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def segments_properly_cross(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
    tol: float = 1e-6,
) -> bool:
    """True if the open segments AB and CD cross at a single interior point."""
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)
    scale_ab = max(1.0, seg_length(a, b))
    scale_cd = max(1.0, seg_length(c, d))
    if (
        abs(d1) <= tol * scale_cd
        or abs(d2) <= tol * scale_cd
        or abs(d3) <= tol * scale_ab
        or abs(d4) <= tol * scale_ab
    ):
        return False  # touching / collinear: not a proper crossing
    return (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0)


def segments_intersect(
    a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float], tol: float = 0.0
) -> bool:
    """Closed-segment intersection (touching counts)."""
    d1 = _orient(c, d, a)
    d2 = _orient(c, d, b)
    d3 = _orient(a, b, c)
    d4 = _orient(a, b, d)
    if ((d1 > 0 and d2 < 0) or (d1 < 0 and d2 > 0)) and (
        (d3 > 0 and d4 < 0) or (d3 < 0 and d4 > 0)
    ):
        return True
    return (
        _on_segment(c, d, a, tol)
        or _on_segment(c, d, b, tol)
        or _on_segment(a, b, c, tol)
        or _on_segment(a, b, d, tol)
    )


def polygon_problems(poly: Sequence[Sequence[float]], min_area: float = 1.0) -> list[str]:
    probs: list[str] = []
    if len(poly) < 3:
        return ["fewer than 3 vertices"]
    if not all(finite_point(p) for p in poly):
        return ["non-finite coordinate"]
    for i in range(len(poly)):
        if seg_length(poly[i], poly[(i + 1) % len(poly)]) < EPS:
            probs.append(f"zero-length edge at vertex {i}")
    if abs(signed_area(poly)) < min_area:
        probs.append("zero or near-zero area")
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        for j in range(i + 1, n):
            if j in (i, (i + 1) % n) or (j + 1) % n == i:
                continue
            c, d = poly[j], poly[(j + 1) % n]
            if segments_intersect(a, b, c, d):
                probs.append(f"self-intersection between edges {i} and {j}")
    return probs


def point_in_polygon(p: Sequence[float], poly: Sequence[Sequence[float]]) -> bool:
    """Strict interior test (points on the boundary are outside)."""
    n = len(poly)
    for i in range(n):
        if _on_segment(poly[i], poly[(i + 1) % n], p, 1e-6):
            return False
    x, y = p
    inside = False
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xin = x1 + (y - y1) * (x2 - x1) / (y2 - y1)
            if xin > x:
                inside = not inside
    return inside


def triangulate(poly: Sequence[Sequence[float]]) -> list[list[Point]]:
    """Ear-clipping triangulation of a simple polygon (any orientation)."""
    pts: list[Point] = [(float(p[0]), float(p[1])) for p in poly]
    if signed_area(pts) < 0:
        pts.reverse()
    idx = list(range(len(pts)))
    tris: list[list[Point]] = []
    guard = 0
    while len(idx) > 3 and guard < 10_000:
        guard += 1
        n = len(idx)
        for k in range(n):
            a, b, c = pts[idx[k - 1]], pts[idx[k]], pts[idx[(k + 1) % n]]
            if _orient(a, b, c) <= EPS:
                continue  # reflex or degenerate corner
            if any(
                _point_in_triangle(pts[m], a, b, c)
                for m in idx
                if m not in (idx[k - 1], idx[k], idx[(k + 1) % n])
            ):
                continue
            tris.append([a, b, c])
            idx.pop(k)
            break
        else:
            break  # no ear found (degenerate input)
    if len(idx) == 3:
        tris.append([pts[i] for i in idx])
    return tris


def _point_in_triangle(p: Point, a: Point, b: Point, c: Point) -> bool:
    d1, d2, d3 = _orient(a, b, p), _orient(b, c, p), _orient(c, a, p)
    return d1 >= -EPS and d2 >= -EPS and d3 >= -EPS


def _clip_convex(subject: list[Point], clip: list[Point]) -> list[Point]:
    """Sutherland–Hodgman: intersection of a polygon with a convex CCW polygon."""
    out = subject
    for i in range(len(clip)):
        a, b = clip[i], clip[(i + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            break
        for j in range(len(inp)):
            cur, prev = inp[j], inp[j - 1]
            cin, pin = _orient(a, b, cur) >= 0, _orient(a, b, prev) >= 0
            if cin != pin:
                dx, dy = cur[0] - prev[0], cur[1] - prev[1]
                den = (b[0] - a[0]) * dy - (b[1] - a[1]) * dx
                if abs(den) > 1e-12:
                    t = ((b[0] - a[0]) * (a[1] - prev[1]) - (b[1] - a[1]) * (a[0] - prev[0])) / den
                    out.append((prev[0] + t * dx, prev[1] + t * dy))
            if cin:
                out.append(cur)
    return out


def intersection_area(p: Sequence[Sequence[float]], q: Sequence[Sequence[float]]) -> float:
    """Area of the intersection of two simple polygons."""
    total = 0.0
    for t1 in triangulate(p):
        for t2 in triangulate(q):
            inter = _clip_convex(t1, t2)
            if len(inter) >= 3:
                total += abs(signed_area(inter))
    return total


def polygons_overlap(
    p: Sequence[Sequence[float]], q: Sequence[Sequence[float]], min_area: float = 1.0
) -> bool:
    """True if the interiors of two simple polygons overlap by more than
    ``min_area`` (mm²). Shared edges and touching corners are not overlaps."""
    bp, bq = bbox(p), bbox(q)
    if bp[2] <= bq[0] + EPS or bq[2] <= bp[0] + EPS or bp[3] <= bq[1] + EPS or bq[3] <= bp[1] + EPS:
        return False
    return intersection_area(p, q) > min_area


def split_collinear_edges(
    edges: list[tuple[Point, Point, str]], tol: float = 1e-6
) -> list[tuple[Point, Point, list[str]]]:
    """Split overlapping collinear edges into unique segments.

    ``edges`` are (start, end, owner). Returns unique segments, each with the
    sorted list of owners whose edge contains it. Used to derive walls shared
    between rooms whose edges only partially coincide.
    """

    # Group edges by their supporting line.
    def line_key(a: Point, b: Point) -> tuple[float, float, float]:
        dx, dy = b[0] - a[0], b[1] - a[1]
        length = math.hypot(dx, dy)
        nx, ny = -dy / length, dx / length
        if nx < -tol or (abs(nx) <= tol and ny < 0):
            nx, ny = -nx, -ny
        c = nx * a[0] + ny * a[1]
        return round(nx, 9), round(ny, 9), round(c, 6)

    groups: dict[tuple[float, float, float], list[tuple[Point, Point, str]]] = {}
    for a, b, owner in edges:
        if seg_length(a, b) <= tol:
            continue
        groups.setdefault(line_key(a, b), []).append((a, b, owner))

    out: list[tuple[Point, Point, list[str]]] = []
    for (nx, ny, _), items in sorted(groups.items()):
        # Parametrise along the line direction (perpendicular to the normal).
        ux, uy = ny, -nx

        def t(p: Point, ux: float = ux, uy: float = uy) -> float:
            return p[0] * ux + p[1] * uy

        cuts = sorted({round(t(p), 6) for a, b, _ in items for p in (a, b)})
        ref_a = items[0][0]
        base_t = t(ref_a)
        for i in range(len(cuts) - 1):
            t0, t1 = cuts[i], cuts[i + 1]
            if t1 - t0 <= tol:
                continue
            mid = (t0 + t1) / 2
            owners = sorted(
                {o for a, b, o in items if min(t(a), t(b)) - tol <= mid <= max(t(a), t(b)) + tol}
            )
            if not owners:
                continue
            p0 = (ref_a[0] + (t0 - base_t) * ux, ref_a[1] + (t0 - base_t) * uy)
            p1 = (ref_a[0] + (t1 - base_t) * ux, ref_a[1] + (t1 - base_t) * uy)
            out.append((p0, p1, owners))
    return out
