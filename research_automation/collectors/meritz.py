"""메리츠증권 Open API 읽기 전용 수집기.

메리츠 Open API 는 모의계좌가 없어 앱키에 연결된 계좌가 곧 실계좌입니다.
그래서 이 모듈은 아래 허용 목록의 조회 API 만 호출하며, 그 밖의 경로나 GET 이 아닌 요청
(토큰 발급 제외)은 네트워크에 나가기 전에 거부합니다.

- 국내 일봉: /market/v1/candles/days
- 국내 투자자별 매매동향(일별): /market/v1/investors
- 해외 일봉·분봉: /market/v1/overseas/candles/days, /market/v1/overseas/candles/minutes

국내 분봉(/market/v1/candles/minutes)은 공식 명세상 KRX 로는 빈 목록, NXT·통합으로는 마지막 봉
1건만 내려와 수집 대상에서 제외합니다.
"""

from __future__ import annotations

import datetime as dt
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import httpx

MERITZ_BASE = "https://openapi.imeritz.com:9443"
SOURCE = "meritz"

TOKEN_PATH = "/oauth2/token"
DOMESTIC_DAILY_PATH = "/market/v1/candles/days"
DOMESTIC_INVESTORS_PATH = "/market/v1/investors"
OVERSEAS_DAILY_PATH = "/market/v1/overseas/candles/days"
OVERSEAS_MINUTES_PATH = "/market/v1/overseas/candles/minutes"
ALLOWED_GET_PATHS = frozenset(
    {DOMESTIC_DAILY_PATH, DOMESTIC_INVESTORS_PATH, OVERSEAS_DAILY_PATH, OVERSEAS_MINUTES_PATH}
)

RETRYABLE_CODES = {"EGW00200"}  # 초당 호출 한도 초과
TOKEN_EXPIRED_CODES = {"EGW00121", "EGW00123"}

OVERSEAS_CURRENCY = {"OQ": "USD", "NY": "USD", "AX": "USD", "HK": "HKD", "SH": "CNY", "SZ": "CNY"}
INVESTOR_FIELDS = {
    "prsn_ntby_vol": "shares",
    "frgn_ntby_vol": "shares",
    "orgn_ntby_vol": "shares",
    "prsn_ntby_tr_pbmn": "KRW mn",
    "frgn_ntby_tr_pbmn": "KRW mn",
    "orgn_ntby_tr_pbmn": "KRW mn",
}
KST = dt.timezone(dt.timedelta(hours=9))


class MeritzSafetyError(RuntimeError):
    """허용 목록 밖의 요청을 막았을 때 발생합니다."""


class MeritzApiError(RuntimeError):
    pass


def check_request(method: str, path: str) -> None:
    if method.upper() == "POST" and path == TOKEN_PATH:
        return
    if method.upper() != "GET" or path not in ALLOWED_GET_PATHS:
        raise MeritzSafetyError(f"read-only client refuses {method.upper()} {path}")


def normalize_code(code: str) -> str:
    value = code.strip().upper().removeprefix("A")
    if len(value) != 6 or not value.isalnum():
        raise ValueError("domestic code must be 6 characters, e.g. 005930")
    return value


def _ymd(value: dt.date) -> str:
    return value.strftime("%Y%m%d")


def _iso_date(yyyymmdd: Any) -> Optional[str]:
    """YYYYMMDD 를 ISO 날짜로. 명세상 날짜가 "00000000" 인 빈 행이 섞일 수 있어 그런 값은 None."""
    try:
        return dt.datetime.strptime(str(yyyymmdd), "%Y%m%d").replace(tzinfo=KST).date().isoformat()
    except ValueError:
        return None


def _number(value: Any) -> Optional[float]:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _rows(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    data = payload.get("data")
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict) and data:
        return [data]
    return []


def parse_domestic_daily(rows: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    security_id = f"KRX:{normalize_code(code)}"
    return [
        {
            "security_id": security_id,
            "observed_at": _iso_date(row.get("date")),
            "open": _number(row.get("oprc")),
            "high": _number(row.get("hprc")),
            "low": _number(row.get("lprc")),
            "close": _number(row.get("stck_clpr")),
            "volume": _number(row.get("acml_vol")),
            "currency": "KRW",
            "source": SOURCE,
        }
        for row in rows
        if _iso_date(row.get("date"))
    ]


def parse_overseas_daily(
    rows: List[Dict[str, Any]], symbol: str, exchange: str
) -> List[Dict[str, Any]]:
    security_id = f"{exchange.upper()}:{symbol.upper()}"
    return [
        {
            "security_id": security_id,
            "observed_at": _iso_date(row.get("date")),
            "open": _number(row.get("oprc")),
            "high": _number(row.get("hprc")),
            "low": _number(row.get("lprc")),
            "close": _number(row.get("prpr")),
            "volume": _number(row.get("acml_vol")),
            "currency": OVERSEAS_CURRENCY.get(exchange.upper()),
            "source": SOURCE,
        }
        for row in rows
        if _iso_date(row.get("date"))
    ]


def parse_overseas_minutes(
    rows: List[Dict[str, Any]], symbol: str, exchange: str
) -> List[Dict[str, Any]]:
    """korea_date + korea_hour(한국시각)를 KST ISO 시각으로 바꿉니다. 형식이 깨진 봉은 버립니다."""
    security_id = f"{exchange.upper()}:{symbol.upper()}"
    results = []
    for row in rows:
        day, hour = str(row.get("korea_date", "")), str(row.get("korea_hour", ""))
        try:
            stamp = dt.datetime.strptime(day + hour, "%Y%m%d%H%M%S").replace(tzinfo=KST)
        except ValueError:
            continue
        results.append(
            {
                "security_id": security_id,
                "observed_at": stamp.isoformat(),
                "open": _number(row.get("oprc")),
                "high": _number(row.get("hprc")),
                "low": _number(row.get("lprc")),
                "close": _number(row.get("prpr")),
                "volume": _number(row.get("cntg_vol")),
                "currency": OVERSEAS_CURRENCY.get(exchange.upper()),
                "source": SOURCE,
            }
        )
    return results


def parse_investors(rows: List[Dict[str, Any]], code: str) -> List[Dict[str, Any]]:
    """투자자별 순매수를 observations 형식(variable_id 하나당 한 행)으로 바꿉니다."""
    normalized = normalize_code(code)
    results = []
    for row in rows:
        observation_date = _iso_date(row.get("date"))
        if observation_date is None:
            continue
        for field, unit in INVESTOR_FIELDS.items():
            results.append(
                {
                    "variable_id": f"KRX:{normalized}:{field}",
                    "observation_date": observation_date,
                    "value": _number(row.get(field)),
                    "unit": unit,
                    "source": SOURCE,
                    "vintage_date": None,
                }
            )
    return results


def _mac_address() -> str:
    node = uuid.getnode()
    return "".join(f"{(node >> shift) & 0xFF:02X}" for shift in range(40, -1, -8))


class MeritzClient:
    def __init__(
        self,
        app_key: str,
        app_secret: str,
        base_url: str = MERITZ_BASE,
        timeout_seconds: float = 15.0,
        min_interval_seconds: float = 0.13,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        if not app_key or not app_secret:
            raise ValueError("Set MERITZ_APP_KEY and MERITZ_APP_SECRET in .env")
        self._app_key = app_key
        self._app_secret = app_secret
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=timeout_seconds, transport=transport
        )
        self._min_interval = min_interval_seconds
        self._last_call = 0.0
        self._token: Optional[str] = None
        self._token_expires = 0.0

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> MeritzClient:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def _throttle(self) -> None:
        wait = self._last_call + self._min_interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()

    def _issue_token(self) -> str:
        if self._token and time.time() < self._token_expires - 60:
            return self._token
        check_request("POST", TOKEN_PATH)
        self._throttle()
        response = self._client.post(
            TOKEN_PATH,
            data={
                "grant_type": "client_credentials",
                "client_id": self._app_key,
                "client_secret": self._app_secret,
                "scope": "login",
            },
        )
        payload = response.json() if response.content else {}
        if response.status_code != 200 or "access_token" not in payload:
            code = payload.get("rsp_cd") or payload.get("error") or ""
            raise MeritzApiError(
                f"token request failed (HTTP {response.status_code}) {code}".strip()
            )
        self._token = str(payload["access_token"])
        self._token_expires = time.time() + int(payload.get("expires_in") or 43_200)
        return self._token

    def _get(self, path: str, params: Dict[str, str]) -> List[Dict[str, Any]]:
        check_request("GET", path)
        for attempt in range(3):
            self._throttle()
            response = self._client.get(
                path,
                params=params,
                headers={
                    "authorization": f"Bearer {self._issue_token()}",
                    "mac_address": _mac_address(),
                },
            )
            payload = response.json() if response.content else {}
            code = str(payload.get("rsp_cd", ""))
            if code in RETRYABLE_CODES and attempt < 2:
                time.sleep(1.0 + attempt)
                continue
            if code in TOKEN_EXPIRED_CODES and attempt < 2:
                self._token = None
                continue
            if response.status_code != 200 or code not in ("", "0000"):
                raise MeritzApiError(
                    f"GET {path} failed (HTTP {response.status_code}) {code} "
                    f"{payload.get('rsp_msg', '')}".strip()
                )
            return _rows(payload)
        raise MeritzApiError(f"GET {path} kept failing after retries")

    def _daily_range(
        self, path: str, params: Dict[str, str], start: dt.date, end: dt.date
    ) -> List[Dict[str, Any]]:
        """긴 기간은 서버가 응답하지 않을 수 있어 1년씩 뒤에서부터 끊어 받습니다."""
        collected: Dict[str, Dict[str, Any]] = {}
        cursor = end
        for _ in range(60):
            if cursor < start:
                break
            window_start = max(start, cursor - dt.timedelta(days=365))
            rows = self._get(path, {**params, "from": _ymd(window_start), "to": _ymd(cursor)})
            dated = [row for row in rows if _iso_date(row.get("date"))]
            dates = sorted(str(row["date"]) for row in dated)
            for row in dated:
                collected.setdefault(str(row["date"]), row)
            if not dates:
                cursor = window_start - dt.timedelta(days=1)
                continue
            earliest = dt.date.fromisoformat(_iso_date(dates[0]) or "")
            # 한 번에 다 오지 않았으면 받은 첫 날 전날부터 이어서 받습니다.
            if earliest > window_start + dt.timedelta(days=10):
                cursor = earliest - dt.timedelta(days=1)
            else:
                cursor = window_start - dt.timedelta(days=1)
        return [collected[key] for key in sorted(collected)]

    def domestic_daily(
        self, code: str, start: dt.date, end: dt.date, adjusted_code: str = "0"
    ) -> List[Dict[str, Any]]:
        params = {
            "mrkt_div_code": "J",
            "iscd": normalize_code(code),
            "mod_stpr_cls_code": adjusted_code,
        }
        rows = self._daily_range(DOMESTIC_DAILY_PATH, params, start, end)
        return parse_domestic_daily(rows, code)

    def domestic_investors(self, code: str, start: dt.date, end: dt.date) -> List[Dict[str, Any]]:
        params = {
            "mrkt_div_code": "J",
            "iscd": normalize_code(code),
            "from": _ymd(start),
            "to": _ymd(end),
        }
        return parse_investors(self._get(DOMESTIC_INVESTORS_PATH, params), code)

    def overseas_daily(
        self, symbol: str, exchange: str, start: dt.date, end: dt.date, adjusted_code: str = "0"
    ) -> List[Dict[str, Any]]:
        params = {
            "mrkt_div_code": "OV",
            "mrkt_cls_code": exchange.upper(),
            "iscd": symbol.upper(),
            "dely_rltm_cls_code": "0",
            "mod_stpr_cls_code": adjusted_code,
        }
        rows = self._daily_range(OVERSEAS_DAILY_PATH, params, start, end)
        return parse_overseas_daily(rows, symbol, exchange)

    def overseas_minutes(
        self, symbol: str, exchange: str, interval_seconds: int = 60
    ) -> List[Dict[str, Any]]:
        """최근 구간부터의 분봉. 기간 지정 파라미터가 없어 과거 이벤트는 미리 쌓아 두어야 합니다."""
        if not 1 <= interval_seconds <= 3600:
            raise ValueError("interval_seconds must be between 1 and 3600")
        params = {
            "mrkt_div_code": "OV",
            "mrkt_cls_code": exchange.upper(),
            "iscd": symbol.upper(),
            "dely_rltm_cls_code": "0",
            "hour_cls_code": str(interval_seconds),
        }
        rows = self._get(OVERSEAS_MINUTES_PATH, params)
        return parse_overseas_minutes(rows, symbol, exchange)


def date_range_args(start: str, end: Optional[str]) -> Tuple[dt.date, dt.date]:
    start_date = dt.date.fromisoformat(start)
    end_date = dt.date.fromisoformat(end) if end else dt.datetime.now(KST).date()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    return start_date, end_date
