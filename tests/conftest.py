"""Shared fixtures. All data is synthetic."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest

from blueprint_pipeline.config.settings import Settings, load_settings
from blueprint_pipeline.testing.fake_server import FakeModelServer

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_server():
    with FakeModelServer() as srv:
        yield srv


def make_settings(tmp_path: Path, url: str | None = None, **overrides: Any) -> Settings:
    ov: dict[str, Any] = {"retry.max_attempts": 1, "retry.backoff_s": 0}
    if url:
        ov.update(
            {
                "inference.primary": {"url": url, "api": "openai"},
                "inference.connect_timeout_s": 2,
                "inference.request_timeout_s": 10,
                "models.extract": "syn-extract",
                "models.review": "syn-review",
            }
        )
    ov.update(overrides)
    return load_settings(project_root=tmp_path, env={}, overrides=ov)


@pytest.fixture
def settings_factory(tmp_path):
    def _f(url: str | None = None, **ov: Any) -> Settings:
        return make_settings(tmp_path, url, **ov)

    return _f


def feature(**kw: Any) -> dict[str, Any]:
    base = {
        "source_ids": ["SYN-A"],
        "evidence_origin_ids": ["ORIGIN-SYN-A"],
        "raw_observation_ids": [],
        "confidence": None,
        "placement_state": "ANCHORED",
        "revision_state": "CURRENT",
        "human_confirmation": "CONFIRMED",
        "conflict_refs": [],
        "hypothetical": False,
        "uncertainty": None,
        "decision_ids": ["DEC-T"],
    }
    base.update(kw)
    return base


def rect(x0: float, y0: float, x1: float, y1: float) -> list[list[float]]:
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]


def make_model(rooms: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
    """Minimal valid canonical model built from room specs."""
    from blueprint_pipeline.canonical.builder import _r
    from blueprint_pipeline.canonical.geometry import split_collinear_edges

    rooms = (
        rooms
        if rooms is not None
        else [
            feature(
                room_id="ROOM-A",
                display_name="A",
                polygon_mm=rect(0, 0, 3000, 2000),
                measurement_ids=[],
            ),
            feature(
                room_id="ROOM-B",
                display_name="B",
                polygon_mm=rect(3000, 0, 5000, 2000),
                measurement_ids=[],
            ),
        ]
    )
    edges = []
    for r in rooms:
        p = r["polygon_mm"]
        if p:
            edges += [
                ((p[i][0], p[i][1]), (p[(i + 1) % len(p)][0], p[(i + 1) % len(p)][1]), r["room_id"])
                for i in range(len(p))
            ]
    walls = []
    for i, (a, b, owners) in enumerate(split_collinear_edges(edges), start=1):
        a2, b2 = sorted([(_r(a[0]), _r(a[1])), (_r(b[0]), _r(b[1]))])
        walls.append(
            feature(
                wall_id=f"W-{i:03d}",
                start_mm=list(a2),
                end_mm=list(b2),
                room_ids=owners,
                thickness_mm=None,
                decision_ids=[],
            )
        )
    m = {
        "schema": "blueprint.canonical-model",
        "schema_version": "1.0.0",
        "model_id": "MODEL-TEST",
        "synthetic": True,
        "units": "mm",
        "coordinate_system": {"y_axis": "up", "status": "anchored", "origin": "test"},
        "rooms": rooms,
        "walls": walls,
        "openings": [],
        "adjacency": [],
        "measurements": [],
        "conflicts": [],
        "revisions": [],
        "decisions": [],
        "unresolved_questions": [],
        "fragments_unplaced": [],
        "provenance": {"inputs": {}, "builder": "test"},
    }
    m.update(kw)
    return copy.deepcopy(m)
