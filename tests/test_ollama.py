import json

import httpx

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
        assert payload["options"]["num_predict"] == 1_500
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
