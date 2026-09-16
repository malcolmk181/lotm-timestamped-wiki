"""Tests for the pydantic schema (single source of truth for the delta shape)."""
import pytest
from pydantic import ValidationError

from harness.schema import (
    DELTA_SCHEMA,
    ExtractionDelta,
    NewEntity,
    NewFact,
    SummaryUpdate,
)


def test_full_delta_roundtrip():
    d = ExtractionDelta.model_validate({
        "new_entities": [{"id": "e1", "name": "One", "type": "concept"}],
        "new_facts": [{
            "id": "f1", "statement": "s", "entities": ["e1"],
            "certainty": "fact", "sources": [{"chapter": 1, "quote": "q"}],
        }],
        "summary_updates": [{"entity": "e1", "text": "hello"}],
    })
    assert d.new_entities[0].id == "e1"
    assert d.new_facts[0].certainty == "fact"
    assert d.summary_updates[0].text == "hello"


def test_bad_entity_type_rejected():
    with pytest.raises(ValidationError):
        NewEntity.model_validate({"id": "e1", "name": "One", "type": "nope"})


def test_bad_certainty_rejected():
    with pytest.raises(ValidationError):
        NewFact.model_validate({"id": "f1", "statement": "s",
                                "entities": ["e1"], "certainty": "maybe"})


def test_missing_required_entity_field_rejected():
    with pytest.raises(ValidationError):
        NewEntity.model_validate({"id": "e1"})  # missing name, type


def test_defaults_applied():
    e = NewEntity.model_validate({"id": "e1", "name": "One", "type": "concept"})
    assert e.aliases == []


def test_schema_exposes_top_level_keys():
    assert set(DELTA_SCHEMA["properties"].keys()) == {
        "new_entities", "new_facts", "summary_updates"}


def test_summary_update_shape_has_no_supersedes_field():
    # summaries are linked by the harness, not the model
    props = SummaryUpdate.model_json_schema()["properties"]
    assert "supersedes" not in props
    assert set(props.keys()) == {"entity", "text"}