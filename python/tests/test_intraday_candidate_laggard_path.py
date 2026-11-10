from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_laggard_path as path
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2026-01-02T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2026-01-02T15:30:00+09:00")
DECISION = OPEN + 62 * MINUTE
SYMBOL = "000001"
PLAN = {"rank_state": "select", "gated_state": "decline", "order_state": "ready",
        "limit": "197/2", "return": "-3/200"}


def _bar(offset, opened, closed):
    start = OPEN + offset * MINUTE
    op, close = Decimal(opened), Decimal(closed)
    return Minute(SYMBOL, start, start + 3 * MINUTE, (op, max(op, close), min(op, close), close),
                  None, volume_state="missing")


def _session(mid="97.5"):
    bars = (_bar(0, 100, 99), _bar(29, 96, mid), _bar(59, 97, "98.5"))
    return SessionInputs(SYMBOL, OPEN, CLOSE, {bar.start_at: bar for bar in bars})


def _both(session, plan=PLAN):
    result = path.classify(SYMBOL, DECISION, plan, session)
    assert result == path.reference_classify(SYMBOL, DECISION, plan, session)
    return result


@pytest.mark.parametrize(("mid", "state", "reason", "exact_mid"), [
    ("97.5", "rebound", "late_close_above_mid", "195/2"),
    ("98.5", "weak", "late_close_at_or_below_mid", "197/2"),
    ("99.5", "weak", "late_close_at_or_below_mid", "199/2"),
])
def test_late_close_compares_with_mid_on_original_rank_only_order(mid, state, reason, exact_mid):
    assert path.ready(PLAN)
    assert _both(_session(mid)) == {
        "state": state, "reason": reason,
        "first_open": "100", "mid_close": exact_mid, "last_close": "197/2",
    }


@pytest.mark.parametrize(("offset", "field"), [(0, "first_open"), (29, "mid_close"), (59, "last_close")])
def test_missing_each_bar_preserves_individual_evidence(offset, field):
    session = _session()
    prices = dict(session.minutes)
    del prices[OPEN + offset * MINUTE]
    expected = {"state": "unresolved", "reason": "path_prices_unavailable",
                "first_open": "100", "mid_close": "195/2", "last_close": "197/2"}
    expected[field] = None
    assert _both(replace(session, minutes=prices)) == expected


def test_late_or_invalid_bar_does_not_supply_price_evidence():
    session = _session()
    start = OPEN + 59 * MINUTE
    last = session.minutes[start]
    for invalid in (replace(last, available_at=DECISION + MINUTE),
                    replace(last, available_at=DECISION - MINUTE),
                    replace(last, available_at="invalid"),
                    replace(last, symbol="000002"), replace(last, start_at=start + MINUTE),
                    replace(last, price_state="missing"),
                    replace(last, ohlc=(Decimal(97), Decimal(97), Decimal(96), Decimal("98.5")))):
        outcome = _both(replace(session, minutes={**session.minutes, start: invalid}))
        assert (outcome["state"], outcome["last_close"]) == ("unresolved", None)


@pytest.mark.parametrize("classify", (path.classify, path.reference_classify))
def test_observed_limit_and_return_mismatches_fail_even_without_mid(classify):
    session = _session()
    prices = dict(session.minutes)
    del prices[OPEN + 29 * MINUTE]
    with pytest.raises(ValueError, match="original_return_differs"):
        classify(SYMBOL, DECISION, {**PLAN, "return": "-1/200"}, replace(session, minutes=prices))
    del prices[OPEN]
    with pytest.raises(ValueError, match="last_close_differs"):
        classify(SYMBOL, DECISION, {**PLAN, "limit": "98"}, replace(session, minutes=prices))


class _BoundedPrices(Mapping):
    def __init__(self, prices, allowed):
        self.prices = prices
        self.allowed = allowed
        self.accesses = []

    def __getitem__(self, at):
        assert at in self.allowed, "read outside fixed path bars"
        self.accesses.append(at)
        return self.prices[at]

    def __len__(self):
        return len(self.prices)

    def __iter__(self):
        raise AssertionError("scanned prices instead of requesting fixed bars")


def test_reads_only_fixed_bars_and_reference_uses_its_own_reader(monkeypatch):
    session = _session()
    requested = [OPEN + offset * MINUTE for offset in path.READY_OFFSETS]
    future = _bar(63, 200, 201)
    prices = _BoundedPrices({**session.minutes, future.start_at: future}, requested)
    session = replace(session, minutes=prices)
    assert _both(session)["state"] == "rebound"
    assert prices.accesses == requested * 2

    def refuse(*args):
        raise AssertionError("reference used production quote reader")

    monkeypatch.setattr(path, "_minute", refuse)
    assert path.reference_classify(SYMBOL, DECISION, PLAN, session)["state"] == "rebound"


@pytest.mark.parametrize("classify", (path.classify, path.reference_classify))
def test_nonready_order_never_reads_prices_and_wrong_decision_refuses(classify):
    session = replace(_session(), minutes=_BoundedPrices({}, ()))
    for plan in ({"rank_state": "decline", "order_state": "ready"},
                 {"rank_state": "select", "order_state": "unresolved"},
                 {"rank_state": "select", "order_state": "decline"}):
        assert classify(SYMBOL, DECISION, plan, session) == {
            "state": "retain", "reason": "original_order_not_ready",
            "first_open": None, "mid_close": None, "last_close": None,
        }
    for decision in (DECISION - MINUTE, DECISION + MINUTE, DECISION.replace(tzinfo=None)):
        with pytest.raises(ValueError):
            classify(SYMBOL, decision, PLAN, session)
    with pytest.raises(ValueError, match="decision_differs"):
        classify("000002", DECISION, PLAN, session)
