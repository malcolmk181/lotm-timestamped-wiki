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
Model is qwen/qwen3.8-27b by default (non-thinking); override with LOTM_MODEL.
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
    DELTA_SCHEMA, SUMMARY_SCHEMA, NewEntity, NewFact, SummaryUpdate,
    clean_type, kind_for,
)
from pydantic import ValidationError

DATA = ROOT / "data"
DRAFT = ROOT / "_draft"
DEFAULT_MODEL = "qwen/qwen3.8-27b"
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
    # Note: summaries are deliberately NOT listed here — the main pass emits
    # entities + facts only, and summaries are a separate, later pass. Keeping
    # them out trims the prior-state prompt as the summary count grows.
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
5. Output NOTHING already captured in the prior state. new_entities is ONLY for entities not listed under ENTITIES below; new_facts is ONLY for claims the FACTS list does not already state. If a chapter adds nothing new, return empty arrays. Re-emitting an existing entity id is a hard error.
6. Enumerate fully. When the text gives a list or roll-call (e.g. "seven orthodox gods: the Eternal Blazing Sun, the Lord of Storms, ..."), do not truncate it: emit an entity for the collective plus one entity for each named member, and a fact that names the complete list. Lists like this are core world-building and must not be shortened.
7. Field values are literal strings ONLY. Never write reasoning, questions, hedging, or alternatives inside a value (e.g. type is "character", never "character, or maybe 'person'").

OUTPUT SCHEMA (exactly this JSON object):
{
  "new_entities": [ {"id": "<slug>", "name": "<name>", "type": "<type>", "aliases": ["<name>"]} ],
  "new_facts": [ {"id": "<slug>", "statement": "<claim>", "entities": ["<entity-id>"], "certainty": "<level>", "sources": [{"chapter": N, "quote": "<exact short quote>"}], "refines": ["<fact-id>"], "supersedes": ["<fact-id>"]} ],
}
Omit empty arrays. Omit optional fields you don't need.

FIELD RULES:
- entity.type: one short lowercase kebab-case label (e.g. character, location, organization, deity, concept, language, item, ritual, city, country, family, pathway, artifact). Reuse an existing label; invent a new one only if nothing fits. Never a synonym of an existing label.
- entity.id: kebab-case slug, stable, e.g. "klein-moretti", "tingen-city". For a NEW entity, invent a unique kebab-case id. For an entity already in the prior state, DO NOT re-emit it — reference its existing id instead.
- entity.aliases: only genuinely different names for the same thing (e.g. "Klein" for "Klein Moretti"). Omit the field (or leave it []) if there is no other name. Never repeat the display name as an alias.
- fact.id: "f-" + kebab-case, e.g. "f-klein-origin". Must be unique (never reuse a prior id).
- fact.entities: the entity ids (existing or newly created in this same output) this fact is about.
- fact.certainty: fact (shown plainly in the text), inference (strongly implied), hypothesis (a character's explicit guess), speculation (loose/uncertain).
- fact.sources[].quote: a short exact phrase from the chapter, verbatim, that backs the claim.
- refines: ids of facts this claim EXTENDS or clarifies without contradicting. Only point at facts established in an EARLIER chapter (their established_at < N).
- supersedes: ids of facts this claim CONTRADICTS/REPLACES. Only earlier facts. This is how the wiki records that a prior understanding was wrong.

Write entity names, quotes, and claims exactly as the chapter states them. Do not editorialize beyond what the text says."""


def build_user_prompt(chapter_num: int, title: str, text: str, prior_prompt: str) -> str:
    return (
        f"CHAPTER {chapter_num}: {title}\n\n"
        f"--- CHAPTER TEXT ---\n{text}\n\n"
        f"--- PRIOR STATE (what the wiki already believes) ---\n{prior_prompt}\n\n"
        f"Output the JSON delta for chapter {chapter_num} (current chapter N = {chapter_num})."
    )


# ---------------------------------------------------------------------------
# Summary pass (separate, deterministic; runs AFTER the main extraction)
# ---------------------------------------------------------------------------
SUMMARY_SYSTEM_PROMPT = """You write concise prose descriptions for a spoiler-safe, chapter-timestamped wiki of "Lord of the Mysteries". You are given a set of entities and, for each, the facts currently known about it as of a given chapter. Write ONE short paragraph (2-4 sentences) per entity summarizing what is currently known, based ONLY on the provided facts. If two facts conflict, prefer the later-established one. Do not add detail the facts do not support, do not editorialize, and do not mention chapter numbers, fact ids, or sources.

Respond with a single JSON object and nothing else — no markdown fences, no prose.

OUTPUT SCHEMA (exactly this JSON object):
{
  "summaries": [ {"entity": "<entity-id>", "text": "<prose paragraph>"} ]
}
"""


def build_summary_prompt(changed: list[dict], chapter_num: int) -> str:
    lines = [f"Chapter {chapter_num}. For each entity below, write one paragraph "
             "summarizing what is currently known about it."]
    for item in changed:
        lines.append(f"\nENTITY {item['id']} | {item['name']} | type {item['type']}")
        for f in item["facts"]:
            lines.append(f"- [{f['certainty']}] {f['statement']}")
        if not item["facts"]:
            lines.append("- (no facts yet)")
    lines.append("\nOutput the JSON summaries for every entity listed above.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# OpenRouter
# ---------------------------------------------------------------------------
class ResponseFormatError(RuntimeError):
    """The provider rejected the requested response_format (downgrade trigger)."""


def _openrouter_post(api_key: str, model: str, messages: list[dict],
                     response_format, max_retries: int = 3) -> dict:
    """Single chat completion with retry/backoff on transient errors.

    Returns the raw API response. Raises ResponseFormatError if the provider
    rejects `response_format` (caller should downgrade), or RuntimeError after
    exhausting retries.
    """
    body = {
        "model": model,
        "messages": messages,
        "temperature": 0,
        "max_tokens": 12000,
        # Force non-thinking mode. Hybrid models (Qwen 3.8, DeepSeek, …) reason
        # by default and leak their chain-of-thought into structured output.
        # OpenRouter maps this to the provider's "thinking off" switch and
        # ignores it for models with no reasoning mode.
        "reasoning": {"enabled": False},
    }
    if response_format is not None:
        body["response_format"] = response_format
    data = json.dumps(body).encode("utf-8")
    last_err = None
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
            if any(k in detail for k in
                   ("response_format", "json_schema", "structured_output")):
                raise ResponseFormatError(detail) from e
            if e.code == 429 or e.code >= 500:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else 2 ** (attempt + 2)
                if attempt < max_retries - 1:
                    time.sleep(min(wait, 60))
                    continue
            # other 4xx: don't retry
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
    raise RuntimeError(f"OpenRouter request failed: {last_err}")


def extract_json(api_key: str, model: str, messages: list[dict],
                 schema: dict, name: str, attempts_per_tier: int = 3) -> dict:
    """Get a parseable, non-empty JSON object, retrying and downgrading.

    Tries response_format tiers (json_schema -> json_object -> none). For each
    tier it retries on empty output and malformed JSON, then downgrades the
    tier. Raises RuntimeError only if every tier and retry fails. Shared by the
    main extraction pass and the summary pass.
    """
    tiers = [
        {"type": "json_schema",
         "json_schema": {"name": name, "schema": schema, "strict": False}},
        {"type": "json_object"},
        None,
    ]
    failure = None
    for tier in tiers:
        print(f"    [response_format] "
              f"{('none' if tier is None else tier.get('type', '?'))}", flush=True)
        for _ in range(attempts_per_tier):
            try:
                resp = _openrouter_post(api_key, model, messages, tier)
            except ResponseFormatError:
                break  # provider rejects this tier -> downgrade immediately
            except RuntimeError:
                failure = "request error"
                continue  # transient -> retry this tier
            try:
                content = resp["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
            if not content or not content.strip():
                failure = "empty output"
                continue  # retry this tier
            try:
                delta = parse_json(content)
                usage = resp.get("usage", {})
                if usage:
                    print(f"    [usage] {usage.get('prompt_tokens')} prompt -> "
                          f"{usage.get('completion_tokens')} completion", flush=True)
                return delta
            except (BuildError, json.JSONDecodeError) as e:
                failure = f"unparseable JSON: {e}"
                continue  # retry this tier
    raise RuntimeError(f"no parseable delta after all tiers/retries "
                       f"(last: {failure})")


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
        raw_type = (e.type or "").strip()
        t = clean_type(raw_type)
        if len(raw_type) > 60:
            warnings.append(f"entity {e.id!r}: type was reasoning-soup, coerced to {t!r}")
        new_entities.append({
            "id": e.id, "name": e.name, "type": t,
            "kind": kind_for(t),
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

    return {
        "new_entities": new_entities,
        "new_facts": new_facts,
    }, warnings


# ---------------------------------------------------------------------------
# Deterministic summary pass
# ---------------------------------------------------------------------------
def compute_changed_entities(normalized: dict) -> set[str]:
    """Entities whose understanding changed this chapter: newly created, or the
    subject of a fact that refines/supersedes an earlier fact."""
    changed = {e["id"] for e in normalized["new_entities"]}
    for f in normalized["new_facts"]:
        if f["refines"] or f["supersedes"]:
            changed.update(f["entities"])
    return changed


def active_facts_for(entity_id: str, prior: dict, chapter_num: int) -> list[dict]:
    """Facts about entity_id in effect as of chapter_num (not superseded)."""
    revoked: set[str] = set()
    for f in prior["facts"]:
        if f.get("established_at", 0) > chapter_num:
            continue
        revoked.update(f.get("supersedes", []))
    acts = []
    for f in prior["facts"]:
        if f.get("established_at", 0) > chapter_num:
            continue
        if f["id"] in revoked or entity_id not in f.get("entities", []):
            continue
        acts.append(f)
    acts.sort(key=lambda f: f["established_at"])
    return acts


def link_summaries(summaries: list[dict], prior: dict, chapter_num: int) -> tuple[list[dict], list[str]]:
    """Validate the summary batch and auto-link each version's supersedes to the
    entity's previous latest summary (harness owns versioning)."""
    warnings: list[str] = []
    entity_ids = {e["id"] for e in prior["entities"]}
    latest: dict[str, str] = {}
    version: dict[str, int] = {}
    for s in prior["summaries"]:
        m = re.search(r"-(\d+)$", s["id"])
        n = int(m.group(1)) if m else 0
        if n >= version.get(s["entity"], 0):
            version[s["entity"]] = n
            latest[s["entity"]] = s["id"]

    out: list[dict] = []
    for su_raw in summaries:
        try:
            su = SummaryUpdate.model_validate(su_raw)
        except ValidationError as err:
            warnings.append(f"summary invalid: {err}")
            continue
        if su.entity not in entity_ids:
            warnings.append(f"summary unknown entity {su.entity!r}")
            continue
        n = version.get(su.entity, 0) + 1
        version[su.entity] = n
        prev = latest.get(su.entity)
        out.append({
            "id": f"s-{su.entity}-{n}",
            "entity": su.entity,
            "established_at": chapter_num,
            "text": su.text,
            "supersedes": [prev] if prev else [],
            "sources": [{"chapter": chapter_num}],
        })
        latest[su.entity] = f"s-{su.entity}-{n}"
    return out, warnings


def generate_summaries(api_key: str, model: str, changed: set[str],
                       prior: dict, chapter_num: int) -> tuple[list[dict], list[str]]:
    """Write prose summaries for the changed entities via one batched call."""
    entities = {e["id"]: e for e in prior["entities"]}
    items = []
    for eid in sorted(changed):
        e = entities.get(eid)
        if e is None:
            continue
        items.append({
            "id": eid, "name": e["name"], "type": e["type"],
            "facts": active_facts_for(eid, prior, chapter_num),
        })
    if not items:
        return [], []

    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": build_summary_prompt(items, chapter_num)},
    ]
    raw = extract_json(api_key, model, messages, SUMMARY_SCHEMA, "summaries")
    return link_summaries(raw.get("summaries", []), prior, chapter_num)


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
            lines.append(f"- **{e['name']}** (`{e['id']}`, {e['type']}, {e.get('kind', '?')})"
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
                raw = extract_json(api_key, model, [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user},
                ], DELTA_SCHEMA, "extraction")
            except RuntimeError as e:
                print(f"chapter {n}: FAILED after retries — {e}")
                continue

        normalized, warnings = normalize_delta(raw, prior, n)
        for w in warnings:
            print(f"  chapter {n}: note: {w}")

        # accumulate entities + facts so the summary pass (and next chapter)
        # sees this chapter's additions as known
        prior["entities"].extend(normalized["new_entities"])
        prior["facts"].extend(normalized["new_facts"])

        # deterministic summary pass: summarize entities whose understanding
        # changed this chapter (new, or refined/superseded). A separate,
        # independent LLM call — parallelizable later.
        summaries: list[dict] = []
        swarn: list[str] = []
        if not args.mock:
            changed = compute_changed_entities(normalized)
            if changed:
                print(f"chapter {n}: summarizing {len(changed)} entities ...",
                      flush=True)
                try:
                    summaries, swarn = generate_summaries(
                        api_key, model, changed, prior, n)
                except RuntimeError as e:
                    print(f"chapter {n}: summary pass failed — {e}")
        for w in swarn:
            print(f"  chapter {n}: note (summary): {w}")
        normalized["summary_updates"] = summaries
        prior["summaries"].extend(summaries)

        report = render_report(n, title, normalized, warnings + swarn)
        path = write_draft(n, normalized, title, model, report, warnings + swarn)
        # accumulate so the next chapter sees this chapter's output as known
        print(f"chapter {n}: drafted {path} "
              f"({len(normalized['new_entities'])} entities, "
              f"{len(normalized['new_facts'])} facts, "
              f"{len(normalized['summary_updates'])} summaries)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())