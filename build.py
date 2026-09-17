#!/usr/bin/env python3
"""Compile the plain-text data/ corpus into the static site's JSON payload.

Run with `uv run python build.py`. Add `--selfcheck` to also exercise the
temporal resolution logic against a tiny synthetic corpus (proves that
supersede/refine chains and "previous understanding" resolve correctly, for
both facts and summaries).

No third-party dependencies: uses only the stdlib (tomllib, json, pathlib).
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
SITE = ROOT / "site"

# Where the novel is cloned and where any chapter can be read online.
NOVEL_DIR = ROOT / ".." / "LOTM-Reader" / "chapters" / "lotm" / "webnovel"
READ_URL_BASE = "https://beyonder.pages.dev/read/lotm/webnovel"

CERTAINTIES = {"fact", "inference", "hypothesis", "speculation"}


class BuildError(Exception):
    pass


def load_toml(path: Path) -> dict:
    with path.open("rb") as fh:
        return tomllib.load(fh)


def read_chapter_titles(novel_dir: Path = NOVEL_DIR) -> dict[int, str]:
    """Map chapter number -> title from the novel's markdown frontmatter."""
    titles: dict[int, str] = {}
    if not novel_dir.exists():
        return titles
    for p in sorted(novel_dir.glob("*.md")):
        n = int(p.stem)
        if n == 0:  # 0000.md is book metadata, not a chapter
            continue
        head = p.read_text()[:600]
        m = re.search(r"^title:\s*(.+)$", head, re.M)
        if m:
            titles[n] = m.group(1).strip(" \t\r\n'\"")
    return titles


def resolve_temporal(items: list[dict]) -> list[dict]:
    """Resolve forward supersedes/refines pointers into reverse pointers.

    Authoring rule: the newer item declares `supersedes` / `refines` on the
    older item's id. Here we derive, on the older item, `revoked_at`
    (from supersedes) and `superseded_by` / `refined_by`. Mutates nothing;
    returns fresh dicts.
    """
    ids = [i["id"] for i in items]
    if len(set(ids)) != len(ids):
        raise BuildError("duplicate id in temporal corpus")
    by_id = {i["id"]: i for i in items}

    for it in items:
        for dep in it.get("supersedes", []):
            if dep not in by_id:
                raise BuildError(f"{it['id']!r}: supersedes unknown {dep!r}")
            if by_id[dep]["established_at"] >= it["established_at"]:
                raise BuildError(
                    f"{it['id']!r}: supersedes {dep!r} which is not strictly "
                    "older — overturn must point backward in time"
                )
        for dep in it.get("refines", []):
            if dep not in by_id:
                raise BuildError(f"{it['id']!r}: refines unknown {dep!r}")
            if by_id[dep]["established_at"] > it["established_at"]:
                raise BuildError(
                    f"{it['id']!r}: refines {dep!r} which is established "
                    "later — refinement must point backward"
                )

    resolved = []
    for it in items:
        r = dict(it)
        r.setdefault("supersedes", [])
        r.setdefault("refines", [])
        r["revoked_at"] = None
        r["superseded_by"] = []
        r["refined_by"] = []
        resolved.append(r)

    for r in resolved:
        for dep_id in r["supersedes"]:
            dep = next(x for x in resolved if x["id"] == dep_id)
            dep["revoked_at"] = r["established_at"]
            dep["superseded_by"].append(r["id"])
        for dep_id in r["refines"]:
            dep = next(x for x in resolved if x["id"] == dep_id)
            dep["refined_by"].append(r["id"])

    for r in resolved:
        r["superseded_by"].sort()
        r["refined_by"].sort()

    return resolved


def validate_entities(entities: list[dict]) -> None:
    ids = [e["id"] for e in entities]
    if len(set(ids)) != len(ids):
        raise BuildError("duplicate entity id")
    for e in entities:
        if not isinstance(e.get("type"), str) or not e["type"].strip():
            raise BuildError(f"entity {e['id']!r}: type must be a non-empty string")
        if e["first_seen"] < 1:
            raise BuildError(f"entity {e['id']!r}: first_seen must be >= 1")


def validate_facts(facts: list[dict], entity_ids: set) -> None:
    for f in facts:
        if f["certainty"] not in CERTAINTIES:
            raise BuildError(f"fact {f['id']!r}: unknown certainty {f['certainty']!r}")
        if f["established_at"] < 1:
            raise BuildError(f"fact {f['id']!r}: established_at must be >= 1")
        for ent in f["entities"]:
            if ent not in entity_ids:
                raise BuildError(f"fact {f['id']!r}: unknown entity {ent!r}")
        for src in f["sources"]:
            if src.get("chapter", 0) < 1:
                raise BuildError(f"fact {f['id']!r}: source missing chapter")


def validate_summaries(summaries: list[dict], entity_ids: set) -> None:
    by_id = {s["id"]: s for s in summaries}
    for s in summaries:
        if s["entity"] not in entity_ids:
            raise BuildError(f"summary {s['id']!r}: unknown entity {s['entity']!r}")
        if s["established_at"] < 1:
            raise BuildError(f"summary {s['id']!r}: established_at must be >= 1")
        for dep in s.get("supersedes", []):
            if dep in by_id and by_id[dep]["entity"] != s["entity"]:
                raise BuildError(
                    f"summary {s['id']!r}: supersedes {dep!r} which belongs "
                    "to a different entity"
                )
        for src in s["sources"]:
            if src.get("chapter", 0) < 1:
                raise BuildError(f"summary {s['id']!r}: source missing chapter")


def build():
    entities = load_toml(DATA / "entities.toml")["entities"]
    facts = load_toml(DATA / "facts.toml")["facts"]
    summaries = load_toml(DATA / "summaries.toml")["summaries"]
    entity_ids = {e["id"] for e in entities}

    validate_entities(entities)
    validate_facts(facts, entity_ids)
    validate_summaries(summaries, entity_ids)

    out_facts = resolve_temporal(facts)
    out_summaries = resolve_temporal(summaries)

    def cmax():
        for e in entities:
            yield e["first_seen"]
        for f in out_facts:
            yield f["established_at"]
            if f["revoked_at"]:
                yield f["revoked_at"]
        for s in out_summaries:
            yield s["established_at"]
            if s["revoked_at"]:
                yield s["revoked_at"]

    max_chapter = max(cmax(), default=0)

    out = SITE / "data"
    out.mkdir(parents=True, exist_ok=True)
    (out / "facts.json").write_text(json.dumps(out_facts, ensure_ascii=False, indent=2))
    (out / "entities.json").write_text(
        json.dumps(entities, ensure_ascii=False, indent=2)
    )
    (out / "summaries.json").write_text(
        json.dumps(out_summaries, ensure_ascii=False, indent=2)
    )
    (out / "meta.json").write_text(
        json.dumps(
            {
                "max_chapter": max_chapter,
                "title": "Lord of the Mysteries — World Wiki",
                "fact_count": len(out_facts),
                "entity_count": len(entities),
                "summary_count": len(out_summaries),
                "chapters": {str(n): t for n, t in read_chapter_titles().items()},
                "read_url_base": READ_URL_BASE,
            },
            indent=2,
        )
    )

    print(
        f"built: {len(entities)} entities, {len(out_facts)} facts, "
        f"{len(out_summaries)} summaries, max chapter {max_chapter} -> {out}"
    )


def selfcheck():
    """Synthetic corpus proving supersede/refine resolution (facts + summaries)."""
    facts = [
        {
            "id": "a",
            "statement": "A",
            "entities": ["x"],
            "established_at": 1,
            "certainty": "fact",
            "sources": [{"chapter": 1}],
        },
        {
            "id": "b",
            "statement": "B",
            "entities": ["x"],
            "established_at": 2,
            "certainty": "fact",
            "sources": [{"chapter": 2}],
            "refines": ["a"],
        },
        {
            "id": "c",
            "statement": "C",
            "entities": ["x"],
            "established_at": 3,
            "certainty": "fact",
            "sources": [{"chapter": 3}],
            "supersedes": ["a"],
        },
    ]
    summaries = [
        {
            "id": "s1",
            "entity": "x",
            "established_at": 1,
            "text": "v1",
            "sources": [{"chapter": 1}],
        },
        {
            "id": "s2",
            "entity": "x",
            "established_at": 3,
            "text": "v2",
            "sources": [{"chapter": 3}],
            "supersedes": ["s1"],
        },
    ]
    rf = resolve_temporal(facts)
    a = next(f for f in rf if f["id"] == "a")
    assert a["refined_by"] == ["b"], a
    assert a["superseded_by"] == ["c"], a
    assert a["revoked_at"] == 3, a

    rs = resolve_temporal(summaries)
    s1 = next(s for s in rs if s["id"] == "s1")
    assert s1["superseded_by"] == ["s2"], s1
    assert s1["revoked_at"] == 3, s1
    print("selfcheck OK: supersede/refine resolution correct for facts and summaries")


if __name__ == "__main__":
    if "--selfcheck" in sys.argv:
        selfcheck()
    else:
        build()
