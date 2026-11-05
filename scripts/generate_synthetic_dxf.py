#!/usr/bin/env python3
"""Generate a synthetic schematic DXF from the synthetic house model.

Usage:
    python scripts/generate_synthetic_dxf.py

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
    config.render_dir = os.path.join(project_root, OUT_DIR)
    os.makedirs(config.render_dir, exist_ok=True)

    model_path = os.path.join(project_root, FIXTURE)
    renderer = Renderer(config)
    out_path = os.path.join(config.render_dir, "synthetic-schematic.dxf")
    result = renderer.render(model_path, dest=out_path)

    print("==================================================")
    print(f"Deterministic schematic: {result.status}")
    print(f"Scale: {result.scale} (uniform = {result.scale_uniform})")
    print(f"Entities: {result.entity_count}, rooms: {result.room_entity_count}")
    print(f"Page: {result.page_w} x {result.page_h} mm, "
          f"geom: {result.geom_w} x {result.geom_h} mm")
    print(f"Clipping: {result.clipping_violations} violations")
    print(f"Out: {out_path}")
    print("==================================================")

    if result.to_dict():
        import json
        report = result.to_dict()
        report_path = out_path.replace(".dxf", "_report.json")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        with open(report_path, "w") as f:
            json.dump(report, f, indent=2)
        print(f"Report: {report_path}")

    sys.exit(0 if result.status in ("PASS", "PARTIAL") else 1)


if __name__ == "__main__":
    main()
