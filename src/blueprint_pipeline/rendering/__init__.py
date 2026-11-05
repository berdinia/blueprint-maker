"""Deterministic rendering: one scene → SVG, PNG, PDF (page) and DXF (model 1:1)."""

from .layout import PageLayout, Rect, Transform, compute_layout, fit_transform
from .scene import RenderOverrides, Scene, build_scene, text_box
from .writers import write_dxf, write_pdf, write_png, write_svg

__all__ = [
    "PageLayout",
    "Rect",
    "RenderOverrides",
    "Scene",
    "Transform",
    "build_scene",
    "compute_layout",
    "fit_transform",
    "text_box",
    "write_dxf",
    "write_pdf",
    "write_png",
    "write_svg",
]
