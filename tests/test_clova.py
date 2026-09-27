from research_automation.llm.clova import filter_grounded_claims, select_relevant_text
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
