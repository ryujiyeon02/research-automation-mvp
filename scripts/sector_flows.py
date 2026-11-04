"""실제 거래일을 하나씩 요청하여 개인·외국인·기관의 일별 수급을 보존합니다."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone
from pathlib import Path

from research_automation import db
from research_automation.collectors.meritz import MeritzClient, parse_investors
from research_automation.config import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("universe", type=Path)
    args = parser.parse_args()
    settings = load_settings()
    universe = json.loads(args.universe.read_text())
    prices = settings.project_root / "data/research_runs" / universe["as_of"] / "sectors"
    output = prices.parent / "investor_flow"
    output.mkdir(parents=True, exist_ok=True)
    receipt = []
    with MeritzClient(
        settings.meritz_app_key, settings.meritz_app_secret, min_interval_seconds=0.22
    ) as client:
        for security in universe["securities"]:
            if security["group"] == "비교 ETF":
                continue
            bars = json.loads((prices / (security["code"] + "_meritz_regular.json")).read_text())
            days = [row["observed_at"] for row in bars[-20:]]
            if len(days) != 20 or days[-1] != universe["as_of"]:
                raise ValueError("20 trading dates ending on the research date are required")
            raw = []
            observations = []
            for day in days:
                rows = client.domestic_investor_day(security["code"], date.fromisoformat(day))
                if not rows:
                    raise ValueError("investor data is missing for a known trading date")
                raw.append({"requested_date": day, "rows": rows})
                for observation in parse_investors(rows, security["code"]):
                    observation["source"] = "meritz_krx_investor_daily"
                    observations.append(observation)
                (output / (security["code"] + "_raw_daily.json")).write_text(
                    json.dumps(raw, ensure_ascii=False, indent=2)
                )
            written = db.upsert_observations(settings.database_path, observations)
            record = {
                **security,
                "market": "J",
                "source": "meritz_krx_investor_daily",
                "method": "one request per trading day, from equals to",
                "trading_days": len(days),
                "first_date": days[0],
                "last_date": days[-1],
                "observations_saved": written,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            receipt.append(record)
            (output / "collection.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2))
            print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
