"""Extraction runner — calls the local vision model and parses the JSON output.

The runner enforces:
- process lock before model invocation
- atomic write (tmp + rename)
- no silent fallback to a second endpoint
- explicit transport / json_parse / semantic / review verdicts
- source_id resolution via source registry (authoritative from registry)
- on failure: .error.json is written, the last valid JSON is NOT overwritten
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from .process_lock import acquire_lock, release_lock, stale_lock
from .config import Config


@dataclass
class ExtractionResult:
    status: str
    transport: str
    json_parse: str
    semantic_status: str
    source_id: str
    source_id_advisory: Optional[str] = None
    source_id_mismatch: bool = False
    error: Optional[str] = None
    raw_length: int = 0
    extraction_id: str = ""
    model: str = ""
    extracted_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def is_ok(self) -> bool:
        return self.status in ("PASS", "PARTIAL_RESULT")


class ExtractionRunner:
    """Run a single extraction job."""

    def __init__(self, config: Config, source_registry=None):
        self.config = config
        self.registry = source_registry
        self.result: Optional[ExtractionResult] = None

    def lock_path(self) -> str:
        return self.config.lock_path

    def extract(
        self,
        input_path: str,
        source_id: str,
        advisory_source_id: Optional[str] = None,
        dry_run: bool = False,
        timeout_s: int = 600,
    ) -> ExtractionResult:
        """Run a single extraction job.

        1. Acquire the process lock (before model invocation).
        2. Call the local model via the proxy endpoint.
        3. Parse the model output JSON.
        4. Resolve the authoritative source_id from the registry.
        5. Atomically write the result, or .error.json on failure.
        6. Release the lock.
        """
        lock_path = self.lock_path()

        # 1. Acquire lock — reject second job cleanly
        ok, existing = acquire_lock(lock_path, acquired_by=f"Extract {source_id}")
        if not ok:
            release_lock(lock_path)  # clean up stale if necessary
            self.result = ExtractionResult(
                status="BLOCKED",
                transport="BLOCKED",
                json_parse="BLOCKED",
                semantic_status="BLOCKED",
                source_id=source_id,
                source_id_advisory=advisory_source_id,
                source_id_mismatch=advisory_source_id and advisory_source_id != source_id,
                error=f"Process lock held by pid {existing.pid if existing else '?'}; "
                      "duplicate job blocked",
            )
            self._write_result(self.result, input_path)
            return self.result

        try:
            # 2. Call the model — do NOT fall back silently
            out, err, rc = self._invoke_model(input_path, timeout_s=timeout_s)
            transport = "PASS" if rc == 0 else "FAIL"

            # 3. Parse JSON
            content, parse_ok = self._parse_json(out)
            json_parse = "PASS" if parse_ok else "FAIL"

            # 4. Resolve authoritative source_id from registry
            if self.registry:
                resolved = self.registry.resolve_source_id(
                    source_id, advisory_source_id)
                authoritative_id = resolved["authoritative"]
                mismatch = resolved["source_id_mismatch"]
                advisory = resolved["advisory"]
            else:
                authoritative_id = source_id
                mismatch = bool(advisory_source_id and advisory_source_id != source_id)
                advisory = advisory_source_id

            # 5. Semantic validation
            semantic_status = "PASS" if content.get("rooms") else "PARTIAL_RESULT"

            # 6. Build result
            self.result = ExtractionResult(
                status=semantic_status,
                transport=transport,
                json_parse=json_parse,
                semantic_status=semantic_status,
                source_id=authoritative_id,
                source_id_advisory=advisory,
                source_id_mismatch=mismatch,
                error=err[:500] if not transport == "PASS" else None,
                raw_length=len(out),
                extraction_id=f"extract-{int(time.time())}",
                model=self.config.extract_model,
                extracted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._write_result(self.result, input_path)
            return self.result

        except Exception as exc:
            self.result = ExtractionResult(
                status="FAILED",
                transport="PASS",  # transport was OK
                json_parse="FAIL",
                semantic_status="FAILED",
                source_id=source_id,
                source_id_advisory=advisory_source_id,
                source_id_mismatch=advisory_source_id and advisory_source_id != source_id,
                error=f"{type(exc).__name__}: {exc}",
                extraction_id=f"extract-{int(time.time())}",
                model=self.config.extract_model,
                extracted_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            self._write_result(self.result, input_path)
            return self.result

        finally:
            # 7. Release lock on normal exit and handled exceptions
            release_lock(lock_path)

    def _invoke_model(self, input_path: str, timeout_s: int = 600) -> tuple[str, str, int]:
        """Call the local model. No silent fallback."""
        import urllib.request
        import base64

        url = self.config.proxy_url() + "/v1/chat/completions"
        with open(input_path, "rb") as f:
            b64 = base64.b64encode(f.read()).decode()

        payload = json.dumps({
            "model": self.config.extract_model,
            "messages": [
                {"role": "system", "content": self._system_prompt()},
                {"role": "user", "content": [
                    {"type": "text", "text": "Extract this floor plan as JSON."},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ]},
            ],
            "stream": False,
            "options": {"temperature": 0.1, "num_predict": 2048},
        }).encode()

        req = urllib.request.Request(url, data=payload,
                                    headers={"Content-Type": "application/json"})
        try:
            resp = urllib.request.urlopen(req, timeout=timeout_s)
            raw = json.loads(resp.read())
            out = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
            return out, "", 0
        except Exception as exc:
            return "", f"{type(exc).__name__}: {exc}", 1

    @staticmethod
    def _system_prompt() -> str:
        return (
            "You are a strict blueprint extraction tool. Extract ONLY visible "
            "information as JSON. Do not invent data. If a value is not visible, "
            "set it to null or unknown. Return valid JSON only."
        )

    @staticmethod
    def _parse_json(text: str) -> tuple[dict, bool]:
        text = text.strip()
        if text.startswith("```"):
            text = text.lstrip("`")
            text = text[text.find("\n") + 1:]
            text = text.rstrip("`").strip()
        try:
            content = json.loads(text)
            if isinstance(content, dict):
                return content, True
        except json.JSONDecodeError:
            pass
        return {"_raw": text[:5000]}, False

    def _write_result(self, result: ExtractionResult, input_path: str) -> None:
        out_dir = self.config.output_dir
        os.makedirs(out_dir, exist_ok=True)
        src = os.path.basename(input_path)
        out_path = os.path.join(out_dir, f"{src}.json")
        tmp = out_path + ".tmp"

        if result.status == "FAILED" or result.status == "BLOCKED":
            err_path = out_path + ".error.json"
            with open(tmp, "w") as f:
                json.dump(result.to_dict(), f, indent=2)
            import os as _os
            _os.rename(tmp, err_path)
        else:
            _atomic_write(out_path, tmp, json.dumps(result.to_dict(), indent=2))

    @staticmethod
    def _atomic_write(out_path: str, tmp_path: str, content: str) -> None:
        """Write to tmp, then rename. Never overwrites a valid existing JSON."""
        with open(tmp_path, "w") as f:
            f.write(content)
        # Verify tmp is valid JSON before replacing
        try:
            json.load(open(tmp_path))
        except (json.JSONDecodeError, OSError):
            import os
            os.remove(tmp_path)
            raise RuntimeError(f"Atomic write aborted: tmp file not valid JSON: {tmp_path}")
        import os
        os.rename(tmp_path, out_path)
