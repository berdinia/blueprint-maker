"""Inference client against the loopback fake server (real HTTP, real sockets)."""

from __future__ import annotations

import json
import socket

import pytest

from blueprint_pipeline.inference import (
    ChatRequest,
    ImageInput,
    InferenceStatus,
    LocalInferenceClient,
)
from blueprint_pipeline.inference.evaluate import evaluate
from blueprint_pipeline.testing.fake_server import FakeModelServer

GOOD = json.dumps({"legible": True, "observations": [{"kind": "note", "raw_text": "SYN"}]})


def req():
    return ChatRequest(
        "sys", "user", [ImageInput(b"synthetic-bytes", "image/png", "0" * 64)], "req-test"
    )


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.mark.parametrize("api", ["openai", "ollama"])
@pytest.mark.parametrize("stream", [True, False])
def test_success_both_protocols(fake_server, settings_factory, api, stream):
    s = settings_factory(
        fake_server.url,
        **{"inference.primary": {"url": fake_server.url, "api": api}, "inference.stream": stream},
    )
    fake_server.enqueue({"mode": "content", "content": GOOD})
    r = LocalInferenceClient(s, "syn-extract").chat(req())
    assert r.status is InferenceStatus.SUCCESS and r.content == GOOD
    assert r.raw  # raw response preserved
    sent = fake_server.requests[0]
    assert sent["request_id"] == "req-test" and sent["model"] == "syn-extract"
    assert sent["path"] == ("/api/chat" if api == "ollama" else "/v1/chat/completions")


@pytest.mark.parametrize(
    "spec,expected",
    [
        ({"mode": "empty"}, InferenceStatus.EMPTY_RESPONSE),
        ({"mode": "malformed_stream"}, InferenceStatus.STREAM_INCOMPLETE),
        ({"mode": "truncated_stream", "content": GOOD}, InferenceStatus.STREAM_INCOMPLETE),
        ({"mode": "length", "content": GOOD[:20]}, InferenceStatus.MODEL_ABORTED),
        ({"mode": "server_error"}, InferenceStatus.MODEL_ABORTED),
        ({"mode": "http_error", "status": 500}, InferenceStatus.HTTP_FAILED),
    ],
)
def test_failure_categories(fake_server, settings_factory, spec, expected):
    s = settings_factory(fake_server.url)
    fake_server.enqueue(spec)
    r = LocalInferenceClient(s, "syn-extract").chat(req())
    assert r.status is expected, r.error
    assert evaluate(r, schema_name="extraction-response").status is expected


def test_http_200_with_semantic_failure_is_not_success(fake_server, settings_factory):
    s = settings_factory(fake_server.url)
    fake_server.enqueue(
        {
            "mode": "content",
            "content": json.dumps({"legible": True, "observations": [], "extra": 1}),
        }
    )
    out = evaluate(LocalInferenceClient(s, "m").chat(req()), schema_name="extraction-response")
    assert out.transport.http_status == 200 and out.status is InferenceStatus.SCHEMA_FAILED
    fake_server.enqueue({"mode": "content", "content": "Sure! " + GOOD})
    out = evaluate(LocalInferenceClient(s, "m").chat(req()), schema_name="extraction-response")
    assert out.status is InferenceStatus.PARSE_FAILED
    assert out.stage_statuses()["transport_status"] == "SUCCESS"


def test_timeout_after_send_is_not_retried(fake_server, settings_factory):
    s = settings_factory(
        fake_server.url, **{"inference.request_timeout_s": 0.5, "retry.max_attempts": 3}
    )
    fake_server.enqueue(
        {"mode": "sleep", "seconds": 2, "then": {"mode": "content", "content": GOOD}}
    )
    r = LocalInferenceClient(s, "m").chat(req())
    assert r.status is InferenceStatus.TRANSPORT_FAILED and "timeout" in r.error
    assert len(fake_server.requests) == 1  # expensive work is never duplicated
    assert r.attempts[0].retry_decision == "not_retried_work_may_be_running"


def test_busy_server_is_retried(fake_server, settings_factory):
    s = settings_factory(fake_server.url, **{"retry.max_attempts": 2})
    fake_server.enqueue({"mode": "http_error", "status": 503}, {"mode": "content", "content": GOOD})
    r = LocalInferenceClient(s, "m").chat(req())
    assert r.status is InferenceStatus.SUCCESS and len(r.attempts) == 2


def test_no_fallback_unless_explicitly_enabled(fake_server, tmp_path, settings_factory):
    dead = f"http://127.0.0.1:{free_port()}"
    s = settings_factory(dead, **{"inference.native": {"url": fake_server.url, "api": "ollama"}})
    fake_server.enqueue({"mode": "content", "content": GOOD})
    r = LocalInferenceClient(s, "m").chat(req())
    assert r.status is InferenceStatus.TRANSPORT_FAILED and not fake_server.requests
    s2 = settings_factory(
        dead,
        **{
            "inference.native": {"url": fake_server.url, "api": "ollama"},
            "inference.fallback_to_native": True,
        },
    )
    r2 = LocalInferenceClient(s2, "m").chat(req())
    assert r2.status is InferenceStatus.SUCCESS and r2.fallback_used
    assert [a.endpoint_role for a in r2.attempts] == ["primary", "native"]


def test_proxy_environment_is_ignored(fake_server, settings_factory, monkeypatch):
    for var in ("HTTP_PROXY", "http_proxy", "HTTPS_PROXY", "https_proxy", "ALL_PROXY"):
        monkeypatch.setenv(var, f"http://127.0.0.1:{free_port()}")
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    s = settings_factory(fake_server.url)
    fake_server.enqueue({"mode": "content", "content": GOOD})
    assert LocalInferenceClient(s, "m").chat(req()).status is InferenceStatus.SUCCESS


def test_api_key_read_from_env_by_name_only(fake_server, settings_factory, monkeypatch):
    monkeypatch.setenv("SYN_LOCAL_KEY", "synthetic-secret")
    s = settings_factory(
        fake_server.url,
        **{
            "inference.primary": {
                "url": fake_server.url,
                "api": "openai",
                "api_key_env": "SYN_LOCAL_KEY",
            }
        },
    )
    fake_server.enqueue({"mode": "content", "content": GOOD})
    r = LocalInferenceClient(s, "m").chat(req())
    assert fake_server.requests[0]["has_authorization"]
    assert "synthetic-secret" not in json.dumps(r.summary())
    assert "synthetic-secret" not in json.dumps(s.redacted_dict())


def test_probe(fake_server, settings_factory):
    s = settings_factory(fake_server.url)
    assert LocalInferenceClient(s, "m").probe()[0] is True
    s2 = settings_factory(f"http://127.0.0.1:{free_port()}")
    assert LocalInferenceClient(s2, "m").probe()[0] is False


def test_server_binds_loopback_only():
    with FakeModelServer() as srv:
        assert srv.httpd.server_address[0] == "127.0.0.1"
