"""Tests for build.py's temporal resolution and validation."""
import pytest

from build import (
    BuildError,
    resolve_temporal,
    validate_entities,
    validate_facts,
    validate_summaries,
)


def test_supersede_sets_revoked_and_superseded_by():
    facts = [
        {"id": "a", "established_at": 1, "refines": [], "supersedes": []},
        {"id": "b", "established_at": 2, "refines": [], "supersedes": ["a"]},
    ]
    out = resolve_temporal(facts)
    a = next(f for f in out if f["id"] == "a")
    assert a["revoked_at"] == 2
    assert a["superseded_by"] == ["b"]


def test_refine_sets_refined_by_but_not_revoked():
    facts = [
        {"id": "a", "established_at": 1, "refines": [], "supersedes": []},
        {"id": "b", "established_at": 2, "refines": ["a"], "supersedes": []},
    ]
    out = resolve_temporal(facts)
    a = next(f for f in out if f["id"] == "a")
    assert a["refined_by"] == ["b"]
    assert a["revoked_at"] is None


def test_supersede_must_point_backward():
    facts = [
        {"id": "a", "established_at": 5, "refines": [], "supersedes": []},
        {"id": "b", "established_at": 3, "refines": [], "supersedes": ["a"]},
    ]
    with pytest.raises(BuildError):
        resolve_temporal(facts)


def test_supersede_unknown_id_rejected():
    facts = [
        {"id": "b", "established_at": 2, "refines": [], "supersedes": ["nope"]},
    ]
    with pytest.raises(BuildError):
        resolve_temporal(facts)


def test_validate_entities_rejects_missing_type():
    with pytest.raises(BuildError):
        validate_entities([{"id": "x", "name": "X", "type": "",
                            "first_seen": 1}])


def test_validate_entities_accepts_new_type():
    # entity types are free-form and grow with the book
    validate_entities([{"id": "x", "name": "X", "type": "pathway",
                        "first_seen": 1}])


def test_validate_facts_rejects_unknown_entity():
    with pytest.raises(BuildError):
        validate_facts([{
            "id": "f1", "statement": "s", "entities": ["nope"],
            "certainty": "fact", "established_at": 1,
            "sources": [{"chapter": 1}],
        }], {"x"})


def test_validate_facts_rejects_bad_certainty():
    with pytest.raises(BuildError):
        validate_facts([{
            "id": "f1", "statement": "s", "entities": ["x"],
            "certainty": "maybe", "established_at": 1,
            "sources": [{"chapter": 1}],
        }], {"x"})


def test_validate_summaries_rejects_unknown_entity():
    with pytest.raises(BuildError):
        validate_summaries([{
            "id": "s1", "entity": "nope", "established_at": 1,
            "text": "t", "sources": [{"chapter": 1}],
        }], {"x"})