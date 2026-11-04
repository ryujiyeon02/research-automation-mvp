"""관심 종목의 일봉을 순차 조회해 로컬에 보존합니다. 계좌·주문 API는 사용하지 않습니다."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timezone

from research_automation import db
from research_automation.collectors.meritz import MeritzClient
from research_automation.config import load_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("universe")
    args = parser.parse_args()
    settings = load_settings()
    with open(args.universe) as file:
        universe = json.load(file)
    start, end = date.fromisoformat(universe["start"]), date.fromisoformat(universe["as_of"])
    output = settings.project_root / "data" / "research_runs" / universe["as_of"] / "sectors"
    output.mkdir(parents=True, exist_ok=True)
    db.init_db(settings.database_path)
    receipt = []
    with MeritzClient(
        settings.meritz_app_key, settings.meritz_app_secret, min_interval_seconds=0.3
    ) as client:
        for security in universe["securities"]:
            rows = client.domestic_daily(security["code"], start, end, adjusted_code="0")
            # 요청한 기간 밖의 행은 저장하지 않습니다.
            rows = [row for row in rows if universe["start"] <= row["observed_at"] <= universe["as_of"]]
            written = db.upsert_market_prices(settings.database_path, rows)
            (output / (security["code"] + "_meritz.json")).write_text(
                json.dumps(rows, ensure_ascii=False, indent=2)
            )
            record = {
                **security,
                "source": "meritz",
                "market": "J",
                "mod_stpr_cls_code": "0",
                "rows_saved": written,
                "first_date": rows[0]["observed_at"] if rows else None,
                "last_date": rows[-1]["observed_at"] if rows else None,
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
            receipt.append(record)
            (output / "price_collection.json").write_text(
                json.dumps(receipt, ensure_ascii=False, indent=2)
            )
            print(json.dumps(record, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
