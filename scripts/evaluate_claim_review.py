"""Run a small, explicitly labelled Ollama review regression sample; save every response."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from research_automation.config import load_settings
from research_automation.llm.audit import write_json
from research_automation.llm.ollama import OllamaClient
from research_automation.llm.review import REVIEW_VERSION, review_claims
from research_automation.models import ClaimsEnvelope


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fixtures", type=Path, nargs="+",
        default=[Path("tests/fixtures/semantic_review_cases.json")],
    )
    args = parser.parse_args()
    fixtures = [json.loads(path.read_text()) for path in args.fixtures]
    groups = [group for fixture in fixtures for group in fixture["groups"]]
    settings = load_settings()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    directory = settings.project_root / "data" / "review_evaluations" / run_id
    receipt = {
        "run_id": run_id, "model": settings.ollama_model, "review_version": REVIEW_VERSION,
        "label_source": [fixture["label_source"] for fixture in fixtures],
        "fixtures": [{"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                     for path in args.fixtures],
        "results": [],
    }
    write_json(directory / "receipt.json", receipt)
    print(f"receipt={directory / 'receipt.json'}", flush=True)
    with OllamaClient(
        settings.ollama_model, settings.ollama_base_url, timeout_seconds=300,
        audit_dir=directory / "traces",
    ) as client:
        for group in groups:
            envelope = ClaimsEnvelope.model_validate({"claims": [case["claim"] for case in group["cases"]]})
            result = review_claims(client, envelope, group["source"])
            for decision in result.decisions:
                case = group["cases"][decision["claim_id"]]
                receipt["results"].append({
                    "group": group["id"], "case": case["id"], "expected": case["expected"],
                    "actual": decision["status"], "matches_expected": decision["status"] == case["expected"],
                    "reason": decision["reason"], "checks": decision.get("checks"),
                    "audit_path": str(result.audit_path),
                })
            write_json(directory / "receipt.json", receipt)
            print(json.dumps({"group": group["id"], "decisions": [
                {key: item[key] for key in ("case", "expected", "actual", "matches_expected")}
                for item in receipt["results"] if item["group"] == group["id"]
            ]}, ensure_ascii=False), flush=True)
    receipt["completed_at"] = datetime.now(timezone.utc).isoformat()
    receipt["matches"] = sum(item["matches_expected"] for item in receipt["results"])
    receipt["cases"] = len(receipt["results"])
    receipt["false_acceptances"] = sum(
        item["expected"] != "supported" and item["actual"] == "supported"
        for item in receipt["results"]
    )
    receipt["false_rejections"] = sum(
        item["expected"] == "supported" and item["actual"] == "rejected"
        for item in receipt["results"]
    )
    receipt["positive_holds"] = sum(
        item["expected"] == "supported" and item["actual"] == "held"
        for item in receipt["results"]
    )
    write_json(directory / "receipt.json", receipt)
    print(f"matches={receipt['matches']}/{receipt['cases']}", flush=True)


if __name__ == "__main__":
    main()
