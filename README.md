# 경제·공시 리서치 자동화

국내외 경제 문서와 기업공시를 수집하고, 문서에 **명시된** 경제 변수 간 인과 주장을 근거 문장과 함께 구조화하는 읽기 전용 MVP입니다.

현재 구현 범위:

- `.env` 기반 비밀키 관리와 Git 노출 방지
- SQLite 문서·인과관계·시계열·시장가격·LLM 사용량 스키마
- SEC EDGAR 10-K·10-Q·8-K 목록 및 원문 수집
- Ollama `qwen3.5:9b` 기반 로컬 인과 주장 JSON 추출
- 원본 응답·제외 주장 보존과 별도 Ollama 의미 검토, 불명확한 결과의 자동 보류
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

2026-11-04 재설치한 모델은 약 7.64 GB입니다. 현재 Homebrew 설치본은 JSON Schema 강제 생성 요청에 501을 반환하므로, 해당 오류에만 스키마를 프롬프트에 넣어 한 번 재요청합니다. 이때도 JSON 자료형 검증과 원문 근거 일치 검증을 모두 통과해야 저장합니다. 실행 방식은 `validated_json`으로 기록하며 강제 생성과 구분합니다.

## 날짜별 리서치 파일

[2026-11-05 BEA 무역 발표 보완·로컬 모델 실험](reports/2026-11-05_trade_reading_check.md): 11월 4일에 빠뜨린 국제무역 원문을 사후 수집했습니다. 기존 `qwen3.5:9b`로 읽기 실험 1회를 수행해 헤드라인 수치 일치와 통계 기준 혼동 오류를 함께 확인했습니다. 모델 전체 정확도를 측정한 벤치마크는 아닙니다. BEA 발표 일정 전체 자동 발견은 아직 미구현입니다.

[2026-11-04 브리핑](reports/2026-11-04_daily_macro_brief.md)은 중간선거 보도, 통화정책, Apple·AMD 실적과 증권사 시세 조회를 함께 검토한 실행 사례입니다. 구조화 추출은 로컬 모델이 담당했고 최종 보고서의 취합과 오류 검토는 Codex가 수행했습니다. 사용자 검수나 무인 정기 생성으로 표시하지 않습니다.

[이차전지·ESS·전력설비 추가 보고서](reports/2026-11-04_sector_brief.md)는 대표 종목 11개와 비교 ETF 2개의 1·5·20·60거래일 가격 흐름을 공식 기업 실적과 함께 정리합니다. 초기 가격 차이의 주원인이 정규장·애프터마켓 기준 혼용임을 확인해 정규장 기준으로 정정했습니다. [가격 계산 결과](reports/2026-11-04_sector_metrics.json)에는 남은 조정가격 차이도 공개합니다. 대상 목록은 `reports/2026-11-04_sector_universe.json`에서 관리합니다.

[수급 추가 보고서](reports/2026-11-04_flow_brief.md)는 최초 보고서의 누락을 보완한 실제 실행 결과입니다. 종목 11개의 20거래일 자료를 메리츠·한투에서 각각 1,320개 관측값으로 저장했습니다. 개인·기관의 수량·금액 일치와 외국인 분류 차이를 구분합니다. [일별 금액·집계 결과](reports/2026-11-04_sector_flows.json)를 함께 보관합니다.

명시된 공개 출처를 다시 수집하고 분석하려면 프로젝트 폴더에서 다음을 실행합니다. 기존 Apple 문서 ID는 각자의 DB에 맞게 출처 목록을 수정해야 합니다. 일부 사이트가 403을 반환하면 실패 기록을 남기고 다음 출처로 넘어갑니다.

```bash
uv run python -m scripts.research_pilot reports/2026-11-04_sources.json --stage ingest
uv run python -m scripts.research_pilot reports/2026-11-04_sources.json --stage extract
```

추가 업종 자료 수집은 다음과 같이 실행합니다. 수집기 실행은 최종 보고서 작성·의미 검토를 포함하지 않습니다.

```bash
uv run python -m scripts.sector_prices reports/2026-11-04_sector_universe.json
uv run python -m scripts.sector_flows reports/2026-11-04_sector_universe.json
uv run python -m scripts.research_pilot reports/2026-11-04_sector_sources.json --stage ingest
uv run python -m scripts.research_pilot reports/2026-11-04_sector_sources.json --stage extract
```

원문과 상세 실행 기록은 Git에서 제외한 `data/research_runs/2026-11-04/`에 저장합니다. 공개 가능한 실행 수치와 제외 이유는 [실행 요약](reports/2026-11-04_run_summary.json)에 있습니다. 날짜별 실행기는 HTTP 공개 문서 수집과 추출만 담당하며, 증권사 조회와 최종 브리핑 작성은 별도로 수행했습니다.

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

2026-11-05부터 근거 문구 검사 후 별도 Ollama 호출로 원인·결과, 방향, 대상, 조건·시차, 전망 여부, 전달 경로를 검토합니다.
기본 동작은 후보와 검토 기록을 Git에서 제외한 `data/llm_runs/`에 보존하는 데까지입니다. 검토 모델이 기존 오해석을 통과시킨 실제 사례가 있어 DB에 자동 반영하지 않습니다.
`--store-model-supported`를 명시한 경우에만 모델 통과 후보를 미검증 상태인 `reported_claim`으로 DB에 넣습니다.
검토 모델도 틀릴 수 있으며 원문의 경제적 인과를 입증하는 기능은 아닙니다. [처리 과정과 한계](docs/CLAIM_REVIEW.md)를 참고하세요.

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

국내 일봉은 `TJ`와 `exch_use_yn=Y`로 **KRX 정규장 가격**을 명시해 조회합니다. 거래량은 KRX·NXT 통합 범위입니다. 기존 `J` 조회는 애프터 포함 마지막 체결가를 반환하므로 정규장 일봉과 분리합니다. 출처에도 정규장·수정 여부를 기록합니다. [실제 응답 대조와 남은 차이](docs/PRICE_SESSIONS.md)

투자자별 순매수는 기간 요청이 누적값을 반환하는 것을 실제로 확인했습니다. `meritz-investors`는 하루씩 `from=to`로 조회하며 날짜가 다르거나 필수 값이 없으면 저장하지 않습니다. `scripts.sector_flows`는 가격 자료에 있는 최근 20거래일만 요청합니다. 메리츠 일별 수급의 출처는 `meritz_krx_investor_daily`입니다. 이 스크립트는 메리츠 수집을 재현하며 보고서에 사용한 한투 대조·해석까지 자동 실행하지는 않습니다.

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

## 현재 검증 범위 (2026-11-05 기준)

| 항목 | 상태 |
| --- | --- |
| 단위·통합 테스트 | `uv run pytest` 60개 통과. 외부 서버 응답을 대신한 테스트이며, 실제 모델의 의미 판정 품질과 별도 |
| 투자자별 수급 조회·저장·분석 | **실제 실행 완료** — 최초 보고서에서 누락 후 보완. 11종목 × 20거래일 × 6항목을 메리츠·한투에서 각각 1,320건 저장하고 1·5·20거래일 합계 분석. 외국인 분류 차이·집계 범위는 수급 보고서에 공개 |
| 근거 없는 주장 폐기 | LLM이 반환한 `evidence_text`가 원문에 없으면 버리는 필터를 테스트로 확인 (`tests/test_ollama.py`) |
| 원본 보존·의미 검토 | 6항목 검토·인용문 검사·오류 시 1회 재시도 후 보류. 기본은 후보 파일 보존만 수행. 실제 7개 표본의 기대 판정 일치 4개, 기존 제외 주장 1개를 통과시켜 자동 DB 반영은 기본 해제. [실행 결과](reports/2026-11-05_semantic_review_check.md) |
| 비밀키 노출 방지 | `doctor` 명령이 키 값이 아니라 설정 여부만 반환하는지 테스트로 확인 |
| SEC 직접 HTTP 호출 | **차단 확인** — 2026-11-04 신원 헤더를 포함한 JSON·원문·HTTP/2·공식 일괄 자료 요청에서 403. 정확한 차단 조건은 미확인 |
| SEC 브라우저 자료 가져오기 | **실제 검증** — Chrome에서 공시 목록 1,002건을 읽고 최신 Apple 10-K·8-K 전체 원문을 저장, DB 문서 ID 6·7로 가져오기 및 해시 대조 완료. 브라우저 저장 단계는 운영자의 조작 또는 Codex 브라우저 도구 필요 |
| Ollama `qwen3.5:9b` 실제 호출 | **재설치 후 실행 확인** — 2026-11-04 문서 5건 분석. 원문 근거 불일치 1개 자동 제외 후 14개 저장, Codex 의미 검토에서 3개 추가 제외. 나머지 11개는 `reported_claim`. 정확도 벤치마크는 아님 |
| 메리츠 Open API 수집기 | **실제 조회·저장 확인** — 2026-11-04 삼성전자·SK하이닉스·AAPL·AMD·NVDA·SPY 일봉 14건 DB 저장. 국내 11월 2~4일, 미국 11월 2~3일. 해외 분봉 실제 호출은 미검증 |
| 한투 국내 일봉 대조 | **종가 기준 차이 확인** — 삼성전자·SK하이닉스는 메리츠 `TJ/Y` 정규장 재조회로 한투와 일치. 업종 표본 13개 중 10개는 75일 전체 일치. 나머지의 조정가격 차이·LS ELECTRIC 당일 100원 차이는 별도 공개 |
| OpenDART·KRX·ECOS·FRED | 설계만 있음, 미구현 |

전체 뉴스 자동 수집·ECOS/FRED 교차검증·브리핑 정기 생성은 아직 미구현입니다. 기존 브리핑의 `analyst_reviewed`는 당시 Codex가 붙인 검토 상태이며 사용자 또는 독립 분석가의 검수를 증명하지 않습니다. 구현·실행·검증 범위를 구분한 MVP로 소개합니다.
