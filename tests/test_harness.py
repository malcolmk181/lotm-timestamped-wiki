"""Tests for the extraction harness's parsing and normalization logic."""
from harness.extract import (
    chapter_files,
    clean_text,
    extract_delta,
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


def test_normalize_delta_dedups_and_autolinks():
    prior = {
        "entities": [{"id": "e1", "name": "One", "type": "concept", "first_seen": 1}],
        "facts": [{"id": "f1", "statement": "s", "established_at": 1,
                   "entities": ["e1"]}],
        "summaries": [{"id": "s-e1-1", "entity": "e1", "established_at": 1,
                       "text": "v1"}],
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
        "summary_updates": [{"entity": "e1", "text": "v2"}],
    }
    norm, warns = normalize_delta(raw, prior, 2)
    assert [e["id"] for e in norm["new_entities"]] == ["e2"]
    assert [f["id"] for f in norm["new_facts"]] == ["f2"]
    s = norm["summary_updates"][0]
    assert s["id"] == "s-e1-2"
    assert s["supersedes"] == ["s-e1-1"]
    assert len(warns) >= 3


def test_normalize_delta_new_entity_summary_has_no_supersede():
    prior = {"entities": [], "facts": [], "summaries": []}
    raw = {
        "new_entities": [{"id": "e1", "name": "One", "type": "character"}],
        "new_facts": [],
        "summary_updates": [{"entity": "e1", "text": "first"}],
    }
    norm, warns = normalize_delta(raw, prior, 1)
    s = norm["summary_updates"][0]
    assert s["id"] == "s-e1-1"
    assert s["supersedes"] == []
    assert not warns


def test_normalize_delta_drops_whole_junk_without_raising():
    prior = {"entities": [], "facts": [], "summaries": []}
    raw = {"new_entities": [{"id": 123}], "new_facts": [{"id": "x"}],
           "summary_updates": [{"entity": "x"}]}
    norm, warns = normalize_delta(raw, prior, 1)
    assert norm["new_entities"] == []
    assert norm["new_facts"] == []
    assert norm["summary_updates"] == []
    assert len(warns) == 3


def test_normalize_delta_assigns_coarse_kind():
    prior = {"entities": [], "facts": [], "summaries": []}
    raw = {"new_entities": [
        {"id": "a", "name": "A", "type": "character"},
        {"id": "b", "name": "B", "type": "pathway"},
        {"id": "c", "name": "C", "type": "zoo", "kind": "idea"},
    ], "new_facts": [], "summary_updates": []}
    norm, _ = normalize_delta(raw, prior, 1)
    kinds = {e["id"]: e["kind"] for e in norm["new_entities"]}
    assert kinds == {"a": "person", "b": "idea", "c": "idea"}


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
    assert "s-e1-1 | e1 | 1" in text


def _resp(content):
    return {"choices": [{"message": {"content": content}}]}


def test_extract_delta_retries_on_empty_then_succeeds(monkeypatch):
    import harness.extract as ex
    calls = {"n": 0}

    def fake_post(api_key, model, messages, response_format):
        calls["n"] += 1
        return _resp("") if calls["n"] == 1 else _resp('{"new_entities": []}')

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_delta("k", "m", []) == {"new_entities": []}
    assert calls["n"] == 2


def test_extract_delta_retries_on_malformed_then_succeeds(monkeypatch):
    import harness.extract as ex
    calls = {"n": 0}

    def fake_post(api_key, model, messages, response_format):
        calls["n"] += 1
        return (_resp('{"new_entities": [broken') if calls["n"] == 1
                else _resp('{"new_entities": []}'))

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_delta("k", "m", []) == {"new_entities": []}
    assert calls["n"] == 2


def test_extract_delta_downgrades_tier_on_response_format_error(monkeypatch):
    import harness.extract as ex
    formats = []

    def fake_post(api_key, model, messages, response_format):
        formats.append(response_format)
        if response_format is not None and response_format.get("type") == "json_schema":
            raise ex.ResponseFormatError("unsupported")
        return _resp('{"new_entities": []}')

    monkeypatch.setattr(ex, "_openrouter_post", fake_post)
    assert ex.extract_delta("k", "m", []) == {"new_entities": []}
    assert formats[0]["type"] == "json_schema"   # rejected -> downgrade
    assert formats[1]["type"] == "json_object"   # accepted