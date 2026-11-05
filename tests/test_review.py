import json
from pathlib import Path

import httpx
import pytest

from research_automation.llm.ollama import OllamaClient
from research_automation.llm.review import (
    REVIEW_VERSION,
    evidence_ids,
    numbered_sentences,
    review_claims,
)
from research_automation.models import CausalClaim, ClaimsEnvelope

SOURCE = "Higher input costs reduced operating margin."


@pytest.fixture(autouse=True)
def focus_on_model_stage(monkeypatch):
    # This module exercises transport/reviewer failure branches. Rule proofs have separate tests.
    monkeypatch.setattr("research_automation.llm.review.source_proof", lambda *args: None)


def claim(evidence=SOURCE):
    return CausalClaim(
        cause_variable="higher input costs", effect_variable="operating margin",
        direction="decrease", transmission_channel="cost pressure",
        horizon="unknown", modality="observed", confidence=0.95, evidence_text=evidence,
    )


def review(**changes):
    result = {
        "claim_id": 0,
        "source_cause_matches": "cause_variable",
        "source_effect_matches": "effect_variable",
        "source_link": "explicit",
        "source_effect_direction": "decrease",
        "source_cause_change": "increase",
        "source_effect_scope_terms": [],
        "source_modality": "observed",
        "source_channel_sentence_ids": [0],
        "evidence_sentence_ids": [0],
        "reason": "The source explicitly states the relationship.",
    }
    result.update(changes)
    return result


def channel_review(claim_id=0, verdict="supported"):
    return {
        "claim_id": claim_id, "checks": {"channel": verdict},
        "evidence_sentence_ids": [0], "reason": "Direct causal paraphrase matches the source.",
    }


def response(item):
    return json.dumps({"reviews": [item]})


def client_for(tmp_path, contents):
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        content = contents[min(len(calls) - 1, len(contents) - 1)]
        if isinstance(content, Exception):
            raise content
        return httpx.Response(200, json={"message": {"content": content}})

    client = OllamaClient(
        "test-model", transport=httpx.MockTransport(handler), audit_dir=tmp_path,
    )
    return client, calls


@pytest.mark.parametrize("check", [
    "cause_effect", "direction", "scope", "qualifiers", "modality", "channel",
])
def test_each_contradiction_is_rejected_without_retry(tmp_path, check):
    item = review()
    source = SOURCE
    candidate = claim()
    if check == "channel":
        contents = [response(item), response(channel_review(verdict="contradicted"))]
    elif check == "direction":
        item["source_effect_direction"] = "increase"
        contents = [response(item)]
    elif check == "scope":
        candidate = candidate.model_copy(update={"effect_variable": "margin"})
        item["source_effect_scope_terms"] = ["operating"]
        contents = [response(item)]
    elif check == "modality":
        item["source_modality"] = "forecast"
        contents = [response(item)]
    elif check == "cause_effect":
        item.update(source_cause_matches="effect_variable", source_effect_matches="cause_variable")
        contents = [response(item)]
    elif check == "qualifiers":
        source = "Higher input costs compared with the second quarter reduced operating margin."
        candidate = claim(source)
        contents = [response(item)]
    else:
        raise AssertionError(check)
    client, calls = client_for(tmp_path, contents)
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[candidate]), source)
    assert result.envelope.claims == []
    assert result.decisions[0]["status"] == "rejected"
    assert len(calls) == (2 if check == "channel" else 1)


def test_supported_is_retained_and_raw_review_saved(tmp_path):
    content = json.dumps({"reviews": [review()]})
    client, calls = client_for(tmp_path, [content, response(channel_review())])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert len(result.envelope.claims) == 1
    trace = json.loads(Path(result.review_audits[0]).read_text())
    assert json.loads(trace["attempts"][0]["response_body"])["message"]["content"] == content
    assert json.loads(result.audit_path.read_text())["candidates"][0] == claim().model_dump()
    node_input, channel_input = [json.loads(call["messages"][1]["content"])["claim"] for call in calls]
    assert "transmission_channel" not in node_input and "evidence_text" not in node_input
    assert "direction" not in node_input and "direction_of_effect" not in node_input
    assert "transmission_channel" in channel_input


@pytest.mark.parametrize("contents", [
    ["invalid JSON"],
    [json.dumps({"reviews": []})],
    [json.dumps({"reviews": [review(claim_id=7)]})],
    [json.dumps({"reviews": [review(), review()]})],
    [httpx.ReadTimeout("test timeout")],
])
def test_invalid_or_unavailable_review_is_held_after_bounded_retry(tmp_path, contents):
    client, calls = client_for(tmp_path, contents)
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert result.envelope.claims == []
    assert result.decisions[0]["status"] == "held"
    assert len(calls) == 2
    assert len(result.review_audits) == 2


def test_uncertain_source_state_is_held_without_retry(tmp_path):
    uncertain = review()
    uncertain["source_cause_change"] = "unknown"
    client, calls = client_for(tmp_path, [json.dumps({"reviews": [uncertain]})])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert result.decisions[0]["status"] == "held"
    assert result.envelope.claims == []
    assert len(calls) == 1


@pytest.mark.parametrize("ids", [[7], [0, 0], [1]])
def test_invalid_or_irrelevant_sentence_ids_cannot_pass(tmp_path, ids):
    client, calls = client_for(tmp_path, [json.dumps({"reviews": [review(evidence_sentence_ids=ids)]})])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE + " Unrelated context.")
    assert result.decisions[0]["status"] == "held"
    assert len(calls) == (1 if ids == [1] else 2)


def test_missing_evidence_never_calls_reviewer(tmp_path):
    client, calls = client_for(tmp_path, [])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim("Invented evidence")]), SOURCE)
    assert not calls
    assert result.decisions[0]["reason"] == "missing_evidence"


def test_oversized_context_is_held_without_silent_truncation(tmp_path):
    client, calls = client_for(tmp_path, [])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE + "x" * 20_000)
    assert not calls
    assert result.decisions[0]["status"] == "held"
    assert "review_context_budget_exceeded" in json.loads(result.audit_path.read_text())["errors"]


def test_extraction_keeps_pre_filter_claims_and_invalid_response(tmp_path):
    candidates = ClaimsEnvelope(claims=[claim(), claim("Invented missing evidence")])
    client, _ = client_for(tmp_path, [candidates.model_dump_json(), "broken JSON"])
    with client:
        result = client.extract_claims(SOURCE)
        trace = json.loads(result.audit_path.read_text())
        assert len(trace["parsed"]["claims"]) == 2
        assert trace["grounding_decisions"][1]["reason"] == "missing_evidence"
        with pytest.raises(ValueError):
            client.extract_claims(SOURCE)
    failed = json.loads(client.last_audit_path.read_text())
    assert failed["status"] == "error"
    assert json.loads(failed["attempts"][0]["response_body"])["message"]["content"] == "broken JSON"


def test_successful_retry_preserves_both_responses(tmp_path):
    client, calls = client_for(tmp_path, ["bad JSON", response(review()), response(channel_review())])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert len(calls) == 3
    assert len(result.envelope.claims) == 1
    assert len(result.review_audits) == 3
    assert calls[0]["messages"][0]["content"] != calls[1]["messages"][0]["content"]


def test_numbered_sentences_preserve_text_and_offsets():
    source = "U.S. policy changed.\n\n국고채금리는 올랐다. 다음 문장입니다."
    sentences = numbered_sentences(source)
    assert "".join(item["text"] for item in sentences) == source
    for item in sentences:
        assert source[item["start"]:item["end"]] == item["text"]
    assert evidence_ids(sentences, "policy changed. 국고채금리는 올랐다.") == [1, 2]


def test_bad_review_affects_only_its_own_claim(tmp_path):
    client, calls = client_for(tmp_path, [
        "bad JSON", "bad JSON", response(review()), response(channel_review()),
    ])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim(), claim()]), SOURCE)
    assert [item["status"] for item in result.decisions] == ["held", "supported"]
    assert len(calls) == 4
    assert all("claims" not in json.loads(call["messages"][1]["content"]) for call in calls)


def test_bad_channel_review_cannot_publish_good_nodes(tmp_path):
    client, calls = client_for(tmp_path, [response(review()), "bad JSON", "bad JSON"])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert len(calls) == 3
    assert result.decisions[0]["status"] == "held"
    assert result.decisions[0]["reason"] == "channel_review_unavailable"
    assert result.envelope.claims == []


def test_qwen_review_uses_model_sampling_and_bounded_output(tmp_path):
    client, calls = client_for(tmp_path, [response(review()), response(channel_review())])
    client.model = "qwen3.5:9b"
    with client:
        review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    for call in calls:
        assert call["think"] is False
        assert call["options"]["temperature"] == .6
        assert call["options"]["presence_penalty"] == 0.0
        assert call["options"]["top_p"] == .95
        assert call["options"]["top_k"] == 20
        assert call["options"]["seed"] == 42
        assert call["options"]["num_predict"] == 700


@pytest.mark.parametrize("label,expected", [
    ("Freight costs", "rejected"),
    ("Higher freight costs", "rejected"),
    ("Lower freight costs", "supported"),
])
def test_typed_source_state_blocks_missing_or_opposite_node_change(tmp_path, label, expected):
    source = "Lower freight costs increased European division margin."
    candidate = claim(source).model_copy(update={
        "cause_variable": label, "effect_variable": "European division margin",
        "direction": "increase", "transmission_channel": "Lower freight costs raised margin",
    })
    # Even if every model check says supported, code must block a missing/opposite cause state.
    item = review(source_effect_direction="increase", source_cause_change="decrease")
    client, calls = client_for(tmp_path, [response(item), response(channel_review())])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[candidate]), source)
    assert result.decisions[0]["status"] == expected
    assert len(calls) == (2 if expected == "supported" else 1)


@pytest.mark.parametrize("source,cause,source_state,expected", [
    ("Record sales increased segment revenue.", "Record sales", "increase", "supported"),
    ("Lower record sales reduced segment revenue.", "Record sales", "other", "rejected"),
    ("Lower record sales reduced segment revenue.", "Lower record sales", "decrease", "supported"),
])
def test_level_qualifier_is_kept_without_forgiving_an_omitted_delta(tmp_path, source, cause, source_state, expected):
    direction = "increase" if "increased" in source else "decrease"
    item = claim(source).model_copy(update={
        "cause_variable": cause, "effect_variable": "Segment revenue", "direction": direction,
        "transmission_channel": source.rstrip("."),
    })
    client, _ = client_for(tmp_path, [response(review(
        source_effect_direction=direction, source_cause_change=source_state,
    ))])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[item]), source)
    assert result.decisions[0]["status"] == expected


@pytest.mark.parametrize("store_candidates", [False, True])
def test_cli_default_does_not_publish_and_opt_in_keeps_reported_status(tmp_path, monkeypatch, store_candidates):
    import argparse

    from research_automation import cli, db
    from research_automation.config import load_settings

    settings = load_settings(tmp_path)
    db.init_db(settings.database_path)
    document_id = db.upsert_document(settings.database_path, {
        "source": "test", "source_document_id": "fixture", "url": "https://example.test",
        "title": "Synthetic integration fixture", "published_at": "2026-11-05",
        "company_id": None, "filing_type": None, "period_end": None,
        "content_hash": "fixture-hash", "raw_text": SOURCE,
    })
    wrong = claim().model_copy(update={"cause_variable": "operating margin", "effect_variable": "input costs"})
    rejected = review(source_cause_matches="effect_variable", source_effect_matches="cause_variable")
    client, _ = client_for(tmp_path / "audit", [
        ClaimsEnvelope(claims=[claim(), wrong]).model_dump_json(),
        json.dumps({"reviews": [review()]}),
        response(channel_review()),
        json.dumps({"reviews": [rejected]}),
    ])
    monkeypatch.setattr(cli, "OllamaClient", lambda **kwargs: client)
    cli.command_extract_claims(settings, argparse.Namespace(
        document_id=document_id, max_chars=1200, store_model_supported=store_candidates,
    ))
    with db.connect(settings.database_path) as connection:
        rows = connection.execute("select * from causal_claims").fetchall()
    assert len(rows) == (1 if store_candidates else 0)
    if store_candidates:
        assert rows[0]["cause_variable"] == "higher input costs"
        assert rows[0]["verification_status"] == "reported_claim"
        assert REVIEW_VERSION in rows[0]["prompt_version"]


def test_pilot_default_archives_without_inserting_claims(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    from research_automation.config import load_settings
    from research_automation.llm.ollama import ExtractionResult

    script = Path(__file__).resolve().parents[1] / "scripts" / "research_pilot.py"
    spec = importlib.util.spec_from_file_location("pilot_under_test", script)
    pilot = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pilot)
    client, _ = client_for(tmp_path, [])
    monkeypatch.setattr(pilot.ollama, "OllamaClient", lambda **kwargs: client)
    # The script's existing global prompt mutation must not leak into other tests.
    monkeypatch.setattr(pilot.ollama, "SYSTEM_PROMPT", pilot.ollama.SYSTEM_PROMPT)
    monkeypatch.setattr(pilot.db, "get_document", lambda *args: {
        "id": 1, "source": "test", "source_document_id": "test", "url": "https://example.test",
        "content_hash": "test", "raw_text": SOURCE,
    })
    generated = ExtractionResult(ClaimsEnvelope(claims=[claim()]), 10, 20)
    reviewed = SimpleNamespace(
        envelope=ClaimsEnvelope(claims=[claim()]), input_tokens=30, output_tokens=40,
        decisions=[{"claim_id": 0, "status": "supported"}], audit_path=tmp_path / "audit.json",
    )
    calls = []
    monkeypatch.setattr(pilot, "extract_reviewed_claims", lambda *args, **kwargs: (generated, reviewed))
    monkeypatch.setattr(pilot.db, "insert_claims", lambda *args: calls.append(args))
    monkeypatch.setattr(pilot.db, "log_llm_usage", lambda *args: None)
    pilot.extract_claims([{"document_id": 1, "label": "test", "extract": True}], load_settings(tmp_path), tmp_path)
    assert not calls
    record = json.loads((tmp_path / "extraction.json").read_text())[0]
    assert record["storage_mode"] == "candidate_only"
    assert record["model_supported_claims"] == 1
    assert record["inserted"] == 0
