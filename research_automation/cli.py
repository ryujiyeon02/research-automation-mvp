from __future__ import annotations

import argparse
import json
import sys
from typing import Iterable, List

from research_automation import db
from research_automation.collectors.sec_edgar import SECEdgarClient
from research_automation.config import Settings, load_settings
from research_automation.llm.clova import PROMPT_VERSION, ClovaClient, result_as_json


def _forms(value: str) -> List[str]:
    return [item.strip().upper() for item in value.split(",") if item.strip()]


def _require_sec(settings: Settings) -> None:
    if not settings.key_status()["SEC_USER_AGENT"]:
        raise RuntimeError("SEC_USER_AGENT를 .env에 실제 프로젝트명/이메일 형식으로 설정해 주세요.")


def command_init_db(settings: Settings, _args: argparse.Namespace) -> None:
    db.init_db(settings.database_path)
    print(f"Database initialized: {settings.database_path}")


def command_doctor(settings: Settings, _args: argparse.Namespace) -> None:
    status = settings.key_status()
    print(f"Project: {settings.project_root}")
    print(f"Database: {settings.database_path}")
    print("Secrets (values are never shown):")
    for name, configured in status.items():
        print(f"  {name}: {'configured' if configured else 'missing'}")


def command_sec_list(settings: Settings, args: argparse.Namespace) -> None:
    _require_sec(settings)
    with SECEdgarClient(settings.sec_user_agent) as client:
        filings = client.list_filings(args.cik, forms=args.forms, limit=args.limit)
    for filing in filings:
        print(
            json.dumps(
                {
                    "cik": filing.cik,
                    "company": filing.company_name,
                    "form": filing.form,
                    "filing_date": filing.filing_date,
                    "report_date": filing.report_date,
                    "accession_number": filing.accession_number,
                    "url": filing.url,
                },
                ensure_ascii=False,
            )
        )


def command_sec_ingest(settings: Settings, args: argparse.Namespace) -> None:
    _require_sec(settings)
    db.init_db(settings.database_path)
    stored = []
    with SECEdgarClient(settings.sec_user_agent) as client:
        filings = client.list_filings(args.cik, forms=args.forms, limit=args.limit)
        for filing in filings:
            document = client.fetch_document(filing)
            document_id = db.upsert_document(settings.database_path, document)
            stored.append((document_id, filing.form, filing.accession_number))
    for document_id, form, accession in stored:
        print(f"stored document_id={document_id} form={form} accession={accession}")


def command_extract_claims(settings: Settings, args: argparse.Namespace) -> None:
    if not settings.clova_api_key:
        raise RuntimeError("CLOVA_API_KEY를 .env에 설정해 주세요.")
    document = db.get_document(settings.database_path, args.document_id)
    if document is None:
        raise RuntimeError(f"document_id={args.document_id} 문서를 찾을 수 없습니다.")

    with ClovaClient(
        api_key=settings.clova_api_key,
        model=settings.clova_model,
        base_url=settings.clova_base_url,
    ) as client:
        result = client.extract_claims(document["raw_text"], max_chars=args.max_chars)

    inserted = db.insert_claims(
        settings.database_path,
        document_id=args.document_id,
        claims=result.envelope.claims,
        model=settings.clova_model,
        prompt_version=PROMPT_VERSION,
    )
    db.log_llm_usage(
        settings.database_path,
        provider="clova",
        model=settings.clova_model,
        operation="extract_causal_claims",
        document_id=args.document_id,
        prompt_version=PROMPT_VERSION,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
    )
    print(result_as_json(result))
    print(f"Inserted {inserted} new claims", file=sys.stderr)
    if result.rejected_claims:
        print(
            f"Rejected {result.rejected_claims} claims whose evidence was not found in the input",
            file=sys.stderr,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-auto", description="경제·공시 리서치 자동화 MVP"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_db_parser = subparsers.add_parser("init-db", help="SQLite 스키마 초기화")
    init_db_parser.set_defaults(handler=command_init_db)

    doctor_parser = subparsers.add_parser("doctor", help="설정 상태 확인")
    doctor_parser.set_defaults(handler=command_doctor)

    sec_list_parser = subparsers.add_parser("sec-list", help="SEC 최근 공시 목록 확인")
    sec_list_parser.add_argument("--cik", required=True)
    sec_list_parser.add_argument("--forms", type=_forms, default=_forms("10-K,10-Q,8-K"))
    sec_list_parser.add_argument("--limit", type=int, default=20)
    sec_list_parser.set_defaults(handler=command_sec_list)

    sec_ingest_parser = subparsers.add_parser("sec-ingest", help="SEC 공시 원문 저장")
    sec_ingest_parser.add_argument("--cik", required=True)
    sec_ingest_parser.add_argument("--forms", type=_forms, default=_forms("10-K,10-Q,8-K"))
    sec_ingest_parser.add_argument("--limit", type=int, default=3)
    sec_ingest_parser.set_defaults(handler=command_sec_ingest)

    extract_parser = subparsers.add_parser(
        "extract-claims", help="저장 문서에서 명시적 인과 주장 추출"
    )
    extract_parser.add_argument("--document-id", type=int, required=True)
    extract_parser.add_argument("--max-chars", type=int, default=12_000)
    extract_parser.set_defaults(handler=command_extract_claims)

    return parser


def main(argv: Iterable[str] = ()) -> None:
    parser = build_parser()
    parsed_argv = list(argv) if argv else None
    args = parser.parse_args(parsed_argv)
    settings = load_settings()
    try:
        args.handler(settings, args)
    except (RuntimeError, ValueError) as exc:
        parser.error(str(exc))
