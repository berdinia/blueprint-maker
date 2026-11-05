"""Synthetic end-to-end run with output verification.

Builds a throw-away project from the synthetic fixture, starts a loopback fake
model server, runs every stage through the real code paths (HTTP client,
lock, atomic writes, schemas, renderers, validators, review loop, packet) and
then verifies the *content* of the outputs, not just their existence.
"""

from __future__ import annotations

import io
import json
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..config.settings import load_settings
from ..io.atomic import sha256_file
from ..orchestration.context import PipelineContext
from ..orchestration.stages import (
    EVIDENCE,
    MODEL,
    RENDER,
    REPAIR_LOG,
    REPORT,
    REVIEW,
    pipeline_status,
    run_stage,
    stage_render,
)
from ..review.packet import PACKET_DIR
from .fake_server import FakeModelServer
from .synthetic import (
    EXTRACTION_RESPONSES,
    REVIEW_RESPONSES,
    generate_images,
    write_fixture_files,
    write_project_config,
)


@dataclass
class Verification:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, fn: Callable[[], tuple[bool, str]]) -> None:
        try:
            ok, detail = fn()
        except Exception as exc:  # report, do not crash the whole run
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        self.checks.append((name, ok, detail))

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.checks)


def run_synthetic(project: Path | None = None) -> dict[str, Any]:
    tmp = None
    if project is None:
        tmp = tempfile.TemporaryDirectory(prefix="blueprint-synthetic-")
        project = Path(tmp.name)
    project.mkdir(parents=True, exist_ok=True)
    write_fixture_files(project)
    generate_images(project / "input")
    v = Verification()
    stages: list[dict[str, Any]] = []
    with FakeModelServer() as server:
        cfg = write_project_config(project, server.url)
        settings = load_settings(cfg, env={})
        ctx = PipelineContext(settings)
        for name in ("inventory", "normalize"):
            stages.append(run_stage(ctx, name).to_dict())
        for sid, spec in EXTRACTION_RESPONSES.items():
            rel = f"normalized/{sid}.json"
            if ctx.store.path(rel).exists():
                server.respond_for_image(ctx.store.read_json(rel)["normalized_sha256"], spec)
        server.enqueue(*REVIEW_RESPONSES)
        for name in ("extract", "classify", "reconcile", "build-model"):
            stages.append(run_stage(ctx, name).to_dict())
        stages.append(run_stage(ctx, "review").to_dict())
        stages.append(run_stage(ctx, "package").to_dict())
        requests = list(server.requests)
        _verify(ctx, v, stages, requests, project)
        status = pipeline_status(ctx)
    result = {
        "project": str(project) if tmp is None else "<temporary>",
        "passed": v.passed,
        "checks": [{"check": n, "ok": ok, "detail": d} for n, ok, d in v.checks],
        "stages": [{k: s[k] for k in ("stage", "status", "summary")} for s in stages],
        "status": status,
    }
    if tmp is not None:
        tmp.cleanup()
    return result


def _verify(
    ctx: PipelineContext,
    v: Verification,
    stages: list[dict[str, Any]],
    requests: list[dict[str, Any]],
    project: Path,
) -> None:
    st = {s["stage"]: s for s in stages}
    store = ctx.store
    inv = store.read_json("inventory/inventory.json")
    codes = {(f["code"], tuple(f["refs"])) for f in inv["findings"]}

    v.check(
        "inventory_findings",
        lambda: (
            ("MISSING_FILE", ("SYN-SRC-008",)) in codes
            and ("EXACT_DUPLICATE", ("SYN-SRC-001", "SYN-SRC-002")) in codes
            and any(
                c == "FILENAME_REGISTER_DISAGREEMENT" and r[0] == "SYN-SRC-002" for c, r in codes
            )
            and any(c == "UNREGISTERED_FILE" for c, _ in codes),
            f"{len(codes)} findings; status {inv['status']}",
        ),
    )
    v.check(
        "stage_statuses",
        lambda: (
            st["inventory"]["status"] == "PARTIAL"
            and st["extract"]["status"] == "PARTIAL"
            and st["build-model"]["status"] in ("PASS", "PARTIAL")
            and st["review"]["status"] in ("PASS", "PARTIAL"),
            ", ".join(f"{s['stage']}={s['status']}" for s in stages),
        ),
    )

    def provenance() -> tuple[bool, str]:
        rec = store.read_json("extraction/SYN-SRC-003/current.json")
        p = rec["provenance"]
        ok = (
            rec["source_id"] == "SYN-SRC-003"
            and p["model_reported_source_id"] == "SYN-SRC-999"
            and p["source_id_mismatch"] is True
            and any(w["code"] == "SOURCE_ID_MISMATCH" for w in rec["warnings"])
            and all(o["source_id"] == "SYN-SRC-003" for o in rec["observations"])
        )
        return ok, f"authoritative {rec['source_id']}, model said {p['model_reported_source_id']}"

    v.check("provenance_enforced", provenance)

    def empty_response() -> tuple[bool, str]:
        err = store.read_error("extraction/SYN-SRC-007/current.json")
        ok = (
            err is not None
            and err["status"] == "EMPTY_RESPONSE"
            and not store.path("extraction/SYN-SRC-007/current.json").exists()
        )
        return ok, f"error artifact status {err and err['status']}"

    v.check("empty_response_not_success", empty_response)

    def raw_preserved() -> tuple[bool, str]:
        attempts = list(store.path("extraction/SYN-SRC-001/attempts").iterdir())
        needed = {
            "request.json",
            "raw-response.bin",
            "assembled.txt",
            "parsed.json",
            "outcome.json",
        }
        have = {p.name for p in attempts[0].iterdir()} if attempts else set()
        return needed <= have, f"attempt files {sorted(have)}"

    v.check("raw_exchange_preserved", raw_preserved)
    ev = store.read_json(EVIDENCE)
    origin = {s: o["origin_id"] for o in ev["evidence_origins"] for s in o["source_ids"]}
    v.check(
        "evidence_origins",
        lambda: (
            origin["SYN-SRC-001"] == origin["SYN-SRC-002"] == origin["SYN-SRC-003"]
            and origin["SYN-SRC-004"] != origin["SYN-SRC-001"]
            and origin["SYN-SRC-005"] != origin["SYN-SRC-001"],
            f"{len(ev['evidence_origins'])} origins; sheet-A origin {origin['SYN-SRC-001']}",
        ),
    )
    cands = {c["candidate_id"]: c for c in ev["identity_candidates"]}
    v.check(
        "identity_resolution",
        lambda: (
            cands["ID-BRAVO"]["status"] == "CORROBORATED"
            and cands["ID-BRAVO"]["independent_origin_count"] == 2
            and cands["ID-ALPHA"]["independent_origin_count"] == 1
            and cands["ID-CHARLIE"]["status"] == "CONFLICTED"
            and cands["ID-CHARLIE"]["revision_state"] == "POSSIBLE_REVISION"
            and "ID-DELTA" in cands,
            "BRAVO corroborated by 2 origins; ALPHA photos not double-counted; CHARLIE conflicted",
        ),
    )
    frags = {f["source_id"]: f for f in ev["fragments"]}
    v.check(
        "fragment_placement",
        lambda: (
            frags["SYN-SRC-006"]["placement_state"] == "DETAIL_ONLY"
            and frags["SYN-SRC-004"]["placement_state"] == "LIKELY"
            and frags["SYN-SRC-005"]["placement_state"] == "AMBIGUOUS"
            and frags["SYN-SRC-007"]["fragment_class"] == "UNKNOWN",
            ", ".join(
                f"{k}={f['fragment_class']}/{f['placement_state']}"
                for k, f in sorted(frags.items())
            ),
        ),
    )
    model = store.read_json(MODEL)
    rooms = {r["room_id"]: r for r in model["rooms"]}

    def canonical() -> tuple[bool, str]:
        charlie_conf = [
            c for c in model["conflicts"] if c["subject"] == "ID-CHARLIE" and c["status"] == "OPEN"
        ]
        meas = next(c for c in charlie_conf if c["kind"] == "MEASUREMENT")
        door = next(o for o in model["openings"] if o["opening_id"] == "OPEN-DOOR-1")
        windows = [o for o in model["openings"] if o["kind"] == "window"]
        ok = (
            rooms["ROOM-DELTA"]["polygon_mm"] is None
            and rooms["ROOM-DELTA"]["placement_state"] == "UNPLACED"
            and rooms["ROOM-HYP-ANNEX"]["hypothetical"] is True
            and sorted(set(meas["values"])) == [2500.0, 2800.0]
            and door["wall_id"] is not None
            and len(windows) == 1
            and windows[0]["wall_id"] is None
            and any(o["placement_state"] == "DETAIL_ONLY" for o in model["openings"])
            and len(model["walls"]) >= 10
        )
        return ok, (
            f"{len(model['rooms'])} rooms, {len(model['walls'])} walls, "
            f"{len(charlie_conf)} open CHARLIE conflicts retained; readings {sorted(meas['values'])}"
        )

    v.check("canonical_model", canonical)
    report = store.read_json(REPORT)
    by_id = {(c["category"], c["check_id"]): c for c in report["checks"]}

    def need(*keys: tuple[str, str]) -> tuple[bool, str]:
        bad = [
            f"{k}={by_id.get(k, {}).get('status')}"
            for k in keys
            if by_id.get(k, {}).get("status") != "PASS"
        ]
        return not bad, "all PASS" if not bad else "; ".join(bad)

    v.check(
        "validation_not_failed",
        lambda: (report["status"] in ("PASS", "PARTIAL"), f"{report['status']} {report['counts']}"),
    )
    v.check(
        "dxf_reopen_and_units",
        lambda: need(
            ("dxf", "dxf_reopen"),
            ("dxf", "units_declared"),
            ("dxf", "entity_counts"),
            ("dxf", "room_polygons_exact"),
        ),
    )
    v.check(
        "svg_valid",
        lambda: need(
            ("svg", "xml_valid"),
            ("svg", "entity_counts"),
            ("svg", "geometry_inside_viewbox"),
            ("svg", "uniform_scale_in_file"),
        ),
    )
    v.check(
        "png_visibility",
        lambda: need(
            ("png", "dimensions"),
            ("png", "non_background_bbox"),
            ("png", "spatial_distribution"),
            ("png", "svg_hash_binding"),
        ),
    )
    v.check(
        "fit_to_page",
        lambda: need(
            ("layout", "uniform_scale"),
            ("layout", "aspect_ratio_preserved"),
            ("layout", "geometry_inside_viewport"),
            ("layout", "fit_to_page"),
        ),
    )
    v.check(
        "text_layout",
        lambda: need(
            ("layout", "text_text_collisions"),
            ("layout", "text_wall_collisions"),
            ("layout", "text_clipping"),
            ("layout", "title_block_separation"),
        ),
    )
    v.check(
        "pdf_valid", lambda: need(("pdf", "xref"), ("pdf", "object_offsets"), ("pdf", "page_size"))
    )
    review = store.read_json(REVIEW)
    log = store.read_json(REPAIR_LOG)["cycles"]
    first = store.read_json("review/history/cycle-0.json")
    v.check(
        "review_bounded_repair",
        lambda: (
            first["review_verdict"] == "FAIL"
            and first["legible"] is False
            and first["transport_status"] == "SUCCESS"
            and first["out_of_scope_items"]
            and first["out_of_scope_items"][0]["category"] == "room_size"
            and review["review_verdict"] == "PASS"
            and review["cycle"] == 1
            and len(log) == 2,
            f"cycle0 legible={first['legible']} verdict={first['review_verdict']}; final {review['review_verdict']} "
            f"after {review['cycle']} repair cycle(s)",
        ),
    )
    v.check(
        "review_bound_to_png",
        lambda: (
            review["reviewed_artifact_sha256"] == sha256_file(store.path(RENDER["png"])),
            "hash matches current PNG",
        ),
    )

    def packet() -> tuple[bool, str]:
        d = ctx.settings.paths.results_dir / PACKET_DIR
        lines = (d / "MANIFEST.sha256").read_text().splitlines()
        bad = [ln for ln in lines if sha256_file(d / ln[66:]) != ln[:64]]
        names = {ln[66:] for ln in lines}
        need_files = {
            "canonical-model.json",
            "plan.dxf",
            "plan.svg",
            "plan.png",
            "plan.pdf",
            "validation-report.json",
            "visual-review.json",
            "attention.json",
            "README.md",
        }
        return not bad and need_files <= names, f"{len(lines)} files, manifest verified"

    v.check("human_review_packet", packet)

    def determinism() -> tuple[bool, str]:
        before = {k: sha256_file(store.path(r)) for k, r in RENDER.items() if k != "png"}
        stage_render(ctx)
        after = {k: sha256_file(store.path(r)) for k, r in RENDER.items() if k != "png"}
        return before == after, "SVG/PDF/DXF byte-identical on re-render"

    v.check("deterministic_render", determinism)

    def no_paths() -> tuple[bool, str]:
        root = str(project.resolve())
        hits = []
        for base in (ctx.settings.paths.work_dir, ctx.settings.paths.results_dir):
            for p in base.rglob("*"):
                if (
                    p.is_file()
                    and p.suffix in (".json", ".svg", ".md", ".dxf", ".txt", ".jsonl")
                    and root in p.read_text(encoding="utf-8", errors="ignore")
                ):
                    hits.append(p.name)
        return not hits, "no absolute project paths in artifacts" if not hits else f"{hits[:5]}"

    v.check("no_absolute_paths_in_artifacts", no_paths)
    v.check(
        "local_only_traffic",
        lambda: (
            all(r["request_id"] for r in requests) and len(requests) >= 7,
            f"{len(requests)} requests, all to the loopback fake server with request IDs",
        ),
    )


def report_markdown(result: dict[str, Any]) -> str:
    out = io.StringIO()
    out.write(
        "# Synthetic end-to-end run\n\nSYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING.\n\n"
    )
    out.write(
        f"**Result: {'PASS' if result['passed'] else 'FAIL'}**\n\n## Stages\n\n| Stage | Status | Summary |\n|---|---|---|\n"
    )
    for s in result["stages"]:
        out.write(f"| {s['stage']} | {s['status']} | {s['summary'].replace('|', '/')[:200]} |\n")
    out.write("\n## Output verification\n\n| Check | Result | Detail |\n|---|---|---|\n")
    for c in result["checks"]:
        out.write(
            f"| {c['check']} | {'PASS' if c['ok'] else 'FAIL'} | {c['detail'].replace('|', '/')[:200]} |\n"
        )
    return out.getvalue()


def dump(result: dict[str, Any]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, default=str)
