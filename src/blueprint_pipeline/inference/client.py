"""Local inference client.

One reusable client for OpenAI-compatible (``/v1/chat/completions``) and native
Ollama (``/api/chat``) endpoints, streaming or non-streaming.

Design decisions
----------------
* Uses :mod:`http.client` directly. Unlike :mod:`urllib.request` it never reads
  ``HTTP_PROXY``/``HTTPS_PROXY`` environment variables, so evidence cannot be
  routed through a proxy by ambient configuration.
* The endpoint URL is re-validated against the privacy policy immediately
  before every connection.
* Retries happen only when the server provably did not start work: connection
  refused / unreachable before the body was sent, or an explicit HTTP 429/503.
  A timeout after the request was sent is **never** retried automatically.
* Endpoint fallback (primary → native) is used only when enabled in
  configuration, only after a connect-phase failure, and is recorded in the
  attempt log and in ``TransportResult.fallback_used``.
* The raw response bytes (the full stream for streaming responses) are kept so
  they can be preserved next to parsed output.
"""

from __future__ import annotations

import base64
import datetime as _dt
import http.client
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from ..config.settings import EndpointSettings, Settings, validate_endpoint
from ..errors import ConfigError
from .types import AttemptRecord, InferenceStatus, TransportResult

log = logging.getLogger("blueprint.inference")

MAX_RAW_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True)
class ImageInput:
    data: bytes
    mime: str  # e.g. image/png
    sha256: str


@dataclass(frozen=True)
class ChatRequest:
    system: str
    user_text: str
    images: Sequence[ImageInput] = ()
    request_id: str = ""


class _ConnectFailed(Exception):
    """Connection could not be established; no request body was sent."""


class _Timeout(Exception):
    pass


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.hostname}:{p.port or (443 if p.scheme == 'https' else 80)}"


def new_request_id() -> str:
    return "req-" + uuid.uuid4().hex


class LocalInferenceClient:
    def __init__(self, settings: Settings, model: str) -> None:
        primary = settings.inference.primary
        if primary is None:
            raise ConfigError("no primary inference endpoint configured")
        self.settings = settings
        self.model = model
        self.primary = primary
        self.native = settings.inference.native
        validate_endpoint(primary.url, settings.privacy)
        if self.native is not None:
            validate_endpoint(self.native.url, settings.privacy)

    # ------------------------------------------------------------------ probe
    def probe(self, endpoint: EndpointSettings | None = None) -> tuple[bool, str]:
        """Cheap liveness probe (model listing). Never sends evidence."""
        ep = endpoint or self.primary
        validate_endpoint(ep.url, self.settings.privacy)
        path = "/api/tags" if ep.api == "ollama" else "/v1/models"
        try:
            conn = self._connection(ep, timeout=self.settings.inference.connect_timeout_s)
            try:
                conn.request("GET", self._path(ep, path), headers=self._headers(ep, None))
                resp = conn.getresponse()
                resp.read(1 << 20)
                return (200 <= resp.status < 300), f"HTTP {resp.status}"
            finally:
                conn.close()
        except (OSError, http.client.HTTPException) as exc:
            return False, f"{type(exc).__name__}: {exc}"

    # ------------------------------------------------------------------- chat
    def chat(self, req: ChatRequest) -> TransportResult:
        request_id = req.request_id or new_request_id()
        attempts: list[AttemptRecord] = []
        max_attempts = self.settings.retry.max_attempts
        endpoints: list[tuple[str, EndpointSettings]] = [("primary", self.primary)]
        if self.settings.inference.fallback_to_native and self.native is not None:
            endpoints.append(("native", self.native))

        last: TransportResult | None = None
        for role, ep in endpoints:
            for attempt in range(1, max_attempts + 1):
                result, rec = self._once(ep, role, req, request_id, len(attempts) + 1)
                attempts.append(rec)
                result.attempts = attempts
                last = result
                result.fallback_used = role == "native"
                retryable = rec.retry_decision == "retryable"
                if not retryable:
                    return result
                if attempt < max_attempts:
                    rec.retry_decision = "retried"
                    time.sleep(self.settings.retry.backoff_s * attempt)
            # Exhausted on this endpoint. Fall back only for connect failures.
            assert last is not None
            if attempts[-1].outcome != "connect_failed":
                return last
            if role == "primary" and len(endpoints) > 1:
                log.warning(
                    "endpoint fallback: primary unreachable, using configured native endpoint",
                    extra={"event": "ENDPOINT_FALLBACK", "request_id": request_id},
                )
                attempts[-1].retry_decision = "fallback_to_native"
        assert last is not None
        return last

    # --------------------------------------------------------------- internal
    def _once(
        self,
        ep: EndpointSettings,
        role: str,
        req: ChatRequest,
        request_id: str,
        attempt_no: int,
    ) -> tuple[TransportResult, AttemptRecord]:
        validate_endpoint(ep.url, self.settings.privacy)  # defence in depth
        started = time.monotonic()
        rec = AttemptRecord(
            attempt=attempt_no,
            endpoint_role=role,
            endpoint=_origin(ep.url),
            started_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            duration_s=0.0,
            outcome="pending",
        )
        result = TransportResult(
            status=InferenceStatus.TRANSPORT_FAILED,
            request_id=request_id,
            model_requested=self.model,
            api=ep.api,
        )
        stream = self.settings.inference.stream
        body = self._body(ep, req, stream)
        path = "/api/chat" if ep.api == "ollama" else "/v1/chat/completions"
        conn: http.client.HTTPConnection | None = None
        try:
            conn = self._connection(ep, timeout=self.settings.inference.connect_timeout_s)
            try:
                conn.connect()
            except (ConnectionRefusedError, socket.gaierror, OSError) as exc:
                raise _ConnectFailed(f"{type(exc).__name__}: {exc}") from exc
            assert conn.sock is not None
            conn.sock.settimeout(self.settings.inference.request_timeout_s)
            conn.request(
                "POST", self._path(ep, path), body=body, headers=self._headers(ep, request_id)
            )
            rec.body_sent = True
            resp = conn.getresponse()
            result.http_status = resp.status
            rec.http_status = resp.status
            if not 200 <= resp.status < 300:
                raw = resp.read(MAX_RAW_BYTES)
                result.raw = raw
                result.status = InferenceStatus.HTTP_FAILED
                result.error = f"HTTP {resp.status}: {raw[:300].decode('utf-8', 'replace')}"
                rec.outcome = "http_failed"
                rec.error = f"HTTP {resp.status}"
                if resp.status in (429, 503):
                    rec.retry_decision = "retryable"
                return result, rec
            if stream:
                self._read_stream(ep, resp, result)
            else:
                self._read_whole(ep, resp, result)
            rec.outcome = result.status.value.lower()
            rec.error = result.error
        except _ConnectFailed as exc:
            result.status = InferenceStatus.TRANSPORT_FAILED
            result.error = f"connect failed: {exc}"
            rec.outcome = "connect_failed"
            rec.error = result.error
            rec.retry_decision = "retryable"
        except TimeoutError as exc:
            result.status = InferenceStatus.TRANSPORT_FAILED
            result.error = f"timeout after request was sent: {exc}"
            rec.outcome = "timeout"
            rec.error = result.error
            rec.retry_decision = "not_retried_work_may_be_running"
        except (OSError, http.client.HTTPException) as exc:
            result.status = InferenceStatus.TRANSPORT_FAILED
            result.error = f"{type(exc).__name__}: {exc}"
            rec.outcome = "transport_failed"
            rec.error = result.error
            rec.retry_decision = "not_retried_work_may_be_running" if rec.body_sent else "retryable"
        finally:
            if conn is not None:
                conn.close()
            rec.duration_s = round(time.monotonic() - started, 3)
        return result, rec

    def _connection(self, ep: EndpointSettings, timeout: float) -> http.client.HTTPConnection:
        p = urlsplit(ep.url)
        host = p.hostname or ""
        if p.scheme == "https":
            return http.client.HTTPSConnection(host, p.port or 443, timeout=timeout)
        return http.client.HTTPConnection(host, p.port or 80, timeout=timeout)

    @staticmethod
    def _path(ep: EndpointSettings, suffix: str) -> str:
        base = urlsplit(ep.url).path.rstrip("/")
        # Allow a base path such as http://127.0.0.1:8080/v1 without duplicating /v1
        if base.endswith("/v1") and suffix.startswith("/v1/"):
            suffix = suffix[3:]
        return (base + suffix) or "/"

    @staticmethod
    def _headers(ep: EndpointSettings, request_id: str | None) -> dict[str, str]:
        h = {"Content-Type": "application/json", "Accept": "application/json"}
        if request_id:
            h["X-Request-ID"] = request_id
        if ep.api_key_env:
            key = os.environ.get(ep.api_key_env)
            if key:
                h["Authorization"] = f"Bearer {key}"
        return h

    def _body(self, ep: EndpointSettings, req: ChatRequest, stream: bool) -> bytes:
        inf = self.settings.inference
        if ep.api == "ollama":
            payload: dict[str, Any] = {
                "model": self.model,
                "stream": stream,
                "format": "json",
                "messages": [
                    {"role": "system", "content": req.system},
                    {
                        "role": "user",
                        "content": req.user_text,
                        "images": [base64.b64encode(i.data).decode() for i in req.images],
                    },
                ],
                "options": {
                    "temperature": inf.temperature,
                    "num_ctx": inf.context_size,
                    "num_predict": inf.max_output_tokens,
                },
            }
        else:
            content: list[dict[str, Any]] = [{"type": "text", "text": req.user_text}]
            for i in req.images:
                b64 = base64.b64encode(i.data).decode()
                content.append(
                    {"type": "image_url", "image_url": {"url": f"data:{i.mime};base64,{b64}"}}
                )
            payload = {
                "model": self.model,
                "stream": stream,
                "temperature": inf.temperature,
                "max_tokens": inf.max_output_tokens,
                "messages": [
                    {"role": "system", "content": req.system},
                    {"role": "user", "content": content},
                ],
            }
        return json.dumps(payload).encode("utf-8")

    # ---------------------------------------------------------- response read
    @staticmethod
    def _finish(result: TransportResult, content: str, finish: str | None) -> None:
        result.content = content
        result.finish_reason = finish
        if finish in ("length", "content_filter", "error", "load"):
            result.status = InferenceStatus.MODEL_ABORTED
            result.error = f"model stopped abnormally: finish_reason={finish}"
        elif not content.strip():
            result.status = InferenceStatus.EMPTY_RESPONSE
            result.error = "HTTP success with empty content"
        else:
            result.status = InferenceStatus.SUCCESS

    def _read_whole(
        self, ep: EndpointSettings, resp: http.client.HTTPResponse, result: TransportResult
    ) -> None:
        raw = resp.read(MAX_RAW_BYTES)
        result.raw = raw
        if not raw.strip():
            result.status = InferenceStatus.EMPTY_RESPONSE
            result.error = "HTTP success with empty body"
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            result.status = InferenceStatus.MODEL_ABORTED
            result.error = f"response envelope is not JSON: {exc.msg}"
            return
        if isinstance(data, dict) and data.get("error"):
            result.status = InferenceStatus.MODEL_ABORTED
            result.error = f"server error: {str(data['error'])[:300]}"
            return
        try:
            if ep.api == "ollama":
                content = data.get("message", {}).get("content") or ""
                finish = data.get("done_reason") or ("stop" if data.get("done") else None)
                result.usage = {
                    k: data[k] for k in ("prompt_eval_count", "eval_count") if k in data
                }
            else:
                choice = (data.get("choices") or [{}])[0]
                content = (choice.get("message") or {}).get("content") or ""
                finish = choice.get("finish_reason")
                result.usage = dict(data.get("usage") or {})
            result.model_reported = data.get("model")
        except (AttributeError, IndexError, TypeError) as exc:
            result.status = InferenceStatus.MODEL_ABORTED
            result.error = f"unexpected response envelope: {exc}"
            return
        self._finish(result, content, finish)

    def _read_stream(
        self, ep: EndpointSettings, resp: http.client.HTTPResponse, result: TransportResult
    ) -> None:
        raw = bytearray()
        parts: list[str] = []
        finish: str | None = None
        terminal = False
        while True:
            line = resp.readline(1 << 20)
            if not line:
                break
            raw.extend(line)
            if len(raw) > MAX_RAW_BYTES:
                result.raw = bytes(raw)
                result.status = InferenceStatus.MODEL_ABORTED
                result.error = "response exceeded maximum raw size"
                return
            s = line.decode("utf-8", "replace").strip()
            if not s:
                continue
            if ep.api != "ollama":
                if s.startswith(":"):
                    continue  # SSE comment / keep-alive
                if not s.startswith("data:"):
                    continue
                s = s[5:].strip()
                if s == "[DONE]":
                    terminal = True
                    break
            try:
                evt = json.loads(s)
            except json.JSONDecodeError:
                result.raw = bytes(raw)
                result.status = InferenceStatus.STREAM_INCOMPLETE
                result.error = "malformed stream event (not JSON)"
                return
            if isinstance(evt, dict) and evt.get("error"):
                result.raw = bytes(raw)
                result.content = "".join(parts)
                result.status = InferenceStatus.MODEL_ABORTED
                result.error = f"server error in stream: {str(evt['error'])[:300]}"
                return
            if ep.api == "ollama":
                parts.append((evt.get("message") or {}).get("content") or "")
                result.model_reported = evt.get("model", result.model_reported)
                if evt.get("done"):
                    finish = evt.get("done_reason") or "stop"
                    result.usage = {
                        k: evt[k] for k in ("prompt_eval_count", "eval_count") if k in evt
                    }
                    terminal = True
                    break
            else:
                choices = evt.get("choices") or []
                if choices:
                    delta = choices[0].get("delta") or {}
                    parts.append(delta.get("content") or "")
                    if choices[0].get("finish_reason"):
                        finish = choices[0]["finish_reason"]
                if evt.get("usage"):
                    result.usage = dict(evt["usage"])
                result.model_reported = evt.get("model", result.model_reported)
        result.raw = bytes(raw)
        # OpenAI-style servers may omit [DONE] but must send a finish_reason.
        if not terminal and ep.api != "ollama" and finish is not None:
            terminal = True
        if not terminal:
            result.content = "".join(parts)
            result.status = InferenceStatus.STREAM_INCOMPLETE
            result.error = "stream ended without a terminal event"
            return
        self._finish(result, "".join(parts), finish)
