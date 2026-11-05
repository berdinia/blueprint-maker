"""Local-only evidence-processing framework for floor-plan reconstruction.

Pipeline:

    source inventory → normalization → local extraction → structured observations
    → source-family analysis → fragment classification → room-identity resolution
    → canonical evidence model → deterministic SVG/DXF/PNG/PDF generation
    → mechanical validation → independent local visual review → bounded repair
    → human-review package

The canonical structured model — never an LLM response or a rendered image —
is the factual authority. All inference runs against configured local endpoints;
there is no cloud provider and no implicit fallback.
"""

__version__ = "0.2.0"

__all__ = ["__version__"]
