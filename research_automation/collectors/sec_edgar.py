from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Iterable, List, Optional

import httpx
from bs4 import BeautifulSoup

from research_automation.models import FilingRef

SEC_DATA_BASE = "https://data.sec.gov"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"


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


class SECEdgarClient:
    def __init__(self, user_agent: str, timeout_seconds: float = 30.0) -> None:
        if not user_agent or "example.com" in user_agent:
            raise ValueError("Set SEC_USER_AGENT to a real project/name and contact email")
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept-Encoding": "gzip, deflate",
                "Accept": "application/json,text/html,*/*",
            },
            timeout=timeout_seconds,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SECEdgarClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def get_submissions(self, cik: str) -> Dict[str, Any]:
        normalized = normalize_cik(cik)
        response = self._client.get(f"{SEC_DATA_BASE}/submissions/CIK{normalized}.json")
        response.raise_for_status()
        return response.json()

    def list_filings(
        self, cik: str, forms: Optional[Iterable[str]] = None, limit: int = 20
    ) -> List[FilingRef]:
        return parse_recent_filings(self.get_submissions(cik), forms=forms, limit=limit)

    def fetch_document(self, reference: FilingRef) -> Dict[str, Any]:
        response = self._client.get(reference.url)
        response.raise_for_status()
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
