"""The independent payoff audit can reuse only immutable prior-session bounds."""

from collections import Counter
from datetime import datetime, timedelta
from decimal import Decimal
from fractions import Fraction

import pytest

from research.intraday_campaign_readiness import KST
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_scenario_management_prices_reference import (
    audited_bar_reader, audited_session_bounds, reconstruct_held_values,
)


def _session(symbol, opening):
    price = Decimal(100)
    minutes = {at: Minute(symbol, at, at + 3 * MINUTE, ohlc=(price,) * 4, shares=None)
               for at in (opening + index * MINUTE for index in range(22))}
    return SessionInputs(symbol, opening, opening + 22 * MINUTE, minutes)


@pytest.mark.parametrize("source_missing", (False, True))
def test_reusing_prior_bounds_keeps_exact_results_and_missing_status(source_missing):
    opening = datetime(2026, 1, 8, 9, tzinfo=KST)
    prior = opening - timedelta(days=1)
    day, source_day = (clock.date().isoformat() for clock in (opening, prior))
    sources = {(day, "QUERY"): _session("QUERY", opening)}
    if not source_missing:
        sources[(source_day, "PRIOR")] = _session("PRIOR", prior)

    def run(cache_bounds):
        reads = Counter()

        def read_session(day, symbol):
            reads[(day, symbol)] += 1
            return sources.get((day, symbol))

        read_bar = audited_bar_reader(read_session)
        read_bounds = audited_session_bounds(read_session) if cache_bounds else None
        results = [
            reconstruct_held_values(day=day, symbol="QUERY", at=opening + 7 * MINUTE, cap=opening + 17 * MINUTE,
                cutoff=opening, entered_at=opening + MINUTE, quantity=4, half_reduced=False,
                stop=Fraction(90), downside_penalty=Fraction(1), sources=((source_day, "PRIOR", prior + 7 * MINUTE),),
                read_session=read_session, read_bar=read_bar, read_bounds=read_bounds)
            for _ in range(4)
        ]
        return results, reads[(source_day, "PRIOR")]

    expected, repeated = run(False)
    actual, cached = run(True)
    assert actual == expected
    assert all(reason == ("selected_prior_session_missing" if source_missing else "ready") for reason, _ in actual)
    assert repeated - cached == 3


def test_compact_audited_prices_keep_exact_values_and_unknown_availability():
    opening = datetime(2026, 1, 8, 9, tzinfo=KST)
    later = opening + MINUTE
    prices = (Decimal("100.1"), Decimal("101"), Decimal("99"), Decimal("99.75"))
    session = SessionInputs("A", opening, opening + 10 * MINUTE, {
        opening: Minute("A", opening, opening + 3 * MINUTE, ohlc=prices, shares=None),
        later: Minute("A", later, later + 4 * MINUTE, ohlc=None, shares=None, price_state="conflict"),
    })
    read = audited_bar_reader(lambda day, symbol: session)
    day = opening.date().isoformat()
    quote = read(day, "A", opening)
    assert (quote.open_price, quote.close_price, quote.available_at) == (
        Fraction("100.1"), Fraction("99.75"), opening + 3 * MINUTE)
    unknown = read(day, "A", later)
    assert unknown.open_price is unknown.close_price is None
    assert unknown.available_at == later + 4 * MINUTE
    assert read(day, "A", later + MINUTE) is None
