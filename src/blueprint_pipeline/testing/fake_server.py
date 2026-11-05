"""Loopback fake model server for synthetic runs and tests.

Speaks both the OpenAI-compatible (``/v1/chat/completions``) and the native
Ollama (``/api/chat``) protocol, streaming or not, and can simulate failure
modes. It binds to 127.0.0.1 only and never forwards anything.

Responses are chosen by the SHA-256 of the image in the request (so canned
answers are tied to specific synthetic inputs) or, failing that, taken from a
FIFO queue.

Response specs (dicts):
  {"mode": "content", "content": "<text>"}         normal answer
  {"mode": "empty"}                                 HTTP 200, empty content
  {"mode": "http_error", "status": 500}             non-2xx
  {"mode": "truncated_stream", "content": "<text>"} stream cut before terminal event
  {"mode": "malformed_stream"}                      stream with a non-JSON event
  {"mode": "length", "content": "<text>"}           finish_reason=length
  {"mode": "server_error"}                          {"error": ...} payload
  {"mode": "sleep", "seconds": 3, "then": {...}}    delay (for timeout tests)
"""

from __future__ import annotations

import base64
import hashlib
import json
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class FakeModelServer:
    def __init__(self) -> None:
        self.by_image: dict[str, dict[str, Any]] = {}
        self.queue: deque[dict[str, Any]] = deque()
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:
                body = json.dumps({"data": [{"id": "synthetic-model"}], "models": []}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                server._handle(self, payload)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}"
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    # ------------------------------------------------------------------ control
    def start(self) -> FakeModelServer:
        self._thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> FakeModelServer:
        return self.start()

    def __exit__(self, *exc: object) -> None:
        self.stop()

    def respond_for_image(self, image_sha256: str, spec: dict[str, Any]) -> None:
        self.by_image[image_sha256] = spec

    def enqueue(self, *specs: dict[str, Any]) -> None:
        self.queue.extend(specs)

    # ------------------------------------------------------------------ handling
    @staticmethod
    def _images(payload: dict[str, Any]) -> list[bytes]:
        out: list[bytes] = []
        for m in payload.get("messages", []):
            for img in m.get("images", []) or []:
                out.append(base64.b64decode(img))
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    url = (part.get("image_url") or {}).get("url", "")
                    if url.startswith("data:"):
                        out.append(base64.b64decode(url.split(",", 1)[1]))
        return out

    def _handle(self, h: BaseHTTPRequestHandler, payload: dict[str, Any]) -> None:
        images = self._images(payload)
        shas = [hashlib.sha256(i).hexdigest() for i in images]
        native = h.path.endswith("/api/chat")
        with self._lock:
            self.requests.append(
                {
                    "path": h.path,
                    "model": payload.get("model"),
                    "stream": payload.get("stream"),
                    "image_sha256": shas,
                    "request_id": h.headers.get("X-Request-ID"),
                    "has_authorization": "Authorization" in h.headers,
                }
            )
            spec = next((self.by_image[s] for s in shas if s in self.by_image), None)
            if spec is None:
                spec = self.queue.popleft() if self.queue else {"mode": "http_error", "status": 404}
        while spec.get("mode") == "sleep":
            time.sleep(float(spec.get("seconds", 1)))
            spec = spec.get("then") or {"mode": "empty"}
        self._send(
            h, spec, stream=bool(payload.get("stream")), native=native, model=payload.get("model")
        )

    def _send(
        self,
        h: BaseHTTPRequestHandler,
        spec: dict[str, Any],
        *,
        stream: bool,
        native: bool,
        model: Any,
    ) -> None:
        mode = spec.get("mode", "content")
        content = spec.get("content", "")
        try:
            if mode == "http_error":
                body = json.dumps({"error": "simulated"}).encode()
                h.send_response(int(spec.get("status", 500)))
                h.send_header("Content-Length", str(len(body)))
                h.end_headers()
                h.wfile.write(body)
                return
            if mode == "server_error" and not stream:
                self._json(h, {"error": "simulated model failure"})
                return
            if mode == "empty":
                content = ""
            finish = "length" if mode == "length" else "stop"
            if not stream:
                if native:
                    self._json(
                        h,
                        {
                            "model": model,
                            "message": {"role": "assistant", "content": content},
                            "done": True,
                            "done_reason": finish,
                        },
                    )
                else:
                    self._json(
                        h,
                        {
                            "model": model,
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {"role": "assistant", "content": content},
                                    "finish_reason": finish,
                                }
                            ],
                            "usage": {},
                        },
                    )
                return
            h.send_response(200)
            h.send_header("Content-Type", "application/x-ndjson" if native else "text/event-stream")
            h.send_header("Connection", "close")
            h.end_headers()
            chunks = [content[i : i + 40] for i in range(0, len(content), 40)] or [""]
            if mode == "server_error":
                self._event(h, {"error": "simulated model failure"}, native)
                return
            if mode == "malformed_stream":
                h.wfile.write(b"data: {not json\n\n" if not native else b"{not json\n")
                return
            for c in chunks:
                if native:
                    self._event(h, {"model": model, "message": {"content": c}, "done": False}, True)
                else:
                    self._event(
                        h,
                        {
                            "model": model,
                            "choices": [{"delta": {"content": c}, "finish_reason": None}],
                        },
                        False,
                    )
            if mode == "truncated_stream":
                return
            if native:
                self._event(
                    h,
                    {
                        "model": model,
                        "message": {"content": ""},
                        "done": True,
                        "done_reason": finish,
                    },
                    True,
                )
            else:
                self._event(
                    h, {"model": model, "choices": [{"delta": {}, "finish_reason": finish}]}, False
                )
                h.wfile.write(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            pass

    @staticmethod
    def _json(h: BaseHTTPRequestHandler, obj: dict[str, Any]) -> None:
        body = json.dumps(obj).encode()
        h.send_response(200)
        h.send_header("Content-Type", "application/json")
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    @staticmethod
    def _event(h: BaseHTTPRequestHandler, obj: dict[str, Any], native: bool) -> None:
        line = json.dumps(obj)
        h.wfile.write((line + "\n").encode() if native else f"data: {line}\n\n".encode())
        h.wfile.flush()
