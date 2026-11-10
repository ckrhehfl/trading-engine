from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal

import pytest

from research import intraday_candidate_laggard_peers as peers
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2026-01-02T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2026-01-02T15:30:00+09:00")
DECISION = OPEN + 62 * MINUTE
SYMBOLS = tuple(f"{index:06d}" for index in range(20))
TARGET = SYMBOLS[0]
MID = OPEN + 29 * MINUTE
LAST = OPEN + 59 * MINUTE
CLASSIFIERS = (peers.classify_day, peers.reference_classify_day)
PEER_ENDS = (94, 95, 96, 97, 99, 100, 101, 102)


def _bar(symbol, start, price):
    close = Decimal(price)
    return Minute(symbol, start, start + 3 * MINUTE, (close,) * 4, None, volume_state="missing")


def _inputs(last=97, peer_ends=PEER_ENDS):
    paths = {symbol: {"state": "retain"} for symbol in SYMBOLS}
    paths[TARGET] = {"state": "weak", "mid_close": "100", "last_close": str(last)}
    endpoints = dict(zip(SYMBOLS, (last, *peer_ends)))
    sessions = {
        symbol: SessionInputs(symbol, OPEN, CLOSE,
                              {MID: _bar(symbol, MID, 100), LAST: _bar(symbol, LAST, endpoints[symbol])}
                              if symbol in endpoints else {})
        for symbol in SYMBOLS
    }
    return paths, sessions


def _both(paths, sessions):
    result = peers.classify_day(paths, DECISION, sessions)
    assert result == peers.reference_classify_day(paths, DECISION, sessions)
    return result


@pytest.mark.parametrize(("last", "state", "reason", "value"), [
    (97, "weaker", "target_below_peer_median", "-3/100"),
    (98, "other", "target_at_or_above_peer_median", "-1/50"),
    (99, "other", "target_at_or_above_peer_median", "-1/100"),
])
def test_exact_leave_one_out_median_tie_and_direction(last, state, reason, value):
    paths, sessions = _inputs(last)
    # A different sealed path may still supply a valid same-day peer return.
    paths[SYMBOLS[1]] = {"state": "unresolved"}
    paths[SYMBOLS[2]] = {"state": "rebound"}
    result = _both(paths, sessions)
    assert result.keys() == paths.keys()
    assert result[TARGET] == {"state": state, "reason": reason, "target_return": value,
                              "peer_count": 8, "peer_median": "-1/50"}
    assert result[SYMBOLS[1]] == {"state": "retain", "reason": "original_path_not_weak",
                                  "target_return": None, "peer_count": None, "peer_median": None}


def test_target_does_not_supply_its_own_eighth_observation():
    paths, sessions = _inputs(peer_ends=PEER_ENDS[:-1])
    assert _both(paths, sessions)[TARGET] == {
        "state": "unresolved", "reason": "peer_quorum_unmet", "target_return": "-3/100",
        "peer_count": 7, "peer_median": None,
    }


def test_independent_evidence_survives_missing_target_price():
    paths, sessions = _inputs()
    sessions[TARGET] = replace(sessions[TARGET], minutes={MID: sessions[TARGET].minutes[MID]})
    assert _both(paths, sessions)[TARGET] == {
        "state": "unresolved", "reason": "target_prices_unavailable", "target_return": None,
        "peer_count": 8, "peer_median": "-1/50",
    }


def test_each_weak_member_excludes_itself_from_the_same_cohort():
    paths, sessions = _inputs()
    second = SYMBOLS[1]
    paths[second] = {"state": "weak", "mid_close": "100", "last_close": "94"}
    result = _both(paths, sessions)
    assert (result[TARGET]["peer_count"], result[TARGET]["peer_median"]) == (8, "-1/50")
    assert (result[second]["peer_count"], result[second]["peer_median"]) == (8, "-1/50")
    assert result[second]["target_return"] == "-3/50"


@pytest.mark.parametrize("classify", CLASSIFIERS)
@pytest.mark.parametrize("at", (MID, LAST))
def test_observed_target_close_must_match_even_if_other_bar_is_missing(classify, at):
    paths, sessions = _inputs()
    sessions[TARGET] = replace(sessions[TARGET], minutes={at: _bar(TARGET, at, 96)})
    with pytest.raises(ValueError, match="target_close_differs"):
        classify(paths, DECISION, sessions)


def test_invalid_or_noncausal_peer_bar_is_missing():
    paths, sessions = _inputs()
    symbol = SYMBOLS[1]
    session = sessions[symbol]
    bar = session.minutes[LAST]
    for invalid in (
        replace(bar, available_at=DECISION + MINUTE),
        replace(bar, available_at=DECISION - MINUTE),
        replace(bar, available_at="invalid"),
        replace(bar, symbol=TARGET),
        replace(bar, start_at=LAST + MINUTE),
        replace(bar, price_state="missing"),
        replace(bar, ohlc=(Decimal(94), Decimal(94), Decimal(95), Decimal(94))),
    ):
        sessions[symbol] = replace(session, minutes={MID: session.minutes[MID], LAST: invalid})
        result = _both(paths, sessions)[TARGET]
        assert (result["state"], result["peer_count"], result["peer_median"]) == ("unresolved", 7, None)


class _BoundedPrices(Mapping):
    def __init__(self, prices):
        self.prices = prices
        self.accesses = []

    def __getitem__(self, at):
        assert at in (MID, LAST), "read outside fixed peer bars"
        self.accesses.append(at)
        return self.prices[at]

    def __len__(self):
        return len(self.prices)

    def __iter__(self):
        raise AssertionError("scanned prices instead of requesting fixed peer bars")


def test_reads_only_fixed_prices_and_audit_reader_is_distinct(monkeypatch):
    paths, sessions = _inputs()
    watches = {}
    for symbol, session in sessions.items():
        later = OPEN + 63 * MINUTE
        watches[symbol] = _BoundedPrices({**session.minutes, later: _bar(symbol, later, 500)})
        sessions[symbol] = replace(session, minutes=watches[symbol])
    assert _both(paths, sessions)[TARGET]["state"] == "weaker"
    assert all(watch.accesses == [MID, LAST, MID, LAST] for watch in watches.values())

    def refuse(*args):
        raise AssertionError("reference used production quote reader")

    monkeypatch.setattr(peers, "_minute", refuse)
    assert peers.reference_classify_day(paths, DECISION, sessions)[TARGET]["state"] == "weaker"


@pytest.mark.parametrize("classify", CLASSIFIERS)
def test_no_weak_path_needs_no_sessions_and_bad_cohort_is_refused(classify):
    paths, sessions = _inputs()
    no_weak = {**paths, TARGET: {"state": "rebound"}}
    assert {row["state"] for row in classify(no_weak, DECISION, {}).values()} == {"retain"}
    with pytest.raises(ValueError, match="original_roster_differs"):
        classify({symbol: paths[symbol] for symbol in SYMBOLS[1:]}, DECISION, {})
    with pytest.raises(ValueError, match="session_roster_differs"):
        classify(paths, DECISION, {symbol: sessions[symbol] for symbol in SYMBOLS[1:]})
    with pytest.raises(ValueError, match="decision_or_session_differs"):
        classify(paths, DECISION + MINUTE, sessions)
