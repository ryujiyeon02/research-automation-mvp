import datetime as dt
from pathlib import Path
from typing import List

import httpx
import pytest

from research_automation import db
from research_automation.collectors import meritz
from research_automation.collectors.meritz import (
    MeritzClient,
    MeritzSafetyError,
    check_request,
    parse_investors,
    parse_overseas_minutes,
)
from research_automation.config import load_settings

# 응답 예시는 메리츠 Open API 포털 명세의 예시 응답을 줄인 것입니다.
DOMESTIC_DAILY = {
    "data": [
        {
            "date": "20260910",
            "oprc": 6668000,
            "hprc": 12382000,
            "lprc": 6668000,
            "stck_clpr": 8389000,
            "acml_vol": 8356,
        },
        {
            "date": "20260909",
            "oprc": 9500000,
            "hprc": 9600000,
            "lprc": 9400000,
            "stck_clpr": 9525000,
            "acml_vol": 1200,
        },
    ],
    "rsp_cd": "0000",
}


def make_client(handler, calls: List[httpx.Request]) -> MeritzClient:
    def recorder(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    return MeritzClient(
        "test-key",
        "test-secret",
        base_url="https://meritz.test",
        min_interval_seconds=0,
        transport=httpx.MockTransport(recorder),
    )


def token_or(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(200, json=payload)

    return handler


def test_check_request_allows_only_reads_and_token() -> None:
    check_request("POST", "/oauth2/token")
    check_request("GET", "/market/v1/candles/days")
    for method, path in [
        ("POST", "/trading/v1/orders/buy"),
        ("GET", "/trading/v1/orders/sell"),
        ("POST", "/market/v1/candles/days"),
        ("GET", "/accounts/v1/me/holdings"),
        ("POST", "/oauth2/revoke"),
    ]:
        with pytest.raises(MeritzSafetyError):
            check_request(method, path)


def test_blocked_path_never_reaches_network() -> None:
    calls: List[httpx.Request] = []
    with make_client(token_or({}), calls) as client, pytest.raises(MeritzSafetyError):
        client._get("/trading/v1/orders/buy", {})
    assert calls == []


def test_domestic_daily_issues_token_once_and_parses() -> None:
    calls: List[httpx.Request] = []
    with make_client(token_or(DOMESTIC_DAILY), calls) as client:
        prices = client.domestic_daily("A005930", dt.date(2026, 9, 1), dt.date(2026, 9, 10))
        client.domestic_daily("005930", dt.date(2026, 9, 1), dt.date(2026, 9, 10))

    assert [call.url.path for call in calls].count("/oauth2/token") == 1
    market_call = next(call for call in calls if call.url.path == "/market/v1/candles/days")
    assert market_call.method == "GET"
    assert market_call.headers["authorization"] == "Bearer tok"
    assert len(market_call.headers["mac_address"]) == 12
    assert market_call.url.params["iscd"] == "005930"
    assert market_call.url.params["from"] == "20260901"
    assert [p["observed_at"] for p in prices] == ["2026-09-09", "2026-09-10"]
    assert prices[1]["close"] == 8389000.0
    assert prices[1]["security_id"] == "KRX:005930"


def test_rate_limit_code_is_retried(monkeypatch) -> None:
    monkeypatch.setattr(meritz.time, "sleep", lambda _seconds: None)
    responses = [{"rsp_cd": "EGW00200", "rsp_msg": "too many"}, DOMESTIC_DAILY]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/oauth2/token":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 86400})
        return httpx.Response(200, json=responses.pop(0))

    calls: List[httpx.Request] = []
    with make_client(handler, calls) as client:
        rows = client._get("/market/v1/candles/days", {"iscd": "005930"})
    assert len(rows) == 2


def test_error_code_raises_without_leaking_secret() -> None:
    calls: List[httpx.Request] = []
    handler = token_or({"rsp_cd": "0760", "rsp_msg": "bad request"})
    with make_client(handler, calls) as client, pytest.raises(meritz.MeritzApiError) as error:
        client._get("/market/v1/candles/days", {"iscd": "005930"})
    assert "0760" in str(error.value)
    assert "test-secret" not in str(error.value)


def test_parse_investors_skips_placeholder_row() -> None:
    rows = [
        {"date": "00000000", "frgn_ntby_vol": 5},
        {
            "date": "20260910",
            "prsn_ntby_vol": -2057529,
            "frgn_ntby_vol": -223832,
            "orgn_ntby_vol": -66491,
            "prsn_ntby_tr_pbmn": -2277305,
            "frgn_ntby_tr_pbmn": 147905,
            "orgn_ntby_tr_pbmn": -45443,
        },
    ]
    observations = parse_investors(rows, "005930")
    assert len(observations) == 6
    assert {o["observation_date"] for o in observations} == {"2026-09-10"}
    foreign = next(o for o in observations if o["variable_id"] == "KRX:005930:frgn_ntby_tr_pbmn")
    assert foreign["value"] == 147905.0
    assert foreign["unit"] == "KRW mn"


def test_parse_overseas_minutes_uses_kst_and_drops_bad_times() -> None:
    rows = [
        {
            "korea_date": "20260911",
            "korea_hour": "164900",
            "prpr": "326.3100",
            "oprc": "326.3100",
            "hprc": "326.3100",
            "lprc": "326.3100",
            "cntg_vol": 10,
        },
        {"korea_date": "20260911", "korea_hour": "2624639", "prpr": "1"},
    ]
    prices = parse_overseas_minutes(rows, "aapl", "oq")
    assert len(prices) == 1
    assert prices[0]["observed_at"] == "2026-09-11T16:49:00+09:00"
    assert prices[0]["security_id"] == "OQ:AAPL"
    assert prices[0]["currency"] == "USD"
    assert prices[0]["close"] == 326.31


def test_market_price_and_observation_upserts_are_idempotent(tmp_path: Path) -> None:
    database_path = tmp_path / "research.db"
    db.init_db(database_path)
    price = {
        "security_id": "KRX:005930",
        "observed_at": "2026-09-10",
        "close": 100.0,
        "currency": "KRW",
        "source": "meritz",
    }
    db.upsert_market_prices(database_path, [price])
    db.upsert_market_prices(database_path, [{**price, "close": 101.0}])
    observation = {
        "variable_id": "KRX:005930:frgn_ntby_vol",
        "observation_date": "2026-09-10",
        "value": 1.0,
        "unit": "shares",
        "source": "meritz",
        "vintage_date": None,
    }
    db.upsert_observations(database_path, [observation])
    db.upsert_observations(database_path, [{**observation, "value": 2.0}])

    with db.connect(database_path) as connection:
        closes = connection.execute("SELECT close FROM market_prices").fetchall()
        values = connection.execute("SELECT value FROM observations").fetchall()
    assert [row["close"] for row in closes] == [101.0]
    assert [row["value"] for row in values] == [2.0]


def test_meritz_keys_are_never_exposed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("MERITZ_APP_KEY", "meritz-key-value")
    monkeypatch.setenv("MERITZ_APP_SECRET", "meritz-secret-value")
    settings = load_settings(tmp_path)
    assert settings.key_status()["MERITZ_APP_SECRET"] is True
    assert "meritz-secret-value" not in repr(settings)
    assert "meritz-key-value" not in repr(settings.key_status())
