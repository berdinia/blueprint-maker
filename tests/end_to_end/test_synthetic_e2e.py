"""Synthetic end-to-end run: every stage through real code paths, outputs verified."""

from __future__ import annotations

import json

import pytest

from blueprint_pipeline.testing.run import run_synthetic
from blueprint_pipeline.testing.synthetic import write_fixture_files
from conftest import REPO

pytestmark = pytest.mark.slow


def test_synthetic_end_to_end(tmp_path):
    result = run_synthetic(tmp_path / "syn")
    failed = [c for c in result["checks"] if not c["ok"]]
    assert result["passed"], failed
    names = {c["check"] for c in result["checks"]}
    assert {
        "provenance_enforced",
        "empty_response_not_success",
        "evidence_origins",
        "identity_resolution",
        "canonical_model",
        "dxf_reopen_and_units",
        "svg_valid",
        "png_visibility",
        "fit_to_page",
        "review_bounded_repair",
        "human_review_packet",
        "deterministic_render",
        "local_only_traffic",
    } <= names
    packet = tmp_path / "syn" / "results" / "human-review-packet"
    for f in (
        "canonical-model.json",
        "plan.dxf",
        "plan.svg",
        "plan.png",
        "plan.pdf",
        "validation-report.json",
        "validation-report.md",
        "visual-review.json",
        "README.md",
    ):
        assert (packet / f).is_file(), f
    assert "SYNTHETIC TEST DATA" in (packet / "README.md").read_text()


def test_committed_fixture_matches_code(tmp_path):
    write_fixture_files(tmp_path)
    for rel in (
        "register/source-register.json",
        "register/relationships.json",
        "register/decisions.json",
        "model-responses.json",
    ):
        committed = json.loads((REPO / "fixtures/synthetic/project" / rel).read_text())
        assert committed == json.loads((tmp_path / rel).read_text()), rel
