"""Page regions and the uniform fit-to-page transform.

Coordinate systems
------------------
* **Model**: millimetres, Y axis up (canonical model).
* **Page**: millimetres from the top-left corner of the sheet, Y axis down
  (SVG/PNG convention; the PDF writer flips once at output).

The transform uses one scale for both axes::

    scale = min(available_width / geometry_width, available_height / geometry_height)
    page_x = left + offset_x + (model_x - min_x) * scale
    page_y = top  + offset_y + (max_y - model_y) * scale

``offset_x/offset_y`` centre the geometry in the region along the axis that
has spare room. Degenerate geometry (zero width and/or height) is handled
explicitly instead of dividing by zero.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..config.settings import RenderSettings

NOMINAL_EXTENT_MM = 1000.0  # used only to scale a single point


@dataclass(frozen=True)
class Rect:
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def w(self) -> float:
        return self.x1 - self.x0

    @property
    def h(self) -> float:
        return self.y1 - self.y0

    def contains(self, other: Rect, tol: float = 1e-6) -> bool:
        return (
            other.x0 >= self.x0 - tol
            and other.y0 >= self.y0 - tol
            and other.x1 <= self.x1 + tol
            and other.y1 <= self.y1 + tol
        )

    def intersects(self, other: Rect, pad: float = 0.0) -> bool:
        return not (
            other.x0 >= self.x1 + pad
            or other.x1 <= self.x0 - pad
            or other.y0 >= self.y1 + pad
            or other.y1 <= self.y0 - pad
        )

    def as_list(self) -> list[float]:
        return [self.x0, self.y0, self.x1, self.y1]


@dataclass(frozen=True)
class PageLayout:
    page: Rect
    frame: Rect  # inside margins
    geometry: Rect  # drawable geometry region
    dim_bottom: Rect
    dim_left: Rect
    side: Rect  # notes and schedules
    title: Rect  # title block


def compute_layout(r: RenderSettings) -> PageLayout:
    m, d = r.margin_mm, r.dim_band_mm
    page = Rect(0.0, 0.0, r.page_w_mm, r.page_h_mm)
    frame = Rect(m, m, r.page_w_mm - m, r.page_h_mm - m)
    title = Rect(m, r.page_h_mm - m - r.title_h_mm, r.page_w_mm - m, r.page_h_mm - m)
    side = Rect(r.page_w_mm - m - r.side_w_mm, m, r.page_w_mm - m, title.y0)
    body = Rect(m, m, side.x0, title.y0)
    geometry = Rect(body.x0 + d, body.y0 + d, body.x1 - d, body.y1 - d)
    dim_bottom = Rect(geometry.x0, geometry.y1, geometry.x1, body.y1)
    dim_left = Rect(body.x0, geometry.y0, geometry.x0, geometry.y1)
    return PageLayout(page, frame, geometry, dim_bottom, dim_left, side, title)


@dataclass(frozen=True)
class Transform:
    scale: float
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    left: float
    top: float
    offset_x: float
    offset_y: float
    degenerate: str | None = None  # None | "zero_width" | "zero_height" | "point"

    def to_page(self, x: float, y: float) -> tuple[float, float]:
        return (
            self.left + self.offset_x + (x - self.min_x) * self.scale,
            self.top + self.offset_y + (self.max_y - y) * self.scale,
        )

    def to_model(self, px: float, py: float) -> tuple[float, float]:
        return (
            (px - self.left - self.offset_x) / self.scale + self.min_x,
            self.max_y - (py - self.top - self.offset_y) / self.scale,
        )

    def scale_ratio(self) -> str:
        """Nominal drawing scale, e.g. '1:50' (page mm per model mm)."""
        return f"1:{max(1, round(1 / self.scale))}"


def fit_transform(bounds: tuple[float, float, float, float], region: Rect) -> Transform:
    """Uniform transform fitting ``bounds`` (model mm) into ``region`` (page mm)."""
    min_x, min_y, max_x, max_y = bounds
    gw, gh = max_x - min_x, max_y - min_y
    if gw < 0 or gh < 0:
        raise ValueError("invalid bounds")
    degenerate = None
    if gw == 0 and gh == 0:
        scale = min(region.w, region.h) / NOMINAL_EXTENT_MM
        degenerate = "point"
    elif gw == 0:
        scale = region.h / gh
        degenerate = "zero_width"
    elif gh == 0:
        scale = region.w / gw
        degenerate = "zero_height"
    else:
        scale = min(region.w / gw, region.h / gh)
    if not scale > 0:
        raise ValueError("non-positive scale")
    offset_x = (region.w - gw * scale) / 2
    offset_y = (region.h - gh * scale) / 2
    return Transform(
        scale, min_x, min_y, max_x, max_y, region.x0, region.y0, offset_x, offset_y, degenerate
    )
