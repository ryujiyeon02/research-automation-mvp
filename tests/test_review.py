import json

import httpx
import pytest

from research_automation.llm.ollama import OllamaClient
from research_automation.llm.review import review_claims
from research_automation.models import CausalClaim, ClaimsEnvelope

SOURCE = "Higher input costs reduced operating margin."


def claim(evidence=SOURCE):
    return CausalClaim(
        cause_variable="higher input costs", effect_variable="operating margin",
        direction="decrease", transmission_channel="cost pressure",
        horizon="unknown", modality="observed", confidence=0.95, evidence_text=evidence,
    )


def review(**changes):
    result = {
        "claim_id": 0,
        "checks": dict.fromkeys(
            ["cause_effect", "direction", "scope", "qualifiers", "modality", "channel"],
            "supported",
        ),
        "cause_quote": "Higher input costs",
        "effect_quote": "reduced operating margin",
        "reason": "The source explicitly states the relationship.",
    }
    result.update(changes)
    return result


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
    item["checks"][check] = "contradicted"
    client, calls = client_for(tmp_path, [json.dumps({"reviews": [item]})])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert result.envelope.claims == []
    assert result.decisions[0]["status"] == "rejected"
    assert len(calls) == 1


def test_supported_is_retained_and_raw_review_saved(tmp_path):
    content = json.dumps({"reviews": [review()]})
    client, _ = client_for(tmp_path, [content])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert len(result.envelope.claims) == 1
    trace = json.loads(next(tmp_path.glob("review-*.json")).read_text())
    assert json.loads(trace["attempts"][0]["response_body"])["message"]["content"] == content
    assert json.loads(result.audit_path.read_text())["candidates"][0] == claim().model_dump()


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


def test_uncertain_and_invented_verifier_quotes_are_held(tmp_path):
    uncertain = review()
    uncertain["checks"]["qualifiers"] = "uncertain"
    for item in [uncertain, review(cause_quote="A made-up quote")]:
        client, calls = client_for(tmp_path, [json.dumps({"reviews": [item]})])
        with client:
            result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
        assert result.decisions[0]["status"] == "held"
        assert result.envelope.claims == []
        assert len(calls) == 1


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
    client, calls = client_for(tmp_path, ["bad JSON", json.dumps({"reviews": [review()]})])
    with client:
        result = review_claims(client, ClaimsEnvelope(claims=[claim()]), SOURCE)
    assert len(calls) == 2
    assert len(result.envelope.claims) == 1
    assert len(result.review_audits) == 2


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
    rejected = review(claim_id=1)
    rejected["checks"]["cause_effect"] = "contradicted"
    client, _ = client_for(tmp_path / "audit", [
        ClaimsEnvelope(claims=[claim(), wrong]).model_dump_json(),
        json.dumps({"reviews": [review(), rejected]}),
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
        assert "causal-review-v1" in rows[0]["prompt_version"]


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
