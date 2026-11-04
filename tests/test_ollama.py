import json

import httpx
import pytest
from pydantic import ValidationError

from research_automation.llm.ollama import (
    OllamaClient,
    filter_grounded_claims,
    select_relevant_text,
)
from research_automation.models import CausalClaim, ClaimsEnvelope


def make_claim(evidence_text: str) -> CausalClaim:
    return CausalClaim(
        cause_variable="input costs",
        effect_variable="operating margin",
        direction="decrease",
        transmission_channel="cost pressure",
        horizon="short",
        modality="observed",
        confidence=0.95,
        evidence_text=evidence_text,
    )


def test_select_relevant_text_keeps_causal_paragraph_and_context() -> None:
    text = "Introduction\nNeutral paragraph\nMargin fell because input costs rose.\nOutlook"
    selected = select_relevant_text(text, max_chars=1_000)
    assert "Neutral paragraph" in selected
    assert "Margin fell because input costs rose." in selected
    assert "Outlook" in selected


def test_filter_grounded_claims_rejects_invented_evidence() -> None:
    source = "Higher input costs reduced operating margin."
    envelope = ClaimsEnvelope(
        claims=[
            make_claim("Higher input costs reduced operating margin."),
            make_claim("Interest rates reduced operating margin."),
        ]
    )
    filtered = filter_grounded_claims(envelope, source)
    assert len(filtered.claims) == 1
    assert filtered.claims[0].evidence_text.startswith("Higher input costs")


def test_ollama_client_uses_json_schema_and_records_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/chat"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen3.5:9b"
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["format"]["type"] == "object"
        assert payload["options"]["temperature"] == 0
        assert payload["options"]["num_ctx"] == 8_192
        assert payload["options"]["num_predict"] == 3_000
        content = ClaimsEnvelope(
            claims=[make_claim("Higher input costs reduced operating margin.")]
        ).model_dump_json()
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": content},
                "prompt_eval_count": 120,
                "eval_count": 45,
            },
        )

    transport = httpx.MockTransport(handler)
    with OllamaClient(
        model="qwen3.5:9b",
        base_url="http://ollama.test",
        transport=transport,
    ) as client:
        result = client.extract_claims("Higher input costs reduced operating margin.")

    assert len(result.envelope.claims) == 1
    assert result.input_tokens == 120
    assert result.output_tokens == 45
    assert result.rejected_claims == 0


def test_mlx_without_xgrammar_falls_back_and_keeps_grounding():
    calls = []

    def handler(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if "format" in payload:
            return httpx.Response(501, json={"error": "structured output is unavailable"})
        assert "JSON Schema:" in payload["messages"][0]["content"]
        content = ClaimsEnvelope(claims=[
            make_claim("Higher input costs reduced operating margin."),
            make_claim("Invented evidence not in the source."),
        ]).model_dump_json()
        return httpx.Response(200, json={"message": {"content": content}})

    with OllamaClient("qwen3.5:9b", transport=httpx.MockTransport(handler)) as client:
        result = client.extract_claims("Higher input costs reduced operating margin.")
        client.extract_claims("Higher input costs reduced operating margin.")
    assert len(calls) == 3
    assert result.output_mode == "validated_json"
    assert len(result.envelope.claims) == 1
    assert result.rejected_claims == 1


def test_schema_fallback_still_rejects_invalid_json():
    def handler(request):
        if "format" in json.loads(request.content):
            return httpx.Response(501, json={"error": "structured output is unavailable"})
        return httpx.Response(200, json={"message": {"content": "not JSON"}})

    with (
        OllamaClient("qwen3.5:9b", transport=httpx.MockTransport(handler)) as client,
        pytest.raises(ValidationError),
    ):
        client.extract_claims("Higher input costs reduced operating margin.")


def test_other_server_errors_do_not_trigger_schema_fallback():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={"error": "model failed"})

    with (
        OllamaClient("qwen3.5:9b", transport=httpx.MockTransport(handler)) as client,
        pytest.raises(RuntimeError, match="model failed"),
    ):
        client.extract_claims("Higher input costs reduced operating margin.")
    assert len(calls) == 1
