import httpx
import pytest

from research_automation.llm.ollama import OllamaClient
from research_automation.llm.review import numbered_sentences, review_claims
from research_automation.llm.rules import linked_verbatim_channel, scope_matches, source_proof
from research_automation.models import CausalClaim, ClaimsEnvelope


def candidate(source, cause="Higher input costs", effect="Operating margin", **changes):
    values = {
        "cause_variable": cause, "effect_variable": effect, "direction": "decrease",
        "transmission_channel": "Higher input costs reduced operating margin",
        "horizon": "unknown", "modality": "observed", "confidence": .9, "evidence_text": source,
    }
    return CausalClaim.model_validate({**values, **changes})


@pytest.mark.parametrize("cause,effect", [
    ("Operating margin", "Higher input costs"),  # reversed edge
    ("Input costs", "Operating margin"),  # missing increase
    ("Lower input costs", "Operating margin"),  # opposite cause state
])
def test_surface_proof_cannot_rescue_missing_or_reversed_nodes(cause, effect):
    source = "Higher input costs reduced operating margin."
    assert source_proof(numbered_sentences(source), candidate(source, cause, effect)) is None


@pytest.mark.parametrize("source,cause,effect", [
    ("Higher input costs did not reduce operating margin.", "Higher input costs", "Operating margin"),
    ("Higher input costs reduced operating margin?", "Higher input costs", "Operating margin"),
    ("A 10% tax reduced household spending.", "A 10 tax", "Household spending"),
    ("A tax increase reduced household spending.", "A tax", "Reduced household spending"),
    ("Input costs and operating margin increased together.", "Input costs", "Operating margin"),
])
def test_surface_rules_decline_negation_questions_units_noun_traps_and_correlation(source, cause, effect):
    assert source_proof(numbered_sentences(source), candidate(source, cause, effect)) is None


def test_conditional_rule_keeps_condition_scope_modality_and_horizon():
    source = "If input costs fall next quarter, the company expects its domestic operating margin to improve."
    item = candidate(
        source, "Input costs falling next quarter", "Domestic operating margin",
        direction="increase", modality="conditional", horizon="short",
    )
    proof = source_proof(numbered_sentences(source), item)
    assert proof["modality"] == "conditional"
    assert proof["direction"] == "increase"
    assert proof["horizon"] == "short"


def test_korean_rule_keeps_two_paths_and_the_inherited_sector():
    source = "원자재 가격 상승으로 배터리 부문의 영업이익률이 하락했으나, ESS 부문은 제품 구성 개선으로 영업이익률이 상승했다."
    battery = candidate(source, "원자재 가격 상승", "배터리 부문의 영업이익률")
    ess = candidate(source, "제품 구성 개선", "ESS 부문의 영업이익률", direction="increase")
    assert source_proof(numbered_sentences(source), battery)["direction"] == "decrease"
    assert source_proof(numbered_sentences(source), ess)["direction"] == "increase"
    crossed = candidate(source, "원자재 가격 상승", "ESS 부문의 영업이익률")
    assert source_proof(numbered_sentences(source), crossed) is None


@pytest.mark.parametrize("ending", ["하락하지 않았다", "하락할 것으로 전망했다"])
def test_korean_actual_event_rule_cannot_convert_negation_or_forecast_to_fact(ending):
    source = f"원자재 가격 상승으로 배터리 부문의 영업이익률이 {ending}."
    assert source_proof(numbered_sentences(source), candidate(source, "원자재 가격 상승", "배터리 부문의 영업이익률")) is None


def test_driven_by_rule_keeps_the_complete_effect_scope_and_record_level():
    source = "AMD delivers record quarter as Data Center revenue grows 97%, driven by record GPU sales"
    item = candidate(source, "record GPU sales", "Data Center revenue grows 97%", direction="increase")
    assert source_proof(numbered_sentences(source), item)["connector"] == "driven by"
    broader = item.model_copy(update={"effect_variable": "revenue grows 97%"})
    assert source_proof(numbered_sentences(source), broader) is None


@pytest.mark.parametrize("modality,expected", [("forecast", "supported"), ("observed", "rejected")])
def test_korean_forecast_is_a_stated_claim_without_becoming_an_observed_event(tmp_path, modality, expected):
    source = "회사 측은 다음 분기 금리 하락이 국내 ESS 프로젝트의 조달 비용을 낮출 것으로 전망했다."
    item = candidate(
        source, "다음 분기 금리 하락", "국내 ESS 프로젝트의 조달 비용",
        modality=modality, horizon="short", transmission_channel=source.rstrip("."),
    )

    def unexpected_request(request):
        pytest.fail("An explicit matched forecast must not depend on a model verdict")

    with OllamaClient("test-model", audit_dir=tmp_path, transport=httpx.MockTransport(unexpected_request)) as client:
        result = review_claims(client, ClaimsEnvelope(claims=[item]), source)
    assert result.decisions[0]["status"] == expected


def test_scope_matching_allows_inserted_specificity_but_not_opposite_scope():
    assert scope_matches("government consumption expenditures", "Government nondefense consumption expenditures")
    assert not scope_matches("government", "nongovernment consumption expenditures")
    assert not scope_matches("government", "non-government consumption expenditures")
    assert not scope_matches("European manufacturing division", "Operating margin")


def test_unrelated_neighboring_mechanism_is_not_a_proof():
    source = "Higher input costs reduced operating margin. Exchange rates increased import volume."
    item = candidate(source, transmission_channel="Exchange rates increased import volume")
    assert linked_verbatim_channel(numbered_sentences(source), [0], item) == []


@pytest.mark.parametrize("direction,expected", [("decrease", "supported"), ("increase", "rejected")])
def test_rule_route_runs_without_a_model_and_checks_effect_direction(tmp_path, direction, expected):
    source = "Higher input costs reduced operating margin."

    def unexpected_request(request):
        pytest.fail("An exact source proof must not call the model")

    with OllamaClient("test-model", audit_dir=tmp_path, transport=httpx.MockTransport(unexpected_request)) as client:
        result = review_claims(client, ClaimsEnvelope(claims=[candidate(source, direction=direction)]), source)
    assert result.decisions[0]["status"] == expected
    assert result.decisions[0]["source_proof"]["route"] == "exact_clause"
    assert result.review_audits == []
    assert result.input_tokens == result.output_tokens == 0
