"""Invented regressions for independent first-entry trace validation."""

from collections.abc import Mapping
from dataclasses import replace
from fractions import Fraction

import pytest

from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_management_entry_reference import verify_initial_entry
from research.intraday_scenario_management_execution import _row
from test_intraday_scenario_management_compare import SYMBOL, at, row
from test_intraday_scenario_management_day import DAY, _day, _run, _session


def _trace(quantity=10, *, price="590", status="confirmed", outcome="modeled", reason="entered"):
    return {
        "day": DAY, "rank": 1, "symbol": SYMBOL, "status": status, "reason": reason,
        "initial": {"stop": "565", "maximum": "605", "trade": [
            "enter", at(27).isoformat(), at(28).isoformat(), quantity, outcome,
            quantity if outcome == "modeled" else 0, price, "higher_value_of_entry",
        ]},
        "event_expiry": at(64).isoformat() if status == "confirmed" else None,
    }


def _verify(trace, day, source, *, cost=33, risk=880):
    verify_initial_entry(trace, day, scenario="S1", read_session=lambda *_: source,
                         risk_budget=Fraction(risk), cost_bps=cost)


@pytest.fixture
def ready():
    session = _session(SYMBOL, overrides=(row(28, opened=590),))
    day, _ = _day({SYMBOL: session})
    return day, session


@pytest.mark.parametrize("cost", (33, 43))
def test_verifies_saved_first_requests_and_unattempted_statuses_for_every_rank(cost):
    below = tuple(row(index, 560) for index in range(20, 65))
    sessions = {
        SYMBOL: _session(SYMBOL, overrides=(row(28, opened=590),)),
        "invented-declined": _session("invented-declined", overrides=(row(28, opened=606),)),
        "invented-unresolved": _session("invented-unresolved", omit=(at(28),)),
        "invented-later-gap": _session("invented-later-gap", overrides=below, omit=(at(33),)),
        "invented-approach": _session("invented-approach", overrides=below),
        "invented-quiet": _session("invented-quiet", overrides=tuple(
            row(index, 560, high=561, low=559) for index in range(15, 65))),
        "invented-late-breakout": _session("invented-late-breakout", overrides=below[:20]),
    }
    day, symbols = _day(sessions)
    result = _run(day, lambda _, symbol: sessions[symbol], cost=cost)
    assert [case.status for case in result.cases[:7]] == [
        "confirmed", "declined", "unresolved", "unknown", "no_entry", "no_event", "unsupported",
    ]
    read = []

    def provider(day_key, symbol):
        read.append((day_key, symbol))
        return sessions[symbol]

    for rank, case in enumerate(result.cases, 1):
        verify_initial_entry(_row(DAY, rank, case), day, scenario="S1", read_session=provider,
                             risk_budget=Fraction(880), cost_bps=cost)
    assert read == [(DAY, symbol) for symbol in symbols[:3]]


@pytest.mark.parametrize(("cost", "risk", "quantity"), ((33, 842, 10), (43, 842, 9), (43, 10_000_000, 3305)))
def test_frozen_quantity_accounts_for_cost_and_notional_cap_before_price_improvement(ready, cost, risk, quantity):
    day, session = ready
    _verify(_trace(quantity), day, session, cost=cost, risk=risk)
    with pytest.raises(InputRefusal, match="request_differs"):
        _verify(_trace(quantity + 1), day, session, cost=cost, risk=risk)


@pytest.mark.parametrize(("path", "value", "error"), [
    (("rank",), 2, "coordinate_differs"),
    (("initial", "stop"), "564", "frozen_bounds_differ"),
    (("initial", "maximum"), "606", "frozen_bounds_differ"),
    (("initial", "trade", 1), at(22).isoformat(), "request_differs"),
    (("initial", "trade", 2), at(29).isoformat(), "request_differs"),
    (("initial", "trade", 3), True, "request_differs"),
    (("initial", "trade", 5), 9, "modeled_open_differs"),
    (("initial", "trade", 6), "591", "modeled_open_differs"),
    (("status",), "unresolved", "modeled_open_differs"),
    (("event_expiry",), at(63).isoformat(), "modeled_open_differs"),
])
def test_rejects_wrong_sealed_trace_fields(ready, path, value, error):
    trace = _trace()
    target = trace
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(InputRefusal, match=error):
        _verify(trace, *ready)


def test_omitted_breakout_and_insufficient_risk_never_read_the_next_open(ready):
    day, session = ready

    class DecisionPrefix(Mapping):
        def __getitem__(self, clock):
            assert clock <= at(24)
            return session.minutes[clock]

        def __len__(self):
            return len(session.minutes)

        def __iter__(self):
            raise AssertionError("the auditor must request exact minute keys")

    prefix = replace(session, minutes=DecisionPrefix())
    trace = dict(_trace(), status="no_entry", reason="insufficient_risk_or_cash_for_one_unit",
                 initial=None, event_expiry=None)
    with pytest.raises(InputRefusal, match="frozen_bounds_differ"):
        _verify(trace, day, prefix)
    _verify(trace, day, prefix, risk=1)


def test_late_open_remains_unresolved_and_limit_prices_use_exact_bounds(ready):
    day, _ = ready
    late = replace(row(28), available_at=at(32))
    _verify(_trace(price=None, status="unresolved", outcome="indeterminate", reason="entry_price_unknown"),
            day, _session(SYMBOL, overrides=(late,)))
    _verify(_trace(price="565", status="declined", outcome="declined", reason="open_outside_frozen_entry_bounds"),
            day, _session(SYMBOL, overrides=(row(28, opened=565),)))
    _verify(_trace(price="605"), day, _session(SYMBOL, overrides=(row(28, opened=605),)))


@pytest.mark.parametrize(("changed", "error"), [
    (row(21, low=561), "breakout_bounds_differ"),
    (row(21, high=612), "breakout_price_differs"),
    (replace(row(24), available_at=at(28)), "breakout_price_unavailable"),
])
def test_request_uses_the_authenticated_breakout_bar_prefix(ready, changed, error):
    day, _ = ready
    with pytest.raises(InputRefusal, match=error):
        _verify(_trace(), day, _session(SYMBOL, overrides=(changed,)))
