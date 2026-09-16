"""Pydantic schema for the extraction harness's model output (the "delta").

Single source of truth for the shape of what the LLM returns each chapter.
`ExtractionDelta.model_json_schema()` is handed to the model as structured
output, and the models validate the response. Keep the `EntityType` and
`Certainty` literals in sync with the `ENTITY_TYPES` / `CERTAINTIES` sets in
`build.py` (build.py stays stdlib-only and keeps its own copies).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

EntityType = Literal[
    "character", "location", "organization", "deity",
    "concept", "language", "item", "ritual", "currency",
]
Certainty = Literal["fact", "inference", "hypothesis", "speculation"]


class Source(BaseModel):
    chapter: int
    quote: str = ""


class NewEntity(BaseModel):
    id: str
    name: str
    type: EntityType
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