"""Human-review packet.

Assembles everything a reviewer needs into ``<results>/human-review-packet/``:
canonical model, renders, validation report, review record and repair log,
open conflicts, unresolved questions, unplaced items, and a SHA-256 manifest.

The packet is built in a sibling staging directory and swapped into place, so
a reader never sees a half-written packet. Its README states plainly whether
the drawing is ready for sign-off and, if not, why.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..io.atomic import canonical_json, sha256_file
from .reviewer import review_currency

if TYPE_CHECKING:
    from ..orchestration.context import PipelineContext
    from ..orchestration.stages import StageResult

PACKET_DIR = "human-review-packet"


def _copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def build_packet(ctx: PipelineContext) -> StageResult:
    from ..orchestration.stages import (
        MODEL,
        RENDER,
        REPAIR_LOG,
        REPORT,
        REPORT_MD,
        REVIEW,
        StageResult,
    )

    store = ctx.store
    if not store.check(MODEL).is_current:
        return StageResult("package", "BLOCKED", "canonical model missing or stale")
    model = store.read_json(MODEL)
    results = ctx.settings.paths.results_dir
    results.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".packet-", dir=results))
    blockers: list[str] = []
    try:
        files = {
            "canonical-model.json": MODEL,
            "plan.svg": RENDER["svg"],
            "plan.png": RENDER["png"],
            "plan.pdf": RENDER["pdf"],
            "plan.dxf": RENDER["dxf"],
            "validation-report.json": REPORT,
            "validation-report.md": REPORT_MD,
            "visual-review.json": REVIEW,
            "repair-log.json": REPAIR_LOG,
        }
        for name, rel in files.items():
            p = store.path(rel)
            if p.exists():
                _copy(p, staging / name)
            else:
                blockers.append(f"missing {name}")
        report = store.read_json(REPORT) if store.path(REPORT).exists() else None
        if report is None:
            blockers.append("no mechanical validation report")
        else:
            current_model_sha = store.read_meta(MODEL).sha256  # type: ignore[union-attr]
            if report["artifacts"].get(MODEL) != current_model_sha:
                blockers.append("validation report is for a different model (stale)")
            elif report["status"] != "PASS":
                blockers.append(f"mechanical validation {report['status']}")
        review = store.read_json(REVIEW) if store.path(REVIEW).exists() else None
        rstate, rwhy = review_currency(review, store)
        if rstate != "CURRENT":
            blockers.append(f"visual review {rstate.lower()}: {rwhy}")
        elif review is not None:
            if review["review_verdict"] != "PASS":
                blockers.append(f"visual review verdict {review['review_verdict']}")
            if review["reviewer"]["kind"] != "LOCAL_MODEL":
                blockers.append("visual review was SIMULATED, not an independent local model")
        open_conf = [c for c in model["conflicts"] if c["status"] == "OPEN"]
        attention = {
            "open_conflicts": open_conf,
            "unresolved_questions": model["unresolved_questions"],
            "not_placed": model["fragments_unplaced"],
            "decisions_not_applied": [d for d in model["decisions"] if not d["applied"]],
        }
        (staging / "attention.json").write_text(canonical_json(attention), encoding="utf-8")
        ready = not blockers
        lines = [
            "# Human review packet",
            "",
            "SYNTHETIC TEST DATA — NOT DERIVED FROM A REAL BUILDING."
            if model.get("synthetic")
            else "",
            "",
            f"Model: `{model['model_id']}` (canonical-model {model['schema_version']})",
            "",
            f"**Ready for sign-off: {'YES' if ready else 'NO'}**",
            "",
        ]
        if blockers:
            lines += ["Blocking items:", ""] + [f"- {b}" for b in blockers] + [""]
        lines += [
            "## Needs a human decision",
            "",
            f"- Open conflicts: {len(open_conf)}",
            f"- Unresolved questions: {len(model['unresolved_questions'])}",
            f"- Items not placed on the plan: {len(model['fragments_unplaced'])}",
            f"- Decisions not applied: {len(attention['decisions_not_applied'])}",
            "",
            "Details: `attention.json`. Conflicts are retained, never averaged; unplaced items",
            "are listed rather than drawn. The canonical model, not the drawing, is authoritative.",
            "",
            "## Files",
            "",
        ]
        manifest = []
        for f in sorted(staging.iterdir()):
            if f.is_file():
                manifest.append(f"{sha256_file(f)}  {f.name}")
                lines.append(f"- `{f.name}`")
        (staging / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
        manifest.append(f"{sha256_file(staging / 'README.md')}  README.md")
        (staging / "MANIFEST.sha256").write_text(
            "\n".join(sorted(manifest, key=lambda s: s[66:])) + "\n", encoding="utf-8"
        )
        final = results / PACKET_DIR
        old = results / f".{PACKET_DIR}.old"
        if old.exists():
            shutil.rmtree(old)
        if final.exists():
            os.replace(final, old)
        os.replace(staging, final)
        if old.exists():
            shutil.rmtree(old)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    outputs = {f"results/{PACKET_DIR}/{p.name}": sha256_file(p) for p in sorted(final.iterdir())}
    detail: dict[str, Any] = {"blockers": blockers, "packet_files": outputs}
    return StageResult(
        "package",
        "PASS" if ready else "PARTIAL",
        "packet ready for sign-off" if ready else f"packet built; not ready: {'; '.join(blockers)}",
        {},
        {"model": store.read_meta(MODEL).sha256},  # type: ignore[union-attr]
        detail,
    )
