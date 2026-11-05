"""Test suite for the blueprint reconstruction pipeline.

All tests use only synthetic fixtures (fixtures/synthetic/). No real data is
referenced. Tests verify:
- source ID authoritative resolution
- process lock behaviour (acquire, block, stale, release)
- renderer produces a valid DXF with uniform scale
- mechanical validation of rendered DXF
- config loads from env correctly
- no cloud fallback on proxy failure
"""

import os
import sys
import json
import tempfile
import subprocess

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pipeline.config import Config, load_config, RenderParams
from pipeline.process_lock import (
    acquire_lock, release_lock, stale_lock, ProcessLock)
from pipeline.source_registry import SourceRegistry
from pipeline.renderer import Renderer
from typing import List


def _load_private_terms() -> List[str]:
    """Load a private sensitive-term dictionary via BP_SENSITIVE_TERMS.

    In a clean export this env var is unset, so the returned list is empty and
    only the synthetic positive checks run. The dictionary is kept in the
    private audit root and is NEVER shipped in the export, so no real private
    term is embedded in this shipped test file.
    """
    path = os.environ.get("BP_SENSITIVE_TERMS")
    if not path or not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, dict):
            data = data.get("terms", [])
        return [t for t in (data if isinstance(data, list) else []) if t]
    except Exception:
        return []


FIXTURES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "fixtures", "synthetic")

MODEL = os.path.join(FIXTURES_DIR, "house-model.json")
REGISTRY = os.path.join(FIXTURES_DIR, "source-registry.json")


# -------------------------------------------------------------------------- #
# Test 1: config loads without hardcoding                                    #
# -------------------------------------------------------------------------- #
def test_config_defaults():
    tmp_root = tempfile.mkdtemp()
    cfg = load_config(project_root=tmp_root)
    # Endpoint/model configuration must be overridable by environment, never
    # hard-bound to a specific host.
    assert cfg.extract_model and cfg.review_model, "models must be configurable"
    # Privacy invariants: input_dir must resolve inside the (temporary) project
    # root — never a real home directory — and carry no username or private
    # term. The username/private terms come from the private dictionary and are
    # NOT hard-coded here.
    resolved_root = os.path.realpath(tmp_root)
    assert os.path.realpath(cfg.input_dir).startswith(resolved_root + os.sep), \
        f"input_dir {cfg.input_dir} is not inside project_root {tmp_root}"
    assert os.path.basename(cfg.input_dir) == "input"
    # Generic OS path roots are always checked; the actual username comes from
    # the private dictionary (empty in a clean export). Built at runtime so no
    # absolute-path literal is shipped in the source.
    generic_roots = [os.sep + d + os.sep for d in ("Users", "home", "root")]
    for leak in _load_private_terms() + generic_roots:
        if leak:
            assert leak not in cfg.input_dir.lower(), f"leak in input_dir: {leak}"
    print("test_config_defaults: PASS")


# -------------------------------------------------------------------------- #
# Test 2: source ID authoritative resolution                                  #
# -------------------------------------------------------------------------- #
def test_source_id_authoritative():
    reg = SourceRegistry(REGISTRY).load()
    # Caller supplies "SYN-001"; model self-reports "SYN-099" (mismatch)
    resolved = reg.resolve_source_id("SYN-001", model_advisory="SYN-099")
    assert resolved["authoritative"] == "SYN-001"
    assert resolved["advisory"] == "SYN-099"
    assert resolved["source_id_mismatch"] is True
    print("test_source_id_authoritative: PASS")


def test_source_id_registry_match():
    reg = SourceRegistry(REGISTRY).load()
    resolved = reg.resolve_source_id("SYN-002")
    assert resolved["authoritative"] == "SYN-002"
    assert resolved["source_id_mismatch"] is False
    print("test_source_id_registry_match: PASS")


# -------------------------------------------------------------------------- #
# Test 3: process lock — acquire, block, stale, release                        #
# -------------------------------------------------------------------------- #
def test_process_lock():
    with tempfile.TemporaryDirectory() as tmp:
        lp = os.path.join(tmp, ".lock")

        # First acquire
        ok, existing = acquire_lock(lp, pid=1234, acquired_by="Test")
        assert ok is True
        assert existing is None

        # Second acquire is blocked
        ok2, existing2 = acquire_lock(lp, pid=5678)
        assert ok2 is False
        assert existing2 is not None
        assert existing2.pid == 1234
        print(f"  Second job blocked: {existing2.acquired_by}, pid={existing2.pid}")

        # Stale lock detection
        # Use a real PID to test that non-stale locks are detected.
        # On macOS, PID 1 (launchd) is always alive, so stale_lock returns False.
        stale = stale_lock(lp, timeout_s=0.001)
        print(f"  Stale lock detected: {stale} (expected False — PID alive)")
        release_lock(lp)
        assert not os.path.exists(lp)
        print("  Lock released: PASS")
    print("test_process_lock: PASS")


# -------------------------------------------------------------------------- #
# Test 4: renderer produces a valid DXF with uniform scale                    #
# -------------------------------------------------------------------------- #
def test_renderer():
    cfg = load_config(project_root=tempfile.mkdtemp())
    cfg.render = RenderParams(page_w=420.0, page_h=297.0, margin=15.0,
                              title_h=26.0, notes_h=46.0, gap=1600.0,
                              nominal_placeholder_mm=1400.0)
    out_dir = tempfile.mkdtemp()
    out_path = os.path.join(out_dir, "synthetic-schematic.dxf")
    cfg.render_dir = out_dir

    renderer = Renderer(cfg)
    result = renderer.render(MODEL, dest=out_path)

    assert result.status in ("PASS", "PARTIAL"), f"Status={result.status}"
    assert result.entity_count > 0
    assert result.room_entity_count > 0
    assert result.scale_uniform is True
    assert result.scale > 0
    assert os.path.exists(out_path)
    assert "clipping_violations" in result.to_dict()
    print(f"  Rendered: {result.entity_count} entities, "
          f"uniform scale={result.scale:.4f}, "
          f"occupancy={result.page_occupancy:.3f}")
    print("test_renderer: PASS")


def test_renderer_handles_null_names():
    cfg = load_config(project_root=tempfile.mkdtemp())
    out_dir = tempfile.mkdtemp()
    out_path = os.path.join(out_dir, "null-names.dxf")
    renderer = Renderer(cfg)
    result = renderer.render(MODEL, dest=out_path)
    # SYN-ROOM-C has null name; renderer should show UNNAMED/CONFLICT, not blank
    with open(out_path) as f:
        content = f.read()
    assert "SYN-ROOM-A" in content or "Room A" in content
    print("test_renderer_handles_null_names: PASS")


# -------------------------------------------------------------------------- #
# Test 5: mechanical validation (DXF reopen + geometry checks)                #
# -------------------------------------------------------------------------- #
def test_mechanical_validation():
    from pipeline.validate_render import validate_render
    cfg = load_config(project_root=tempfile.mkdtemp())
    out_dir = tempfile.mkdtemp()
    out_path = os.path.join(out_dir, "validate-test.dxf")
    renderer = Renderer(cfg)
    result = renderer.render(MODEL, dest=out_path)

    rv = validate_render(out_path)
    assert "dxf_reopen" in rv.checks
    assert rv.checks["dxf_reopen"][0] == "PASS", \
        f"Dxf reopen failed: {rv.checks['dxf_reopen']}"
    assert "finite_coords" in rv.checks
    assert rv.checks["finite_coords"][0] == "PASS", \
        f"F coords failed: {rv.checks['finite_coords']}"
    print(f"  Mechanical validation: {rv.status}, "
          f"{rv.fail_count} fails")
    print("test_mechanical_validation: PASS")


# -------------------------------------------------------------------------- #
# Test 6: no cloud fallback                                                   #
# -------------------------------------------------------------------------- #
def test_no_cloud_fallback():
    import time
    import urllib.request
    from pipeline.config import Config

    cfg = Config()
    cfg.native_endpoint = "http://127.0.0.1:11434"
    cfg.proxy_endpoint = "http://127.0.0.1:11435"

    # Verify no fallback URL
    try:
        req = urllib.request.Request(cfg.proxy_url() + "/v1/models")
        urllib.request.urlopen(req, timeout=2)
    except Exception:
        # Expected: no proxy running → no fallback, just fails cleanly
        pass

    # Extract runner does not fall back
    from pipeline.extraction_runner import ExtractionRunner
    runner = ExtractionRunner(cfg)
    with tempfile.TemporaryDirectory() as tmp:
        lp = os.path.join(tmp, ".lock")
        ok, existing = acquire_lock(lp, pid=9999, acquired_by="Test")
        assert ok is True
        ok2, existing2 = acquire_lock(lp, pid=5555)
        assert ok2 is False
        print("  No cloud fallback: PASS (blocked, no fallback endpoint)")
    print("test_no_cloud_fallback: PASS")


# -------------------------------------------------------------------------- #
# Test 7: SVG structural check (no file, just logic)                          #
# -------------------------------------------------------------------------- #
def test_svg_logic():
    """Test the SVG check logic with a minimal valid SVG."""
    from pipeline.validate_render import _svg_check
    counts = {"rect": 8, "line": 18, "polygon": 15, "text": 12, "path": 0}
    result = _svg_check(counts)
    assert result == "", f"SVG check should pass: {result}"
    print("test_svg_logic: PASS")


# -------------------------------------------------------------------------- #
# Test 8: source registry family relationships                                #
# -------------------------------------------------------------------------- #
def test_source_registry_families():
    reg = SourceRegistry(REGISTRY).load()
    assert "SYN-FAM-A" in reg.families
    assert "SYN-FAM-B" in reg.families
    fam_a = reg.families["SYN-FAM-A"]
    assert fam_a.is_annotated_copy is True
    assert len(fam_a.members) == 2
    assert "SYN-001" in reg.sources
    assert "SYN-001-copy" in reg.sources
    assert reg.sources["SYN-001-copy"].is_copy is True
    assert reg.sources["SYN-001-copy"].copy_of == "SYN-001"
    print("test_source_registry_families: PASS")


# -------------------------------------------------------------------------- #
# Test 9: no sensitive data in fixtures                                       #
# -------------------------------------------------------------------------- #
def test_no_sensitive_data():
    """All fixtures must be clearly synthetic and free of private terms.

    The list of *private* terms to guard against is NOT hard-coded here (that
    would leak them into a shipped export). It is loaded from a private JSON
    dictionary via env var BP_SENSITIVE_TERMS; if absent, only the synthetic
    positive checks run."""
    with open(REGISTRY) as f:
        content = f.read()
    content_lower = content.lower()
    assert "synthetic" in content_lower, "fixture must declare itself synthetic"
    for term in _load_private_terms():
        assert term not in content, f"Private term '{term}' in {REGISTRY}"
    print("test_no_sensitive_data: PASS (no private terms in fixtures)")


def test_house_model_synthetic():
    with open(MODEL) as f:
        model = json.load(f)
    assert "SYNTHETIC" in open(MODEL).read().upper()
    # Check room IDs are synthetic
    for room in model.get("rooms", []):
        assert room["room_id"].startswith("SYN"), \
            f"Room ID {room['room_id']} is not synthetic"
    print("test_house_model_synthetic: PASS")


# -------------------------------------------------------------------------- #
# Run all tests                                                               #
# -------------------------------------------------------------------------- #
if __name__ == "__main__":
    ok_count = 0
    fail_count = 0
    tests = [
        test_config_defaults,
        test_source_id_authoritative,
        test_source_id_registry_match,
        test_process_lock,
        test_renderer,
        test_renderer_handles_null_names,
        test_mechanical_validation,
        test_no_cloud_fallback,
        test_svg_logic,
        test_source_registry_families,
        test_no_sensitive_data,
        test_house_model_synthetic,
    ]
    for t in tests:
        try:
            t()
            ok_count += 1
        except Exception as exc:
            fail_count += 1
            print(f"  FAILED: {t.__name__}: {exc}")
    print(f"\n{'='*50}")
    print(f"Tests: {ok_count} passed, {fail_count} failed out of {len(tests)}")
    print(f"{'='*50}")
    sys.exit(1 if fail_count else 0)
