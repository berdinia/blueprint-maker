"""Blueprint reconstruction pipeline.

Generic, cloud-safe scaffolding for a local-first floor-plan reconstruction
pipeline. All data flows through local model endpoints; no cloud calls are made.

The pipeline stages are:

1. Source inventory — enumerate the source registry.
2. Normalization — resolve source-family relationships and deduplication.
3. Local extraction — call the local vision model, parse JSON output.
4. Source-family analysis — group fragments, annotate copies, detect conflicts.
5. Fragment classification — classify each source, resolve identity.
6. Canonical model — build a coherent house model from reconciled extractions.
7. Deterministic CAD generation — render DXF/SVG/PNG from the model.
8. Mechanical validation — verify geometry, dimensions, and rendering integrity.
9. Independent visual review — local model reviews the rendered image.
10. Human review — final sign-off.

Configuration is read from environment variables (see config/example.env).
No absolute paths, model names, or project-specific data are embedded.
"""

from .config import Config, load_config
from .process_lock import ProcessLock, stale_lock, acquire_lock, release_lock
from .source_registry import SourceRegistry, Source, SourceFamily
from .extraction_runner import ExtractionRunner, ExtractionResult
from .renderer import Renderer, RenderResult
from .validate_render import validate_render, RenderValidation

__all__ = [
    "Config", "load_config",
    "ProcessLock", "stale_lock", "acquire_lock", "release_lock",
    "SourceRegistry", "Source", "SourceFamily",
    "ExtractionRunner", "ExtractionResult",
    "Renderer", "RenderResult",
    "validate_render", "RenderValidation",
]
