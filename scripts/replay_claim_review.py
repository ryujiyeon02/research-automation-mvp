"""Re-run a review receipt using saved HTTP responses; never contact a model server."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path

import httpx

from research_automation.llm.audit import write_json
from research_automation.llm.ollama import OllamaClient
from research_automation.llm.review import REVIEW_VERSION, review_claims
from research_automation.models import ClaimsEnvelope


def key(payload):
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    args = parser.parse_args()
    original = json.loads(args.receipt.read_text())
    directory = Path("data/review_replays") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    output = {"source_receipt": str(args.receipt.resolve()), "version": REVIEW_VERSION,
              "model": original["model"], "review_version": REVIEW_VERSION,
              "new_model_calls": 0, "results": [], "replayed_requests": 0}
    grouped = defaultdict(list)
    for item in original["results"]:
        grouped[item["group"]].append(item)
    for group, cases in grouped.items():
        trace = json.loads(Path(cases[0]["audit_path"]).read_text())
        responses = defaultdict(deque)
        for name in trace["review_audits"]:
            audit = json.loads(Path(name).read_text())
            for attempt in audit["attempts"]:
                if "response_body" in attempt:
                    responses[key(attempt["request"])].append(attempt)

        def handler(request, responses=responses):
            fingerprint = key(json.loads(request.content))
            if request.url.path != "/api/chat" or not responses[fingerprint]:
                raise RuntimeError("Replay request differs from the saved request; no network fallback allowed")
            response = responses[fingerprint].popleft()
            output["replayed_requests"] += 1
            return httpx.Response(response["status_code"], text=response["response_body"])

        with OllamaClient(
            original["model"], base_url="http://replay.invalid",
            transport=httpx.MockTransport(handler), audit_dir=directory / group,
        ) as client:
            result = review_claims(
                client, ClaimsEnvelope.model_validate({"claims": trace["candidates"]}), trace["source"],
                source_reference=trace.get("source_reference"),
            )
        for decision in result.decisions:
            case = cases[decision["claim_id"]]
            output["results"].append({
                "group": group, "case": case["case"], "expected": case["expected"],
                "actual": decision["status"], "reason": decision["reason"],
                "exact_match": decision["status"] == case["expected"],
                "filter_match": (decision["status"] == "supported") == (case["expected"] == "supported"),
                "audit_path": str(result.audit_path.resolve()),
            })
        write_json(directory / "receipt.json", output)
    output.update(
        cases=len(output["results"]),
        exact_matches=sum(item["exact_match"] for item in output["results"]),
        filter_matches=sum(item["filter_match"] for item in output["results"]),
    )
    write_json(directory / "receipt.json", output)
    print(json.dumps({k: v for k, v in output.items() if k != "results"}, ensure_ascii=False))
    print(f"receipt={directory / 'receipt.json'}")


if __name__ == "__main__":
    main()
