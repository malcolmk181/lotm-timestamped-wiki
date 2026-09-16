# Author-side tracking (NOT shipped to the site)

This file is for the person *authoring* the wiki (who has read ahead). It
records known future reversals so that, when we reach the relevant chapter,
we author the `supersedes` correctly. **Do not put any of this in `data/`**
— it would leak spoilers into the rendered wiki and break the chapter-bounded
model.

## Known upcoming reversals / refinements to author later

- **`f-transmigration`** (ch 1) and **`f-parallel-world`** (ch 3) are the seed
  of the big reveal: the protagonist has not transmigrated to a *parallel*
  world — it is the *same* world at a later time (the "future"). When we reach
  the chapters that reveal this, author a new fact with
  `supersedes = ["f-transmigration", "f-parallel-world"]` (and any sibling
  facts that rest on the parallel-world premise). Until then both facts stand
  as the *current* understanding, exactly as a first-time reader believes them.

- The brass revolver / "suicide" (f-klein-suicide) is revealed to be anything
  but ordinary in the early arc — expect a refinement or overturn very soon
  (Volume 1, the "Clown" arc).

- The luck enhancement ritual, Hermes-language note, and Murabella/"Everyone
  will die, including me" thread turn out to be connected to the world's
  mysticism — many `f-*` facts from ch 1–3 will gain `refines` links as the
  magic system (Beyonders, Sequences, Pathways) is introduced.

Add entries here as we author further and notice seams that will later flip.