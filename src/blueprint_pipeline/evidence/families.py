"""Source relationships, evidence origins, families and fragment classification.

Relationship kinds
------------------
Same evidence origin (never counted as independent corroboration):
  EXACT_FILE_DUPLICATE, SAME_SCAN_OR_PHOTO_VARIANT, SAME_DRAWING_RESCANNED, ANNOTATED_COPY
Independent origins that may corroborate:
  REDRAWN_SAME_ROOM, SUPPLEMENTARY_DETAIL
Independent origins that must NOT be merged silently:
  POSSIBLE_REVISION, CONFLICTING_VERSION
Weak / negative:
  POSSIBLY_SAME_SUBJECT, UNRELATED, UNKNOWN

Any pair without a stated or computed relationship is implicitly ``UNKNOWN``.

Assertion precedence: HUMAN > HEURISTIC > MODEL for the same pair. A heuristic
(perceptual-hash similarity) never overrides a human statement — a revised
drawing can look almost identical to its predecessor.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ..normalization import hamming_hex

SAME_ORIGIN = {
    "EXACT_FILE_DUPLICATE",
    "SAME_SCAN_OR_PHOTO_VARIANT",
    "SAME_DRAWING_RESCANNED",
    "ANNOTATED_COPY",
}
INDEPENDENT_CORROBORATING = {"REDRAWN_SAME_ROOM", "SUPPLEMENTARY_DETAIL"}
REVISION_KINDS = {"POSSIBLE_REVISION", "CONFLICTING_VERSION"}
NON_FAMILY = {"UNRELATED", "UNKNOWN"}
_PRECEDENCE = {"HUMAN": 3, "HEURISTIC": 2, "MODEL": 1}

DETAIL_CLASSES = {"OPENING_DETAIL", "PARTIAL_WALL_OR_CORNER", "TEXT_OR_NOTES"}
ROOMISH_CLASSES = {"ROOM", "MULTI_ROOM_RELATIONSHIP", "CORRIDOR_OR_TRANSITION"}

# dHash distance (of 64 bits) at or below which two different files are
# considered the same scan/photo. Conservative: false negatives only lose a
# de-duplication; false positives would hide a revision (mitigated by
# precedence and by same-origin measurement conflicts being reported).
VARIANT_MAX_HAMMING = 6


@dataclass(frozen=True)
class Relationship:
    a: str
    b: str
    kind: str
    asserted_by: str
    confidence: float | None
    basis: str
    subject: str | None = None

    def key(self) -> tuple[str, str]:
        return (self.a, self.b) if self.a <= self.b else (self.b, self.a)

    def to_dict(self) -> dict[str, Any]:
        a, b = self.key()
        return {
            "a": a,
            "b": b,
            "kind": self.kind,
            "asserted_by": self.asserted_by,
            "confidence": self.confidence,
            "basis": self.basis,
            "subject": self.subject,
        }


class _UnionFind:
    def __init__(self, items: Iterable[str]) -> None:
        self.parent = {i: i for i in items}

    def find(self, x: str) -> str:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: str, b: str) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            lo, hi = sorted((ra, rb))
            self.parent[hi] = lo

    def groups(self) -> list[list[str]]:
        g: dict[str, list[str]] = defaultdict(list)
        for x in self.parent:
            g[self.find(x)].append(x)
        return sorted((sorted(v) for v in g.values()), key=lambda v: v[0])


def computed_relationships(
    sources: list[dict[str, Any]], dhashes: dict[str, str]
) -> list[Relationship]:
    """Exact duplicates (checksum) and photo/scan variants (perceptual hash)."""
    rels: list[Relationship] = []
    by_sha: dict[str, list[str]] = defaultdict(list)
    for s in sources:
        if s.get("sha256"):
            by_sha[s["sha256"]].append(s["source_id"])
    exact_pairs: set[tuple[str, str]] = set()
    for ids in by_sha.values():
        ids = sorted(ids)
        for i in range(len(ids)):
            for j in range(i + 1, len(ids)):
                exact_pairs.add((ids[i], ids[j]))
                rels.append(
                    Relationship(
                        ids[i],
                        ids[j],
                        "EXACT_FILE_DUPLICATE",
                        "HEURISTIC",
                        1.0,
                        "identical SHA-256",
                    )
                )
    ids = sorted(dhashes)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            if (a, b) in exact_pairs:
                continue
            d = hamming_hex(dhashes[a], dhashes[b])
            if d <= VARIANT_MAX_HAMMING:
                rels.append(
                    Relationship(
                        a,
                        b,
                        "SAME_SCAN_OR_PHOTO_VARIANT",
                        "HEURISTIC",
                        round(1 - d / 64, 3),
                        f"perceptual hash distance {d}/64",
                    )
                )
    return rels


def resolve_relationships(
    declared: list[Relationship], computed: list[Relationship], known_ids: set[str]
) -> tuple[list[Relationship], list[dict[str, Any]]]:
    """Merge declared and computed relationships by precedence.

    Returns (relationships, findings). Exact duplicates are facts about bytes
    and are always kept; a human may still add a second relationship for the
    same pair (e.g. annotate), which is recorded too.
    """
    findings: list[dict[str, Any]] = []
    best: dict[tuple[str, str], Relationship] = {}
    exact: dict[tuple[str, str], Relationship] = {}
    for r in computed + declared:
        if r.a not in known_ids or r.b not in known_ids:
            findings.append(
                {
                    "code": "RELATIONSHIP_UNKNOWN_SOURCE",
                    "message": f"relationship {r.a}–{r.b} references an unregistered source",
                }
            )
            continue
        if r.a == r.b:
            findings.append(
                {"code": "RELATIONSHIP_SELF", "message": f"self-relationship on {r.a} ignored"}
            )
            continue
        k = r.key()
        if r.kind == "EXACT_FILE_DUPLICATE" and r.asserted_by == "HEURISTIC":
            exact[k] = r
            continue
        cur = best.get(k)
        if cur is None or _PRECEDENCE[r.asserted_by] > _PRECEDENCE[cur.asserted_by]:
            if cur is not None and cur.kind != r.kind:
                findings.append(
                    {
                        "code": "RELATIONSHIP_OVERRIDDEN",
                        "message": f"{k[0]}–{k[1]}: {cur.asserted_by} {cur.kind} overridden by "
                        f"{r.asserted_by} {r.kind}",
                    }
                )
            best[k] = r
        elif _PRECEDENCE[r.asserted_by] == _PRECEDENCE[cur.asserted_by] and cur.kind != r.kind:
            findings.append(
                {
                    "code": "RELATIONSHIP_DISAGREEMENT",
                    "message": f"{k[0]}–{k[1]}: {cur.kind} vs {r.kind} (same authority); "
                    "kept the first, review required",
                }
            )
    out = list(exact.values())
    for k, r in best.items():
        if k in exact and r.kind in SAME_ORIGIN:
            continue  # already same-origin by bytes
        out.append(r)
    out.sort(key=lambda r: (r.key(), r.kind))
    return out, findings


def evidence_origins(
    source_ids: list[str], rels: list[Relationship]
) -> tuple[dict[str, str], list[dict[str, Any]]]:
    uf = _UnionFind(source_ids)
    basis: dict[str, list[str]] = defaultdict(list)
    for r in rels:
        if r.kind in SAME_ORIGIN:
            uf.union(r.a, r.b)
    groups = uf.groups()
    origin_of: dict[str, str] = {}
    origins = []
    for g in groups:
        oid = f"ORIGIN-{g[0]}"
        for sid in g:
            origin_of[sid] = oid
    for r in rels:
        if r.kind in SAME_ORIGIN:
            basis[origin_of[r.a]].append(f"{r.a}~{r.b}: {r.kind} ({r.asserted_by})")
    for g in groups:
        oid = origin_of[g[0]]
        origins.append({"origin_id": oid, "source_ids": g, "basis": sorted(basis.get(oid, []))})
    return origin_of, origins


def families(
    source_ids: list[str], rels: list[Relationship], origin_of: dict[str, str]
) -> list[dict[str, Any]]:
    uf = _UnionFind(source_ids)
    rev = _UnionFind(source_ids)
    for r in rels:
        if r.kind not in NON_FAMILY:
            uf.union(r.a, r.b)
        if r.kind in REVISION_KINDS:
            rev.union(r.a, r.b)
    out = []
    rev_groups = [g for g in rev.groups() if len(g) > 1]
    for g in uf.groups():
        out.append(
            {
                "family_id": f"FAM-{g[0]}",
                "source_ids": g,
                "origin_ids": sorted({origin_of[s] for s in g}),
                "revision_groups": [rg for rg in rev_groups if rg[0] in g],
            }
        )
    return out


def classify_fragments(
    source_ids: list[str],
    declared: dict[str, str | None],
    extraction: dict[str, dict[str, Any] | None],
    origin_of: dict[str, str],
    room_labels: dict[str, set[str]],
) -> list[dict[str, Any]]:
    """Fragment class + placement state relative to the whole-floor base plan.

    Class precedence: register-declared (HUMAN) > model (MODEL) > UNKNOWN.
    Placement:
      WHOLE_FLOOR             ANCHORED if it is the only whole-floor origin, else AMBIGUOUS
                              (a revision of one *room* does not unanchor the whole sheet;
                              it is handled as an identity-level REVISION conflict)
      ROOM / MULTI_ROOM / CORRIDOR
                              LIKELY if all its labels appear on a whole-floor origin,
                              AMBIGUOUS if some do, UNPLACED if none
      OPENING_DETAIL / PARTIAL_WALL_OR_CORNER / TEXT_OR_NOTES   DETAIL_ONLY
      UNKNOWN                 UNPLACED
    """
    classes: dict[str, tuple[str, str, float | None]] = {}
    for sid in source_ids:
        rec = extraction.get(sid)
        if declared.get(sid):
            classes[sid] = (str(declared[sid]), "HUMAN", 1.0)
        elif rec and rec.get("fragment"):
            f = rec["fragment"]
            classes[sid] = (f["fragment_class"], "MODEL", f.get("confidence"))
        else:
            classes[sid] = ("UNKNOWN", "HEURISTIC", None)

    whole_origins = sorted({origin_of[s] for s, (c, _, _) in classes.items() if c == "WHOLE_FLOOR"})
    base_labels: set[str] = set()
    for s, (c, _, _) in classes.items():
        if c == "WHOLE_FLOOR":
            base_labels |= room_labels.get(s, set())

    out = []
    for sid in source_ids:
        cls, by, conf = classes[sid]
        labels = room_labels.get(sid, set())
        if cls == "WHOLE_FLOOR":
            if len(whole_origins) == 1:
                state, why = "ANCHORED", "single whole-floor evidence origin defines the frame"
            else:
                state, why = "AMBIGUOUS", f"{len(whole_origins)} whole-floor origins"
        elif cls in ROOMISH_CLASSES:
            if labels and labels <= base_labels:
                state, why = "LIKELY", "all labels appear on the whole-floor plan"
            elif labels & base_labels:
                state, why = "AMBIGUOUS", "some labels appear on the whole-floor plan"
            else:
                state, why = "UNPLACED", "no label links this fragment to the base plan"
        elif cls in DETAIL_CLASSES:
            state, why = "DETAIL_ONLY", "detail fragment; not placed on the base plan"
        else:
            state, why = "UNPLACED", "fragment class unknown"
        rec = extraction.get(sid)
        out.append(
            {
                "source_id": sid,
                "fragment_class": cls,
                "class_asserted_by": by,
                "class_confidence": conf,
                "placement_state": state,
                "placement_basis": why,
                "origin_id": origin_of.get(sid),
                "extraction_status": "SUCCESS" if rec else "NO_CURRENT_EXTRACTION",
            }
        )
    return out
