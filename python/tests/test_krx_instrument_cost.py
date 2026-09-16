"""Tests for `research.krx_instrument_cost`.

The module answers an operator decision CLAUDE.md has carried open, so
the properties that matter are the ones that stop it from answering it
flatteringly:

- **The futures figure is a lower bound**, because its commission is
  unsourced. A futures *win* is therefore the weaker claim and a futures
  *loss* the stronger one, and the code has to be built that way round.
- **An empty book is not an error.** KIS answers `rt_cd=0` with nothing
  for an expired or unlisted contract, and treating that as a failure
  would have hidden the expiry roll that made six guessed codes look
  unavailable.
- **The spread convention matches rd-f's**, or the two numbers cannot be
  compared at all.
"""

from __future__ import annotations

import pytest

from research.krx_instrument_cost import (
    CONTRACT_SHARES,
    SPOT_COMMISSION_BP,
    SPOT_TAX_BP,
    InstrumentCost,
    spread_bp,
)


def _cost(spot=12.0, fut=20.0, volume=100_000, price=250_000.0):
    return InstrumentCost(
        code="005930", name="005930", futures_code="A11610",
        spot_spread_bp=spot, futures_spread_bp=fut,
        futures_volume=volume, futures_price=price,
    )


# --------------------------------------------------------- the spread


def test_the_spread_is_measured_against_the_mid():
    """rd-f §1.2's convention. Against the bid or the ask instead, the two
    documents' numbers would not be comparable, which is the only reason
    this measurement exists."""
    assert spread_bp(250_500, 250_000) == pytest.approx(
        500 / 250_250 * 1e4, rel=1e-9
    )


def test_a_one_tick_book_on_a_large_price_is_still_wide():
    """삼성전자's futures tick is 500 on a 250,000 price — 20bp for a
    single tick. A tick is a fraction of price, not a small number."""
    assert spread_bp(250_500, 250_000) == pytest.approx(20.0, abs=0.1)


def test_a_touching_book_costs_nothing():
    assert spread_bp(100.0, 100.0) == 0.0


def test_a_crossed_book_is_refused():
    """Ask below bid is not a tight spread, it is bad data — and it would
    read as a negative cost."""
    with pytest.raises(ValueError, match="crossed book"):
        spread_bp(99.0, 100.0)


@pytest.mark.parametrize("ask,bid", [(0.0, 1.0), (1.0, 0.0), (-1.0, -2.0)])
def test_a_non_positive_quote_is_refused(ask, bid):
    with pytest.raises(ValueError, match="must be positive"):
        spread_bp(ask, bid)


# ----------------------------------------------------- the comparison


def test_the_spot_round_trip_carries_the_tax_and_the_commission():
    """rd-f §1's form, reproduced exactly so the two are comparable."""
    r = _cost(spot=12.1)
    assert r.spot_round_trip_bp == pytest.approx(SPOT_TAX_BP + 12.1 + SPOT_COMMISSION_BP)


def test_the_futures_round_trip_is_spread_only_and_that_favours_futures():
    """**The bias is deliberate and must not be removed.** Futures
    commission is unsourced, so the futures figure is a lower bound: a
    futures win is the weaker claim, a futures loss the stronger one."""
    r = _cost(spot=12.1, fut=7.3)
    assert r.futures_round_trip_bp == 7.3, "the spread, and nothing added"
    # Neither the tax nor the commission may appear in it — asserted
    # against the value they would produce, not against a bare constant,
    # so a coincidental equality cannot make this pass.
    assert r.futures_round_trip_bp != 7.3 + SPOT_TAX_BP
    assert r.futures_round_trip_bp != 7.3 + SPOT_COMMISSION_BP
    assert r.futures_round_trip_bp < r.spot_round_trip_bp


def test_a_deep_futures_book_beats_spot_despite_no_tax_saving_being_counted():
    """SK하이닉스: 5.7bp futures against 29.2bp spot."""
    assert _cost(spot=5.7, fut=5.7).cheaper == "futures"


def test_a_thin_futures_book_loses_even_with_the_tax_removed():
    """HLB: 157bp of futures spread against a 39.3bp spot round trip. The
    tax saving is 20bp and the spread penalty is 141bp."""
    assert _cost(spot=15.8, fut=157.0).cheaper == "spot"


def test_the_boundary_is_where_the_spread_penalty_equals_the_tax_saving():
    """Futures win exactly while `futures_spread < spot_spread + 23.54`."""
    boundary = 10.0 + SPOT_TAX_BP + SPOT_COMMISSION_BP
    assert _cost(spot=10.0, fut=boundary - 0.1).cheaper == "futures"
    assert _cost(spot=10.0, fut=boundary + 0.1).cheaper == "spot"


def test_an_unquoted_instrument_yields_no_verdict_rather_than_a_default():
    """An empty book is how KIS answers for an expired contract. Calling
    that 'spot is cheaper' would turn a missing measurement into a
    finding."""
    assert _cost(fut=None).cheaper is None
    assert _cost(spot=None).cheaper is None
    assert _cost(fut=None).futures_round_trip_bp is None


# ------------------------------------------------------- contract size


def test_a_contract_is_ten_shares():
    """Verified in the master file's own description field, `F 202610
    (  10)`, for every KR-10 name. It decides whether a personal account
    can hold one at all."""
    assert CONTRACT_SHARES == 10
    assert _cost(price=250_000.0).contract_notional_krw == 2_500_000.0


def test_the_notional_is_what_a_real_order_must_clear():
    """SK하이닉스 at 1,765,000 is ₩17.65M a contract — the same arithmetic
    that ruled out KOSPI200 index futures at ₩265M against this project's
    2% max-order-notional limit."""
    assert _cost(price=1_765_000.0).contract_notional_krw == 17_650_000.0


# ------------------------------------------------------- the constants


def test_the_tax_is_spot_only():
    """The entire premise: futures pay no 증권거래세. If this ever becomes
    non-zero for futures the comparison has to be rebuilt, not adjusted."""
    assert SPOT_TAX_BP == 20.0
    assert _cost(fut=1.0).futures_round_trip_bp == 1.0


def test_the_commission_matches_rd_f():
    assert SPOT_COMMISSION_BP == 3.54
