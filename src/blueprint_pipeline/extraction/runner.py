"""Extraction runner.

For one source:

1. resolve identity from the source register (never from the model);
2. require a current normalized copy;
3. validate inference configuration (fails before any lock or request);
4. skip if the current extraction is fresh (input hash, prompt hash, config
   hash and model identity all unchanged) unless forced;
5. take the cross-process heavy-job lock;
6. call the local model and preserve every phase of the exchange under
   ``extraction/<source_id>/attempts/<attempt_id>/``;
7. enforce authoritative provenance, then schema and semantic checks;
8. on SUCCESS atomically replace ``extraction/<source_id>/current.json``;
   otherwise write ``current.json.error.json`` and leave the last valid result.
"""

from __future__ import annotations

import datetime as _dt
import secrets
from dataclasses import dataclass, field
from typing import Any

from ..errors import StageBlocked
from ..evidence.measurements import parse_length
from ..evidence.provenance import enforce_source_identity
from ..inference.client import ChatRequest, ImageInput, LocalInferenceClient, new_request_id
from ..inference.evaluate import evaluate
from ..inference.types import InferenceOutcome, InferenceStatus
from ..inventory.register import SourceRecord
from ..io.atomic import atomic_write_bytes, atomic_write_json, atomic_write_text
from ..orchestration.context import PipelineContext
from ..orchestration.lock import HeavyJobLock
from ..schemas import schema_errors, schema_tag
from .prompts import EXTRACTION_SYSTEM, EXTRACTION_TEMPLATE_ID, EXTRACTION_USER, template_sha256


@dataclass
class ExtractionSummary:
    source_id: str
    status: str  # an InferenceStatus value, SKIPPED_CURRENT, or BLOCKED
    detail: str = ""
    attempt_id: str | None = None
    warnings: list[dict[str, Any]] = field(default_factory=list)


def current_rel(source_id: str) -> str:
    return f"extraction/{source_id}/current.json"


def _attempt_id() -> str:
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{ts}-{secrets.token_hex(3)}"


def semantic_check_extraction(obj: dict[str, Any]) -> tuple[list[str], list[dict[str, Any]]]:
    errors: list[str] = []
    warnings: list[dict[str, Any]] = []
    obs = obj.get("observations", [])
    if obj.get("legible") is True and not obs:
        errors.append("model reported a legible source but returned no observations")
    if obj.get("legible") is False:
        if obs:
            errors.append("model reported the source illegible but returned observations")
        warnings.append({"code": "ILLEGIBLE_SOURCE", "message": obj.get("notes") or "no notes"})
    for i, o in enumerate(obs):
        if o["kind"] in ("room_label", "dimension") and not (
            o.get("raw_text") or o.get("normalized")
        ):
            errors.append(f"observation {i} ({o['kind']}) has neither raw_text nor normalized text")
    seen: set[tuple[Any, ...]] = set()
    for o in obs:
        key = (o["kind"], o.get("raw_text"), o.get("subject"), o.get("dimension_axis"))
        if key in seen:
            warnings.append(
                {"code": "DUPLICATE_OBSERVATION", "message": f"repeated observation {key!r}"}
            )
        seen.add(key)
    return errors, warnings


class ExtractionRunner:
    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    def normalized_record(self, source_id: str) -> dict[str, Any]:
        rel = f"normalized/{source_id}.json"
        rep = self.ctx.store.check(rel)
        if not rep.is_current:
            raise StageBlocked(
                f"{source_id}: normalized copy is {rep.state.value}; run 'blueprint normalize'"
            )
        rec: dict[str, Any] = self.ctx.store.read_json(rel)
        png_rel = rec["normalized_path"]
        png_rep = self.ctx.store.check(png_rel, inputs={"source": rec["input_sha256"]})
        if not png_rep.is_current:
            raise StageBlocked(f"{source_id}: normalized image is {png_rep.state.value}")
        return rec

    def run(self, record: SourceRecord, *, force: bool = False) -> ExtractionSummary:
        ctx = self.ctx
        s = ctx.settings
        sid = record.source_id
        # (3) configuration must be valid before anything else happens
        endpoint, model = s.require_inference("extract")
        norm = self.normalized_record(sid)
        inputs = {
            "normalized": norm["normalized_sha256"],
            "source": norm["input_sha256"],
            "prompt": template_sha256(EXTRACTION_TEMPLATE_ID),
        }
        cfg_hash = s.config_hash()
        rel = current_rel(sid)
        if not force:
            fresh = ctx.store.check(
                rel,
                inputs=inputs,
                config_hash=cfg_hash,
                model=model,
                content_schema=schema_tag("extraction-record"),
            )
            if fresh.is_current:
                return ExtractionSummary(sid, "SKIPPED_CURRENT", "current extraction is fresh")

        image_bytes = ctx.store.path(norm["normalized_path"]).read_bytes()
        request_id = new_request_id()
        attempt_id = _attempt_id()
        adir = f"extraction/{sid}/attempts/{attempt_id}"
        req = ChatRequest(
            system=EXTRACTION_SYSTEM,
            user_text=EXTRACTION_USER,
            images=[ImageInput(image_bytes, "image/png", norm["normalized_sha256"])],
            request_id=request_id,
        )
        client = LocalInferenceClient(s, model)
        request_meta = {
            "request_id": request_id,
            "attempt_id": attempt_id,
            "source_id": sid,
            "model": model,
            "api": endpoint.api,
            "stream": s.inference.stream,
            "prompt": {
                "template_id": EXTRACTION_TEMPLATE_ID,
                "template_sha256": inputs["prompt"],
            },
            "images": [{"sha256": norm["normalized_sha256"], "mime": "image/png"}],
            "limits": {
                "context_size": s.inference.context_size,
                "max_output_tokens": s.inference.max_output_tokens,
                "request_timeout_s": s.inference.request_timeout_s,
            },
        }
        store = ctx.store
        atomic_write_json(store.path(f"{adir}/request.json"), request_meta)

        with HeavyJobLock(
            s.paths.lock_path,
            operation="extract",
            model=model,
            source_id=sid,
            wait_s=s.lock.wait_s,
            poll_s=s.lock.poll_s,
        ):
            ctx.event(f"extract {sid}", event="EXTRACT_START", source_id=sid, request_id=request_id)
            transport = client.chat(req)

        atomic_write_bytes(store.path(f"{adir}/raw-response.bin"), transport.raw)
        atomic_write_text(store.path(f"{adir}/assembled.txt"), transport.content)
        provenance_box: dict[str, Any] = {}

        def _pre(obj: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
            enforced, prov, warns = enforce_source_identity(obj, sid)
            provenance_box["p"] = prov
            return enforced, warns

        outcome = evaluate(
            transport,
            schema_name="extraction-response",
            pre_schema=_pre,
            semantic=semantic_check_extraction,
        )
        if outcome.parsed is not None:
            atomic_write_json(store.path(f"{adir}/parsed.json"), outcome.parsed)
        outcome_doc = {
            "status": outcome.status.value,
            "stage_status": outcome.stage_statuses(),
            "transport": transport.summary(),
            "parse_error": outcome.parse_error,
            "parse_notes": outcome.parse_notes,
            "schema_errors": outcome.schema_errors,
            "semantic_errors": outcome.semantic_errors,
            "warnings": outcome.warnings,
        }
        atomic_write_json(store.path(f"{adir}/outcome.json"), outcome_doc)
        ctx.event(
            f"extract {sid}: {outcome.status.value}",
            event="EXTRACT_END",
            source_id=sid,
            request_id=request_id,
            status=outcome.status.value,
        )

        if outcome.status is not InferenceStatus.SUCCESS:
            store.write_error(
                rel,
                {
                    "source_id": sid,
                    "attempt_id": attempt_id,
                    "request_id": request_id,
                    "status": outcome.status.value,
                    "stage_status": outcome.stage_statuses(),
                    "error": transport.error
                    or outcome.parse_error
                    or "; ".join(outcome.schema_errors[:5] + outcome.semantic_errors[:5]),
                    "attempt_dir": adir,
                    "last_valid_preserved": store.path(rel).exists(),
                },
            )
            return ExtractionSummary(
                sid,
                outcome.status.value,
                transport.error or outcome.parse_error or "see attempt outcome",
                attempt_id,
                outcome.warnings,
            )

        doc = self._record(record, norm, outcome, provenance_box["p"], request_meta, attempt_id)
        errs = schema_errors(doc, "extraction-record")
        if errs:  # internal invariant: a bug, not a model failure
            store.write_error(rel, {"status": "INTERNAL_SCHEMA_ERROR", "errors": errs})
            raise RuntimeError(f"extraction record failed its own schema: {errs[:3]}")
        store.write_json(
            rel,
            doc,
            stage="extract",
            inputs=inputs,
            config_hash=cfg_hash,
            model=model,
            content_schema=schema_tag("extraction-record"),
            extra={"attempt_id": attempt_id, "request_id": request_id},
        )
        return ExtractionSummary(sid, "SUCCESS", "", attempt_id, outcome.warnings)

    @staticmethod
    def _record(
        record: SourceRecord,
        norm: dict[str, Any],
        outcome: InferenceOutcome,
        prov: Any,
        request_meta: dict[str, Any],
        attempt_id: str,
    ) -> dict[str, Any]:
        parsed = outcome.parsed or {}
        sid = record.source_id
        observations = []
        for i, o in enumerate(parsed.get("observations", []), start=1):
            value_mm = unit = value_unc = None
            if o["kind"] == "dimension" or (
                o["kind"] == "opening" and o.get("dimension_axis") not in (None, "unknown")
            ):
                value_mm, unit, value_unc = parse_length(o.get("normalized") or o.get("raw_text"))
                if value_mm is None and o.get("normalized") and o.get("raw_text"):
                    value_mm, unit, value_unc = parse_length(o.get("raw_text"))
            observations.append(
                {
                    "observation_id": f"{sid}/OBS-{i:04d}",
                    "source_id": sid,
                    "kind": o["kind"],
                    "raw_text": o.get("raw_text"),
                    "normalized": o.get("normalized"),
                    "confidence": o.get("confidence"),
                    "uncertainty": o.get("uncertainty"),
                    "subject": o.get("subject"),
                    "dimension_axis": o.get("dimension_axis"),
                    "opening_type": o.get("opening_type"),
                    "region_hint": o.get("region_hint"),
                    "value_mm": value_mm,
                    "unit": unit,
                    "value_uncertainty": value_unc,
                }
            )
        t = outcome.transport
        doc: dict[str, Any] = {
            "schema": "blueprint.extraction-record",
            "schema_version": "1.0.0",
            "source_id": sid,
            "input_sha256": norm["input_sha256"],
            "normalized_sha256": norm["normalized_sha256"],
            "normalized_path": norm["normalized_path"],
            "request_id": t.request_id,
            "attempt_id": attempt_id,
            "status": outcome.status.value,
            "stage_status": outcome.stage_statuses(),
            "model": {
                "requested": t.model_requested,
                "reported": t.model_reported,
                "api": t.api,
                "fallback_used": t.fallback_used,
            },
            "prompt": request_meta["prompt"],
            "provenance": prov.to_dict(),
            "legible": parsed.get("legible"),
            "notes": parsed.get("notes"),
            "observations": observations,
            "warnings": [{"code": w["code"], "message": w["message"]} for w in outcome.warnings],
        }
        if parsed.get("fragment_class"):
            doc["fragment"] = {
                "fragment_class": parsed["fragment_class"],
                "confidence": parsed.get("fragment_class_confidence"),
                "asserted_by": "MODEL",
            }
        return doc
