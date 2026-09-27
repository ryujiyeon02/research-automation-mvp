# 경제·공시 리서치 자동화

국내외 경제 문서와 기업공시를 수집하고, 문서에 **명시된** 경제 변수 간 인과 주장을 근거 문장과 함께 구조화하는 읽기 전용 MVP입니다.

현재 구현 범위:

- `.env` 기반 비밀키 관리와 Git 노출 방지
- SQLite 문서·인과관계·시계열·시장가격·LLM 사용량 스키마
- SEC EDGAR 10-K·10-Q·8-K 목록 및 원문 수집
- CLOVA Studio 기반 인과 주장 JSON 추출
- 동일 문서 중복 저장 방지
- API 키 상태를 값 노출 없이 확인하는 진단 명령

주문 및 자동매매 기능은 포함하지 않습니다.

## 시작하기

```bash
uv sync --extra dev
uv run python -m research_automation init-db
uv run python -m research_automation doctor
uv run pytest
```

`.env`에서 다음 값을 먼저 수정합니다.

```env
SEC_USER_AGENT="실명 또는 프로젝트명 실제이메일@example.com"
CLOVA_API_KEY=
```

API 키는 이 대화나 GitHub에 올리지 않고 로컬 `.env`에만 입력합니다.

## SEC 공시 확인

애플의 CIK `0000320193`을 예로 들면:

```bash
uv run python -m research_automation sec-list \
  --cik 0000320193 \
  --forms 10-K,10-Q,8-K \
  --limit 10
```

공시 원문까지 SQLite에 저장하려면:

```bash
uv run python -m research_automation sec-ingest \
  --cik 0000320193 \
  --forms 10-K,10-Q,8-K \
  --limit 3
```

저장된 문서에서 명시적 인과 주장만 추출하려면:

```bash
uv run python -m research_automation extract-claims --document-id 1
```

추출 명령은 `CLOVA_API_KEY`가 있을 때만 호출됩니다. 결과에는 원인·결과·방향·전달경로·시차·확실성·원문 근거가 저장됩니다.

## 설계 원칙

1. 수집·정제·수치 계산은 Python으로 처리합니다.
2. LLM은 새로운 중요 문서의 구조화 추출과 최종 브리핑에만 사용합니다.
3. 원문에 없는 연결고리를 추론해 저장하지 않습니다.
4. 뉴스나 보고서의 주장은 `reported_claim`으로 저장하며, 계량적으로 입증된 인과관계로 표현하지 않습니다.
5. API 사용량과 모델·프롬프트 버전을 함께 기록합니다.

세부 구조는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 데이터 소스 범위는 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)를 참고하세요.

## 현재 검증 범위 (2026-09-27 기준)

| 항목 | 상태 |
| --- | --- |
| 단위 테스트 | `uv run pytest` 8개 통과. SEC 응답은 저장된 fixture(`tests/fixtures/sec_submissions.json`)로, CLOVA 추출은 네트워크 없이 근거 필터 로직만 검증 |
| 근거 없는 주장 폐기 | LLM이 반환한 `evidence_text`가 원문에 없으면 버리는 필터를 테스트로 확인 (`tests/test_clova.py`) |
| 비밀키 노출 방지 | `doctor` 명령이 키 값이 아니라 설정 여부만 반환하는지 테스트로 확인 |
| SEC EDGAR 실제 호출 | **미검증** — 로컬 DB에 수집된 문서 없음 |
| CLOVA Studio 실제 호출 | **미검증** — `llm_usage` 기록 없음 |
| OpenDART·KRX·ECOS·FRED | 설계만 있음, 미구현 |

실제 외부 서비스 연동을 확인하기 전까지는 이 저장소를 "근거 추적 설계와 테스트가 있는 MVP"로만 소개합니다.
