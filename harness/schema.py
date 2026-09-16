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

from typing import Literal

from pydantic import BaseModel, Field

Certainty = Literal["fact", "inference", "hypothesis", "speculation"]

Kind = Literal["person", "place", "thing", "idea"]

# Coarse kind by fine type (fallback when the model doesn't emit an explicit
# kind). New types default to "thing". Extend freely as the taxonomy grows.
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


def kind_for(type_: str, kind: str | None) -> str:
    """Resolve the coarse kind: explicit kind, else KIND_MAP, else 'thing'."""
    if kind in {"person", "place", "thing", "idea"}:
        return kind
    return KIND_MAP.get((type_ or "").strip().lower(), "thing")


class Source(BaseModel):
    chapter: int
    quote: str = ""


class NewEntity(BaseModel):
    id: str
    name: str
    type: str
    kind: Kind | None = None
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
    new_entities: list[NewEntity] = Field(default_factory=list)
    new_facts: list[NewFact] = Field(default_factory=list)
    summary_updates: list[SummaryUpdate] = Field(default_factory=list)


# The JSON schema we hand to the model for structured output / guidance.
DELTA_SCHEMA = ExtractionDelta.model_json_schema()