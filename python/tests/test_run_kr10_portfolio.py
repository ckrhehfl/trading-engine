"""Tests for `research.run_kr10_portfolio` — Multi-Asset Task F.

Two of these exist because the first execution of the real run got them
wrong, and both errors were the kind that produce a confident wrong
number rather than a crash:

- an **annualized** Sharpe fed to `evaluate_psr`, which takes a
  per-observation one, saturating PSR at 1.0 and reporting a spurious
  PASS on that gate;
- the expected-bar-count check running **after** the single-access
  holdout claim was consumed, so an off-by-one in a range bound spent a
  one-shot resource.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from metrics.metrics import Metrics
from research.run_kr10_portfolio import (
    MemberResult,
    PortfolioRunError,
    aggregate,
    evaluate,
    preflight_bar_counts,
)


def _metrics(curve: list[str], *, trades: int = 5) -> Metrics:
    eq = [Decimal(c) for c in curve]
    return Metrics(
        starting_equity=eq[0],
        final_equity=eq[-1],
        equity_curve=eq,
        closed_trades=[],
        total_return=(eq[-1] - eq[0]) / eq[0],
        sharpe_ratio=None,
        max_drawdown=Decimal("0"),
        win_rate=None,
        num_trades=trades,
        profit_factor=None,
    )


def _member(name: str, curve: list[str], *, trades: int = 5) -> MemberResult:
    return MemberResult(symbol=f"KRX:{name}", name=name, metrics=_metrics(curve, trades=trades))


class _Prereg:
    """Only what `evaluate` reads."""

    def __init__(self, **over):
        self.primary_criterion = {
            "threshold": 0.95,
            "max_drawdown_ceiling": 0.20,
            "min_total_trades": 94,
            "profit_factor_floor": 1.3,
            **over,
        }
        self.config = {"declared_detection_floor_sharpe": 0.5942}


# ------------------------------------------------------------ aggregation


def test_the_portfolio_curve_is_the_sum_of_member_curves():
    members = [_member("a", ["100", "110", "120"]), _member("b", ["100", "90", "100"])]
    result = aggregate(members, bars_per_day=1)
    assert result.equity_curve == [Decimal("200"), Decimal("200"), Decimal("220")]


def test_trades_are_summed_across_members():
    members = [_member("a", ["100", "101"], trades=7), _member("b", ["100", "101"], trades=11)]
    assert aggregate(members, bars_per_day=1).total_trades == 18


def test_curves_of_differing_length_are_refused_rather_than_truncated():
    """Truncating would silently misalign dates between members, and a
    portfolio built from misaligned series is not the registered one."""
    members = [_member("a", ["100", "110", "120"]), _member("b", ["100", "90"])]
    with pytest.raises(PortfolioRunError, match="misalign"):
        aggregate(members, bars_per_day=1)


def test_the_portfolio_sharpe_is_not_the_mean_of_member_sharpes():
    """The hypothesis is about what aggregation does to volatility.
    Averaging the inputs would assume away the thing being measured: two
    members moving oppositely have a flat portfolio, whatever their own
    Sharpes are."""
    up = _member("up", ["100", "110", "120", "130"])
    down = _member("down", ["100", "90", "80", "70"])
    result = aggregate([up, down], bars_per_day=1)
    assert result.equity_curve == [Decimal("200")] * 4
    assert result.sharpe_ratio is None  # zero variance, not a fabricated 0.0


def test_psr_is_not_saturated_by_a_realistic_curve():
    """The regression for the real bug. An annualized Sharpe fed to
    `evaluate_psr` over ~1,900 daily points saturates at 1.0; the correct
    helper resamples and derives everything from one series."""
    curve = [Decimal(100 + i * 0.02 + (i % 7) * 0.5) for i in range(400)]
    result = aggregate([MemberResult("KRX:x", "x", _metrics([str(c) for c in curve]))],
                       bars_per_day=1)
    assert result.psr is not None
    assert 0.0 <= result.psr < 1.0, f"PSR saturated at {result.psr}"


# --------------------------------------------------------------- gating


def _result(**over):
    base = aggregate([_member("a", ["100", "110"], trades=100)], bars_per_day=1)
    from dataclasses import replace

    return replace(base, **over)


def test_every_gate_must_pass_for_a_pass():
    r = _result(psr=0.99, sharpe_ratio=0.9, max_drawdown=Decimal("0.10"),
                profit_factor=2.0, total_trades=500)
    assert evaluate(r, _Prereg()).verdict == "PASS"


@pytest.mark.parametrize(
    "over",
    [
        {"psr": 0.90},
        {"sharpe_ratio": 0.50},
        {"max_drawdown": Decimal("0.25")},
        {"profit_factor": 1.1},
        {"total_trades": 50},
    ],
    ids=["psr", "sharpe", "drawdown", "profit_factor", "trades"],
)
def test_any_single_miss_is_inconclusive_not_a_pass(over):
    base = dict(psr=0.99, sharpe_ratio=0.9, max_drawdown=Decimal("0.10"),
                profit_factor=2.0, total_trades=500)
    base.update(over)
    assert evaluate(_result(**base), _Prereg()).verdict == "INCONCLUSIVE"


def test_a_non_positive_psr_is_a_fail_not_an_inconclusive():
    r = _result(psr=0.0, sharpe_ratio=0.9, max_drawdown=Decimal("0.10"),
                profit_factor=2.0, total_trades=500)
    assert evaluate(r, _Prereg()).verdict == "FAIL"


def test_a_sharpe_at_the_detection_floor_does_not_clear_it():
    """Strictly above. At the floor the result is indistinguishable from
    noise, which is the whole point of declaring one."""
    r = _result(psr=0.99, sharpe_ratio=0.5942, max_drawdown=Decimal("0.10"),
                profit_factor=2.0, total_trades=500)
    assert evaluate(r, _Prereg()).gates["sharpe_above_detection_floor"] is False


def test_a_missing_statistic_never_counts_as_a_pass():
    r = _result(psr=None, sharpe_ratio=None, max_drawdown=Decimal("0.10"),
                profit_factor=None, total_trades=500)
    g = evaluate(r, _Prereg()).gates
    assert g["psr"] is False and g["profit_factor"] is False
    assert g["sharpe_above_detection_floor"] is False


# ------------------------------------------------------------- preflight


def test_preflight_counts_without_consuming_anything(tmp_path):
    """It runs before the holdout claim, so it must reach the store on its
    own rather than through the claiming loader."""
    from data.store import connect, upsert_klines
    from data.kis_klines import trading_date_to_ms
    from data.bingx_klines import KlineRow

    db = tmp_path / "k.sqlite3"
    conn = connect(db)
    rows = [
        KlineRow(
            open_time_ms=trading_date_to_ms(d),
            open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
            close=Decimal("2"), volume=Decimal("1"),
        )
        for d in ("20240102", "20240103", "20240104")
    ]
    upsert_klines(conn, "KRX:005930", "1d", rows)
    conn.close()

    counts = preflight_bar_counts(
        ["KRX:005930", "KRX:000660"], "1d",
        trading_date_to_ms("20240101"), trading_date_to_ms("20240201"), db,
    )
    assert counts == {"KRX:005930": 3, "KRX:000660": 0}


def test_preflight_respects_the_half_open_upper_bound(tmp_path):
    """The off-by-one that spent a one-shot claim on the first real run:
    an end bound set to the last bar's own open time excludes it."""
    from data.store import connect, upsert_klines
    from data.kis_klines import trading_date_to_ms
    from data.bingx_klines import KlineRow

    db = tmp_path / "k.sqlite3"
    conn = connect(db)
    upsert_klines(conn, "KRX:005930", "1d", [
        KlineRow(open_time_ms=trading_date_to_ms(d), open=Decimal("1"), high=Decimal("2"),
                 low=Decimal("1"), close=Decimal("2"), volume=Decimal("1"))
        for d in ("20240102", "20240103")
    ])
    conn.close()
    lo = trading_date_to_ms("20240102")
    last = trading_date_to_ms("20240103")
    assert preflight_bar_counts(["KRX:005930"], "1d", lo, last, db) == {"KRX:005930": 1}
    assert preflight_bar_counts(["KRX:005930"], "1d", lo, last + 86_400_000, db) == {
        "KRX:005930": 2
    }
