"""Tests for the pydantic schema (single source of truth for the delta shape)."""

import pytest
from pydantic import ValidationError

from harness.schema import (
    DELTA_SCHEMA,
    SUMMARY_SCHEMA,
    ExtractionDelta,
    NewEntity,
    NewFact,
    SummaryBatch,
    SummaryUpdate,
    clean_type,
    kind_for,
)


def test_full_delta_roundtrip():
    d = ExtractionDelta.model_validate(
        {
            "new_entities": [{"id": "e1", "name": "One", "type": "concept"}],
            "new_facts": [
                {
                    "id": "f1",
                    "statement": "s",
                    "entities": ["e1"],
                    "certainty": "fact",
                    "sources": [{"chapter": 1, "quote": "q"}],
                }
            ],
        }
    )
    assert d.new_entities[0].id == "e1"
    assert d.new_facts[0].certainty == "fact"


def test_new_entity_type_accepted():
    e = NewEntity.model_validate({"id": "e1", "name": "One", "type": "sealed-artifact"})
    assert e.type == "sealed-artifact"


def test_bad_certainty_rejected():
    with pytest.raises(ValidationError):
        NewFact.model_validate(
            {"id": "f1", "statement": "s", "entities": ["e1"], "certainty": "maybe"}
        )


def test_missing_required_entity_field_rejected():
    with pytest.raises(ValidationError):
        NewEntity.model_validate({"id": "e1"})  # missing name, type


def test_defaults_applied():
    e = NewEntity.model_validate({"id": "e1", "name": "One", "type": "concept"})
    assert e.aliases == []


def test_schema_exposes_top_level_keys():
    assert set(DELTA_SCHEMA["properties"].keys()) == {"new_entities", "new_facts"}


def test_summary_batch_schema():
    assert set(SUMMARY_SCHEMA["properties"].keys()) == {"summaries"}
    b = SummaryBatch.model_validate({"summaries": [{"entity": "e1", "text": "hi"}]})
    assert b.summaries[0].entity == "e1"


def test_summary_update_shape_has_no_supersedes_field():
    # summaries are linked by the harness, not the model
    props = SummaryUpdate.model_json_schema()["properties"]
    assert "supersedes" not in props
    assert set(props.keys()) == {"entity", "text"}


def test_kind_for_maps_known_types():
    assert kind_for("character") == "person"
    assert kind_for("location") == "place"
    assert kind_for("pathway") == "idea"
    assert kind_for("xylophone") == "thing"  # unknown -> thing


def test_clean_type_keeps_short_labels():
    assert clean_type("character") == "character"
    assert clean_type("Sealed-Artifact") == "sealed-artifact"


def test_clean_type_coerces_soup():
    soup = (
        "character-human-from-earth-in-transmigration-process-very-long-"
        "essay-the-model-wrote-instead-of-a-type-label"
    )
    assert clean_type(soup) == "character"  # recovers leading known type
