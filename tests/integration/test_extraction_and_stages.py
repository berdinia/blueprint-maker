"""Extraction runner and stage orchestration with real files and a loopback model."""

from __future__ import annotations

import json
import subprocess
import sys

import pytest
from PIL import Image

from blueprint_pipeline.config.settings import load_settings
from blueprint_pipeline.errors import ConfigError, LockError
from blueprint_pipeline.extraction import ExtractionRunner, current_rel
from blueprint_pipeline.orchestration.context import PipelineContext
from blueprint_pipeline.orchestration.lock import HeavyJobLock
from blueprint_pipeline.orchestration.stages import pipeline_status, resume, run_stage

GOOD = {
    "source_id": None,
    "legible": True,
    "fragment_class": "ROOM",
    "observations": [
        {"kind": "room_label", "raw_text": "ALPHA"},
        {"kind": "dimension", "raw_text": "3000 mm", "subject": "ALPHA", "dimension_axis": "width"},
    ],
}


@pytest.fixture
def project(tmp_path, fake_server):
    (tmp_path / "input").mkdir()
    Image.new("RGB", (64, 48), "white").save(tmp_path / "input" / "syn-a.png")
    (tmp_path / "register").mkdir()
    (tmp_path / "register" / "source-register.json").write_text(
        json.dumps(
            {
                "schema": "blueprint.source-register",
                "schema_version": "1.0.0",
                "synthetic": True,
                "sources": [{"source_id": "SYN-A", "path": "syn-a.png"}],
            }
        )
    )
    settings = load_settings(
        project_root=tmp_path,
        env={},
        overrides={
            "inference.primary": {"url": fake_server.url, "api": "openai"},
            "models.extract": "syn-extract",
            "models.review": "syn-review",
            "retry.max_attempts": 1,
            "inference.request_timeout_s": 10,
        },
    )
    ctx = PipelineContext(settings)
    assert run_stage(ctx, "inventory").status == "PASS"
    assert run_stage(ctx, "normalize").status == "PASS"
    return ctx


def _respond(fake_server, ctx, spec):
    sha = ctx.store.read_json("normalized/SYN-A.json")["normalized_sha256"]
    fake_server.respond_for_image(sha, spec)


def test_success_then_failure_never_overwrites_valid(project, fake_server):
    ctx = project
    _respond(
        fake_server,
        ctx,
        {"mode": "content", "content": json.dumps({**GOOD, "source_id": "SYN-OTHER"})},
    )
    runner = ExtractionRunner(ctx)
    rec_src = ctx.register().get("SYN-A")
    assert runner.run(rec_src).status == "SUCCESS"
    valid = ctx.store.path(current_rel("SYN-A")).read_bytes()
    rec = json.loads(valid)
    assert rec["source_id"] == "SYN-A" and rec["provenance"]["source_id_mismatch"]
    assert rec["observations"][1]["value_mm"] == 3000.0
    assert rec["input_sha256"] and rec["normalized_sha256"]
    # unchanged inputs -> skipped (idempotent), no new request
    n = len(fake_server.requests)
    assert runner.run(rec_src).status == "SKIPPED_CURRENT" and len(fake_server.requests) == n
    # forced re-run that fails must not replace the last valid result
    _respond(fake_server, ctx, {"mode": "empty"})
    s = runner.run(rec_src, force=True)
    assert s.status == "EMPTY_RESPONSE"
    assert ctx.store.path(current_rel("SYN-A")).read_bytes() == valid
    err = ctx.store.read_error(current_rel("SYN-A"))
    assert err["status"] == "EMPTY_RESPONSE" and err["last_valid_preserved"] is True
    assert len(list(ctx.store.path("extraction/SYN-A/attempts").iterdir())) == 2


def test_output_input_hash_mismatch_detected(project, fake_server):
    ctx = project
    _respond(fake_server, ctx, {"mode": "content", "content": json.dumps(GOOD)})
    ExtractionRunner(ctx).run(ctx.register().get("SYN-A"))
    norm = ctx.store.read_json("normalized/SYN-A.json")
    fresh = ctx.store.check(
        current_rel("SYN-A"),
        inputs={"normalized": "f" * 64, "source": norm["input_sha256"], "prompt": "x"},
    )
    assert fresh.state.value == "STALE"
    # evidence replaced on disk after inventory -> normalize refuses (immutability)
    Image.new("RGB", (64, 48), "black").save(ctx.settings.paths.input_dir / "syn-a.png")
    res = run_stage(ctx, "normalize")
    assert res.status == "FAIL" and "immutability" in res.summary


def test_config_errors_fail_before_lock_or_request(tmp_path, fake_server, project):
    ctx = project
    bad = PipelineContext(load_settings(project_root=ctx.settings.paths.project_root, env={}))
    with pytest.raises(ConfigError):
        ExtractionRunner(bad).run(bad.register().get("SYN-A"))
    assert run_stage(bad, "extract").status == "BLOCKED"
    assert not fake_server.requests


def test_lock_busy_blocks_without_any_request(project, fake_server):
    ctx = project
    _respond(fake_server, ctx, {"mode": "content", "content": json.dumps(GOOD)})
    holder = HeavyJobLock(ctx.settings.paths.lock_path, operation="review")
    holder.acquire()
    try:
        with pytest.raises(LockError):
            ExtractionRunner(ctx).run(ctx.register().get("SYN-A"))
        res = run_stage(ctx, "extract")
        assert res.status == "BLOCKED" and "held by a live process" in res.summary
        assert not fake_server.requests
    finally:
        holder.release()


def test_status_and_resume_follow_artifact_hashes(project, fake_server, tmp_path):
    ctx = project
    _respond(fake_server, ctx, {"mode": "content", "content": json.dumps(GOOD)})
    fake_server.enqueue(
        {
            "mode": "content",
            "content": json.dumps({"legible": True, "verdict": "PASS", "issues": []}),
        }
    )
    results = resume(ctx)
    assert [r.stage for r in results][-1] == "package"
    st = pipeline_status(ctx)
    assert all(s["freshness"] == "RECORDED" for s in st["stages"])
    assert st["review"]["state"] == "CURRENT" and st["lock"]["state"] == "FREE"
    assert resume(ctx) == []  # nothing to do
    # tamper with the render: status reports it stale, review becomes stale too
    png = ctx.store.path("render/plan.png")
    png.write_bytes(png.read_bytes() + b"\0")
    st = pipeline_status(ctx)
    render = next(s for s in st["stages"] if s["stage"] == "render")
    assert render["freshness"] == "STALE"
    assert st["review"]["state"] == "STALE"


def test_cli_entry_point(project):
    ctx = project
    root = str(ctx.settings.paths.project_root)
    out = subprocess.run(
        [sys.executable, "-m", "blueprint_pipeline.cli", "--project", root, "status", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout)["stages"][0]["stage"] == "inventory"
    out = subprocess.run(
        [sys.executable, "-m", "blueprint_pipeline.cli", "--project", root, "lock", "inspect"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout)["state"] == "FREE"
    bad = subprocess.run(
        [sys.executable, "-m", "blueprint_pipeline.cli", "--project", root, "extract"],
        capture_output=True,
        text=True,
        env={"BLUEPRINT_PROXY_ENDPOINT": "x", "PATH": ""},
    )
    assert bad.returncode == 2 and "legacy environment variable" in bad.stderr


def test_cli_discovers_project_config(tmp_path):
    root = tmp_path / "proj"
    subprocess.run(
        [sys.executable, "-m", "blueprint_pipeline.cli", "--project", str(root), "init"],
        check=True,
        capture_output=True,
    )
    cfg = (root / "blueprint.yaml").read_text().replace("extract: null", "extract: syn-x")
    (root / "blueprint.yaml").write_text(cfg)
    out = subprocess.run(
        [sys.executable, "-m", "blueprint_pipeline.cli", "--project", str(root), "config"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert '"extract": "syn-x"' in out.stdout
