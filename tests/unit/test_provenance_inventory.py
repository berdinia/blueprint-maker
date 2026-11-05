from __future__ import annotations

import pytest
from PIL import Image

from blueprint_pipeline.errors import ProvenanceError
from blueprint_pipeline.evidence.provenance import enforce_source_identity
from blueprint_pipeline.inventory import SourceRegister, run_inventory
from blueprint_pipeline.io.atomic import sha256_file


def reg_doc(*sources):
    return {
        "schema": "blueprint.source-register",
        "schema_version": "1.0.0",
        "sources": list(sources),
    }


# -------------------------------------------------------------- provenance
def test_model_generated_wrong_source_id_is_overridden_and_reported():
    parsed = {"source_id": "SYN-WRONG", "observations": []}
    out, prov, warns = enforce_source_identity(parsed, "SYN-SRC-001")
    assert out["source_id"] == "SYN-SRC-001"
    assert parsed["source_id"] == "SYN-WRONG"  # input untouched
    assert prov.model_reported_source_id == "SYN-WRONG" and prov.source_id_mismatch
    assert [w["code"] for w in warns] == ["SOURCE_ID_MISMATCH"]


def test_missing_model_source_id_is_not_a_mismatch():
    out, prov, warns = enforce_source_identity({"observations": []}, "SYN-SRC-001")
    assert out["source_id"] == "SYN-SRC-001" and not prov.source_id_mismatch and not warns


def test_known_but_different_model_id_never_becomes_authority():
    # Regression for D-P1: a model echoing *another registered* ID must not win.
    out, prov, _ = enforce_source_identity({"source_id": "SYN-SRC-002"}, "SYN-SRC-001")
    assert out["source_id"] == "SYN-SRC-001" and prov.source_id_mismatch


def test_non_string_source_id_flagged():
    _, prov, warns = enforce_source_identity({"source_id": 7}, "SYN-SRC-001")
    assert prov.source_id_mismatch
    assert {w["code"] for w in warns} == {"SOURCE_ID_INVALID_TYPE", "SOURCE_ID_MISMATCH"}


# -------------------------------------------------------------- register
def test_duplicate_source_ids_rejected():
    with pytest.raises(ProvenanceError, match="duplicate source_id"):
        SourceRegister.from_document(
            reg_doc(
                {"source_id": "SYN-1", "path": "a.png"}, {"source_id": "SYN-1", "path": "b.png"}
            )
        )


def test_case_only_difference_and_duplicate_paths_rejected():
    with pytest.raises(ProvenanceError, match="differs only in case"):
        SourceRegister.from_document(
            reg_doc(
                {"source_id": "SYN-a", "path": "a.png"}, {"source_id": "SYN-A", "path": "b.png"}
            )
        )
    with pytest.raises(ProvenanceError, match="already registered"):
        SourceRegister.from_document(
            reg_doc(
                {"source_id": "SYN-1", "path": "a.png"}, {"source_id": "SYN-2", "path": "./a.png"}
            )
        )


def test_path_traversal_rejected():
    with pytest.raises(ProvenanceError, match="relative"):
        SourceRegister.from_document(reg_doc({"source_id": "SYN-1", "path": "../outside.png"}))


def test_missing_manifest_entry_and_missing_register(tmp_path):
    reg = SourceRegister.from_document(reg_doc({"source_id": "SYN-1", "path": "a.png"}))
    with pytest.raises(ProvenanceError, match="not in the source register"):
        reg.get("SYN-2")
    with pytest.raises(ProvenanceError, match="not found"):
        SourceRegister.load(tmp_path / "nope.json")


# -------------------------------------------------------------- inventory
def _png(p, color="white"):
    p.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (32, 32), color).save(p, format="PNG")


def test_inventory_findings(tmp_path):
    inp = tmp_path / "input"
    _png(inp / "a.png")
    (inp / "copy-of-syn-1.png").write_bytes((inp / "a.png").read_bytes())
    _png(inp / "c.jpg", "black")  # PNG content with .jpg extension
    _png(inp / "stray.png", "red")
    (inp / "d.pdf").write_bytes(b"%PDF-1.4\n")
    _png(inp / "e.png", "blue")
    reg = SourceRegister.from_document(
        reg_doc(
            {"source_id": "SYN-1", "path": "a.png"},
            {"source_id": "SYN-2", "path": "copy-of-syn-1.png"},
            {"source_id": "SYN-3", "path": "c.jpg"},
            {"source_id": "SYN-4", "path": "missing.png"},
            {"source_id": "SYN-5", "path": "d.pdf"},
            {"source_id": "SYN-6", "path": "e.png", "sha256": "0" * 64},
        )
    )
    inv = run_inventory(reg, inp)
    codes = {(f["code"], tuple(f["refs"])) for f in inv["findings"]}
    assert ("EXACT_DUPLICATE", ("SYN-1", "SYN-2")) in codes
    assert ("FILENAME_REGISTER_DISAGREEMENT", ("SYN-2", "SYN-1")) in codes
    assert ("EXTENSION_MISMATCH", ("SYN-3",)) in codes
    assert ("MISSING_FILE", ("SYN-4",)) in codes
    assert ("UNSUPPORTED_MEDIA", ("SYN-5",)) in codes
    assert ("CHECKSUM_MISMATCH", ("SYN-6",)) in codes
    assert any(c == "UNREGISTERED_FILE" for c, _ in codes)
    assert sorted(inv["blocked_source_ids"]) == ["SYN-4", "SYN-5", "SYN-6"]
    assert inv["status"] == "PARTIAL"
    by = {s["source_id"]: s for s in inv["sources"]}
    assert by["SYN-1"]["sha256"] == sha256_file(inp / "a.png")
    assert by["SYN-1"]["checksum_status"] == "RECORDED"
