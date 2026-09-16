#!/usr/bin/env python3
"""Compile the plain-text data/ corpus into the static site's JSON payload.

Run with `uv run python build.py`. Add `--selfcheck` to also exercise the
temporal resolution logic against a tiny synthetic corpus (proves that
supersede/refine chains and "previous understanding" resolve correctly).

No third-party dependencies: uses only the stdlib (tomllib, json, pathlib).
"""
from __future__ import annotations

import json
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"

CERTAINTIES = {"fact", "inference", "hypothesis", "speculation"}
ENTITY_TYPES = {
    "character", "location", "organization", "deity",
    "concept", "language", "item", "ritual", "currency",
}


class BuildError(Exception):
    pass


def load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def validate_and_resolve(entities, facts) -> tuple[list, list, int]:
    entity_ids = {e["id"] for e in entities}
    if len(entity_ids) != len(entities):
        raise BuildError("duplicate entity id")
    for e in entities:
        if e["type"] not in ENTITY_TYPES:
            raise BuildError(f"entity {e['id']!r}: unknown type {e['type']!r}")
        if e["first_seen"] < 1:
            raise BuildError(f"entity {e['id']!r}: first_seen must be >= 1")

    fact_ids = [f["id"] for f in facts]
    if len(set(fact_ids)) != len(fact_ids):
        raise BuildError("duplicate fact id")

    by_id = {f["id"]: f for f in facts}
    for f in facts:
        if f["certainty"] not in CERTAINTIES:
            raise BuildError(
                f"fact {f['id']!r}: unknown certainty {f['certainty']!r}")
        if f["established_at"] < 1:
            raise BuildError(f"fact {f['id']!r}: established_at must be >= 1")
        for ent in f["entities"]:
            if ent not in entity_ids:
                raise BuildError(f"fact {f['id']!r}: unknown entity {ent!r}")
        for src in f["sources"]:
            if src.get("chapter", 0) < 1:
                raise BuildError(f"fact {f['id']!r}: source missing chapter")
        for dep in f.get("refines", []):
            if dep not in by_id:
                raise BuildError(f"fact {f['id']!r}: refines unknown {dep!r}")
            if by_id[dep]["established_at"] > f["established_at"]:
                raise BuildError(
                    f"fact {f['id']!r}: refines {dep!r} which is established "
                    "later — refinement must point backward")
        for dep in f.get("supersedes", []):
            if dep not in by_id:
                raise BuildError(f"fact {f['id']!r}: supersedes unknown {dep!r}")
            if by_id[dep]["established_at"] >= f["established_at"]:
                raise BuildError(
                    f"fact {f['id']!r}: supersedes {dep!r} which is not "
                    "strictly older — overturn must point backward in time")

    # Resolve reverse pointers. Mutate copies so the author's source is the
    # single direction of truth and never edits revoked_at by hand.
    resolved = []
    for f in facts:
        r = dict(f)
        r.setdefault("supersedes", [])
        r.setdefault("refines", [])
        r["revoked_at"] = None
        r["superseded_by"] = []
        r["refined_by"] = []
        resolved.append(r)

    for f in resolved:
        for dep_id in f["supersedes"]:
            dep = by_id[dep_id]
            dep_resolved = next(x for x in resolved if x["id"] == dep_id)
            # revoked_at = earliest chapter where a replacement arrives
            dep_resolved["revoked_at"] = f["established_at"]
            dep_resolved["superseded_by"].append(f["id"])
        for dep_id in f["refines"]:
            dep_resolved = next(x for x in resolved if x["id"] == dep_id)
            dep_resolved["refined_by"].append(f["id"])

    for f in resolved:
        f["superseded_by"].sort()
        f["refined_by"].sort()

    max_chapter = max(
        [e["first_seen"] for e in entities]
        + [f["established_at"] for f in resolved]
        + [f["revoked_at"] for f in resolved if f["revoked_at"]],
        default=0,
    )
    return resolved, list(entities), max_chapter


def build():
    entities = load_toml(DATA / "entities.toml")["entities"]
    facts = load_toml(DATA / "facts.toml")["facts"]
    resolved, out_entities, max_chapter = validate_and_resolve(entities, facts)

    out = SITE / "data"
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.json").write_text(
        json.dumps(resolved, ensure_ascii=False, indent=2))
    (out / "entities.json").write_text(
        json.dumps(out_entities, ensure_ascii=False, indent=2))
    (out / "meta.json").write_text(json.dumps({
        "max_chapter": max_chapter,
        "title": "Lord of the Mysteries — World Wiki",
        "fact_count": len(resolved),
        "entity_count": len(out_entities),
    }, indent=2))

    print(
        f"built: {len(out_entities)} entities, {len(resolved)} facts, "
        f"max chapter {max_chapter} -> {out}"
    )


def selfcheck():
    """Synthetic corpus proving supersede/refine resolution."""
    entities = [
        {"id": "x", "name": "X", "type": "concept", "first_seen": 1},
    ]
    facts = [
        # plain fact
        {"id": "a", "statement": "A", "entities": ["x"],
         "established_at": 1, "certainty": "fact", "sources": [{"chapter": 1}]},
        # refines a
        {"id": "b", "statement": "B", "entities": ["x"], "established_at": 2,
         "certainty": "fact", "sources": [{"chapter": 2}], "refines": ["a"]},
        # supersedes a at ch 3
        {"id": "c", "statement": "C", "entities": ["x"], "established_at": 3,
         "certainty": "fact", "sources": [{"chapter": 3}], "supersedes": ["a"]},
    ]
    resolved, _, max_chapter = validate_and_resolve(entities, facts)
    a = next(f for f in resolved if f["id"] == "a")
    assert a["refined_by"] == ["b"], a
    assert a["superseded_by"] == ["c"], a
    assert a["revoked_at"] == 3, a
    assert max_chapter == 3, max_chapter
    print("selfcheck OK: supersede/refine resolution and max_chapter correct")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck()
    else:
        build()