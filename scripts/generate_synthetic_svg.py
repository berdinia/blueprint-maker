#!/usr/bin/env python3
"""Generate a synthetic SVG from the synthetic house model.

Usage:
    python scripts/generate_synthetic_svg.py

This does NOT reference any real building. It uses fixtures/synthetic/house-model.json.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import Config, load_config
from pipeline.renderer import Renderer


FIXTURE = "fixtures/synthetic/house-model.json"
OUT_DIR = "output"


def main():
    root = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(root)
    config = load_config(project_root=project_root)
    os.makedirs(os.path.join(project_root, OUT_DIR), exist_ok=True)

    model_path = os.path.join(project_root, FIXTURE)
    renderer = Renderer(config)
    out_path = os.path.join(project_root, OUT_DIR, "synthetic-schematic.svg")
    result = renderer.render(model_path, dest=out_path)

    print(f"SVG: {result.status}")
    sys.exit(0 if result.status in ("PASS", "PARTIAL") else 1)


if __name__ == "__main__":
    main()
