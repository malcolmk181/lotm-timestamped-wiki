# LOTM Timestamped Wiki

A spoiler-safe, chapter-timestamped wiki of the world of *Lord of the
Mysteries*. Explore the setting **as understood at any given chapter** — facts
are missing until established, and overturned facts are kept as *previous
understandings* with sources and a pointer to what replaced them.

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
```

## Status

- [x] Schema: entities + timestamped facts + timestamped prose summaries
- [x] Build pipeline (validation + resolution)
- [x] Static site with chapter slider, entity browser, changes view
- [x] `data/` authored for chapters 1–3 (33 entities, 29 facts, 36 summaries)
- [ ] Extract chapters 4–25
- [ ] Automated/LLM-assisted extraction pipeline (spoiler-bounded, per-chapter)
- [ ] GitHub Pages deployment