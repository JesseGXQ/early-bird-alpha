from __future__ import annotations

import sys
import types

import pandas as pd
import pytest

from account.models import AccountSnapshot
from analytics.recorder import PerformanceRecorder


def _snapshot(day: int, equity: float) -> AccountSnapshot:
    timestamp = pd.Timestamp("2026-01-01") + pd.Timedelta(days=day)
    return AccountSnapshot(
        timestamp=timestamp,
        cash=equity,
        realized_pnl=equity - 100.0,
        unrealized_pnl=0.0,
        commission=0.0,
        equity=equity,
        positions={},
    )


def test_quantstats_report_passes_period_returns_to_metrics(monkeypatch):
    captured = {}

    def metrics(returns, **kwargs):
        captured["returns"] = returns
        captured["kwargs"] = kwargs
        return pd.DataFrame({"value": [1.0]}, index=["sharpe"])

    fake_quantstats = types.SimpleNamespace(reports=types.SimpleNamespace(metrics=metrics))
    monkeypatch.setitem(sys.modules, "quantstats", fake_quantstats)

    recorder = PerformanceRecorder()
    recorder.record_snapshot(_snapshot(0, 100.0))
    recorder.record_snapshot(_snapshot(1, 110.0))
    recorder.record_snapshot(_snapshot(2, 99.0))

    report = recorder.quantstats_report(periods_per_year=252)

    pd.testing.assert_series_equal(
        captured["returns"],
        pd.Series(
            [0.1, -0.1],
            index=pd.Index(pd.to_datetime(["2026-01-02", "2026-01-03"]), name="timestamp"),
            name="strategy",
        ),
    )
    assert captured["kwargs"]["display"] is False
    assert captured["kwargs"]["periods_per_year"] == 252
    assert report.loc["sharpe", "value"] == 1.0


def test_quantstats_report_requires_output_for_html(monkeypatch):
    fake_quantstats = types.SimpleNamespace(reports=types.SimpleNamespace())
    monkeypatch.setitem(sys.modules, "quantstats", fake_quantstats)

    recorder = PerformanceRecorder()
    recorder.record_snapshot(_snapshot(0, 100.0))
    recorder.record_snapshot(_snapshot(1, 101.0))

    with pytest.raises(ValueError, match="output is required"):
        recorder.quantstats_report(report="html")
