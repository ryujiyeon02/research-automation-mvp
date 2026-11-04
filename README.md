# 경제·공시 리서치 자동화

국내외 경제 문서와 기업공시를 수집하고, 문서에 **명시된** 경제 변수 간 인과 주장을 근거 문장과 함께 구조화하는 읽기 전용 MVP입니다.

현재 구현 범위:

- `.env` 기반 비밀키 관리와 Git 노출 방지
- SQLite 문서·인과관계·시계열·시장가격·LLM 사용량 스키마
- SEC EDGAR 10-K·10-Q·8-K 목록 및 원문 수집
- Ollama `qwen3.5:9b` 기반 로컬 인과 주장 JSON 추출
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
OLLAMA_MODEL=qwen3.5:9b
OLLAMA_BASE_URL=http://127.0.0.1:11434
```

외부 LLM API 키는 필요하지 않습니다. 데이터 소스의 API 키는 이 대화나 GitHub에 올리지 않고 로컬 `.env`에만 입력합니다.

## 로컬 LLM 준비

[Ollama](https://ollama.com/download)를 설치한 뒤 모델을 한 번 받습니다.

```bash
ollama pull qwen3.5:9b
```

Ollama 앱 또는 `ollama serve`가 실행 중이면 `extract-claims`가 로컬 API를 호출합니다.

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

추출 명령은 Ollama를 로컬로 호출합니다. 결과에는 원인·결과·방향·전달경로·시차·확실성·원문 근거가 저장됩니다.

### SEC가 403을 반환할 때

연락처를 포함한 User-Agent와 올바른 API 주소를 사용해도 SEC 보안 필터가 요청을 거부할 수 있습니다. 현재 환경에서는 Chrome에서 같은 주소가 열리는 것을 확인했습니다. 수집기는 연락처만 설정한 경우 프로그램 이름을 자동으로 붙이며, 단일 클라이언트의 요청 간격은 기본 0.25초로 제한합니다. 여러 클라이언트를 병렬로 실행하지 마세요.

직접 호출이 차단되면 Chrome에서 공식 공시 목록 JSON과 필요한 공시 HTML을 같은 폴더에 저장한 뒤 가져올 수 있습니다. 이 모드는 네트워크를 호출하지 않으며 **저장 시점의 자료**를 읽습니다. 새 공시는 브라우저에서 파일을 갱신해야 하므로 무인 정기 수집과는 구분합니다.

1. 공시 목록: `https://data.sec.gov/submissions/CIK0000320193.json`을 전체 파일 `CIK0000320193.json`으로 저장합니다.
2. `sec-list --browser-dir`로 공식 원문 URL을 확인합니다.
3. 원문 URL을 Chrome에서 열고 '페이지 저장'으로 같은 폴더에 저장합니다. 파일명은 원래 이름(예: `aapl-20260926.html`) 또는 공시번호(예: `0000320193-26-000026.html`)를 사용합니다. 원본 URL 주석이 보존된 HTML이 필요합니다.
4. 저장한 유형과 개수에 맞춰 가져옵니다.

```bash
uv run python -m research_automation sec-list --cik 320193 \
  --forms 10-K,8-K --limit 2 --browser-dir data/sec_browser/2026-11-04

uv run python -m research_automation sec-ingest --cik 320193 \
  --forms 10-K,8-K --limit 2 --browser-dir data/sec_browser/2026-11-04
```

가져올 때 JSON의 CIK·배열 길이, HTML의 저장 원본 URL·공시 유형·차단 문구·인코딩을 확인합니다. 원본 URL 주석은 파일 간 일치 여부를 확인하는 수단이며 진본 인증 수단은 아닙니다. 모든 대상 파일을 검증한 뒤 DB에 기록합니다. 원문 파일은 `data/` 아래에 보존하고, DB에는 공식 URL·공시번호·발표일·정리된 전체 텍스트·SHA-256을 저장합니다. 수집 출처는 `sec_edgar_browser`로 표시해 직접 API 수집과 구분합니다.

실행 기록과 한계는 [docs/SEC_ACCESS.md](docs/SEC_ACCESS.md)에 있습니다.

## 메리츠증권 시세 수집 (읽기 전용)

`.env`에 `MERITZ_APP_KEY`, `MERITZ_APP_SECRET`을 넣으면 메리츠 Open API로 시세를 `market_prices`에, 투자자별 순매수를 `observations`에 저장합니다. 포털에 등록한 IP에서만 호출됩니다.

```bash
# 국내 일봉 (security_id = KRX:005930)
uv run python -m research_automation meritz-daily --symbol 005930 --start 2026-09-01
# 해외 일봉 (security_id = OQ:AAPL)
uv run python -m research_automation meritz-daily --symbol AAPL --exchange OQ --start 2026-09-01
# 해외 최근 분봉 (KST 시각으로 저장)
uv run python -m research_automation meritz-minutes --symbol AAPL --exchange OQ --interval 60
# 국내 개인·외국인·기관 순매수 (variable_id = KRX:005930:frgn_ntby_tr_pbmn 등)
uv run python -m research_automation meritz-investors --code 005930 --start 2026-09-01
```

- 메리츠 Open API는 모의계좌가 없어 키가 실계좌에 연결됩니다. 수집기는 허용한 조회 경로 4개와 토큰 발급 외의 요청을 네트워크에 보내기 전에 막습니다.
- 국내 분봉은 공식 명세상 빈 목록이나 1건만 내려와 수집하지 않습니다. 해외 분봉은 기간 지정이 없어 과거 공시 반응을 보려면 미리 쌓아 두어야 합니다.
- 일봉 공식값은 KRX를 기준으로 삼고, 메리츠 일봉은 대조·보조용으로 씁니다 (2026-09 CBT 중 일부 종가 불일치 보고).

## 설계 원칙

1. 수집·정제·수치 계산은 Python으로 처리합니다.
2. LLM은 새로운 중요 문서의 구조화 추출과 최종 브리핑에만 사용합니다.
3. 원문에 없는 연결고리를 추론해 저장하지 않습니다.
4. 뉴스나 보고서의 주장은 `reported_claim`으로 저장하며, 계량적으로 입증된 인과관계로 표현하지 않습니다.
5. API 사용량과 모델·프롬프트 버전을 함께 기록합니다.

세부 구조는 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), 데이터 소스 범위는 [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md)를 참고하세요.

## 현재 검증 범위 (2026-11-04 기준)

| 항목 | 상태 |
| --- | --- |
| 단위 테스트 | `uv run pytest` 31개 통과. 외부 서버 응답을 대신한 테스트이며, 실제 호출 결과와 별도 |
| 근거 없는 주장 폐기 | LLM이 반환한 `evidence_text`가 원문에 없으면 버리는 필터를 테스트로 확인 (`tests/test_ollama.py`) |
| 비밀키 노출 방지 | `doctor` 명령이 키 값이 아니라 설정 여부만 반환하는지 테스트로 확인 |
| SEC 직접 HTTP 호출 | **차단 확인** — 2026-11-04 신원 헤더를 포함한 JSON·원문·HTTP/2·공식 일괄 자료 요청에서 403. 정확한 차단 조건은 미확인 |
| SEC 브라우저 자료 가져오기 | **실제 검증** — Chrome에서 공시 목록 1,002건을 읽고 최신 Apple 10-K·8-K 전체 원문을 저장, DB 문서 ID 6·7로 가져오기 및 해시 대조 완료. 브라우저 저장 단계는 운영자의 조작 또는 Codex 브라우저 도구 필요 |
| Ollama `qwen3.5:9b` 실제 호출 | **과거 실행 확인** — 2026-10-04~05 실행 기록과 추출 결과가 남아 있음. 이후 용량 확보를 위해 프로그램·모델을 삭제했으므로 현재 로컬 추출 실행 불가. 정확도 벤치마크는 아님 |
| 메리츠 Open API 수집기 | **실제 조회 검증** — 2026-11-04 토큰 발급, 삼성전자 일봉 2건·Apple 일봉 2건·삼성전자 수급 12건 정상 수신(조회 기간 11월 2~3일). 해당 실행은 DB에 저장하지 않음. 해외 분봉 실제 호출은 미검증 |
| OpenDART·KRX·ECOS·FRED | 설계만 있음, 미구현 |

전체 뉴스 자동 수집·ECOS/FRED 교차검증·브리핑 정기 생성은 아직 미구현입니다. 기존 브리핑의 `analyst_reviewed`는 당시 Codex가 붙인 검토 상태이며 사용자 또는 독립 분석가의 검수를 증명하지 않습니다. 구현·실행·검증 범위를 구분한 MVP로 소개합니다.
