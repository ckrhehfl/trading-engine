"""Tests for `python/research/strategies/vwap_mid_reversion.py` --
Scalping Strategy Research Task S4's first candidate.

**Synthetic fixtures only.** No test in this file loads, queries or
touches `python/data/var/klines.sqlite3` or any real BingX 1m kline
data -- every `Kline` here is hand-built. This is the same inviolable
rule `test_daily_tsmom_ensemble.py` was built under: the 1m holdout
(the ENTIRE available 1m window, per Task S3's design decision) must
not be accessed by anything except the single, later, dedicated
execution step.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from backtest.kline import Kline
from research.preregistration import load_preregistration
from research.strategies.vwap_mid_reversion import (
    DEFAULT_DEVIATION_K,
    DEFAULT_VWAP_PERIOD,
    VwapMidBands,
    VwapMidReversionStrategy,
    VwapMidReversionTrainable,
    _vwap_signal,
)
from schemas.order_intent import OrderType, Side

BASE_TIME = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
PREREGISTRATION_PATH = "../configs/research/preregistrations/vwap-mid-reversion-1m-holdout.json"


def _minute_kline(i: int, close: str, volume: str = "1", base_time: datetime = BASE_TIME) -> Kline:
    c = Decimal(close)
    return Kline(
        open_time=base_time + timedelta(minutes=i),
        open=c,
        high=c,
        low=c,
        close=c,
        volume=Decimal(volume),
    )


def _flat_klines(count: int, price: str = "100", volume: str = "1", base_time: datetime = BASE_TIME) -> list[Kline]:
    return [_minute_kline(i, price, volume, base_time) for i in range(count)]


def _strategy(**overrides: object) -> VwapMidReversionStrategy:
    kwargs: dict[str, Any] = dict(symbol="BTC-USDT", vwap_period=10, deviation_k=Decimal("2"), vol_period=2)
    kwargs.update(overrides)
    return VwapMidReversionStrategy(**kwargs)


# ---------------------------------------------------------------------------
# VwapMidBands
# ---------------------------------------------------------------------------


class TestVwapMidBandsConstruction:
    def test_rejects_period_below_two(self):
        with pytest.raises(ValueError, match="period must be at least 2"):
            VwapMidBands(period=1)

    def test_rejects_non_positive_k(self):
        with pytest.raises(ValueError, match="k must be positive"):
            VwapMidBands(period=5, k=Decimal("0"))


class TestVwapMidBandsWarmup:
    def test_returns_none_before_period_bars_seen(self):
        bands = VwapMidBands(period=5)
        for i in range(4):
            assert bands.update(_minute_kline(i, "100")) is None
        assert bands.bars_seen == 4

    def test_returns_a_reading_on_the_period_th_bar(self):
        bands = VwapMidBands(period=5)
        for i in range(4):
            bands.update(_minute_kline(i, "100"))
        result = bands.update(_minute_kline(4, "100"))
        assert result is not None


class TestVwapMidBandsComputation:
    def test_flat_price_and_volume_gives_zero_width_band_at_the_price(self):
        bands = VwapMidBands(period=5, k=Decimal("2"))
        result = None
        for i in range(5):
            result = bands.update(_minute_kline(i, "100", volume="1"))
        assert result == (Decimal("100"), Decimal("100"), Decimal("100"))

    def test_vwap_is_volume_weighted_not_a_simple_mean(self):
        # Two bars: price 100 with volume 1, price 200 with volume 9.
        # Simple mean would be 150; volume-weighted mean is much closer to 200.
        bands = VwapMidBands(period=2, k=Decimal("2"))
        bands.update(_minute_kline(0, "100", volume="1"))
        result = bands.update(_minute_kline(1, "200", volume="9"))
        assert result is not None
        vwap, _, _ = result
        # (100*1 + 200*9) / 10 = 190
        assert vwap == Decimal("190")

    def test_zero_total_volume_window_returns_none_not_a_crash(self):
        bands = VwapMidBands(period=3)
        assert bands.update(_minute_kline(0, "100", volume="0")) is None
        assert bands.update(_minute_kline(1, "100", volume="0")) is None
        # Third bar completes the window, but total volume is still zero.
        assert bands.update(_minute_kline(2, "100", volume="0")) is None

    def test_band_width_uses_sample_stdev_of_raw_close_prices(self):
        # closes 100,100,100,100,105 (period=5, volume=1 flat so vwap=mean).
        bands = VwapMidBands(period=5, k=Decimal("2"))
        result = None
        for i, close in enumerate(["100", "100", "100", "100", "105"]):
            result = bands.update(_minute_kline(i, close, volume="1"))
        assert result is not None
        vwap, upper, lower = result
        closes = [Decimal("100"), Decimal("100"), Decimal("100"), Decimal("100"), Decimal("105")]
        mean = sum(closes) / 5
        variance = sum((c - mean) ** 2 for c in closes) / 4
        expected_stdev = variance.sqrt()
        assert vwap == mean
        assert upper == mean + Decimal("2") * expected_stdev
        assert lower == mean - Decimal("2") * expected_stdev


class TestVwapSignal:
    def test_below_lower_band_is_oversold_plus_one(self):
        assert _vwap_signal(Decimal("90"), lower=Decimal("95"), upper=Decimal("105")) == 1

    def test_above_upper_band_is_overbought_minus_one(self):
        assert _vwap_signal(Decimal("110"), lower=Decimal("95"), upper=Decimal("105")) == -1

    def test_inside_bands_is_zero(self):
        assert _vwap_signal(Decimal("100"), lower=Decimal("95"), upper=Decimal("105")) == 0

    def test_exactly_on_a_band_is_zero_strict_inequality(self):
        assert _vwap_signal(Decimal("105"), lower=Decimal("95"), upper=Decimal("105")) == 0
        assert _vwap_signal(Decimal("95"), lower=Decimal("95"), upper=Decimal("105")) == 0


# ---------------------------------------------------------------------------
# VwapMidReversionStrategy
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_rejects_non_positive_reference_equity(self):
        with pytest.raises(ValueError, match="reference_equity must be positive"):
            _strategy(reference_equity=Decimal("0"))


class TestWarmup:
    def test_silent_during_bands_warmup(self):
        strategy = _strategy(vwap_period=5, vol_period=2)
        window = _flat_klines(4)
        for i in range(4):
            assert strategy([*window[: i + 1]]) is None
        assert strategy.position_sign == 0


class TestEntryAndFlatten:
    def test_no_entry_while_price_stays_inside_the_bands(self):
        strategy = _strategy(vwap_period=5, vol_period=2)
        klines = _flat_klines(10, price="100")
        for i in range(len(klines)):
            intent = strategy([*klines[: i + 1]])
            assert intent is None
        assert strategy.position_sign == 0

    def test_enters_long_on_transition_into_oversold(self):
        # vwap_period=10: 9 flat bars at 100 warm the bands, then a drop to
        # 50 breaches the lower band -- empirically confirmed (not
        # hand-derived: a small-window Bollinger-style band is often
        # mathematically impossible for a single outlier bar to breach --
        # for a sample of n points with n-1 equal, the maximum achievable
        # |z-score| of the differing point is (n-1)/sqrt(n), which is
        # below k=2 for n<6 -- so these fixture values were found by
        # directly exercising VwapMidBands, not guessed).
        strategy = _strategy(vwap_period=10, vol_period=2, reference_equity=Decimal("10000"))
        closes = ["100"] * 9 + ["50"]
        window: list[Kline] = []
        intent = None
        for i, close in enumerate(closes):
            window.append(_minute_kline(i, close, volume="1"))
            intent = strategy(window)
        assert intent is not None
        assert intent.side == Side.LONG
        assert intent.order_type == OrderType.GUARDED_MARKET
        assert intent.quantity > 0
        assert strategy.position_sign == 1

    def test_enters_short_on_transition_into_overbought(self):
        strategy = _strategy(vwap_period=10, vol_period=2, reference_equity=Decimal("10000"))
        closes = ["100"] * 9 + ["150"]
        window: list[Kline] = []
        intent = None
        for i, close in enumerate(closes):
            window.append(_minute_kline(i, close, volume="1"))
            intent = strategy(window)
        assert intent is not None
        assert intent.side == Side.SHORT
        assert strategy.position_sign == -1

    def test_flattens_on_reversion_to_neutral(self):
        strategy = _strategy(vwap_period=10, vol_period=2, reference_equity=Decimal("10000"))
        closes = ["100"] * 9 + ["50"]
        window: list[Kline] = []
        for i, close in enumerate(closes):
            window.append(_minute_kline(i, close, volume="1"))
            strategy(window)
        assert strategy.position_sign == 1

        # Price reverts back toward the VWAP -- inside the bands again
        # (empirically confirmed: close=100 at this point is inside).
        window.append(_minute_kline(len(window), "100", volume="1"))
        intent = strategy(window)
        assert intent is not None
        assert intent.side == Side.SHORT  # closing a LONG
        assert strategy.position_sign == 0

    def test_same_bar_flip_from_long_to_short_bundles_close_and_reopen(self):
        strategy = _strategy(vwap_period=10, vol_period=2, reference_equity=Decimal("10000"))
        closes = ["100"] * 9 + ["50"]
        window: list[Kline] = []
        for i, close in enumerate(closes):
            window.append(_minute_kline(i, close, volume="1"))
            strategy(window)
        assert strategy.position_sign == 1
        closing_quantity = strategy.position_quantity

        # Directly to overbought -- no bar spent at neutral in between
        # (empirically confirmed: close=150 here breaches the upper band).
        window.append(_minute_kline(len(window), "150", volume="1"))
        intent = strategy(window)
        assert intent is not None
        assert intent.side == Side.SHORT
        assert strategy.position_sign == -1
        # The single intent's quantity bundles the old LONG's close with
        # the fresh SHORT's open -- strictly greater than either alone.
        assert intent.quantity > closing_quantity
        assert intent.quantity > strategy.position_quantity


class TestSizingRejectionRetry:
    def test_a_sizing_rejected_entry_is_retried_once_sizing_becomes_available(self):
        # vwap_period=10 (bands warm on the 10th close) and vol_period=10
        # (RollingRealizedVolatility needs period+1=11 closes) -- so bands
        # warm up exactly one bar before vol-targeting does, forcing a
        # single rejection-then-retry cycle: the real bug
        # mean_reversion.py's own docstring documents fixing for its
        # sibling strategy (a signal that fires the trigger but gets
        # rejected purely by sizing must not be silently "consumed").
        strategy = _strategy(vwap_period=10, vol_period=10, reference_equity=Decimal("10000"))
        window: list[Kline] = []

        # Bars 0-8: flat at 100, neither bands (needs 10) nor vol (needs
        # 11) are warm yet.
        for i, close in enumerate(["100"] * 9):
            window.append(_minute_kline(i, close, volume="1"))
            intent = strategy(window)
            assert intent is None

        # Bar 9 (10th close): a drop to 50 -- bands are now warm and
        # oversold (empirically confirmed to breach at this exact window
        # shape), but vol-targeting is not (only 10 closes, needs 11) --
        # entry must be REJECTED (no crash, no intent), and the raw
        # signal must not be silently consumed by this rejection.
        window.append(_minute_kline(9, "50", volume="1"))
        intent = strategy(window)
        assert intent is None
        assert strategy.position_sign == 0

        # Bar 10 (11th close): a further close of 40 -- still oversold
        # (empirically confirmed), and vol-targeting now has its 11th
        # close and produces a real reading -- this must now succeed,
        # proving the entry was retried rather than permanently lost.
        window.append(_minute_kline(10, "40", volume="1"))
        intent = strategy(window)
        assert intent is not None
        assert intent.side == Side.LONG
        assert strategy.position_sign == 1


# ---------------------------------------------------------------------------
# VwapMidReversionTrainable
# ---------------------------------------------------------------------------


class TestTrainable:
    def test_fit_returns_a_fresh_strategy_instance_not_the_scoring_one(self, tmp_path):
        runs_path = str(tmp_path / "runs.jsonl")
        trainable = VwapMidReversionTrainable(
            strategy_id="vwap-mid-reversion-test",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("10"),
            bars_per_day=1440,
            vwap_period=5,
            vol_period=2,
            runs_path=runs_path,
        )
        klines = _flat_klines(30, price="100")
        strategy = trainable.fit(klines, {"symbol": "BTC-USDT"}, parent_run_id="parent-1")
        assert isinstance(strategy, VwapMidReversionStrategy)
        # A freshly-returned strategy has seen zero bars of its own.
        assert strategy.bars_seen == 0

    def test_fit_logs_exactly_one_candidate_record(self, tmp_path):
        from research.experiment_log import read_records

        runs_path = str(tmp_path / "runs.jsonl")
        trainable = VwapMidReversionTrainable(
            strategy_id="vwap-mid-reversion-test",
            strategy_version="v1",
            fee_bps=Decimal("5"),
            slippage_bps=Decimal("10"),
            bars_per_day=1440,
            vwap_period=5,
            vol_period=2,
            runs_path=runs_path,
        )
        klines = _flat_klines(30, price="100")
        trainable.fit(klines, {"symbol": "BTC-USDT"}, parent_run_id="parent-2")
        records = list(read_records(runs_path))
        assert len(records) == 1
        assert records[0]["strategy_id"] == "vwap-mid-reversion-test"
        assert records[0]["is_holdout_run"] is False
        assert records[0]["candidate_index"] == 0
        assert records[0]["total_candidates"] == 1


# ---------------------------------------------------------------------------
# Pre-registration sanity check -- loads cleanly, no data access.
# ---------------------------------------------------------------------------


class TestPreregistrationLoads:
    def test_the_committed_registration_validates_cleanly(self):
        prereg = load_preregistration(PREREGISTRATION_PATH)
        assert prereg.strategy_id == "vwap-mid-reversion"
        assert prereg.strategy_family == "btc-scalping"
        assert prereg.is_holdout_confirmation is True
        assert prereg.total_candidates == 1
