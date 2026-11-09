from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
import unittest
from unittest.mock import patch

from research import intraday_candidate_laggard_confirmation as gate
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2026-01-02T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2026-01-02T15:30:00+09:00")
DECISION = OPEN + 62 * MINUTE
STOP = Fraction(100)
SYMBOL = "000001"


def _bar(offset, close):
    at = DECISION + offset * MINUTE
    price = Decimal(close)
    return Minute(SYMBOL, at, at + 3 * MINUTE, (price,) * 4, None, volume_state="missing")


def _session(*closes):
    bars = [_bar(offset, close) for offset, close in enumerate(closes) if close is not None]
    return SessionInputs(SYMBOL, OPEN, CLOSE, {bar.start_at: bar for bar in bars})


class _OnlyConfirmationPrices(Mapping):
    def __init__(self, minutes):
        self.minutes = minutes
        self.allowed = {DECISION + offset * MINUTE for offset in range(3)}
        self.accesses = []

    def __getitem__(self, at):
        if at not in self.allowed:
            raise AssertionError("read outside confirmation window")
        self.accesses.append(at)
        return self.minutes[at]

    def __len__(self):
        return len(self.minutes)

    def __iter__(self):
        raise AssertionError("scanned prices instead of requesting fixed coordinates")


class LaggardConfirmationTest(unittest.TestCase):
    def _checked(self, session):
        result = gate.confirmation(session, DECISION, STOP)
        self.assertEqual(result, gate.reference_confirmation(session, DECISION, STOP))
        return result

    def test_last_equal_to_first_passes_with_exact_prices(self):
        self.assertEqual(self._checked(_session("100.01", "100.5", "100.01")),
                         {"state": "select", "reason": "confirmation_stable",
                          "closes": ["10001/100", "201/2", "10001/100"]})

    def test_each_close_must_be_strictly_above_stop(self):
        for offset in range(3):
            with self.subTest(offset=offset):
                closes = [101, 102, 103]
                closes[offset] = 100
                self.assertEqual(self._checked(_session(*closes)),
                                 {"state": "decline", "reason": "confirmation_at_or_below_stop",
                                  "closes": [str(close) for close in closes]})

    def test_last_below_first_declines_even_when_all_above_stop(self):
        self.assertEqual(self._checked(_session(103, 104, 102)),
                         {"state": "decline", "reason": "confirmation_close_weaker",
                          "closes": ["103", "104", "102"]})

    def test_missing_close_is_unresolved_even_with_a_known_stop_breach(self):
        self.assertEqual(self._checked(_session(99, None, 102)),
                         {"state": "unresolved", "reason": "confirmation_close_unknown",
                          "closes": ["99", None, "102"]})

    def test_invalid_quote_does_not_become_a_known_close(self):
        session = _session(101, 102, 103)
        at = DECISION + 2 * MINUTE
        last = session.minutes[at]
        for bar in (replace(last, available_at=DECISION + 6 * MINUTE),
                    replace(last, available_at=DECISION + 4 * MINUTE),
                    replace(last, available_at="invalid"), replace(last, symbol="000002"),
                    replace(last, start_at=DECISION), replace(last, price_state="missing"),
                    replace(last, ohlc=(Decimal(103), Decimal(102), Decimal(101), Decimal(103))),
                    replace(last, ohlc=(Decimal(0),) * 4)):
            with self.subTest(bar=bar):
                invalid = replace(session, minutes={**session.minutes, at: bar})
                self.assertEqual(self._checked(invalid),
                                 {"state": "unresolved", "reason": "confirmation_close_unknown",
                                  "closes": ["101", "102", None]})

    def test_paths_read_only_requested_prices_and_use_distinct_quote_readers(self):
        session = _session(101, 102, 103, 99)
        prices = _OnlyConfirmationPrices(session.minutes)
        session = replace(session, minutes=prices)
        expected = [DECISION + offset * MINUTE for offset in range(3)]
        self.assertEqual(self._checked(session)["state"], "select")
        self.assertEqual(prices.accesses, expected * 2)
        with patch.object(gate, "_minute", side_effect=AssertionError("production reader used")):
            self.assertEqual(gate.reference_confirmation(session, DECISION, STOP)["state"], "select")

    def test_malformed_session_decision_and_stop_are_refused(self):
        for fn in (gate.confirmation, gate.reference_confirmation):
            for session, decision, stop in ((_session(101, 102, 103), DECISION + MINUTE, STOP),
                                            (_session(101, 102, 103), DECISION.replace(tzinfo=None), STOP),
                                            (_session(101, 102, 103), DECISION, Fraction(0)),
                                            (replace(_session(), closing=DECISION + 4 * MINUTE), DECISION, STOP)):
                with self.subTest(fn=fn.__name__, decision=decision, stop=stop):
                    with self.assertRaises(ValueError):
                        fn(session, decision, stop)
