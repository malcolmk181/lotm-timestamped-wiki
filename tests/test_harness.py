"""Tests for the extraction harness's parsing and normalization logic."""
from harness.extract import (
    active_facts_for,
    chapter_files,
    clean_text,
    compute_changed_entities,
    extract_json,
    link_summaries,
    normalize_delta,
    parse_json,
    prior_state_prompt,
)

SAMPLE_MD = """---
title: Test
---

## Chapter 1

Some text here.

![alt](path/to/img.webp){.hidden-caption fetchpriority="high"}

More text.
"""


def test_clean_text_strips_frontmatter_and_images():
    out = clean_text(SAMPLE_MD)
    assert "title:" not in out
    assert "![alt]" not in out
    assert "Some text here." in out
    assert "More text." in out


def test_parse_json_plain():
    assert parse_json('{"a": 1}') == {"a": 1}


def test_parse_json_fenced():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_surrounded_by_prose():
    assert parse_json('the answer is: {"a": 1} ok') == {"a": 1}


def test_normalize_delta_dedups_entities_and_facts():
    prior = {
        "entities": [{"id": "e1", "name": "One", "type": "concept", "first_seen": 1}],
        "facts": [{"id": "f1", "statement": "s", "established_at": 1,
                   "entities": ["e1"]}],
        "summaries": [],
    }
    raw = {
        "new_entities": [
            {"id": "e1", "name": "One", "type": "concept"},       # duplicate
            {"id": "e2", "name": "Two", "type": "character"},
            {"id": "e3", "type": "character"},                    # missing name
        ],
        "new_facts": [
            {"id": "f2", "statement": "s2", "entities": ["e2"],
             "certainty": "fact", "sources": [{"chapter": 2, "quote": "q"}]},
            {"id": "f3", "statement": "s3", "entities": ["e2"],
             "certainty": "wrong", "sources": [{"chapter": 2, "quote": "q"}]},
        ],
    }
    norm, warns = normalize_delta(raw, prior, 2)
    assert [e["id"] for e in norm["new_entities"]] == ["e2"]
    assert [f["id"] for f in norm["new_facts"]] == ["f2"]
    assert len(warns) >= 3


def test_normalize_delta_has_no_summary_key():
    prior = {"entities": [], "facts": [], "summaries": []}
    norm, _ = normalize_delta({"new_entities": [], "new_facts": []}, prior, 1)
    assert "summary_updates" not in norm


def test_normalize_delta_drops_whole_junk_without_raising():
    prior = {"entities": [], "facts": [], "summaries": []}
    raw = {"new_entities": [{"id": 123}], "new_facts": [{"id": "x"}]}
    norm, warns = normalize_delta(raw, prior, 1)
    assert norm["new_entities"] == []
    assert norm["new_facts"] == []
    assert len(warns) == 2


def test_normalize_delta_assigns_coarse_kind():
    prior = {"entities": [], "facts": [], "summaries": []}
    raw = {"new_entities": [
        {"id": "a", "name": "A", "type": "character"},
        {"id": "b", "name": "B", "type": "pathway"},
        {"id": "c", "name": "C", "type": "zoo"},
    ], "new_facts": []}
    norm, _ = normalize_delta(raw, prior, 1)
    kinds = {e["id"]: e["kind"] for e in norm["new_entities"]}
    assert kinds == {"a": "person", "b": "idea", "c": "thing"}


def test_compute_changed_entities_includes_new_and_refined():
    normalized = {
        "new_entities": [{"id": "a", "name": "A", "type": "concept"}],
        "new_facts": [
            {"entities": ["b"], "refines": ["f-old"], "supersedes": []},
            {"entities": ["c"], "refines": [], "supersedes": []},  # accretion only
        ],
    }
    assert compute_changed_entities(normalized) == {"a", "b"}


def test_active_facts_for_excludes_superseded():
    prior = {
        "entities": [],
        "facts": [
            {"id": "f1", "statement": "old", "established_at": 1,
             "entities": ["e"], "supersedes": []},
            {"id": "f2", "statement": "new", "established_at": 2,
             "entities": ["e"], "supersedes": ["f1"]},
        ],
        "summaries": [],
    }
    assert [f["id"] for f in active_facts_for("e", prior, 2)] == ["f2"]


def test_link_summaries_autolinks_supersede():
    prior = {
        "entities": [{"id": "e1", "name": "One", "type": "concept", "first_seen": 1}],
        "facts": [],
        "summaries": [{"id": "s-e1-1", "entity": "e1", "established_at": 1,
                       "text": "v1"}],
    }
    out, warns = link_summaries([{"entity": "e1", "text": "v2"}], prior, 2)
    assert out[0]["id"] == "s-e1-2"
    assert out[0]["supersedes"] == ["s-e1-1"]
    assert not warns


def test_link_summaries_new_entity_has_no_supersede():
    prior = {
        "entities": [{"id": "e1", "name": "One", "type": "character", "first_seen": 1}],
        "facts": [],
        "summaries": [],
    }
    out, warns = link_summaries([{"entity": "e1", "text": "first"}], prior, 1)
    assert out[0]["id"] == "s-e1-1"
    assert out[0]["supersedes"] == []
    assert not warns


def test_chapter_files_skips_metadata(tmp_path):
    for n in (0, 1, 2):
        (tmp_path / f"{n:04d}.md").write_text("x")
    files = chapter_files(tmp_path)
    assert [n for n, _ in files] == [1, 2]


def test_prior_state_prompt_lists_entities_and_facts():
    prior = {
        "entities": [{"id": "e1", "name": "One", "type": "concept"}],
        "facts": [{"id": "f1", "statement": "s", "established_at": 1}],
        "summaries": [{"id": "s-e1-1", "entity": "e1", "established_at": 1}],
    }
    text = prior_state_prompt(prior)
    assert "e1 | One | concept" in text
    assert "f1 | s | 1" in text
    assert "s-e1-1" not in text  # summaries excluded from the main prior state


def _resp(content):
    return {"choices": [{"message": {"content": content}}]}


def test_extract_json_retries_on_empty_then_succeeds(monkeypatch):
    import harness.extract as ex
    calls = {"n": 0}

    def fake_post(api_key, model, messages, response_format):
        calls["n"] += 1
        return _resp("") if calls["n"] == 1 else _resp('{"new_entities": []}')

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_json("k", "m", [], {}, "test") == {"new_entities": []}
    assert calls["n"] == 2


def test_extract_json_retries_on_malformed_then_succeeds(monkeypatch):
    import harness.extract as ex
    calls = {"n": 0}

    def fake_post(api_key, model, messages, response_format):
        calls["n"] += 1
        return (_resp('{"new_entities": [broken') if calls["n"] == 1
                else _resp('{"new_entities": []}'))

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_json("k", "m", [], {}, "test") == {"new_entities": []}
    assert calls["n"] == 2


def test_extract_json_downgrades_tier_on_response_format_error(monkeypatch):
    import harness.extract as ex
    formats = []

    def fake_post(api_key, model, messages, response_format):
        formats.append(response_format)
        if response_format is not None and response_format.get("type") == "json_schema":
            raise ex.ResponseFormatError("unsupported")
        return _resp('{"new_entities": []}')

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_json("k", "m", [], {}, "test") == {"new_entities": []}
    assert formats[0]["type"] == "json_schema"   # rejected -> downgrade
    assert formats[1]["type"] == "json_object"   # accepted