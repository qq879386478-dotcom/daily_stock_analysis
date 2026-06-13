# -*- coding: utf-8 -*-
"""Tests for Tencent direct daily K-line fetcher."""

from __future__ import annotations

from unittest.mock import patch

import pandas as pd

from data_provider.tencent_fetcher import TencentFetcher, _to_tencent_symbol


def test_tencent_symbol_conversion_supports_a_share_markets() -> None:
    assert _to_tencent_symbol("600519") == "sh600519"
    assert _to_tencent_symbol("000001") == "sz000001"
    assert _to_tencent_symbol("920748") == "bj920748"


def test_tencent_fetcher_parses_qfq_daily_response() -> None:
    payload = {
        "data": {
            "sz000001": {
                "qfqday": [
                    ["2026-05-06", "10.00", "10.50", "10.80", "9.90", "12345", "67890"],
                    ["2026-05-07", "10.50", "10.70", "10.90", "10.30", "22345", "77890"],
                ]
            }
        }
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return payload

    captured = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return FakeResponse()

    fetcher = TencentFetcher()
    with patch("data_provider.tencent_fetcher.requests.get", fake_get):
        df = fetcher.get_daily_data("000001", start_date="2026-05-01", end_date="2026-05-10")

    assert captured["url"] == "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
    assert captured["params"]["param"].startswith("sz000001,day,,,")
    assert captured["params"]["param"].endswith(",qfq")
    assert list(df.columns) == [
        "date",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "pct_chg",
        "ma5",
        "ma10",
        "ma20",
        "volume_ratio",
    ]
    assert len(df) == 2
    assert float(df.iloc[0]["close"]) == 10.5
    assert float(df.iloc[1]["amount"]) == 77890.0


def test_tencent_fetcher_preserves_amount_column_when_missing() -> None:
    payload = {
        "data": {
            "sh600519": {
                "qfqday": [
                    ["2026-05-06", "100.00", "101.00", "102.00", "99.00", "1000"],
                ]
            }
        }
    }

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return payload

    with patch("data_provider.tencent_fetcher.requests.get", return_value=FakeResponse()):
        df = TencentFetcher().get_daily_data("600519", start_date="2026-05-01", end_date="2026-05-10")

    assert "amount" in df.columns
    assert pd.isna(df.iloc[0]["amount"])
