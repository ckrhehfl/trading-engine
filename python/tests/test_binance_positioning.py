"""Tests for `data.binance_positioning` -- Trade Management Task B.

Weighted toward the failure modes that would be **invisible**: this data
cannot be backfilled, so a silently-empty series is unrecoverable in a
way a loud crash is not.
"""

from __future__ import annotations

import json

import pytest

from data.binance_positioning import (
    MAX_LIMIT,
    SPECS,
    BinancePositioningError,
    fetch_metric,
)


def _stub(monkeypatch, payload, capture=None):
    def fake(url, *, attempts=3):
        if capture is not None:
            capture.append(url)
        return payload if isinstance(payload, str) else json.dumps(payload)

    monkeypatch.setattr("data.binance_positioning._get_with_retry", fake)


class TestFieldExtraction:
    def test_open_interest_yields_both_documented_fields(self, monkeypatch):
        _stub(monkeypatch, [{"timestamp": 1000, "sumOpenInterest": "10", "sumOpenInterestValue": "20"}])
        rows = fetch_metric("open_interest", "BTCUSDT")
        assert {r.metric for r in rows} == {"open_interest", "open_interest_value"}
        assert all(r.timestamp_ms == 1000 for r in rows)

    def test_global_ratio_yields_three_series(self, monkeypatch):
        _stub(monkeypatch, [{"timestamp": 1, "longAccount": "0.6",
                             "shortAccount": "0.4", "longShortRatio": "1.5"}])
        assert len(fetch_metric("global_long_short", "BTCUSDT")) == 3

    def test_top_positions_is_size_weighted_and_distinct_from_accounts(self):
        """Two different endpoints, deliberately kept as distinct metrics:
        one counts whales, the other weighs their capital."""
        assert SPECS["top_accounts"].path != SPECS["top_positions"].path
        assert set(SPECS["top_accounts"].fields.values()) != set(SPECS["top_positions"].fields.values())

    def test_values_are_strings_not_floats(self, monkeypatch):
        """A float round-trip would perturb a ratio the strategy layer
        compares against a threshold."""
        _stub(monkeypatch, [{"timestamp": 1, "buySellRatio": "1.0000000001",
                             "buyVol": "1", "sellVol": "1"}])
        row = next(r for r in fetch_metric("taker_flow", "BTCUSDT") if r.metric == "taker_buy_sell_ratio")
        assert row.value == "1.0000000001"

    def test_period_is_carried_onto_every_row(self, monkeypatch):
        _stub(monkeypatch, [{"timestamp": 1, "longShortRatio": "1.2"}])
        assert all(r.period == "5m" for r in fetch_metric("top_accounts", "BTCUSDT", period="5m"))


class TestFailsLoudly:
    """A silently-empty series is unrecoverable here: the endpoint keeps
    ~30 days, so a quiet failure destroys history rather than delaying
    it."""

    def test_a_renamed_field_raises_rather_than_yielding_nothing(self, monkeypatch):
        _stub(monkeypatch, [{"timestamp": 1, "sumOpenInterest": "10"}])  # value field gone
        with pytest.raises(BinancePositioningError, match="missing documented field"):
            fetch_metric("open_interest", "BTCUSDT")

    def test_an_error_envelope_is_not_mistaken_for_no_data(self, monkeypatch):
        """These endpoints return a bare array; an object is Binance's
        error shape."""
        _stub(monkeypatch, {"code": -1121, "msg": "Invalid symbol."})
        with pytest.raises(BinancePositioningError, match="expected a JSON array"):
            fetch_metric("open_interest", "NOPE")

    def test_a_row_without_a_timestamp_raises(self, monkeypatch):
        _stub(monkeypatch, [{"sumOpenInterest": "10", "sumOpenInterestValue": "20"}])
        with pytest.raises(BinancePositioningError, match="missing 'timestamp'"):
            fetch_metric("open_interest", "BTCUSDT")

    def test_non_json_raises(self, monkeypatch):
        _stub(monkeypatch, "<html>maintenance</html>")
        with pytest.raises(BinancePositioningError, match="not JSON"):
            fetch_metric("open_interest", "BTCUSDT")

    def test_an_empty_array_is_accepted_as_genuinely_no_data(self, monkeypatch):
        """Distinct from the failures above: an empty array IS the
        documented 'nothing in this window' response."""
        _stub(monkeypatch, [])
        assert fetch_metric("open_interest", "BTCUSDT") == []


class TestArguments:
    def test_unknown_metric_is_rejected(self):
        with pytest.raises(ValueError, match="unknown metric"):
            fetch_metric("liquidations", "BTCUSDT")

    def test_liquidations_are_deliberately_absent(self):
        """Binance publishes only the largest liquidation per 1000ms
        window, so any aggregate is a lower bound. Collecting a number
        that understates itself during exactly the bursts that matter
        would be worse than not having it."""
        assert "liquidations" not in SPECS

    @pytest.mark.parametrize("period", ["1m", "3d", "", "1H"])
    def test_undocumented_periods_are_rejected(self, period):
        with pytest.raises(ValueError, match="period must be one of"):
            fetch_metric("open_interest", "BTCUSDT", period=period)

    @pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1])
    def test_out_of_range_limits_are_rejected(self, limit):
        with pytest.raises(ValueError, match="limit must be in"):
            fetch_metric("open_interest", "BTCUSDT", limit=limit)

    def test_the_request_carries_symbol_period_and_limit(self, monkeypatch):
        seen: list[str] = []
        _stub(monkeypatch, [], capture=seen)
        fetch_metric("open_interest", "ETHUSDT", period="1h", limit=100)
        assert "symbol=ETHUSDT" in seen[0]
        assert "period=1h" in seen[0]
        assert "limit=100" in seen[0]
