# LOTM Timestamped Wiki

A spoiler-safe, chapter-timestamped wiki of the world of *Lord of the
Mysteries*. Explore the setting **as understood at any given chapter** — facts
are missing until established, and overturned facts are kept as *previous
understandings* with sources and a pointer to what replaced them.

## Attribution

*Lord of the Mysteries* is written by **Cuttlefish That Loves Diving**
(爱潜水的乌贼). All rights to the novel and its world belong to the author and
the official publisher. Read the official English translation on
[Webnovel](https://www.webnovel.com/book/lord-of-mysteries_110227330062345055).

The chapter text this wiki is built from is an unofficial fan translation,
sourced from **LOTM-Reader**, a fan project by
[@Bittu5134](https://github.com/Bittu5134) hosted at
[beyonder.pages.dev](https://beyonder.pages.dev)
([github.com/Bittu5134/LOTM-Reader](https://github.com/Bittu5134/LOTM-Reader));
we use its `webnovel` translation. This wiki is an independent, non-profit fan
reference and is not affiliated with the author, the publisher, or LOTM-Reader.
If you enjoy the story, please support the official release.

## Built with

Authored with [Hermes Agent](https://hermes-agent.nousresearch.com/docs) (Nous
Research) and [Qwen](https://qwenlm.github.io/) and
[DeepSeek](https://www.deepseek.com) language models via
OpenRouter. The extraction harness feeds each chapter to the model one at a
time; its structured output is reviewed by a human before entering `data/`.

## How it works

There is no server and no database. The whole thing is:

1. **Plain-text data** in [`data/`](data/) — `entities.toml` (the stable
   "things"), `facts.toml` (timestamped claims), and `summaries.toml`
   (timestamped prose descriptions). This is the source of truth, and it lives
   in git, so every revision of the wiki's understanding has full history,
   diffs, and blame for free.
2. **`build.py`** — a stdlib-only Python script that validates the corpus,
   resolves the supersede/refine chains, and emits JSON into `site/data/`.
3. **A static site** in [`site/`](site/) — a single-page app with a chapter
   slider. It loads the JSON and does all temporal filtering in the browser
   (the corpus is small; there is nothing to query server-side).

Deploy by pointing GitHub Pages at the `site/` folder (or any static host).
Run `uv run python build.py` and commit the regenerated `site/data/*.json`.

## Data model

See the field documentation at the top of [`data/entities.toml`](data/entities.toml),
[`data/facts.toml`](data/facts.toml), and [`data/summaries.toml`](data/summaries.toml).

Core idea: a fact is **valid over an interval** `[established_at, revoked_at)`.

- `established_at` — chapter where this understanding first holds.
- `revoked_at` — chapter where it is replaced (set *automatically* by the
  build when a later fact declares `supersedes`; never written by hand).
- `refines` — a later fact extends/clarifies an earlier one without
  contradicting it.
- `supersedes` — a later fact overturns an earlier one.

A **summary** is the same interval idea applied to prose: each entity has
versioned paragraphs, and a new version `supersedes` an earlier one when the
understanding changes. On the entity page the current paragraph is shown with
any earlier versions available as "earlier understanding" — so at chapter 3
the Evernight Goddess is "one of the seven orthodox gods," but at chapter 1
the count of seven is correctly absent.

The two axes that make the wiki faithful to a mystery novel:

- **Temporal status** (derived): *established* → *refined* → *overturned*.
- **Epistemic certainty** (authored): `fact` · `inference` · `hypothesis` ·
  `speculation`. The narrator's own uncertainty is part of the picture.

### Spoiler discipline

A fact must never mention knowledge from a chapter later than its
`established_at`. Reversals are authored *as* they are reached in the text,
themselves tagged with the chapter that reveals them. The big reversal the
reader of the whole book knows about (the transmigration → "same world,
future" reveal) is deliberately **not present** — it is tracked for the author
in [`TRACKING.md`](TRACKING.md) and becomes a `supersedes` of
`f-transmigration` only when we author those later chapters.

## Commands

```sh
uv run python build.py            # validate + compile JSON into site/data/
uv run python build.py --selfcheck  # exercise supersede/refine resolution
uv run python -m http.server -d site 8000   # preview locally

# extraction harness (needs OPENROUTER_API_KEY in .env)
uv run python harness/extract.py --chapters 1 25          # draft mode (no data change)
uv run python harness/extract.py --chapters 1 3 --mock    # offline plumbing test

# tests
uv run pytest               # run the test suite
uv run pytest -q            # condensed
```

## Extraction harness

[`harness/extract.py`](harness/extract.py) turns novel chapters into the data
schema one chapter at a time, in strict reading order. It feeds an LLM (via
OpenRouter) only the current chapter's text plus a compact "prior state" (what
`data/` currently believes) and asks for a *delta*: new entities and new facts
— so it cannot leak later chapters, and it has to link `refines`/`supersedes`
to existing ids.

Summaries are a **separate, deterministic pass**: after the facts are
extracted, the harness computes which entities' understanding *changed* this
chapter (newly created, or the subject of a `refines`/`supersedes`), then makes
a second, batched model call that writes a prose summary for just those
entities. The summary calls are independent of one another and can be
parallelized; the main fact-extraction pass stays sequential.

- **Model:** `qwen/qwen3.8-27b` (cheap, open-weight). Reasoning is forced off
  (`reasoning:{enabled:false}`) so chain-of-thought can't leak into the JSON.
  Override with `LOTM_MODEL`.
- **Source:** the `webnovel` translation (not `oldtl`).
- **Schema + structured output:** the delta shape is defined once in
  [`harness/schema.py`](harness/schema.py) as pydantic models; their JSON
  schema is sent as the model's `response_format` (with graceful fallback to
  plain JSON mode), and the models validate the response. `build.py` remains
  stdlib-only and keeps its own copies of the type/certainty vocabularies.
- **Lenient normalization:** invalid items (duplicate entity, bad certainty,
  unknown ref) are dropped with a warning rather than rejecting the whole
  chapter — an LLM's output is treated as salvageable, not as strict data.
- **Draft-first:** it writes proposed additions to `_draft/` and never touches
  `data/` unless you `--apply` (merge step still to come). Review the
  `.report.md` per chapter, then merge by hand or via apply.
- **Bringing it up:** run it on ch 1–3 and diff against the hand-authored
  `data/`; tune the prompt until entity recall, fact recall, and
  refine/supersede linkage match, then let it run 4 → 1435.

This is a plain script, not an agent loop: one deterministic pass per chapter,
validated against the schema, output committed to git.

## Status

- [x] Schema: entities + timestamped facts + timestamped prose summaries
- [x] Build pipeline (validation + resolution)
- [x] Static site with chapter slider, entity browser, changes view
- [x] `data/` authored for chapters 1–25 (222 entities, 502 facts, 233 summaries)
- [x] Extraction harness (spoiler-bounded, per-chapter, draft-first)
- [x] Deterministic summary pass (separate, parallelizable)
- [x] `--apply` merge (promote `_draft/` → `data/`)
- [x] GitHub Pages deployment
- [ ] Prior-state compaction (prompt currently grows ~1K tokens/chapter)