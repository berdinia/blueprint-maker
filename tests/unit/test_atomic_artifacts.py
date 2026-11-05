from __future__ import annotations

import json

import pytest

from blueprint_pipeline.errors import ArtifactError
from blueprint_pipeline.io.artifacts import ArtifactStore, Freshness
from blueprint_pipeline.io.atomic import atomic_write_bytes, atomic_write_json


def test_failed_validation_never_replaces_valid_file(tmp_path):
    p = tmp_path / "out.json"
    atomic_write_json(p, {"ok": 1})

    def reject(_):
        raise ValueError("bad")

    with pytest.raises(ArtifactError):
        atomic_write_bytes(p, b"{broken", validate=reject)
    assert json.loads(p.read_text()) == {"ok": 1}
    assert [x.name for x in tmp_path.iterdir()] == ["out.json"]  # no temp files left


def test_store_freshness_states(tmp_path):
    s = ArtifactStore(tmp_path)
    assert s.check("a.json").state is Freshness.MISSING
    s.write_json(
        "a.json", {"v": 1}, stage="t", inputs={"in": "1" * 64}, config_hash="c1", model="m1"
    )
    assert s.check("a.json", inputs={"in": "1" * 64}, config_hash="c1", model="m1").is_current
    stale = s.check("a.json", inputs={"in": "2" * 64})
    assert stale.state is Freshness.STALE and "input 'in'" in stale.reasons[0]
    assert s.check("a.json", model="m2").state is Freshness.STALE
    assert s.check("a.json", config_hash="c2").state is Freshness.STALE
    s.path("a.json").write_text('{"v": 2}')  # edited behind the store's back
    assert s.check("a.json").state is Freshness.CORRUPT
    s.meta_path("a.json").unlink()
    assert s.check("a.json").state is Freshness.NO_METADATA


def test_error_artifact_is_separate_and_cleared_on_success(tmp_path):
    s = ArtifactStore(tmp_path)
    s.write_json("r.json", {"good": True}, stage="t")
    s.write_error("r.json", {"status": "EMPTY_RESPONSE"})
    assert s.read_json("r.json") == {"good": True}
    assert s.read_error("r.json")["status"] == "EMPTY_RESPONSE"
    s.write_json("r.json", {"good": 2}, stage="t")
    assert s.read_error("r.json") is None


def test_idempotent_rewrite_keeps_metadata(tmp_path):
    s = ArtifactStore(tmp_path)
    m1 = s.write_json("x.json", {"a": 1}, stage="t", inputs={"i": "x"})
    m2 = s.write_json("x.json", {"a": 1}, stage="t", inputs={"i": "x"})
    assert m1.created_at == m2.created_at and m1.sha256 == m2.sha256


def test_store_refuses_paths_outside_root(tmp_path):
    with pytest.raises(ArtifactError):
        ArtifactStore(tmp_path / "work").path("../escape.json")
