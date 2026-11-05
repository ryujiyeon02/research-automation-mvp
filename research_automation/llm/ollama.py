from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

import httpx
from pydantic import BaseModel

from research_automation.llm.audit import Audit
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
    output_mode: str = "json_schema"
    audit_path: Optional[Path] = None
    selected_text: str = ""


@dataclass(frozen=True)
class JsonResult:
    parsed: Any
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    output_mode: str
    audit: Audit


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
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
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
            if not selected:
                selected.append(paragraph[:max_chars])
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


def parse_claim_content(content: Any) -> ClaimsEnvelope:
    """JSON 전체를 감싼 코드 블록만 벗기며 앞뒤 설명문은 허용하지 않습니다."""
    return parse_json_content(content, ClaimsEnvelope)


def parse_json_content(content: Any, schema: type[BaseModel]) -> BaseModel:
    if isinstance(content, dict):
        return schema.model_validate(content)
    text = str(content)
    fenced = re.fullmatch(r"\s*```(?:json)?[ \t]*\r?\n([\s\S]*?)\r?\n```\s*", text)
    if fenced:
        text = fenced.group(1)
    return schema.model_validate_json(text)


class OllamaClient:
    def __init__(
        self,
        model: str,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180.0,
        transport: Optional[httpx.BaseTransport] = None,
        audit_dir: Optional[Path] = None,
    ) -> None:
        if not model.strip():
            raise ValueError("OLLAMA_MODEL is not configured")
        self.model = model.strip()
        self.base_url = base_url.rstrip("/")
        self._schema_supported = True
        self.audit_dir = audit_dir if audit_dir is not None else Path("data/llm_runs")
        self.last_audit_path: Optional[Path] = None
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

        generated = self.generate_json(
            SYSTEM_PROMPT, excerpt, ClaimsEnvelope, "extraction", PROMPT_VERSION,
        )
        parsed = generated.parsed
        grounded = filter_grounded_claims(parsed, excerpt)
        generated.audit.save(
            grounding_decisions=[{
                "claim_index": index,
                "status": "grounded" if claim in grounded.claims else "rejected",
                "reason": "evidence_found" if claim in grounded.claims else "missing_evidence",
                "claim": claim.model_dump(),
            } for index, claim in enumerate(parsed.claims)],
            grounded=grounded.model_dump(),
        )
        return ExtractionResult(
            envelope=grounded,
            input_tokens=generated.input_tokens,
            output_tokens=generated.output_tokens,
            rejected_claims=len(parsed.claims) - len(grounded.claims),
            output_mode=generated.output_mode,
            audit_path=generated.audit.path,
            selected_text=excerpt,
        )

    def generate_json(
        self, system: str, text: str, schema: type[BaseModel], operation: str, version: str,
        context_size: int = 8_192,
    ) -> JsonResult:
        audit = Audit(self.audit_dir, operation, self.model, version, text)
        self.last_audit_path = audit.path
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": text},
            ],
            "stream": False,
            "think": False,
            "format": schema.model_json_schema(),
            "options": {
                "temperature": 0,
                "num_ctx": context_size,
                # 긴 공시에서 여러 관계가 추출되면 1,500 토큰 안에서 JSON이
                # 중간에 잘릴 수 있다. 로컬 추론이므로 완결성을 우선한다.
                "num_predict": 3_000,
            },
        }

        def use_validated_json() -> None:
            payload.pop("format", None)
            payload["messages"][0]["content"] = (
                system + "\nJSON Schema:\n" + json.dumps(schema.model_json_schema(), ensure_ascii=False)
            )

        def post() -> httpx.Response:
            attempt = {"request": json.loads(json.dumps(payload))}
            audit.record["attempts"].append(attempt)
            audit.save()
            response = self._client.post("/api/chat", json=payload)
            # Save even invalid JSON, truncated generations and HTTP errors before interpretation.
            attempt.update(status_code=response.status_code, response_body=response.text)
            audit.save(status="received")
            return response

        if not self._schema_supported:
            use_validated_json()
        try:
            response = post()
            if response.status_code == 501:
                try:
                    unsupported = response.json().get("error") == "structured output is unavailable"
                except (ValueError, AttributeError):
                    unsupported = False
                if unsupported and "format" in payload:
                    # 일부 Homebrew MLX 설치본은 xgrammar 없이 배포된다.
                    # 생성 형식 강제 대신 프롬프트에 스키마를 주고 아래에서 동일하게 검증한다.
                    self._schema_supported = False
                    use_validated_json()
                    response = post()
            response.raise_for_status()
            data = response.json()
            if data.get("done") is False or data.get("done_reason") == "length":
                raise ValueError("Incomplete model generation; raw response retained")
            parsed = parse_json_content(data["message"]["content"], schema)
            audit.save(status="validated", parsed=parsed.model_dump())
        except httpx.ConnectError as exc:
            audit.save(status="error", error_type=type(exc).__name__)
            raise RuntimeError(
                f"Ollama에 연결할 수 없습니다: {self.base_url}. Ollama가 실행 중인지 확인하세요."
            ) from exc
        except httpx.HTTPStatusError as exc:
            audit.save(status="error", error_type=type(exc).__name__)
            try:
                detail = response.json().get("error", response.text)
            except (json.JSONDecodeError, AttributeError):
                detail = response.text
            raise RuntimeError(f"Ollama 요청 실패 ({response.status_code}): {detail}") from exc
        except Exception as exc:
            audit.save(status="error", error_type=type(exc).__name__)
            raise
        return JsonResult(
            parsed=parsed,
            input_tokens=data.get("prompt_eval_count"),
            output_tokens=data.get("eval_count"),
            output_mode="json_schema" if "format" in payload else "validated_json",
            audit=audit,
        )


def result_as_json(result: ExtractionResult) -> str:
    return json.dumps(result.envelope.model_dump(), ensure_ascii=False, indent=2)
