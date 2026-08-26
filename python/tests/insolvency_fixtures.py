"""Shared test fixtures for `backtest.engine.run_backtest`'s optional
insolvency floor (Scalping Strategy Research Task S7,
`.planning/scalp-s7-backtest-insolvency-floor.md`).

Used identically by `test_walkforward.py` and
`test_run_preregistered_holdout.py`'s own bounded-vs-unbounded
integration tests -- centralized here (CodeRabbit review finding on the
PR that added this) so the two tests can't silently drift into proving
two different scenarios while claiming to test the same circuit breaker.
Follows this test suite's existing `fake_*_server.py` precedent for a
shared, non-`test_`-prefixed helper module (pytest's default collection
pattern never picks this file up as its own test module).
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from backtest.kline import Kline
from schemas.order_intent import OrderIntent, OrderType, Side

BASE_TIME = datetime(2026, 1, 1, tzinfo=timezone.utc)


def crashing_klines(count: int, start_price: int, drop: int) -> list[Kline]:
    """Every bar's `open == close`, monotonically decreasing by `drop` per
    bar -- a simple, hand-verifiable "the market only ever moves against a
    long position" fixture.
    """
    return [
        Kline(
            open_time=BASE_TIME + timedelta(minutes=i),
            open=Decimal(start_price - drop * i),
            high=Decimal(start_price - drop * i + 1),
            low=Decimal(start_price - drop * i - 1),
            close=Decimal(start_price - drop * i),
            volume=Decimal("1"),
        )
        for i in range(count)
    ]


class RepeatedLosingRoundTripStrategy:
    """A `TrainableStrategy` test double mirroring the real mechanism
    behind this task's own two motivating catastrophic results
    (`.planning/scalp-s4-vwap-mid-reversion-result.md`,
    `.planning/scalp-s6-ofi-momentum-result.md`): many repeated,
    fixed-size open/close round trips against a market that only ever
    moves against the position, each one a real, small realized loss --
    not one single runaway position, but many discrete losing trades
    compounding one after another for as long as the strategy keeps
    trading. `fit()` ignores `train_klines` entirely -- what matters here
    is the returned strategy's own behavior against whatever window it's
    scored against.
    """

    def fit(self, train_klines, params, *, parent_run_id=None):
        state = {"flat": True}

        def _strategy(visible_klines):
            i = len(visible_klines) - 1
            if state["flat"]:
                state["flat"] = False
                side = Side.LONG
            else:
                state["flat"] = True
                side = Side.SHORT
            return OrderIntent(
                intent_id=UUID(int=i + 1),
                symbol="BTC-USDT",
                side=side,
                order_type=OrderType.GUARDED_MARKET,
                quantity=Decimal("10"),
                limit_price=None,
                signal_timeframe="1m",
                created_at=visible_klines[-1].open_time,
            )

        return _strategy
