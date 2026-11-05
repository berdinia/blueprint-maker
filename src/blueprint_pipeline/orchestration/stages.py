"""Pipeline stages with explicit inputs, outputs and durable stage records.

Each stage writes its artifacts through the :class:`ArtifactStore` and finishes
by writing ``state/stages/<stage>.json``: status, input hashes and output
hashes. ``status`` and ``resume`` decide what is current by recomputing input
hashes and re-hashing outputs — never from logs or modification times.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..canonical.builder import build_canonical_model
from ..errors import BlueprintError, ConfigError, LockError, StageBlocked
from ..evidence.families import (
    Relationship,
    classify_fragments,
    computed_relationships,
    evidence_origins,
    families,
    resolve_relationships,
)
from ..evidence.identity import normalize_label, resolve_identities
from ..extraction.prompts import (
    EXTRACTION_TEMPLATE_ID,
    REVIEW_SYSTEM,
    REVIEW_USER,
    template_sha256,
)
from ..extraction.runner import ExtractionRunner, current_rel
from ..inference.client import ChatRequest, ImageInput, LocalInferenceClient, new_request_id
from ..io.atomic import (
    atomic_write_bytes,
    atomic_write_json,
    canonical_json,
    read_json,
    sha256_bytes,
    sha256_file,
)
from ..normalization import NORMALIZATION_VERSION, normalize_image
from ..rendering import RenderOverrides, build_scene, write_dxf, write_pdf, write_png, write_svg
from ..review.repair import MAX_CYCLES_HARD_LIMIT, propose_adjustment
from ..review.reviewer import interpret_review, review_currency, simulated_review
from ..schemas import schema_errors, schema_tag
from ..validation import (
    build_report,
    check_dxf,
    check_geometry,
    check_layout,
    check_model,
    check_pdf,
    check_png,
    check_svg,
    report_markdown,
)
from ..validation.checks import Check
from .context import PipelineContext
from .lock import HeavyJobLock, inspect_lock

STAGE_ORDER = (
    "inventory",
    "normalize",
    "extract",
    "classify",
    "reconcile",
    "build-model",
    "render",
    "validate",
    "review",
    "package",
)

INVENTORY = "inventory/inventory.json"
CLASSIFICATION = "evidence/classification.json"
EVIDENCE = "evidence/evidence.json"
MODEL = "canonical/model.json"
OVERRIDES = "review/overrides.json"
RENDER = {
    "svg": "render/plan.svg",
    "png": "render/plan.png",
    "pdf": "render/plan.pdf",
    "dxf": "render/plan.dxf",
}
SCENE = "render/scene.json"
REPORT = "validation/report.json"
REPORT_MD = "validation/report.md"
REVIEW = "review/review.json"
REPAIR_LOG = "review/repair-log.json"


@dataclass
class StageResult:
    stage: str
    status: str  # PASS | PARTIAL | FAIL | BLOCKED
    summary: str
    outputs: dict[str, str] = field(default_factory=dict)  # rel -> sha256
    inputs: dict[str, str] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha_of(ctx: PipelineContext, rel: str) -> str | None:
    p = ctx.store.path(rel)
    return sha256_file(p) if p.exists() else None


def _record(ctx: PipelineContext, res: StageResult) -> StageResult:
    doc = {**res.to_dict(), "finished_at": _now(), "config_hash": ctx.settings.config_hash()}
    atomic_write_json(ctx.store.path(f"state/stages/{res.stage}.json"), doc)
    ctx.event(
        f"{res.stage}: {res.status} - {res.summary}",
        event="STAGE_END",
        stage=res.stage,
        status=res.status,
    )
    return res


def _inventory(ctx: PipelineContext) -> dict[str, Any]:
    if not ctx.store.check(INVENTORY).is_current:
        raise StageBlocked("inventory missing or stale; run 'blueprint inventory'")
    data: dict[str, Any] = ctx.store.read_json(INVENTORY)
    return data


def _usable(inv: dict[str, Any]) -> list[dict[str, Any]]:
    return [s for s in inv["sources"] if s["usable"]]


# --------------------------------------------------------------------- inventory
def stage_inventory(ctx: PipelineContext, **_: Any) -> StageResult:
    from ..inventory.scan import run_inventory

    reg = ctx.register()
    inv = run_inventory(reg, ctx.settings.paths.input_dir)
    reg_sha = sha256_file(ctx.register_path)
    meta = ctx.store.write_json(INVENTORY, inv, stage="inventory", inputs={"register": reg_sha})
    n = len(inv["sources"])
    blocked = len(inv["blocked_source_ids"])
    warn = sum(1 for f in inv["findings"] if f["severity"] == "WARN")
    return StageResult(
        "inventory",
        inv["status"],
        f"{n} registered, {n - blocked} usable, {blocked} blocked, {warn} warnings",
        {INVENTORY: meta.sha256},
        {"register": reg_sha},
        {"findings": inv["findings"]},
    )


# --------------------------------------------------------------------- normalize
def _norm_params(ctx: PipelineContext) -> str:
    inf = ctx.settings.inference
    return sha256_bytes(
        canonical_json(
            {
                "v": NORMALIZATION_VERSION,
                "max_px": inf.max_image_px,
                "max_bytes": inf.max_image_bytes,
            }
        ).encode()
    )


def stage_normalize(ctx: PipelineContext, **_: Any) -> StageResult:
    inv = _inventory(ctx)
    params = _norm_params(ctx)
    outputs: dict[str, str] = {}
    failed: list[str] = []
    skipped = 0
    for src in _usable(inv):
        sid = src["source_id"]
        path = ctx.settings.paths.input_dir / src["path"]
        actual = sha256_file(path)
        if actual != src["sha256"]:
            failed.append(f"{sid}: evidence changed since inventory (immutability violated)")
            continue
        rec_rel, png_rel = f"normalized/{sid}.json", f"normalized/{sid}.png"
        inputs = {"source": actual, "params": params}
        if (
            ctx.store.check(rec_rel, inputs=inputs).is_current
            and ctx.store.check(png_rel, inputs={"source": actual}).is_current
        ):
            skipped += 1
            outputs[rec_rel] = ctx.store.read_meta(rec_rel).sha256  # type: ignore[union-attr]
            continue
        try:
            data, rec = normalize_image(
                path,
                max_px=ctx.settings.inference.max_image_px,
                max_bytes=ctx.settings.inference.max_image_bytes,
            )
        except Exception as exc:  # Pillow raises many decoder-specific types
            ctx.store.write_error(
                rec_rel, {"source_id": sid, "error": f"{type(exc).__name__}: {exc}"}
            )
            failed.append(f"{sid}: {type(exc).__name__}")
            continue
        ctx.store.write_bytes(png_rel, data, stage="normalize", inputs={"source": actual})
        rec.update({"source_id": sid, "input_sha256": actual, "normalized_path": png_rel})
        meta = ctx.store.write_json(rec_rel, rec, stage="normalize", inputs=inputs)
        outputs[rec_rel] = meta.sha256
    total = len(_usable(inv))
    status = "PASS" if not failed else ("PARTIAL" if outputs else "FAIL")
    if total == 0:
        status = "BLOCKED"
    return StageResult(
        "normalize",
        status,
        f"{len(outputs)}/{total} normalized ({skipped} already current)"
        + (f"; failed: {failed}" if failed else ""),
        outputs,
        {"inventory": ctx.store.read_meta(INVENTORY).sha256, "params": params},  # type: ignore[union-attr]
    )


# --------------------------------------------------------------------- extract
def stage_extract(
    ctx: PipelineContext, *, source_ids: list[str] | None = None, force: bool = False, **_: Any
) -> StageResult:
    ctx.settings.require_inference("extract")  # config errors before any work
    inv = _inventory(ctx)
    reg = ctx.register()
    runner = ExtractionRunner(ctx)
    wanted = [
        s["source_id"] for s in _usable(inv) if source_ids is None or s["source_id"] in source_ids
    ]
    if source_ids:
        unknown = sorted(set(source_ids) - set(wanted))
        if unknown:
            return StageResult("extract", "BLOCKED", f"not usable or not registered: {unknown}")
    results: dict[str, str] = {}
    outputs: dict[str, str] = {}
    for sid in wanted:
        try:
            summary = runner.run(reg.get(sid), force=force)
        except LockError as exc:
            return _record(
                ctx,
                StageResult(
                    "extract",
                    "BLOCKED",
                    str(exc),
                    outputs,
                    details={"results": results, "lock": exc.state},
                ),
            )
        except StageBlocked as exc:
            results[sid] = f"BLOCKED: {exc}"
            continue
        results[sid] = summary.status
        meta = ctx.store.read_meta(current_rel(sid))
        if meta is not None:
            outputs[current_rel(sid)] = meta.sha256
    ok = [s for s, r in results.items() if r in ("SUCCESS", "SKIPPED_CURRENT")]
    status = "PASS" if len(ok) == len(wanted) and wanted else "PARTIAL" if ok else "FAIL"
    if not wanted:
        status = "BLOCKED"
    return StageResult(
        "extract",
        status,
        f"{len(ok)}/{len(wanted)} sources have a successful current extraction",
        outputs,
        {"prompt": template_sha256(EXTRACTION_TEMPLATE_ID)},
        {"results": results},
    )


def _current_extractions(ctx: PipelineContext, inv: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Successful extractions whose inputs still match the current normalized copies."""
    out: dict[str, dict[str, Any]] = {}
    prompt = template_sha256(EXTRACTION_TEMPLATE_ID)
    for src in _usable(inv):
        sid = src["source_id"]
        nrel = f"normalized/{sid}.json"
        if not ctx.store.check(nrel).is_current:
            continue
        norm = ctx.store.read_json(nrel)
        rep = ctx.store.check(
            current_rel(sid),
            inputs={
                "normalized": norm["normalized_sha256"],
                "source": norm["input_sha256"],
                "prompt": prompt,
            },
            content_schema=schema_tag("extraction-record"),
        )
        if rep.is_current:
            out[sid] = ctx.store.read_json(current_rel(sid))
    return out


# --------------------------------------------------------------------- classify
def _declared_relationships(ctx: PipelineContext) -> tuple[list[Relationship], str | None]:
    p = ctx.relationships_path
    if not p.exists():
        return [], None
    doc = read_json(p)
    errs = schema_errors(doc, "relationships")
    if errs:
        raise StageBlocked(f"relationships.json invalid: {errs[:3]}")
    rels = [
        Relationship(
            r["a"],
            r["b"],
            r["kind"],
            r["asserted_by"],
            r.get("confidence"),
            f"declared ({r['asserted_by'].lower()})" + (f": {r['note']}" if r.get("note") else ""),
            r.get("subject"),
        )
        for r in doc["relationships"]
    ]
    return rels, sha256_file(p)


def stage_classify(ctx: PipelineContext, **_: Any) -> StageResult:
    inv = _inventory(ctx)
    reg = ctx.register()
    usable = _usable(inv)
    ids = [s["source_id"] for s in inv["sources"]]
    dh = {}
    for s in usable:
        rel = f"normalized/{s['source_id']}.json"
        if ctx.store.check(rel).is_current:
            dh[s["source_id"]] = ctx.store.read_json(rel)["dhash"]
    extractions = _current_extractions(ctx, inv)
    declared, rel_sha = _declared_relationships(ctx)
    rels, findings = resolve_relationships(declared, computed_relationships(usable, dh), set(ids))
    origin_of, origins = evidence_origins(ids, rels)
    fams = families(ids, rels, origin_of)
    room_labels = {
        sid: {
            lab
            for o in rec["observations"]
            if o["kind"] == "room_label"
            and (lab := normalize_label(o.get("normalized") or o.get("raw_text"))) is not None
        }
        for sid, rec in extractions.items()
    }
    frags = classify_fragments(
        ids,
        {r.source_id: r.declared_fragment_class for r in reg.records()},
        {sid: extractions.get(sid) for sid in ids},
        origin_of,
        room_labels,
    )
    inputs = {"inventory": ctx.store.read_meta(INVENTORY).sha256}  # type: ignore[union-attr]
    if rel_sha:
        inputs["relationships"] = rel_sha
    for sid, rec in sorted(extractions.items()):
        inputs[f"extraction:{sid}"] = sha256_bytes(canonical_json(rec).encode())
    doc = {
        "schema": "blueprint.evidence",
        "schema_version": "1.0.0",
        "synthetic": reg.synthetic,
        "relationships": [r.to_dict() for r in rels],
        "evidence_origins": origins,
        "families": fams,
        "fragments": frags,
        "identity_candidates": [],
        "conflicts": [],
        "unresolved_questions": [
            {"question_id": f"Q-REL-{i}", "text": f["message"], "refs": []}
            for i, f in enumerate(findings, start=1)
            if f["code"] in ("RELATIONSHIP_DISAGREEMENT", "RELATIONSHIP_UNKNOWN_SOURCE")
        ],
        "inputs": inputs,
    }
    errs = schema_errors(doc, "evidence")
    if errs:
        raise RuntimeError(f"classification failed its own schema: {errs[:3]}")
    meta = ctx.store.write_json(
        CLASSIFICATION, doc, stage="classify", inputs=inputs, content_schema=schema_tag("evidence")
    )
    missing = [s["source_id"] for s in usable if s["source_id"] not in extractions]
    status = "PASS" if not missing and usable else "PARTIAL" if extractions else "BLOCKED"
    return StageResult(
        "classify",
        status,
        f"{len(origins)} evidence origins in {len(fams)} families from {len(ids)} sources"
        + (f"; no current extraction for {missing}" if missing else ""),
        {CLASSIFICATION: meta.sha256},
        inputs,
        {"findings": findings},
    )


# --------------------------------------------------------------------- reconcile
def stage_reconcile(ctx: PipelineContext, **_: Any) -> StageResult:
    if not ctx.store.check(CLASSIFICATION).is_current:
        raise StageBlocked("classification missing or stale; run 'blueprint classify'")
    cls = ctx.store.read_json(CLASSIFICATION)
    inv = _inventory(ctx)
    extractions = _current_extractions(ctx, inv)
    expected = {k.split(":", 1)[1] for k in cls["inputs"] if k.startswith("extraction:")}
    if expected != set(extractions):
        raise StageBlocked("extractions changed since classification; run 'blueprint classify'")
    origin_of = {s: o["origin_id"] for o in cls["evidence_origins"] for s in o["source_ids"]}
    rels = [
        Relationship(
            r["a"],
            r["b"],
            r["kind"],
            r["asserted_by"],
            r.get("confidence"),
            r["basis"],
            r.get("subject"),
        )
        for r in cls["relationships"]
    ]
    cands, conflicts, questions = resolve_identities(extractions, origin_of, rels)
    doc = dict(cls)
    doc["identity_candidates"] = cands
    doc["conflicts"] = conflicts
    doc["unresolved_questions"] = sorted(
        cls["unresolved_questions"] + questions, key=lambda q: q["question_id"]
    )
    inputs = {"classification": ctx.store.read_meta(CLASSIFICATION).sha256}  # type: ignore[union-attr]
    doc["inputs"] = {**cls["inputs"], **inputs}
    errs = schema_errors(doc, "evidence")
    if errs:
        raise RuntimeError(f"evidence failed its own schema: {errs[:3]}")
    meta = ctx.store.write_json(
        EVIDENCE, doc, stage="reconcile", inputs=inputs, content_schema=schema_tag("evidence")
    )
    by_status: dict[str, int] = {}
    for c in cands:
        by_status[c["status"]] = by_status.get(c["status"], 0) + 1
    status = "PASS" if cands and not conflicts else "PARTIAL" if cands else "BLOCKED"
    return StageResult(
        "reconcile",
        status,
        f"{len(cands)} identity candidates {by_status}; {len(conflicts)} open conflicts",
        {EVIDENCE: meta.sha256},
        inputs,
    )


# --------------------------------------------------------------------- build-model
def stage_build_model(ctx: PipelineContext, **_: Any) -> StageResult:
    if not ctx.store.check(EVIDENCE).is_current:
        raise StageBlocked("evidence missing or stale; run 'blueprint reconcile'")
    ev = ctx.store.read_json(EVIDENCE)
    inv = _inventory(ctx)
    extractions = _current_extractions(ctx, inv)
    reg = ctx.register()
    decisions = None
    inputs = {"evidence": ctx.store.read_meta(EVIDENCE).sha256}  # type: ignore[union-attr]
    if ctx.decisions_path.exists():
        decisions = read_json(ctx.decisions_path)
        errs = schema_errors(decisions, "decisions")
        if errs:
            raise StageBlocked(f"decisions.json invalid: {errs[:3]}")
        inputs["decisions"] = sha256_file(ctx.decisions_path)
    inputs["register"] = sha256_file(ctx.register_path)
    model = build_canonical_model(
        evidence=ev,
        extractions=extractions,
        decisions=decisions,
        synthetic=reg.synthetic,
        input_hashes=inputs,
    )
    checks = check_model(
        model,
        known_source_ids=set(reg.ids()),
        known_origins={o["origin_id"] for o in ev["evidence_origins"]},
    )
    failed = [c for c in checks if c.status == "FAIL"]
    if failed:
        ctx.store.write_error(
            MODEL, {"status": "REJECTED", "failed_checks": [c.to_dict() for c in failed]}
        )
        return StageResult(
            "build-model",
            "FAIL",
            "canonical model rejected: "
            + "; ".join(f"{c.check_id}: {c.detail}" for c in failed)[:500],
            inputs=inputs,
        )
    meta = ctx.store.write_json(
        MODEL,
        model,
        stage="build-model",
        inputs=inputs,
        content_schema=schema_tag("canonical-model"),
    )
    not_applied = [d for d in model["decisions"] if not d["applied"]]
    open_conf = [c for c in model["conflicts"] if c["status"] == "OPEN"]
    placed = sum(1 for r in model["rooms"] if r["polygon_mm"])
    status = "PASS" if not (not_applied or open_conf or model["fragments_unplaced"]) else "PARTIAL"
    return StageResult(
        "build-model",
        status,
        f"{placed}/{len(model['rooms'])} rooms placed, {len(model['walls'])} walls, "
        f"{len(open_conf)} open conflicts, {len(model['fragments_unplaced'])} unplaced items, "
        f"{len(not_applied)} decisions not applied",
        {MODEL: meta.sha256},
        inputs,
        {"decisions_not_applied": not_applied},
    )


# --------------------------------------------------------------------- render
def _overrides(ctx: PipelineContext) -> RenderOverrides:
    p = ctx.store.path(OVERRIDES)
    if not p.exists():
        return RenderOverrides()
    return RenderOverrides(**read_json(p)["overrides"])


def _render_inputs(ctx: PipelineContext, ov: RenderOverrides) -> dict[str, str]:
    return {
        "model": ctx.store.read_meta(MODEL).sha256,  # type: ignore[union-attr]
        "render_settings": ctx.settings.render_hash(),
        "overrides": sha256_bytes(canonical_json(ov.to_dict()).encode()),
    }


def stage_render(ctx: PipelineContext, **_: Any) -> StageResult:
    if not ctx.store.check(MODEL).is_current:
        raise StageBlocked("canonical model missing or stale; run 'blueprint build-model'")
    model = ctx.store.read_json(MODEL)
    ov = _overrides(ctx)
    inputs = _render_inputs(ctx, ov)
    scene = build_scene(model, ctx.settings.render, ov)
    nd = ctx.settings.render.coordinate_decimals
    svg = write_svg(scene, nd)
    svg_meta = ctx.store.write_bytes(RENDER["svg"], svg, stage="render", inputs=inputs)
    png, info = write_png(scene, ctx.settings.render.png_dpi, source_svg_sha256=svg_meta.sha256)
    out = {RENDER["svg"]: svg_meta.sha256}
    out[RENDER["png"]] = ctx.store.write_bytes(
        RENDER["png"], png, stage="render", inputs={**inputs, "svg": svg_meta.sha256}, extra=info
    ).sha256
    out[RENDER["pdf"]] = ctx.store.write_bytes(
        RENDER["pdf"], write_pdf(scene), stage="render", inputs=inputs
    ).sha256
    out[RENDER["dxf"]] = ctx.store.write_bytes(
        RENDER["dxf"], write_dxf(scene, nd), stage="render", inputs=inputs
    ).sha256
    out[SCENE] = ctx.store.write_json(SCENE, scene.to_dict(), stage="render", inputs=inputs).sha256
    return StageResult(
        "render",
        "PASS",
        f"scale {scene.transform.scale_ratio() if scene.transform else 'n/a'}, "
        f"{len(scene.lines)} lines, {len(scene.polys)} polylines, {len(scene.texts)} texts",
        out,
        inputs,
    )


# --------------------------------------------------------------------- validate
def run_validation(ctx: PipelineContext) -> tuple[dict[str, Any], list[Check]]:
    if not ctx.store.check(MODEL).is_current:
        raise StageBlocked("canonical model missing or stale")
    model = ctx.store.read_json(MODEL)
    ev = ctx.store.read_json(EVIDENCE) if ctx.store.path(EVIDENCE).exists() else None
    reg = ctx.register()
    ov = _overrides(ctx)
    scene = build_scene(model, ctx.settings.render, ov)
    checks: list[Check] = []
    checks += check_model(
        model,
        known_source_ids=set(reg.ids()),
        known_origins={o["origin_id"] for o in ev["evidence_origins"]} if ev else None,
    )
    checks += check_geometry(model)
    checks += check_layout(scene, model)
    inputs = _render_inputs(ctx, ov)
    artifacts: dict[str, str] = {MODEL: ctx.store.read_meta(MODEL).sha256}  # type: ignore[union-attr]
    for kind, rel in RENDER.items():
        rep = ctx.store.check(rel, inputs=None)
        if not rep.is_current:
            checks.append(Check(f"{kind}_present", kind, "FAIL", f"{rel}: {rep.state.value}"))
            continue
        meta = rep.meta
        assert meta is not None
        stale = {k: v for k, v in inputs.items() if meta.inputs.get(k) != v}
        if stale:
            checks.append(
                Check(
                    f"{kind}_current",
                    kind,
                    "FAIL",
                    f"{rel} was rendered from different inputs: {sorted(stale)}",
                )
            )
        artifacts[rel] = meta.sha256
    svg_p = ctx.store.path(RENDER["svg"])
    if svg_p.exists():
        svg = svg_p.read_bytes()
        checks += check_svg(svg, scene, model)
        if ctx.store.path(RENDER["png"]).exists():
            checks += check_png(
                ctx.store.path(RENDER["png"]).read_bytes(),
                scene,
                dpi=ctx.settings.render.png_dpi,
                svg_sha256=sha256_bytes(svg),
            )
    else:
        checks.append(Check("svg_present", "svg", "SKIP", "no SVG to validate"))
    if ctx.store.path(RENDER["dxf"]).exists():
        checks += check_dxf(ctx.store.path(RENDER["dxf"]).read_bytes(), scene, model)
    if ctx.store.path(RENDER["pdf"]).exists():
        checks += check_pdf(ctx.store.path(RENDER["pdf"]).read_bytes(), scene)
    report = build_report(checks, artifacts)
    return report, checks


def stage_validate(ctx: PipelineContext, **_: Any) -> StageResult:
    report, _checks = run_validation(ctx)
    errs = schema_errors(report, "validation-report")
    if errs:
        raise RuntimeError(f"validation report failed its own schema: {errs[:3]}")
    inputs = dict(report["artifacts"])
    meta = ctx.store.write_json(
        REPORT,
        report,
        stage="validate",
        inputs=inputs,
        content_schema=schema_tag("validation-report"),
    )
    md = ctx.store.write_bytes(
        REPORT_MD, report_markdown(report).encode(), stage="validate", inputs=inputs
    )
    c = report["counts"]
    return StageResult(
        "validate",
        report["status"],
        f"PASS {c['PASS']} WARN {c['WARN']} FAIL {c['FAIL']} SKIP {c['SKIP']}",
        {REPORT: meta.sha256, REPORT_MD: md.sha256},
        inputs,
    )


# --------------------------------------------------------------------- review
FACT_CATEGORIES = {"canonical_model", "geometry", "dxf", "pdf"}


def _issue_category(check_id: str) -> str:
    """Map a failed mechanical presentation check to a repair category.

    Only collisions, label placement and clipping have deterministic repairs;
    anything else is a rendering defect that escalates to a human.
    """
    if "collision" in check_id:
        return "collision"
    if "room_labels" in check_id:
        return "label_placement"
    if "clipping" in check_id:
        return "clipping"
    return "rendering_defect"


def _local_review(ctx: PipelineContext, png_rel: str, png_sha: str, cycle: int) -> dict[str, Any]:
    s = ctx.settings
    _ep, model = s.require_inference("review")
    client = LocalInferenceClient(s, model)
    data = ctx.store.path(png_rel).read_bytes()
    req = ChatRequest(
        system=REVIEW_SYSTEM,
        user_text=REVIEW_USER,
        images=[ImageInput(data, "image/png", png_sha)],
        request_id=new_request_id(),
    )
    with HeavyJobLock(
        s.paths.lock_path,
        operation="review",
        model=model,
        source_id=None,
        wait_s=s.lock.wait_s,
        poll_s=s.lock.poll_s,
    ):
        transport = client.chat(req)
    adir = f"review/attempts/cycle-{cycle}-{transport.request_id}"
    atomic_write_bytes(ctx.store.path(f"{adir}/raw-response.bin"), transport.raw)
    atomic_write_json(ctx.store.path(f"{adir}/transport.json"), transport.summary())
    return interpret_review(
        transport, artifact_rel=png_rel, artifact_sha256=png_sha, cycle=cycle, model=model
    )


def stage_review(ctx: PipelineContext, *, simulated: bool = False, **_: Any) -> StageResult:
    if not simulated:
        ctx.settings.require_inference("review")
    max_cycles = min(ctx.settings.review.max_repair_cycles, MAX_CYCLES_HARD_LIMIT)
    log: list[dict[str, Any]] = []
    if ctx.store.path(REPAIR_LOG).exists():
        log = ctx.store.read_json(REPAIR_LOG)["cycles"]
    cycle = 0
    record: dict[str, Any] | None = None
    while True:
        # Mechanical validation always precedes visual review.
        render_res = stage_render(ctx)
        _record(ctx, render_res)
        val = stage_validate(ctx)
        _record(ctx, val)
        report = ctx.store.read_json(REPORT)
        fact_fail = [
            c
            for c in report["checks"]
            if c["status"] == "FAIL" and c["category"] in FACT_CATEGORIES
        ]
        if fact_fail:
            return StageResult(
                "review",
                "BLOCKED",
                "mechanical validation failed on model/geometry/DXF/PDF; visual review cannot repair facts: "
                + ", ".join(c["check_id"] for c in fact_fail),
            )
        png_rel = RENDER["png"]
        png_sha = sha256_file(ctx.store.path(png_rel))
        if simulated:
            scene = build_scene(ctx.store.read_json(MODEL), ctx.settings.render, _overrides(ctx))
            record = simulated_review(
                check_layout(scene, ctx.store.read_json(MODEL)),
                artifact_rel=png_rel,
                artifact_sha256=png_sha,
                cycle=cycle,
            )
        else:
            try:
                record = _local_review(ctx, png_rel, png_sha, cycle)
            except LockError as exc:
                return StageResult("review", "BLOCKED", str(exc))
        errs = schema_errors(record, "review-record")
        if errs:
            raise RuntimeError(f"review record failed its own schema: {errs[:3]}")
        atomic_write_json(ctx.store.path(f"review/history/cycle-{cycle}.json"), record)
        layout_fail = [
            {
                "category": _issue_category(c["check_id"]),
                "severity": "major",
                "description": c["detail"],
            }
            for c in report["checks"]
            if c["category"] in ("layout", "svg", "png") and c["status"] == "FAIL"
        ]
        entry = {
            "cycle": cycle,
            "png_sha256": png_sha,
            "review_verdict": record["review_verdict"],
            "review_issues": record["issues"],
            "mechanical_status": report["status"],
            "mechanical_failures": [
                c["check_id"] for c in report["checks"] if c["status"] == "FAIL"
            ],
            "overrides": _overrides(ctx).to_dict(),
        }
        done = record["review_verdict"] == "PASS" and report["status"] != "FAIL"
        if done or record["review_verdict"] == "NOT_REVIEWED" or cycle >= max_cycles:
            entry["action"] = "stop"
            log.append(entry)
            break
        new, notes = propose_adjustment(record["issues"] + layout_fail, _overrides(ctx))
        entry["adjustment_notes"] = notes
        if new is None:
            entry["action"] = "escalate: no automatic repair available"
            log.append(entry)
            break
        entry["action"] = f"repair -> cycle {cycle + 1}"
        log.append(entry)
        atomic_write_json(
            ctx.store.path(OVERRIDES), {"overrides": new.to_dict(), "set_by_cycle": cycle}
        )
        cycle += 1
    atomic_write_json(ctx.store.path(REPAIR_LOG), {"cycles": log, "max_cycles": max_cycles})
    assert record is not None
    meta = ctx.store.write_json(
        REVIEW,
        record,
        stage="review",
        inputs={"png": record["reviewed_artifact_sha256"]},
        content_schema=schema_tag("review-record"),
    )
    verdict = record["review_verdict"]
    status = (
        "PASS"
        if verdict == "PASS" and report["status"] == "PASS"
        else ("PARTIAL" if verdict == "PASS" else "FAIL" if verdict == "FAIL" else "BLOCKED")
    )
    who = record["reviewer"]["kind"]
    msg = f"{who} review {verdict} after {cycle} repair cycle(s); mechanical {report['status']}"
    if verdict != "PASS":
        msg += "; human review required"
    return StageResult(
        "review", status, msg, {REVIEW: meta.sha256}, {"png": record["reviewed_artifact_sha256"]}
    )


# --------------------------------------------------------------------- package
def stage_package(ctx: PipelineContext, **_: Any) -> StageResult:
    from ..review.packet import build_packet

    return build_packet(ctx)


STAGES: dict[str, Callable[..., StageResult]] = {
    "inventory": stage_inventory,
    "normalize": stage_normalize,
    "extract": stage_extract,
    "classify": stage_classify,
    "reconcile": stage_reconcile,
    "build-model": stage_build_model,
    "render": stage_render,
    "validate": stage_validate,
    "review": stage_review,
    "package": stage_package,
}


def run_stage(ctx: PipelineContext, name: str, **opts: Any) -> StageResult:
    ctx.event(f"{name}: start", event="STAGE_START", stage=name)
    try:
        res = STAGES[name](ctx, **opts)
    except StageBlocked as exc:
        res = StageResult(name, "BLOCKED", str(exc))
    except ConfigError as exc:
        res = StageResult(name, "BLOCKED", f"configuration: {exc}")
    except LockError as exc:
        res = StageResult(name, "BLOCKED", str(exc), details={"lock": exc.state})
    except BlueprintError as exc:
        res = StageResult(name, "FAIL", f"{type(exc).__name__}: {exc}")
    return _record(ctx, res)


# --------------------------------------------------------------------- status
def stage_state(ctx: PipelineContext, name: str) -> dict[str, Any]:
    p = ctx.store.path(f"state/stages/{name}.json")
    if not p.exists():
        return {"stage": name, "status": "NOT_RUN", "freshness": "NOT_RUN", "summary": ""}
    rec = json.loads(p.read_text(encoding="utf-8"))
    stale = []
    for rel, sha in rec.get("outputs", {}).items():
        if _sha_of(ctx, rel) != sha:
            stale.append(f"output {rel} changed or missing")
    if rec.get("config_hash") != ctx.settings.config_hash() and name not in (
        "inventory",
        "package",
    ):
        stale.append("configuration changed")
    return {
        "stage": name,
        "status": rec["status"],
        "freshness": "STALE" if stale else "RECORDED",
        "summary": rec.get("summary", ""),
        "finished_at": rec.get("finished_at"),
        "reasons": stale,
    }


def pipeline_status(ctx: PipelineContext) -> dict[str, Any]:
    stages = [stage_state(ctx, n) for n in STAGE_ORDER]
    # Upstream staleness propagates: if a stage's inputs no longer match what an
    # upstream stage currently records, it is stale.
    upstream_stale = False
    for st in stages:
        if upstream_stale and st["freshness"] == "RECORDED":
            st["freshness"] = "STALE"
            st.setdefault("reasons", []).append("an upstream stage is stale or not run")
        if st["freshness"] != "RECORDED" or st["status"] in ("FAIL", "BLOCKED"):
            upstream_stale = True
    review = ctx.store.read_json(REVIEW) if ctx.store.path(REVIEW).exists() else None
    rstate, rwhy = review_currency(review, ctx.store)
    lock = inspect_lock(ctx.settings.paths.lock_path)
    return {
        "stages": stages,
        "review": {
            "state": rstate,
            "reason": rwhy,
            "verdict": review["review_verdict"] if review else None,
            "reviewer": review["reviewer"]["kind"] if review else None,
        },
        "lock": {"state": lock.state, "detail": lock.detail, "record": lock.record},
    }


def resume(
    ctx: PipelineContext, *, simulated_review: bool = False, until: str | None = None
) -> list[StageResult]:
    results: list[StageResult] = []
    status = pipeline_status(ctx)
    first = next(
        (
            s["stage"]
            for s in status["stages"]
            if s["freshness"] != "RECORDED" or s["status"] in ("FAIL", "BLOCKED")
        ),
        None,
    )
    if first is None:
        return results
    for name in STAGE_ORDER[STAGE_ORDER.index(first) :]:
        opts: dict[str, Any] = {"simulated": simulated_review} if name == "review" else {}
        res = run_stage(ctx, name, **opts)
        results.append(res)
        # A failed visual review still gets a human-review packet.
        if (res.status in ("FAIL", "BLOCKED") and name != "review") or name == until:
            break
    return results
