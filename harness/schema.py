"""Pydantic schema for the extraction harness's model output (the "delta").

Single source of truth for the shape of what the LLM returns each chapter.
`ExtractionDelta.model_json_schema()` is handed to the model as structured
output, and the models validate the response.

Entity `type` is a FREE-FORM string: the taxonomy grows as the book reveals new
kinds of things (pathways, sealed artifacts, eras, ...), so it is deliberately
NOT an enum — new types must pass through, not be rejected. `Certainty` is the
one closed enum (a fixed epistemic scale). Keep it in sync with the
`CERTAINTIES` set in `build.py` (build.py stays stdlib-only).
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

Certainty = Literal["fact", "inference", "hypothesis", "speculation"]

# Coarse kind by fine type (the coarse axis is DERIVED here, never emitted by
# the model — emitting it made the model dump reasoning into the type field).
# New/unknown types default to "thing". Extend freely as the taxonomy grows.
KIND_MAP = {
    "character": "person",
    "deity": "person",
    "location": "place",
    "organization": "thing",
    "concept": "idea",
    "language": "idea",
    "item": "thing",
    "ritual": "idea",
    "currency": "thing",
    "pathway": "idea",
    "artifact": "thing",
    "era": "idea",
    "event": "idea",
    "creature": "thing",
    "family": "thing",
    "sequence": "idea",
    "potion": "thing",
}


def kind_for(type_: str) -> str:
    """Map a fine type to its coarse kind (person / place / thing / idea)."""
    return KIND_MAP.get((type_ or "").strip().lower(), "thing")


def clean_type(type_: str) -> str:
    """Return a short kebab-case label; coerce reasoning-soup to a clean value.

    Sometimes the model writes an essay into `type` (a chain-of-thought leak).
    Anything too long or not kebab-case is collapsed: reuse the first
    hyphen-segment if it's a known type, else a generic label by coarse kind.
    """
    t = (type_ or "").strip().lower()
    if t and len(t) <= 60 and re.fullmatch(r"[a-z0-9][a-z0-9_-]*", t):
        return t
    first = t.split("-")[0] if t else ""
    if first and first in KIND_MAP:
        return first
    generic = {"person": "character", "place": "location",
               "thing": "item", "idea": "concept"}
    return generic[kind_for(t)]


class Source(BaseModel):
    chapter: int
    quote: str = ""


class NewEntity(BaseModel):
    id: str
    name: str
    type: str
    aliases: list[str] = Field(default_factory=list)


class NewFact(BaseModel):
    id: str
    statement: str
    entities: list[str] = Field(default_factory=list)
    certainty: Certainty
    sources: list[Source] = Field(default_factory=list)
    refines: list[str] = Field(default_factory=list)
    supersedes: list[str] = Field(default_factory=list)


class SummaryUpdate(BaseModel):
    entity: str
    text: str


class ExtractionDelta(BaseModel):
    reasoning: str = ""
    new_entities: list[NewEntity] = Field(default_factory=list)
    new_facts: list[NewFact] = Field(default_factory=list)
    summary_updates: list[SummaryUpdate] = Field(default_factory=list)


# The JSON schema we hand to the model for structured output / guidance.
DELTA_SCHEMA = ExtractionDelta.model_json_schema()