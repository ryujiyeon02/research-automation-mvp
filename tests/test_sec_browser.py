import hashlib
import json
from pathlib import Path

import httpx
import pytest

from research_automation import cli, db
from research_automation.collectors import sec_edgar
from research_automation.collectors.sec_edgar import (
    SECEdgarClient,
    declared_user_agent,
    load_browser_document,
    load_browser_filings,
    parse_recent_filings,
)
from research_automation.config import load_settings

FIXTURE = Path(__file__).parent / "fixtures/sec_submissions.json"


def saved_fixture(directory):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    (directory / "CIK0000320193.json").write_text(json.dumps(payload), encoding="utf-8")
    reference = parse_recent_filings(payload, forms=["10-Q"], limit=1)[0]
    html = (
        f"<!-- saved from url=({len(reference.url):04d}){reference.url} -->"
        '<html><body><ix:nonNumeric name="dei:DocumentType">10-Q</ix:nonNumeric>'
        "<p>FORM 10-Q</p><p>Margin fell because component costs rose.</p>"
        "<script>ignored()</script></body></html>"
    )
    path = directory / Path(reference.primary_document).with_suffix(".html")
    path.write_text(html, encoding="utf-8")
    return payload, reference, path


def test_email_only_agent_includes_program_name():
    assert declared_user_agent("contact@sample.org") == "ResearchClient/0.1 contact@sample.org"
    assert declared_user_agent("ResearchClient contact@sample.org") == "ResearchClient contact@sample.org"
    with pytest.raises(ValueError):
        declared_user_agent("ResearchClient")


def test_live_client_sends_declared_agent_and_stops_on_403():
    calls = []

    def respond(request):
        calls.append(request)
        assert request.headers["user-agent"] == "ResearchClient/0.1 contact@sample.org"
        return httpx.Response(403, text="Your Request Originates from an Undeclared Automated Tool")

    with (
        SECEdgarClient("contact@sample.org", transport=httpx.MockTransport(respond)) as client,
        pytest.raises(RuntimeError, match="--browser-dir") as error,
    ):
        client.get_submissions("320193")
    assert len(calls) == 1
    assert "contact@sample.org" not in str(error.value)


def test_client_throttles_between_requests(monkeypatch):
    times = iter([0.0, 0.01, 0.25])
    waits = []
    monkeypatch.setattr(sec_edgar.time, "monotonic", lambda: next(times))
    monkeypatch.setattr(sec_edgar.time, "sleep", waits.append)
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
    with SECEdgarClient("contact@sample.org", transport=transport) as client:
        client.get_submissions("320193")
        client.get_submissions("320193")
    assert waits == [pytest.approx(0.24)]


def test_saved_json_matches_cik_and_filters_filings(tmp_path):
    saved_fixture(tmp_path)
    filings = load_browser_filings(tmp_path, "320193", forms=["10-Q"], limit=1)
    assert len(filings) == 1
    assert filings[0].form == "10-Q"
    with pytest.raises(ValueError, match="CIK"):
        (tmp_path / "CIK0000789019.json").write_text(
            (tmp_path / "CIK0000320193.json").read_text(), encoding="utf-8"
        )
        load_browser_filings(tmp_path, "789019")


def test_saved_json_rejects_partial_copy_and_misaligned_arrays(tmp_path):
    path = tmp_path / "CIK0000320193.json"
    path.write_text('{"cik":"0000320193",', encoding="utf-8")
    with pytest.raises(ValueError, match="전체 SEC JSON"):
        load_browser_filings(tmp_path, "320193")
    payload = json.loads(FIXTURE.read_text())
    payload["filings"]["recent"]["form"].append("10-K")
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="열 길이"):
        load_browser_filings(tmp_path, "320193")


def test_saved_html_preserves_canonical_source_and_content_hash(tmp_path):
    _, reference, _ = saved_fixture(tmp_path)
    document = load_browser_document(tmp_path, reference)
    assert document["source"] == "sec_edgar_browser"
    assert document["url"] == reference.url
    assert document["source_document_id"] == reference.accession_number
    assert "Margin fell because component costs rose." in document["raw_text"]
    assert "ignored" not in document["raw_text"]
    assert document["content_hash"] == hashlib.sha256(document["raw_text"].encode()).hexdigest()


def test_saved_html_rejects_wrong_origin(tmp_path):
    _, reference, path = saved_fixture(tmp_path)
    path.write_text(path.read_text().replace(reference.url, "https://example.org/filing.html"))
    with pytest.raises(ValueError, match="원본 URL"):
        load_browser_document(tmp_path, reference)


def test_saved_html_supports_browser_windows_1252_encoding(tmp_path):
    _, reference, path = saved_fixture(tmp_path)
    html = path.read_text().replace(
        "<html>", '<html><head><meta charset="windows-1252"></head>'
    ).replace("FORM 10-Q</p>", "FORM 10-Q — cost €100</p>")
    path.write_bytes(html.encode("windows-1252"))
    document = load_browser_document(tmp_path, reference)
    assert "— cost €100" in document["raw_text"]


def test_saved_html_rejects_block_page(tmp_path):
    _, reference, path = saved_fixture(tmp_path)
    path.write_text(
        f"<!-- saved from url=({len(reference.url):04d}){reference.url} -->"
        "<h1>Your Request Originates from an Undeclared Automated Tool</h1>"
    )
    with pytest.raises(ValueError, match="차단 페이지"):
        load_browser_document(tmp_path, reference)


def test_saved_html_rejects_wrong_document_type(tmp_path):
    _, reference, path = saved_fixture(tmp_path)
    path.write_text(path.read_text().replace(">10-Q</ix:nonNumeric>", ">8-K</ix:nonNumeric>"))
    with pytest.raises(ValueError, match="공시 유형"):
        load_browser_document(tmp_path, reference)


def test_browser_cli_import_needs_no_network_and_is_idempotent(tmp_path, monkeypatch, capsys):
    saved_fixture(tmp_path)
    database = tmp_path / "research.db"
    monkeypatch.setenv("RESEARCH_DB_PATH", str(database))
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(tmp_path))

    def no_network(*args, **kwargs):
        pytest.fail("browser-dir must not instantiate the HTTP client")

    monkeypatch.setattr(cli, "SECEdgarClient", no_network)
    common = ["--cik", "320193", "--forms", "10-Q", "--limit", "1", "--browser-dir", str(tmp_path)]
    cli.main(["sec-list", *common])
    cli.main(["sec-ingest", *common])
    cli.main(["sec-ingest", *common])
    with db.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    captured = capsys.readouterr()
    assert "source=browser_saved_snapshot" in captured.err
    assert "stored document_id=" in captured.out


def test_browser_import_validates_all_files_before_creating_database(tmp_path, monkeypatch):
    saved_fixture(tmp_path)
    database = tmp_path / "research.db"
    monkeypatch.setenv("RESEARCH_DB_PATH", str(database))
    monkeypatch.setattr(cli, "load_settings", lambda: load_settings(tmp_path))
    with pytest.raises(SystemExit):
        cli.main(["sec-ingest", "--cik", "320193", "--forms", "10-Q,8-K",
                  "--limit", "2", "--browser-dir", str(tmp_path)])
    assert not database.exists()
