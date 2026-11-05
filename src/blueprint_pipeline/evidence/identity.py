"""Room-identity resolution.

Candidates are formed across *all* sources by normalized label — never one
room per file. Each candidate tracks the evidence origins behind it, so that
several photographs of one sheet count as one origin.

Rules
-----
* Labels are normalized only for grouping (case, whitespace, punctuation). The
  raw labels are kept.
* Observations without a usable label are never merged by position or
  filename; each becomes its own ``UNRESOLVED`` candidate.
* A candidate whose sources are related by POSSIBLE_REVISION /
  CONFLICTING_VERSION gets a REVISION conflict and is not merged silently.
* Measurements for the same axis that disagree across (or within) origins are
  kept side by side as a MEASUREMENT conflict; nothing is averaged.
* Status: CONFLICTED if any conflict, CORROBORATED with >= 2 independent
  origins, CANDIDATE otherwise, UNRESOLVED for unlabeled observations.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any

from .families import REVISION_KINDS, Relationship
from .measurements import values_agree

_PUNCT = re.compile(r"[^0-9A-Z ]+")


def normalize_label(text: str | None) -> str | None:
    if text is None:
        return None
    s = _PUNCT.sub(" ", text.upper())
    s = " ".join(s.split())
    return s or None


def _slug(label: str) -> str:
    return label.replace(" ", "-")


def resolve_identities(
    extractions: dict[str, dict[str, Any]],
    origin_of: dict[str, str],
    rels: list[Relationship],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (candidates, conflicts, unresolved_questions)."""
    revision_pairs = {r.key(): r.kind for r in rels if r.kind in REVISION_KINDS}

    cands: dict[str, dict[str, Any]] = {}

    def cand(cid: str, label: str | None) -> dict[str, Any]:
        if cid not in cands:
            cands[cid] = {
                "candidate_id": cid,
                "label_normalized": label,
                "label_raw": [],
                "observation_ids": [],
                "source_ids": set(),
                "origin_ids": set(),
                "measurements": defaultdict(list),
                "conflict_ids": [],
            }
        return cands[cid]

    for sid in sorted(extractions):
        rec = extractions[sid]
        origin = origin_of.get(sid, f"ORIGIN-{sid}")
        for o in rec.get("observations", []):
            kind = o["kind"]
            if kind == "room_label":
                label = normalize_label(o.get("normalized") or o.get("raw_text"))
                if label is None:
                    h = hashlib.sha256(o["observation_id"].encode()).hexdigest()[:8]
                    c = cand(f"ID-UNLABELED-{h}", None)
                else:
                    c = cand(f"ID-{_slug(label)}", label)
                c["label_raw"].append(o.get("raw_text"))
            elif kind in ("dimension", "opening") and o.get("subject"):
                label = normalize_label(o["subject"])
                if label is None:
                    continue
                c = cand(f"ID-{_slug(label)}", label)
                if kind == "dimension":
                    axis = o.get("dimension_axis") or "unknown"
                    c["measurements"][axis].append(
                        {
                            "value_mm": o.get("value_mm"),
                            "raw_text": o.get("raw_text"),
                            "observation_id": o["observation_id"],
                            "source_id": sid,
                            "origin_id": origin,
                            "confidence": o.get("confidence"),
                        }
                    )
            else:
                continue
            c["observation_ids"].append(o["observation_id"])
            c["source_ids"].add(sid)
            c["origin_ids"].add(origin)

    conflicts: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []

    def add_conflict(c: dict[str, Any], kind: str, detail: str, **extra: Any) -> None:
        cid = f"CONFLICT-{kind[:4]}-{c['candidate_id'][3:]}-{len(c['conflict_ids']) + 1}"
        conflicts.append(
            {
                "conflict_id": cid,
                "kind": kind,
                "subject": c["candidate_id"],
                "detail": detail,
                "status": "OPEN",
                "resolution": None,
                **extra,
            }
        )
        c["conflict_ids"].append(cid)
        questions.append(
            {
                "question_id": f"Q-{cid}",
                "text": f"Resolve {kind.lower()} conflict for {c['candidate_id']}: {detail}",
                "refs": [cid, c["candidate_id"]],
            }
        )

    out: list[dict[str, Any]] = []
    for cid in sorted(cands):
        c = cands[cid]
        srcs = sorted(c["source_ids"])
        revision_state = "CURRENT"
        rev_srcs: set[str] = set()
        for i in range(len(srcs)):
            for j in range(i + 1, len(srcs)):
                k = (srcs[i], srcs[j])
                if k in revision_pairs:
                    rev_srcs |= set(k)
                    if revision_pairs[k] == "CONFLICTING_VERSION":
                        revision_state = "CONFLICTING_VERSION"
                    elif revision_state != "CONFLICTING_VERSION":
                        revision_state = "POSSIBLE_REVISION"
        if rev_srcs:
            add_conflict(
                c,
                "REVISION",
                f"sources {', '.join(sorted(rev_srcs))} may be different versions of this subject",
                source_ids=sorted(rev_srcs),
                observation_ids=[],
            )
        for axis in sorted(c["measurements"]):
            ms = [m for m in c["measurements"][axis] if m["value_mm"] is not None]
            if len(ms) < 2:
                continue
            values = sorted({m["value_mm"] for m in ms})
            if not all(values_agree(values[0], v) for v in values[1:]):
                same_origin = len({m["origin_id"] for m in ms}) == 1
                detail = (
                    f"{axis} readings disagree: "
                    + ", ".join(f"{m['value_mm']:g} mm ({m['source_id']})" for m in ms)
                    + ("; all readings come from one evidence origin" if same_origin else "")
                )
                add_conflict(
                    c,
                    "MEASUREMENT",
                    detail,
                    observation_ids=[m["observation_id"] for m in ms],
                    source_ids=sorted({m["source_id"] for m in ms}),
                    values=[m["value_mm"] for m in ms],
                )
        if c["label_normalized"] is None:
            status = "UNRESOLVED"
            questions.append(
                {
                    "question_id": f"Q-{cid}",
                    "text": f"Unlabeled room observation {cid}: which room is this?",
                    "refs": [cid, *c["observation_ids"]],
                }
            )
        elif c["conflict_ids"]:
            status = "CONFLICTED"
        elif len(c["origin_ids"]) >= 2:
            status = "CORROBORATED"
        else:
            status = "CANDIDATE"
        out.append(
            {
                "candidate_id": cid,
                "label_normalized": c["label_normalized"],
                "label_raw": c["label_raw"],
                "observation_ids": sorted(c["observation_ids"]),
                "source_ids": srcs,
                "origin_ids": sorted(c["origin_ids"]),
                "independent_origin_count": len(c["origin_ids"]),
                "status": status,
                "revision_state": revision_state,
                "measurements": {k: v for k, v in sorted(c["measurements"].items())},
                "conflict_ids": c["conflict_ids"],
            }
        )
    return out, conflicts, questions
