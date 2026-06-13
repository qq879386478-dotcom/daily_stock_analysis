# -*- coding: utf-8 -*-
"""Tencent direct daily K-line fetcher for A-share fallback routing."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

import pandas as pd
import requests

from .base import BaseFetcher, DataFetchError, normalize_stock_code, is_bse_code

logger = logging.getLogger(__name__)


class TencentFetcher(BaseFetcher):
    """Fetch qfq daily K-line data from Tencent's direct quote endpoint."""

    name = "TencentFetcher"
    priority = 0

    _KLINE_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    _HTTP_TIMEOUT_SECONDS = 8

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        code = normalize_stock_code(stock_code)
        symbol = _to_tencent_symbol(code)
        if not symbol:
            raise DataFetchError(f"TencentFetcher unsupported stock code: {stock_code}")

        lookback = _estimate_lookback_days(start_date=start_date, end_date=end_date)
        response = requests.get(
            self._KLINE_ENDPOINT,
            params={"param": f"{symbol},day,,,{lookback},qfq"},
            headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json,text/plain,*/*"},
            timeout=self._HTTP_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        payload = response.json()
        rows = _extract_kline_rows(payload, symbol=symbol)
        if not rows:
            raise DataFetchError(f"TencentFetcher empty daily history for {stock_code}")

        df = pd.DataFrame(rows)
        df = df[(df["date"] >= start_date) & (df["date"] <= end_date)]
        if df.empty:
            raise DataFetchError(
                f"TencentFetcher daily history outside requested range for {stock_code}: {start_date}~{end_date}"
            )
        return df

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        normalized = df.copy()
        for column in ("open", "high", "low", "close", "volume", "amount"):
            if column in normalized.columns:
                normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        if "pct_chg" not in normalized.columns:
            normalized["pct_chg"] = normalized["close"].pct_change().fillna(0.0) * 100
        normalized = normalized[["date", "open", "high", "low", "close", "volume", "amount", "pct_chg"]]
        return normalized


def _to_tencent_symbol(stock_code: str) -> str:
    code = normalize_stock_code(stock_code)
    if not code or not code.isdigit() or len(code) != 6:
        return ""
    if is_bse_code(code):
        return f"bj{code}"
    if code.startswith(("6", "5", "9")):
        return f"sh{code}"
    return f"sz{code}"


def _estimate_lookback_days(*, start_date: str, end_date: str) -> int:
    try:
        start = datetime.strptime(start_date, "%Y-%m-%d")
        end = datetime.strptime(end_date, "%Y-%m-%d")
        calendar_days = max(1, (end - start).days + 1)
    except ValueError:
        calendar_days = 90
    # Trading days are sparse over calendar days; add margin for holidays/suspensions.
    return max(30, min(800, int(calendar_days * 1.8) + 20))


def _extract_kline_rows(payload: dict[str, Any], *, symbol: str) -> list[dict[str, Any]]:
    data = payload.get("data") if isinstance(payload, dict) else None
    item = data.get(symbol) if isinstance(data, dict) else None
    if not isinstance(item, dict):
        return []
    rows = item.get("qfqday") or item.get("day") or []
    result: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        amount: Optional[Any] = row[6] if len(row) > 6 else None
        result.append(
            {
                "date": str(row[0]),
                "open": row[1],
                "close": row[2],
                "high": row[3],
                "low": row[4],
                "volume": row[5],
                "amount": amount,
            }
        )
    return result
