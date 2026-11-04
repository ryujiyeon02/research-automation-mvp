# SEC 수집 복구 기록 — 2026-11-04

## 확인된 현상

- 동일한 Apple submissions 주소가 사용자 Chrome에서는 열립니다.
- Codex 명령 실행 경로의 Python·curl 요청은 연락처와 프로그램 이름을 포함해도 HTTP 403을 반환합니다.
- 공식 예시 형식, 괄호 형식, HTTP/2 요청을 비교했지만 결과는 같습니다. 공식 bulk submissions ZIP의 HEAD 요청도 403이었습니다.
- 응답 제목은 `SEC.gov | Your Request Originates from an Undeclared Automated Tool`이며, JSON 요청의 서버 헤더는 `AkamaiGHost`였습니다.
- 사용자 브라우저와 프로그램의 요청/연결/접속 경로 차이 중 정확히 무엇이 차단을 유발하는지는 확정하지 못했습니다. IP 전체 차단이나 User-Agent 누락으로 단정하지 않습니다.
- 일반 Terminal 앱을 통한 추가 비교는 컴퓨터 사용 도구가 허용하지 않아 실행하지 않았습니다. 다른 방법으로 해당 제한을 우회하지 않았습니다.

## 실제로 동작하는 경로

`공식 SEC 페이지를 Chrome으로 열기 → 원본 파일 저장 → --browser-dir로 검사·정리 → SQLite 저장`

이번 실행에서 Codex 브라우저 도구로 Chrome 페이지를 읽고 '페이지 저장'을 수행했습니다. CAPTCHA 해결, TLS 검증 해제, 브라우저 보안 설정 변경, 프록시 교체, 외부 유료 데이터 서비스는 사용하지 않았습니다.

목록 파일은 `data/sec_browser/2026-11-04/CIK0000320193.json`이며 최근 공시 1,002건을 포함합니다. 일부 복사한 발췌문이 아닌 전체 JSON을 사용했습니다.

| 자료 | 발표일 | 원본 저장 크기 | DB 텍스트 글자 수 | 문서 ID |
|---|---|---:|---:|---:|
| Apple 10-K, 0000320193-26-000026 | 2026-11-03 | 1,677,077바이트 | 246,979 | 6 |
| Apple 8-K, 0000320193-26-000024 | 2026-11-02 | 43,390바이트 | 3,987 | 7 |

두 HTML은 Windows-1252 인코딩이었습니다. UTF-8로 가정하면 해석에 실패하므로 HTML 인코딩을 판별하고 문자 손실 여부를 확인하도록 수정했습니다. 정리된 텍스트에서 다시 계산한 SHA-256이 DB 값과 일치하는 것을 확인했습니다.

원본 HTML·부속 파일·JSON과 DB는 Git에서 제외된 `data/` 아래에 있습니다. 브라우저 출처는 DB의 `source=sec_edgar_browser`로 구분합니다. 기존 발췌 문서와 추출 결과는 변경하지 않았습니다.

## 실행 명령

```bash
uv run python -m research_automation sec-list --cik 320193 \
  --forms 10-K,8-K,10-Q --limit 3 --browser-dir data/sec_browser/2026-11-04

uv run python -m research_automation sec-ingest --cik 320193 \
  --forms 10-K,8-K --limit 2 --browser-dir data/sec_browser/2026-11-04
```

같은 폴더를 다시 가져와도 문서 수는 늘어나지 않습니다. 저장하지 않은 공시가 선택되면 해당 파일을 먼저 저장해야 합니다.

## 검증 범위와 한계

- 브라우저 저장을 포함한 무인 정기 실행은 구현하지 않았습니다. 새 공시를 읽으려면 저장 파일을 갱신해야 합니다.
- 이 경로는 직접 HTTP API의 403을 해제한 것이 아닙니다.
- `--browser-dir`는 저장 시점의 자료를 읽으며 네트워크를 호출하지 않습니다. 최신 자료라고 자동 간주하지 않습니다.
- HTML의 저장 원본 URL 주석·공시 유형을 JSON과 대조합니다. 다만 주석은 수정할 수 있으므로 진본임을 증명하는 전자서명은 아닙니다.
- 이번에는 문서 수집·저장까지 실행했습니다. 삭제한 Ollama를 재설치하지 않았고 새 AI 분석도 실행하지 않았습니다.

## 공식 자료

- [SEC 공시 데이터 접근 안내](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data)
- [SEC 공개 API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
- [Apple 최신 10-K 원문](https://www.sec.gov/Archives/edgar/data/320193/000032019326000026/aapl-20260926.htm)
- [Apple 최신 8-K 원문](https://www.sec.gov/Archives/edgar/data/320193/000032019326000024/aapl-20261102.htm)
