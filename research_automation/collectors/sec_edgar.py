from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path, PurePosixPath
from typing import Any, Dict, Iterable, List, Optional

import httpx
from bs4 import BeautifulSoup, UnicodeDammit

from research_automation.models import FilingRef

SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


def declared_user_agent(value: str) -> str:
    """연락처만 설정한 경우에도 프로그램 이름을 포함해 SEC에 신원을 표시합니다."""
    value = value.strip()
    if (
        not value
        or "example.com" in value.casefold()
        or "\n" in value
        or "\r" in value
        or not re.search(r"[^\s<>]+@[^\s<>]+\.[^\s<>]+", value)
    ):
        raise ValueError("SEC_USER_AGENT에 실제 연락처 이메일을 설정해 주세요.")
    return value if any(character.isspace() for character in value) else f"ResearchClient/0.1 {value}"


def normalize_cik(cik: str) -> str:
    digits = re.sub(r"\D", "", cik)
    if not digits or len(digits) > 10:
        raise ValueError("CIK must contain 1 to 10 digits")
    return digits.zfill(10)


def filing_url(cik: str, accession_number: str, primary_document: str) -> str:
    cik_integer = str(int(normalize_cik(cik)))
    accession_compact = accession_number.replace("-", "")
    return f"{SEC_ARCHIVES_BASE}/{cik_integer}/{accession_compact}/{primary_document}"


def parse_recent_filings(
    payload: Dict[str, Any],
    forms: Optional[Iterable[str]] = None,
    limit: Optional[int] = None,
) -> List[FilingRef]:
    if limit is not None and limit < 1:
        raise ValueError("limit must be at least 1")
    cik = normalize_cik(str(payload["cik"]))
    company_name = str(payload.get("name", ""))
    recent = payload["filings"]["recent"]
    allowed = {item.strip().upper() for item in forms or [] if item.strip()}
    results: List[FilingRef] = []

    for index, form in enumerate(recent["form"]):
        normalized_form = str(form).upper()
        if allowed and normalized_form not in allowed:
            continue
        accession = str(recent["accessionNumber"][index])
        primary_document = str(recent["primaryDocument"][index])
        report_date = str(recent["reportDate"][index]).strip() or None
        results.append(
            FilingRef(
                cik=cik,
                company_name=company_name,
                accession_number=accession,
                form=normalized_form,
                filing_date=str(recent["filingDate"][index]),
                report_date=report_date,
                primary_document=primary_document,
                url=filing_url(cik, accession, primary_document),
            )
        )
        if limit is not None and len(results) >= limit:
            break
    return results


def html_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "noscript"]):
        element.decompose()
    text = soup.get_text("\n")
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def load_browser_filings(
    directory: Path,
    cik: str,
    forms: Optional[Iterable[str]] = None,
    limit: int = 20,
) -> List[FilingRef]:
    """브라우저에서 저장한 전체 submissions JSON을 읽습니다. 네트워크는 호출하지 않습니다."""
    expected_cik = normalize_cik(cik)
    path = directory / f"CIK{expected_cik}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise RuntimeError(f"SEC 공시 목록 파일을 읽을 수 없습니다: {path}") from exc
    except (json.JSONDecodeError, UnicodeError) as exc:
        raise ValueError("전체 SEC JSON 파일이 필요합니다. 일부 복사한 텍스트는 사용할 수 없습니다.") from exc
    try:
        if normalize_cik(str(payload["cik"])) != expected_cik:
            raise ValueError("저장된 SEC JSON의 CIK가 요청 기업과 다릅니다.")
        recent = payload["filings"]["recent"]
        keys = ("form", "accessionNumber", "primaryDocument", "filingDate", "reportDate")
        if not all(isinstance(recent[key], list) for key in keys):
            raise ValueError("SEC 최근 공시 목록은 배열이어야 합니다.")
        if len({len(recent[key]) for key in keys}) != 1:
            raise ValueError("SEC 공시 목록의 열 길이가 서로 다릅니다.")
        return parse_recent_filings(payload, forms=forms, limit=limit)
    except (KeyError, TypeError, AttributeError) as exc:
        raise ValueError("SEC submissions 형식의 JSON이 아닙니다.") from exc


def load_browser_document(directory: Path, reference: FilingRef) -> Dict[str, Any]:
    """Chrome의 '페이지 저장' 원본 URL을 공시 목록과 대조한 뒤 HTML을 정리합니다."""
    name = PurePosixPath(reference.primary_document).name
    candidates = (
        directory / f"{reference.accession_number}.html",
        directory / name,
        directory / Path(name).with_suffix(".html"),
    )
    root = directory.resolve()
    path = next((item for item in candidates if item.is_file()), None)
    if path is None:
        raise RuntimeError(
            f"저장된 {reference.form} 원문이 없습니다: {name}. "
            f"공식 URL을 브라우저에서 열어 이 폴더에 HTML로 저장해 주세요: {reference.url}"
        )
    if path.resolve().parent != root:
        raise ValueError("공시 원문 파일은 지정한 폴더 안에 있어야 합니다.")
    try:
        decoded = UnicodeDammit(path.read_bytes(), is_html=True)
    except OSError as exc:
        raise RuntimeError(f"SEC HTML 파일을 읽을 수 없습니다: {path.name}") from exc
    if decoded.unicode_markup is None or decoded.contains_replacement_characters:
        raise ValueError(f"{path.name}: HTML 문자 인코딩을 손실 없이 해석할 수 없습니다.")
    html = decoded.unicode_markup
    origin = re.search(r"<!--\s*saved from url=\(\d+\)(https://[^\s<>]+)\s*-->", html)
    if origin is None or origin.group(1) != reference.url:
        raise ValueError(
            f"{path.name}: 저장된 페이지의 원본 URL이 공시 목록과 일치하지 않습니다. "
            "Chrome에서 해당 공시를 '페이지 저장'으로 다시 저장해 주세요."
        )
    raw_text = html_to_text(html)
    if any(
        marker in raw_text.casefold()
        for marker in ("your request originates from an undeclared", "request rate threshold exceeded")
    ):
        raise ValueError(f"{path.name}: 공시 원문 대신 SEC 차단 페이지가 저장돼 있습니다.")
    soup = BeautifulSoup(html, "html.parser")
    document_type = soup.find(attrs={"name": "dei:DocumentType"})
    if document_type is not None and document_type.get_text(strip=True) != reference.form:
        raise ValueError(f"{path.name}: HTML의 공시 유형이 목록과 다릅니다.")
    if not raw_text:
        raise ValueError(f"{path.name}: 원문이 비어 있습니다.")
    period_label = f" ({reference.report_date})" if reference.report_date else ""
    return {
        "source": "sec_edgar_browser",
        "source_document_id": reference.accession_number,
        "url": reference.url,
        "title": f"{reference.company_name} {reference.form}{period_label}",
        "published_at": reference.filing_date,
        "company_id": reference.cik,
        "filing_type": reference.form,
        "period_end": reference.report_date,
        "content_hash": hashlib.sha256(raw_text.encode("utf-8")).hexdigest(),
        "raw_text": raw_text,
    }


class SECEdgarClient:
    def __init__(
        self,
        user_agent: str,
        timeout_seconds: float = 30.0,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        min_interval_seconds: float = 0.25,
    ) -> None:
        self._client = httpx.Client(
            headers={
                "User-Agent": declared_user_agent(user_agent),
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/html,*/*",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
            transport=transport,
        )
        self._interval = max(0.1, min_interval_seconds)
        self._last_request: Optional[float] = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SECEdgarClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _get(self, url: str) -> httpx.Response:
        if self._last_request is not None:
            delay = self._interval - (time.monotonic() - self._last_request)
            if delay > 0:
                time.sleep(delay)
        self._last_request = time.monotonic()
        try:
            response = self._client.get(url)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                raise RuntimeError(
                    "SEC가 HTTP 403을 반환했습니다. 이름·연락처를 표시해도 차단될 수 있습니다. "
                    "브라우저에서 저장한 자료는 --browser-dir로 읽을 수 있습니다. "
                    "README의 'SEC가 403을 반환할 때'를 참고하세요."
                ) from None
            raise RuntimeError(f"SEC 요청 실패: HTTP {exc.response.status_code}") from None
        except httpx.RequestError as exc:
            raise RuntimeError(f"SEC 연결 실패: {type(exc).__name__}") from None

    def get_submissions(self, cik: str) -> Dict[str, Any]:
        normalized = normalize_cik(cik)
        response = self._get(f"{SEC_DATA_BASE}/submissions/CIK{normalized}.json")
        return response.json()

    def list_filings(
        self, cik: str, forms: Optional[Iterable[str]] = None, limit: int = 20
    ) -> List[FilingRef]:
        return parse_recent_filings(self.get_submissions(cik), forms=forms, limit=limit)

    def fetch_document(self, reference: FilingRef) -> Dict[str, Any]:
        response = self._get(reference.url)
        raw_text = html_to_text(response.text)
        content_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        period_label = f" ({reference.report_date})" if reference.report_date else ""
        return {
            "source": "sec_edgar",
            "source_document_id": reference.accession_number,
            "url": reference.url,
            "title": f"{reference.company_name} {reference.form}{period_label}",
            "published_at": reference.filing_date,
            "company_id": reference.cik,
            "filing_type": reference.form,
            "period_end": reference.report_date,
            "content_hash": content_hash,
            "raw_text": raw_text,
        }
