from __future__ import annotations

import json

from blueprint_pipeline.inference.types import InferenceStatus, TransportResult
from blueprint_pipeline.io.artifacts import ArtifactStore
from blueprint_pipeline.io.atomic import sha256_file
from blueprint_pipeline.rendering.scene import RenderOverrides
from blueprint_pipeline.review import interpret_review, propose_adjustment, review_currency
from blueprint_pipeline.schemas import schema_errors

SHA = "a" * 64


def tr(content="", status=InferenceStatus.SUCCESS):
    return TransportResult(
        status=status,
        request_id="req-1",
        model_requested="syn-review",
        api="openai",
        content=content,
        http_status=200,
    )


def review(payload, **kw):
    rec = interpret_review(
        tr(json.dumps(payload)),
        artifact_rel="render/plan.png",
        artifact_sha256=SHA,
        cycle=0,
        model="syn-review",
        **kw,
    )
    assert not schema_errors(rec, "review-record")
    return rec


def test_legible_false_is_a_failed_review_even_if_model_says_pass():
    rec = review({"legible": False, "verdict": "PASS", "issues": []})
    assert rec["transport_status"] == "SUCCESS" and rec["parse_status"] == "SUCCESS"
    assert rec["review_verdict"] == "FAIL"


def test_http_200_and_parsed_json_are_not_review_success():
    empty = interpret_review(tr(""), artifact_rel="p.png", artifact_sha256=SHA, cycle=0, model="m")
    assert empty["review_verdict"] == "NOT_REVIEWED" and empty["transport_status"] == "SUCCESS"
    wrong_shape = interpret_review(
        tr('{"ok": true}'), artifact_rel="p.png", artifact_sha256=SHA, cycle=0, model="m"
    )
    assert wrong_shape["parse_status"] == "SUCCESS" and wrong_shape["schema_status"] == "FAILED"
    assert wrong_shape["review_verdict"] == "NOT_REVIEWED"
    dead = interpret_review(
        tr("", InferenceStatus.TRANSPORT_FAILED),
        artifact_rel="p.png",
        artifact_sha256=SHA,
        cycle=0,
        model="m",
    )
    assert (
        dead["transport_status"] == "TRANSPORT_FAILED" and dead["review_verdict"] == "NOT_REVIEWED"
    )


def test_reviewer_cannot_judge_facts():
    rec = review(
        {
            "legible": True,
            "verdict": "PASS",
            "issues": [
                {
                    "category": "wrong_room_size",
                    "severity": "blocking",
                    "description": "kitchen too small",
                },
                {"category": "collision", "severity": "minor", "description": "labels close"},
            ],
        }
    )
    assert rec["review_verdict"] == "PASS"  # minor presentation issue only
    assert [i["category"] for i in rec["out_of_scope_items"]] == ["wrong_room_size"]
    assert [i["category"] for i in rec["issues"]] == ["collision"]


def test_major_issue_fails():
    rec = review(
        {
            "legible": True,
            "verdict": "PASS",
            "issues": [{"category": "clipping", "severity": "major", "description": "x"}],
        }
    )
    assert rec["review_verdict"] == "FAIL"


def test_stale_review_detected(tmp_path):
    store = ArtifactStore(tmp_path)
    store.write_bytes("render/plan.png", b"png-1", stage="render")
    rec = {
        "reviewed_artifact": "render/plan.png",
        "reviewed_artifact_sha256": sha256_file(store.path("render/plan.png")),
    }
    assert review_currency(rec, store)[0] == "CURRENT"
    store.write_bytes("render/plan.png", b"png-2", stage="render")
    assert review_currency(rec, store)[0] == "STALE"
    assert review_currency(None, store)[0] == "MISSING"


def test_repair_is_bounded_and_presentation_only():
    ov = RenderOverrides()
    seen = []
    for _ in range(10):
        new, _notes = propose_adjustment([{"category": "collision"}], ov)
        if new is None:
            break
        seen.append(new)
        ov = new
    assert seen and seen[-1].prefer_tags  # eventually tags, then nothing more to do
    assert len(seen) <= 4
    assert propose_adjustment([{"category": "clipping"}], RenderOverrides())[0] is None
    assert set(RenderOverrides().to_dict()) == {"label_scale", "prefer_tags", "line_weight_scale"}
