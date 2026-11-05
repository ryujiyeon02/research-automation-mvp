"""명시한 출처만 가져오는 리서치 실행기. 예약 실행이나 투자 주문은 하지 않습니다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, UnicodeDammit

from research_automation import db
from research_automation.config import load_settings
from research_automation.llm import ollama
from research_automation.llm.review import REVIEW_VERSION, extract_reviewed_claims


def excerpt(text, start=None, end=None):
    if start:
        match = re.search(r"\s+".join(re.escape(part) for part in start.split()), text)
        if match is None:
            raise ValueError("입력 문서에서 지정한 시작 문장을 찾을 수 없습니다.")
        text = text[match.start():]
    if end:
        position = text.find(end, len(start or ""))
        if position >= 0:
            text = text[:position]
    return text.strip()


def ingest(manifest, settings, output):
    db.init_db(settings.database_path)
    saved = []
    with httpx.Client(
        timeout=25, follow_redirects=True, headers={"User-Agent": "ResearchClient/1.0"}
    ) as client:
        for item in manifest["sources"]:
            try:
                response = client.get(item["url"])
                response.raise_for_status()
                decoded = UnicodeDammit(response.content, is_html=True)
                if decoded.unicode_markup is None or decoded.contains_replacement_characters:
                    raise ValueError("문자 인코딩을 손실 없이 해석할 수 없습니다.")
                soup = BeautifulSoup(decoded.unicode_markup, "html.parser")
                for node in soup(["script", "style", "noscript"]):
                    node.decompose()
                text = "\n".join(
                    " ".join(line.split())
                    for line in soup.get_text("\n").splitlines()
                    if line.strip()
                )
                text = excerpt(text, item.get("start"), item.get("end"))
                document = {
                    "source": item["source"],
                    "source_document_id": item["id"],
                    "url": item["url"],
                    "title": item["title"],
                    "published_at": item["published_at"],
                    "company_id": item.get("company_id"),
                    "filing_type": item.get("filing_type"),
                    "period_end": None,
                    "content_hash": hashlib.sha256(text.encode()).hexdigest(),
                    "raw_text": text,
                }
                document_id = db.upsert_document(settings.database_path, document)
                raw_path = output / (item["id"] + ".html")
                raw_path.write_bytes(response.content)
                saved.append({
                    "document_id": document_id,
                    "label": item["title"],
                    "url": item["url"],
                    "extract": item.get("extract", False),
                    "text_characters": len(text),
                    "raw_file": str(raw_path),
                    "original_file_sha256": hashlib.sha256(response.content).hexdigest(),
                    "collection_method": "http_html_body_excerpt",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                })
                print(json.dumps({"stored": document_id, "source": item["id"], "characters": len(text)}), flush=True)
            except (httpx.HTTPError, ValueError) as error:
                saved.append({"label": item["title"], "url": item["url"], "error_type": type(error).__name__})
                print(json.dumps({"failed": item["id"], "error_type": type(error).__name__}), flush=True)
    for item in manifest.get("existing_documents", []):
        if db.get_document(settings.database_path, item["document_id"]) is None:
            raise ValueError("지정한 기존 문서가 없습니다.")
        saved.append(item)
    return saved


def extract_claims(items, settings, output, store_model_supported=False):
    # 이번 실행의 출력 개수 제한을 프롬프트 버전에도 기록한다.
    ollama.SYSTEM_PROMPT += "\n중요한 경제 인과 주장만 최대 4개 추출하세요."
    prompt_version = ollama.PROMPT_VERSION + "-max4-pilot"
    results = []
    with ollama.OllamaClient(
        model=settings.ollama_model, base_url=settings.ollama_base_url, timeout_seconds=300,
        audit_dir=settings.project_root / "data" / "llm_runs",
    ) as client:
        for item in items:
            if not item.get("extract") or "document_id" not in item:
                continue
            document = db.get_document(settings.database_path, item["document_id"])
            text = excerpt(document["raw_text"], item.get("start"), item.get("end"))
            try:
                result, reviewed = extract_reviewed_claims(
                    client, text, max_chars=2_400,
                    source_reference={key: document[key] for key in (
                        "id", "source", "source_document_id", "url", "content_hash",
                    )},
                )
                actual_prompt_version = prompt_version + "-" + result.output_mode + "-" + REVIEW_VERSION
                inserted = db.insert_claims(
                    settings.database_path, item["document_id"], reviewed.envelope.claims,
                    settings.ollama_model, actual_prompt_version,
                ) if store_model_supported else 0
                db.log_llm_usage(
                    settings.database_path, "ollama", settings.ollama_model,
                    "review_causal_claims", item["document_id"], REVIEW_VERSION,
                    reviewed.input_tokens, reviewed.output_tokens,
                )
                db.log_llm_usage(
                    settings.database_path, "ollama", settings.ollama_model,
                    "extract_causal_claims", item["document_id"], actual_prompt_version,
                    result.input_tokens, result.output_tokens,
                )
                record = {
                    "document_id": item["document_id"],
                    "label": item["label"],
                    "output_mode": result.output_mode,
                    "inserted": inserted,
                    "storage_mode": "reported_claim" if store_model_supported else "candidate_only",
                    "grounded_claims": len(result.envelope.claims),
                    "model_supported_claims": len(reviewed.envelope.claims),
                    "semantic_decisions": reviewed.decisions,
                    "audit_path": str(reviewed.audit_path),
                    "rejected_missing_evidence": result.rejected_claims,
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "claims": reviewed.envelope.model_dump()["claims"],
                }
            except (httpx.HTTPError, ValueError, RuntimeError) as error:
                record = {"document_id": item["document_id"], "label": item["label"], "error_type": type(error).__name__}
            results.append(record)
            (output / "extraction.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
            print(json.dumps({key: value for key, value in record.items() if key != "claims"}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--stage", choices=("ingest", "extract"), required=True)
    parser.add_argument("--store-model-supported", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    manifest = json.loads(args.manifest.read_text())
    output = settings.project_root / "data" / "research_runs" / manifest.get("run_id", manifest["research_date"])
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "ingest.json"
    if args.stage == "ingest":
        receipt.write_text(json.dumps(ingest(manifest, settings, output), ensure_ascii=False, indent=2))
    else:
        extract_claims(json.loads(receipt.read_text()), settings, output, args.store_model_supported)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError) as error:
        print(type(error).__name__, file=sys.stderr)
        raise SystemExit(1)
