#!/usr/bin/env python3
"""LLM extraction harness: turn novel chapters into the wiki's data schema.

Runs one chapter at a time (strictly in order), feeding the model only that
chapter's text plus a compact "prior state" (what the wiki currently believes),
and asking for a *delta*: new entities, new facts, and summary revisions.

Draft-first workflow: by default it writes proposed additions to `_draft/` and
does NOT touch `data/`. Review the drafts, then `--apply` merges them into the
real corpus (or wait until you trust it, then use --apply directly).

Usage:
    uv run python harness/extract.py --chapters 1 25          # extract, draft
    uv run python harness/extract.py --chapters 1 3 --mock    # offline plumbing test
    uv run python harness/extract.py --chapters 4 --apply     # extract AND merge ch 4

Requires OPENROUTER_API_KEY in a .env at the repo root (see .env.example).
Model is deepseek/deepseek-v4-flash by default; override with LOTM_MODEL.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from build import BuildError, load_toml  # noqa: E402
from harness.schema import (  # noqa: E402
    DELTA_SCHEMA, NewEntity, NewFact, SummaryUpdate,
)
from pydantic import ValidationError

DATA = ROOT / "data"
DRAFT = ROOT / "_draft"
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
DEFAULT_WEBWOVEL = ROOT / ".." / "LOTM-Reader" / "chapters" / "lotm" / "webnovel"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


# ---------------------------------------------------------------------------
# .env (loaded via python-dotenv in main())
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Chapter text
# ---------------------------------------------------------------------------
def clean_text(md: str) -> str:
    """Strip YAML frontmatter and image markdown, returning readable prose."""
    # remove frontmatter (--- ... ---) at the top
    if md.startswith("---"):
        end = md.find("---", 3)
        if end != -1:
            md = md[end + 3:]
    # remove markdown images ![alt](path){...} — keep nothing of them
    md = re.sub(r"!\[[^\]]*\]\([^)]*\)(?:\{[^}]*\})?", "", md)
    # collapse blank lines, trim trailing spaces
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def chapter_files(webnovel_dir: Path) -> list[tuple[int, Path]]:
    out = []
    for p in sorted(webnovel_dir.glob("*.md")):
        n = int(p.stem)
        if n == 0:  # 0000.md is book metadata, not a chapter
            continue
        out.append((n, p))
    return out


# ---------------------------------------------------------------------------
# Prior state (what the wiki currently believes) — both compact text for the
# prompt and the parsed dicts for validation.
# ---------------------------------------------------------------------------
def load_prior_state() -> dict:
    entities = load_toml(DATA / "entities.toml")["entities"]
    facts = load_toml(DATA / "facts.toml")["facts"]
    summaries = load_toml(DATA / "summaries.toml")["summaries"]
    return {"entities": entities, "facts": facts, "summaries": summaries}


def prior_state_prompt(prior: dict) -> str:
    lines = []
    lines.append("ENTITIES (id | name | type):")
    for e in prior["entities"]:
        lines.append(f"- {e['id']} | {e['name']} | {e['type']}")
    lines.append("")
    lines.append("FACTS (id | statement | established_at):")
    for f in prior["facts"]:
        lines.append(f"- {f['id']} | {f['statement']} | {f['established_at']}")
    lines.append("")
    lines.append("SUMMARIES (id | entity | established_at):")
    for s in prior["summaries"]:
        lines.append(f"- {s['id']} | {s['entity']} | {s['established_at']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------
SYSTEM_PROMPT = """You are building the data for a spoiler-safe, chapter-timestamped wiki of the web novel "Lord of the Mysteries". You are given ONE chapter at a time, in strict reading order, plus a list of everything the wiki currently believes. You output only the NEW knowledge this chapter adds.

HARD RULES:
1. Use ONLY the provided chapter text and the prior state. IGNORE anything you might already know or remember about this novel, its characters, or its later plot. If a claim would rely on knowledge from a future chapter, do NOT include it.
2. Current chapter is N. Every fact and summary you emit is first known at chapter N. Never mention anything checkable only at a later chapter.
3. Respond with a single JSON object and nothing else — no markdown fences, no prose.
4. Be noisy and detailed: prefer many small, specific claims over a few big ones. This is what makes later refinements and overturns meaningful.
5. Output NOTHING already captured in the prior state. new_entities is ONLY for entities not listed under ENTITIES below; new_facts is ONLY for claims the FACTS list does not already state; emit a summary_update ONLY when you add to or change an entity's description. If a chapter adds nothing new, return empty arrays. Re-emiting an existing entity id is a hard error.
6. Enumerate fully. When the text gives a list or roll-call (e.g. "seven orthodox gods: the Eternal Blazing Sun, the Lord of Storms, ..."), do not truncate it: emit an entity for the collective plus one entity for each named member, and a fact that names the complete list. Lists like this are core world-building and must not be shortened.

OUTPUT SCHEMA (exactly this JSON object):
{
  "new_entities": [ {"id": "<slug>", "name": "<name>", "type": "<type>", "aliases": ["<name>"]} ],
  "new_facts": [ {"id": "<slug>", "statement": "<claim>", "entities": ["<entity-id>"], "certainty": "<level>", "sources": [{"chapter": N, "quote": "<exact short quote>"}], "refines": ["<fact-id>"], "supersedes": ["<fact-id>"]} ],
  "summary_updates": [ {"entity": "<entity-id>", "text": "<prose paragraph>"} ],
}
Omit empty arrays. Omit optional fields you don't need.

FIELD RULES:
- entity.type: a short, lowercase, hyphenated label for the KIND of thing. Reuse an existing type whenever it fits (character, location, organization, deity, concept, language, item, ritual — and new kinds as the book introduces them, e.g. pathway, artifact, era, event). If nothing fits, invent a clear new type; never coin a near-synonym of an existing one (use "character", not "person"; "location", not "place").
- entity.type guidance: "item" for significant, recurring physical objects (a named revolver, a specific book, an heirloom, a coin); "concept" for abstract or cosmic phenomena (the crimson moon); "ritual" for ceremonies; "currency" for money; "language" for languages.
- entity.id: kebab-case slug, stable, e.g. "klein-moretti", "tingen-city". For a NEW entity, invent a unique kebab-case id. For an entity already in the prior state, DO NOT re-emit it — reference its existing id instead.
- entity.aliases: only genuinely different names for the same thing (e.g. "Klein" for "Klein Moretti"). Omit the field (or leave it []) if there is no other name. Never repeat the display name as an alias.
- fact.id: "f-" + kebab-case, e.g. "f-klein-origin". Must be unique (never reuse a prior id).
- fact.entities: the entity ids (existing or newly created in this same output) this fact is about.
- fact.certainty: fact (shown plainly in the text), inference (strongly implied), hypothesis (a character's explicit guess), speculation (loose/uncertain).
- fact.sources[].quote: a short exact phrase from the chapter, verbatim, that backs the claim.
- refines: ids of facts this claim EXTENDS or clarifies without contradicting. Only point at facts established in an EARLIER chapter (their established_at < N).
- supersedes: ids of facts this claim CONTRADICTS/REPLACES. Only earlier facts. This is how the wiki records that a prior understanding was wrong.
- summary_updates: write a readable prose PARAGRAPH describing an entity, in natural English, suitable for a reader. Emit one when a chapter meaningfully changes or adds to an entity's description — either a brand-new entity (entity id may be one you just created above) or an updated understanding of an existing one. You do NOT set supersedes: the harness automatically links your new paragraph to the entity's previous version. A changed understanding is as important as a new entity: if a chapter tells you something new about an entity that already has a summary (for example, learning that the Evernight Goddess is one of the seven orthodox gods), you MUST emit a summary_update for it. If a chapter adds nothing new about that entity, do not emit a summary for it.
- summary text must be chapter-accurate: it may only state what is knowable by chapter N.

Write entity names, quotes, and claims exactly as the chapter states them. Do not editorialize beyond what the text says."""


def build_user_prompt(chapter_num: int, title: str, text: str, prior_prompt: str) -> str:
    return (
        f"CHAPTER {chapter_num}: {title}\n\n"
        f"--- CHAPTER TEXT ---\n{text}\n\n"
        f"--- PRIOR STATE (what the wiki already believes) ---\n{prior_prompt}\n\n"
        f"Output the JSON delta for chapter {chapter_num} (current chapter N = {chapter_num})."
    )


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------
def call_openrouter(api_key: str, model: str, messages: list[dict], max_retries: int = 4) -> dict:
    # Structured-output preference order: full JSON schema first (strictly
    # guides the shape), then plain JSON mode, then nothing. We degrade on a
    # provider that rejects a given response_format.
    response_formats = [
        {"type": "json_schema",
         "json_schema": {"name": "extraction", "schema": DELTA_SCHEMA, "strict": False}},
        {"type": "json_object"},
        None,
    ]
    last_err = None
    for rf in response_formats:
        print(f"    [response_format] {('none' if rf is None else rf.get('type', '?'))}",
              flush=True)
        body = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 8000,
        }
        if rf is not None:
            body["response_format"] = rf
        data = json.dumps(body).encode("utf-8")
        for attempt in range(max_retries):
            req = urllib.request.Request(
                API_URL, data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                last_err = e
                detail = e.read().decode("utf-8", "replace")
                # provider doesn't support this response_format -> next format
                if any(k in detail for k in ("response_format", "json_schema", "structured_output")):
                    break
                # rate limit / server error -> back off and retry
                if e.code == 429 or e.code >= 500:
                    retry_after = e.headers.get("Retry-After")
                    wait = float(retry_after) if retry_after else 2 ** (attempt + 2)
                    if attempt < max_retries - 1:
                        time.sleep(min(wait, 60))
                        continue
                # otherwise (400/401/403/404): don't retry this format
                break
            except urllib.error.URLError as e:
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt < max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
    raise RuntimeError(f"OpenRouter call failed after retries: {last_err}")


def parse_json(text: str) -> dict:
    text = text.strip()
    # strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # fall back to first {...} block
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise BuildError("model did not return valid JSON")


# ---------------------------------------------------------------------------
# Validation / normalisation of the model's delta
# ---------------------------------------------------------------------------
def normalize_delta(raw: dict, prior: dict, chapter_num: int) -> tuple[dict, list[str]]:
    """Normalize + validate the model's delta, leniently.

    Salvages valid items; drops invalid ones with a warning rather than
    rejecting the whole chapter (an LLM will occasionally re-emit an entity,
    invent a fact id, or point at a non-existent target). Returns
    (normalized, warnings).
    """
    warnings: list[str] = []
    existing_entity = {e["id"] for e in prior["entities"]}
    existing_fact = {f["id"] for f in prior["facts"]}
    facts_index = {f["id"]: f for f in prior["facts"]}

    # latest summary version id per entity, to auto-link supersedes
    latest_summary_id: dict[str, str] = {}
    summary_version: dict[str, int] = {}
    for s in prior["summaries"]:
        m = re.search(r"-(\d+)$", s["id"])
        n = int(m.group(1)) if m else 0
        if n >= summary_version.get(s["entity"], 0):
            summary_version[s["entity"]] = n
            latest_summary_id[s["entity"]] = s["id"]

    # --- entities (pydantic-validated; drop dupes/bad on warning) ---
    seen_entity_ids: set[str] = set()
    new_entities: list[dict] = []
    for e_raw in raw.get("new_entities", []):
        try:
            e = NewEntity.model_validate(e_raw)
        except ValidationError as err:
            warnings.append(f"entity invalid: {err}")
            continue
        if e.id in existing_entity or e.id in seen_entity_ids:
            warnings.append(f"entity {e.id!r}: already known, dropped")
            continue
        seen_entity_ids.add(e.id)
        new_entities.append({
            "id": e.id, "name": e.name, "type": e.type,
            "aliases": e.aliases, "first_seen": chapter_num,
        })
    entity_ids = existing_entity | seen_entity_ids

    # --- facts (pydantic-validated; drop invalid ones individually) ---
    new_facts: list[dict] = []
    for f_raw in raw.get("new_facts", []):
        try:
            f = NewFact.model_validate(f_raw)
        except ValidationError as err:
            warnings.append(f"fact invalid: {err}")
            continue
        if f.id in existing_fact:
            warnings.append(f"fact {f.id!r}: already known, dropped")
            continue
        ents = [x for x in f.entities if x in entity_ids]
        if not ents:
            warnings.append(f"fact {f.id!r}: no valid entity refs, dropped")
            continue
        refines = [d for d in f.refines
                   if d in existing_fact and facts_index[d]["established_at"] < chapter_num]
        supersedes = [d for d in f.supersedes
                      if d in existing_fact and facts_index[d]["established_at"] < chapter_num]
        srcs = [{"chapter": chapter_num, "quote": s.quote}
                for s in f.sources if s.quote]
        if not srcs:
            srcs = [{"chapter": chapter_num}]
        new_facts.append({
            "id": f.id,
            "statement": f.statement,
            "entities": ents,
            "certainty": f.certainty,
            "sources": srcs,
            "refines": refines,
            "supersedes": supersedes,
            "established_at": chapter_num,
        })

    # --- summary updates (harness owns versioning + linking) ---
    out_summaries: list[dict] = []
    for su_raw in raw.get("summary_updates", []):
        try:
            su = SummaryUpdate.model_validate(su_raw)
        except ValidationError as err:
            warnings.append(f"summary invalid: {err}")
            continue
        if su.entity not in entity_ids:
            warnings.append(f"summary_update unknown entity {su.entity!r}")
            continue
        n = summary_version.get(su.entity, 0) + 1
        summary_version[su.entity] = n
        prev = latest_summary_id.get(su.entity)
        out_summaries.append({
            "id": f"s-{su.entity}-{n}",
            "entity": su.entity,
            "established_at": chapter_num,
            "text": su.text,
            "supersedes": [prev] if prev else [],
            "sources": [{"chapter": chapter_num}],
        })
        latest_summary_id[su.entity] = f"s-{su.entity}-{n}"

    return {
        "new_entities": new_entities,
        "new_facts": new_facts,
        "summary_updates": out_summaries,
    }, warnings


# ---------------------------------------------------------------------------
# Draft writing (and optional apply)
# ---------------------------------------------------------------------------
def write_draft(chapter_num: int, normalized: dict, title: str, model: str, report: str, warnings: list[str]) -> Path:
    DRAFT.mkdir(exist_ok=True)
    payload = {
        "chapter": chapter_num,
        "title": title,
        "model": model,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "warnings": warnings,
        **normalized,
    }
    path = DRAFT / f"ch-{chapter_num:04d}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    (DRAFT / f"ch-{chapter_num:04d}.report.md").write_text(report)
    return path


def render_report(chapter_num: int, title: str, normalized: dict, warnings: list[str]) -> str:
    lines = [f"# Chapter {chapter_num}: {title} — draft", ""]
    ne = normalized["new_entities"]
    nf = normalized["new_facts"]
    su = normalized["summary_updates"]
    lines.append(f"- {len(ne)} new entities, {len(nf)} new facts, {len(su)} summary updates")
    if ne:
        lines += ["", "## New entities", ""]
        for e in ne:
            lines.append(f"- **{e['name']}** (`{e['id']}`, {e['type']})"
                         + (f" aka {', '.join(e['aliases'])}" if e.get("aliases") else ""))
    if nf:
        lines += ["", "## New facts", ""]
        for f in nf:
            links = ", ".join(f.get("refines", [])) or "—"
            over = ", ".join(f.get("supersedes", [])) or "—"
            srcs = "; ".join(f"ch{s['chapter']} “{s.get('quote','')}”" for s in f["sources"])
            lines.append(f"- [{f['certainty']}] {f['statement']} `{f['id']}`")
            lines.append(f"    - entities: {', '.join(f['entities'])}")
            lines.append(f"    - refines: {links} · supersedes: {over}")
            lines.append(f"    - source: {srcs}")
    if su:
        lines += ["", "## Summary revisions", ""]
        for s in su:
            over = ", ".join(s["supersedes"]) or "(new)"
            lines.append(f"- **{s['entity']}** → `{s['id']}` (replaces {over})")
            lines.append(f"    - {s['text']}")
    if warnings:
        lines += ["", "## Dropped / warnings", ""]
        lines += [f"- {w}" for w in warnings]
    lines += ["", "Review, then `uv run python harness/extract.py --chapters "
              f"{chapter_num} --apply` to merge (or edit data/*.toml by hand)."]
    return "\n".join(lines)


# Mock response for offline plumbing testing only (NOT real content).
def mock_delta(chapter_num: int, prior: dict) -> dict:
    return {
        "new_entities": [
            {"id": f"mock-entity-{chapter_num}", "name": "Mock Entity", "type": "concept"},
        ],
        "new_facts": [
            {"id": f"f-mock-{chapter_num}", "statement": "MOCK FACT — do not trust.",
             "entities": [f"mock-entity-{chapter_num}"],
             "certainty": "fact", "sources": [{"chapter": chapter_num, "quote": "mock"}]},
        ],
        "summary_updates": [],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--chapters", nargs="+", type=int, required=True,
                    help="single chapter, or start end (inclusive)")
    ap.add_argument("--model", default=None)
    ap.add_argument("--webnovel-dir", default=None)
    ap.add_argument("--mock", action="store_true", help="offline plumbing test (no API call)")
    ap.add_argument("--apply", action="store_true", help="merge drafts into data/ (not yet implemented)")
    ap.add_argument("--fresh", action="store_true",
                    help="start from an empty corpus (ignore existing data/) for a clean from-scratch run")
    args = ap.parse_args()

    if args.apply:
        print("--apply is not implemented yet; run draft mode and review first.")
        return 1

    load_dotenv(ROOT / ".env")
    model = args.model or os.environ.get("LOTM_MODEL") or DEFAULT_MODEL
    webnovel_dir = Path(args.webnovel_dir or os.environ.get("LOTM_WEBWOVEL_DIR") or DEFAULT_WEBWOVEL)

    start_n = args.chapters[0]
    end_n = args.chapters[-1]

    if not args.mock:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        if not api_key:
            print("OPENROUTER_API_KEY not found in .env — add it (see .env.example).")
            return 1

    prior = load_prior_state() if not args.fresh else {"entities": [], "facts": [], "summaries": []}
    files = dict(chapter_files(webnovel_dir))
    missing = [n for n in range(start_n, end_n + 1) if n not in files]
    if missing:
        print(f"chapters not found in {webnovel_dir}: {missing[:10]}...")
        return 1

    for n in range(start_n, end_n + 1):
        raw_md = files[n].read_text()
        text = clean_text(raw_md)
        title = ""
        m = re.search(r"title:\s*(.+)", raw_md[:500])
        if m:
            title = m.group(1).strip()
        prior_prompt = prior_state_prompt(prior)
        user = build_user_prompt(n, title, text, prior_prompt)

        if args.mock:
            raw = mock_delta(n, prior)
        else:
            print(f"chapter {n}: calling {model} ...", flush=True)
            try:
                resp = call_openrouter(api_key, model, [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ])
                content = resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as e:
                print(f"chapter {n}: bad API response {e}")
                continue
            except RuntimeError as e:
                print(f"chapter {n}: API failed — {e}")
                continue
            if not content or not content.strip():
                print(f"chapter {n}: empty model output, skipping")
                continue
            try:
                raw = parse_json(content)
            except (BuildError, json.JSONDecodeError) as e:
                print(f"chapter {n}: unparseable JSON — {e}; skipping")
                continue

        normalized, warnings = normalize_delta(raw, prior, n)
        for w in warnings:
            print(f"  chapter {n}: note: {w}")

        report = render_report(n, title, normalized, warnings)
        path = write_draft(n, normalized, title, model, report, warnings)
        # accumulate so the next chapter sees this chapter's output as known
        prior["entities"].extend(normalized["new_entities"])
        prior["facts"].extend(normalized["new_facts"])
        prior["summaries"].extend(normalized["summary_updates"])
        print(f"chapter {n}: drafted {path} "
              f"({len(normalized['new_entities'])} entities, "
              f"{len(normalized['new_facts'])} facts, "
              f"{len(normalized['summary_updates'])} summaries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())