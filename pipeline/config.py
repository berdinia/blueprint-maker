"""Configuration loader for the blueprint reconstruction pipeline.

All configuration comes from environment variables; no absolute paths or
project-specific identifiers are embedded.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Environment variable names (documented, not hardcoded values)
# ---------------------------------------------------------------------------
ENV = {
    "native_endpoint": "BLUEPRINT_NATIVE_ENDPOINT",
    "proxy_endpoint":  "BLUEPRINT_PROXY_ENDPOINT",
    "extract_model":   "BLUEPRINT_EXTRACT_MODEL",
    "review_model":    "BLUEPRINT_REVIEW_MODEL",
    "input_dir":       "BLUEPRINT_INPUT_DIR",
    "output_dir":      "BLUEPRINT_OUTPUT_DIR",
    "render_dir":      "BLUEPRINT_RENDER_DIR",
    "model_dir":       "BLUEPRINT_MODEL_DIR",
    "registry_path":   "BLUEPRINT_REGISTRY",
    "lock_path":       "BLUEPRINT_LOCK_PATH",
}

@dataclass
class RenderParams:
    """Rendering and layout parameters. Millimetres are the canonical unit."""
    page_w:  float = 210.0
    page_h:  float = 297.0
    margin:  float = 15.0
    title_h: float = 26.0
    notes_h: float = 46.0
    gap:     float = 1600.0
    nominal_placeholder_mm: float = 1400.0

@dataclass
class Config:
    """Fully resolved pipeline configuration.

    All fields have safe defaults that do NOT reference any real project.
    """
    native_endpoint: Optional[str] = None
    proxy_endpoint:  Optional[str] = None
    extract_model:   str = "qwen3.8:27b"
    review_model:    str = "gemma4:31b"

    input_dir:  str = ""
    output_dir: str = ""
    render_dir: str = ""
    model_dir:  str = ""
    registry_path: str = "source-registry.json"
    lock_path: str = ".blueprint.lock"

    render: RenderParams = field(default_factory=RenderParams)

    def resolve_paths(self, project_root: str = ".") -> "Config":
        """Resolve relative paths against ``project_root`` and return self."""
        pr = str(Path(project_root).resolve())
        self.input_dir  = str(Path(self.input_dir or pr, "input"))
        self.output_dir = str(Path(self.output_dir or pr, "results"))
        self.render_dir = str(Path(self.render_dir or pr, "rendered"))
        self.model_dir  = str(Path(self.model_dir or pr, "whole-house/model"))
        reg = self.registry_path
        if not os.path.isabs(reg):
            self.registry_path = str(Path(pr, reg))
        lk = self.lock_path
        if not os.path.isabs(lk):
            self.lock_path = str(Path(pr, lk))
        return self

    # --- endpoint selection --------------------------------------------------
    def native_url(self) -> str:
        ep = self.native_endpoint or os.environ.get("BLUEPRINT_NATIVE_ENDPOINT")
        if not ep:
            raise RuntimeError(
                "Native endpoint not configured. Set BLUEPRINT_NATIVE_ENDPOINT.")
        return ep.rstrip("/")

    def proxy_url(self) -> str:
        ep = self.proxy_endpoint or os.environ.get("BLUEPRINT_PROXY_ENDPOINT")
        if not ep:
            raise RuntimeError(
                "Proxy endpoint not configured. Set BLUEPRINT_PROXY_ENDPOINT.")
        return ep.rstrip("/")


def load_config(project_root: str | None = None) -> Config:
    """Load configuration from environment variables."""
    cfg = Config()

    cfg.native_endpoint = os.environ.get("BLUEPRINT_NATIVE_ENDPOINT")
    cfg.proxy_endpoint  = os.environ.get("BLUEPRINT_PROXY_ENDPOINT")

    ext = os.environ.get("BLUEPRINT_EXTRACT_MODEL")
    if ext:
        cfg.extract_model = ext
    rev = os.environ.get("BLUEPRINT_REVIEW_MODEL")
    if rev:
        cfg.review_model = rev

    for env_key, attr in [
        ("BLUEPRINT_INPUT_DIR",     "input_dir"),
        ("BLUEPRINT_OUTPUT_DIR",    "output_dir"),
        ("BLUEPRINT_RENDER_DIR",    "render_dir"),
        ("BLUEPRINT_MODEL_DIR",     "model_dir"),
        ("BLUEPRINT_REGISTRY",      "registry_path"),
        ("BLUEPRINT_LOCK_PATH",     "lock_path"),
    ]:
        val = os.environ.get(env_key)
        if val:
            setattr(cfg, attr, val)

    # Render parameters
    rp = cfg.render
    rp.page_w  = float(os.environ.get("BLUEPRINT_PAGE_W_MM", rp.page_w))
    rp.page_h  = float(os.environ.get("BLUEPRINT_PAGE_H_MM", rp.page_h))
    rp.margin  = float(os.environ.get("BLUEPRINT_MARGIN_MM", rp.margin))
    rp.title_h = float(os.environ.get("BLUEPRINT_TITLE_H_MM", rp.title_h))
    rp.notes_h = float(os.environ.get("BLUEPRINT_NOTES_H_MM", rp.notes_h))
    rp.gap     = float(os.environ.get("BLUEPRINT_GAP_MM", rp.gap))

    root = project_root or os.environ.get("BLUEPRINT_PROJECT_ROOT", ".")
    return cfg.resolve_paths(root)
