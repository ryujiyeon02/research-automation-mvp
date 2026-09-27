import json
from pathlib import Path

from research_automation.collectors.sec_edgar import (
    filing_url,
    html_to_text,
    normalize_cik,
    parse_recent_filings,
)

FIXTURE = Path(__file__).parent / "fixtures/sec_submissions.json"


def test_normalize_cik() -> None:
    assert normalize_cik("320193") == "0000320193"


def test_parse_recent_filings_filters_forms() -> None:
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    filings = parse_recent_filings(payload, forms=["10-Q"], limit=10)
    assert len(filings) == 1
    assert filings[0].form == "10-Q"
    assert filings[0].cik == "0000320193"
    assert filings[0].url == filing_url("0000320193", "0000320193-26-000001", "aapl-20251227.htm")


def test_html_to_text_removes_scripts_and_normalizes_lines() -> None:
    html = "<html><script>ignore()</script><body><p>Hello   world</p><p>Next line</p></body></html>"
    assert html_to_text(html) == "Hello world\nNext line"
