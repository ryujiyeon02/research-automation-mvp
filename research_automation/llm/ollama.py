from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

from research_automation.models import ClaimsEnvelope

PROMPT_VERSION = "causal-claims-v1"

SYSTEM_PROMPT = """당신은 경제·기업 문서의 인과 주장 추출기입니다.
문서 작성자가 명시적으로 서술한 원인과 결과만 추출하세요.
상식이나 외부지식으로 누락된 중간 경로를 보충하지 마세요.
각 관계에는 입력 문서에 실제 존재하는 짧은 근거 문장을 그대로 포함하세요.
단순 동시 발생이나 상관관계는 인과관계로 바꾸지 마세요.
인과 주장이 없으면 claims를 빈 배열로 반환하세요.
direction과 horizon이 불명확하면 unknown을 사용하세요.
modality는 실제 발생한 서술은 observed, 전망은 forecast, 가능성은 possible,
명시적 조건문은 conditional, 불확실성을 강조하면 uncertain로 분류하세요.
confidence는 문장 내 인과 표현이 얼마나 명시적인지에 대한 점수이며 진실 확률이 아닙니다.
반드시 주어진 JSON Schema에 맞는 JSON만 반환하세요.
""".strip()

CLAIMS_SCHEMA: Dict[str, Any] = ClaimsEnvelope.model_json_schema()


@dataclass(frozen=True)
class ExtractionResult:
    envelope: ClaimsEnvelope
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    rejected_claims: int = 0


CAUSAL_MARKERS = (
    "because",
    "due to",
    "as a result",
    "resulted in",
    "driven by",
    "caused by",
    "impact of",
    "led to",
    "contributed to",
    "때문에",
    "영향으로",
    "결과로",
    "기인",
    "원인",
    "초래",
    "이어져",
    "압력",
)


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().casefold()


def select_relevant_text(text: str, max_chars: int = 12_000) -> str:
    """인과 표현이 있는 문단과 앞뒤 문맥을 우선 선택해 입력 토큰을 줄입니다."""
    paragraphs = [" ".join(part.split()) for part in re.split(r"\n+", text) if part.strip()]
    if not paragraphs:
        return ""

    relevant_indexes = {
        index
        for index, paragraph in enumerate(paragraphs)
        if any(marker in paragraph.casefold() for marker in CAUSAL_MARKERS)
    }
    if not relevant_indexes:
        return "\n\n".join(paragraphs)[:max_chars]

    context_indexes = set()
    for index in relevant_indexes:
        context_indexes.update(range(max(0, index - 1), min(len(paragraphs), index + 2)))

    selected = []
    current_length = 0
    for index in sorted(context_indexes):
        paragraph = paragraphs[index]
        added_length = len(paragraph) + (2 if selected else 0)
        if current_length + added_length > max_chars:
            break
        selected.append(paragraph)
        current_length += added_length
    return "\n\n".join(selected)


def filter_grounded_claims(envelope: ClaimsEnvelope, source_text: str) -> ClaimsEnvelope:
    """근거 문장이 실제 입력에 포함된 주장만 통과시킵니다."""
    normalized_source = _normalize_text(source_text)
    grounded = [
        claim
        for claim in envelope.claims
        if _normalize_text(claim.evidence_text) in normalized_source
    ]
    return ClaimsEnvelope(claims=grounded)


class OllamaClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not model.strip():
            raise ValueError("OLLAMA_MODEL is not configured")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout_seconds,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OllamaClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def extract_claims(self, text: str, max_chars: int = 12_000) -> ExtractionResult:
        excerpt = select_relevant_text(text, max_chars=max_chars)
        if not excerpt:
            return ExtractionResult(
                envelope=ClaimsEnvelope(claims=[]),
                input_tokens=0,
                output_tokens=0,
            )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": excerpt},
            ],
            "stream": False,
            "think": False,
            "format": CLAIMS_SCHEMA,
            "options": {
                "temperature": 0,
                "num_ctx": 8_192,
                # 긴 공시에서 여러 관계가 추출되면 1,500 토큰 안에서 JSON이
                # 중간에 잘릴 수 있다. 로컬 추론이므로 완결성을 우선한다.
                "num_predict": 3_000,
            },
        }
        try:
            response = self._client.post("/api/chat", json=payload)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise RuntimeError(
                f"Ollama에 연결할 수 없습니다: {self.base_url}. Ollama가 실행 중인지 확인하세요."
            ) from exc
        except httpx.HTTPStatusError as exc:
            try:
                detail = response.json().get("error", response.text)
            except (json.JSONDecodeError, AttributeError):
                detail = response.text
            raise RuntimeError(f"Ollama 요청 실패 ({response.status_code}): {detail}") from exc

        data = response.json()
        content = data["message"]["content"]
        if isinstance(content, dict):
            parsed = ClaimsEnvelope.model_validate(content)
        else:
            parsed = ClaimsEnvelope.model_validate_json(str(content))
        grounded = filter_grounded_claims(parsed, excerpt)
        return ExtractionResult(
            envelope=grounded,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            rejected_claims=len(parsed.claims) - len(grounded.claims),
        )


def result_as_json(result: ExtractionResult) -> str:
    return json.dumps(result.envelope.model_dump(), ensure_ascii=False, indent=2)
