from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import unittest
from unittest.mock import patch

from research import intraday_candidate_laggard_selection as laggards
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2026-01-02T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2026-01-02T15:30:00+09:00")
SYMBOLS = tuple(str(number) for number in range(100000, 100020))


def _minute(symbol, offset, close, *, low=None):
    at = OPEN + offset * MINUTE
    price = Decimal(close)
    prices = (price, price, price if low is None else Decimal(low), price)
    return Minute(symbol, at, at + 3 * MINUTE, prices, None, volume_state="missing")


def _sessions(closes):
    sessions = {}
    for index, symbol in reversed(tuple(enumerate(SYMBOLS))):
        prices = {}
        if index < len(closes) and closes[index] is not None:
            bars = [_minute(symbol, 0, 100)]
            bars += [_minute(symbol, offset, closes[index], low=1) for offset in range(45, 60)]
            prices = {bar.start_at: bar for bar in bars}
        sessions[symbol] = SessionInputs(symbol, OPEN, CLOSE, prices)
    return sessions


class _BoundedPrices(Mapping):
    def __init__(self, prices, offsets):
        self.prices = prices
        self.allowed = {OPEN + offset * MINUTE for offset in offsets}

    def __getitem__(self, at):
        if at not in self.allowed:
            raise AssertionError("read an unrequested minute")
        return self.prices[at]

    def __iter__(self):
        raise AssertionError("enumerated prices instead of requesting coordinates")

    def __len__(self):
        return len(self.prices)


class LaggardSelectionTest(unittest.TestCase):
    def _checked(self, sessions):
        result = laggards.select_day(sessions)
        self.assertEqual(result, laggards.reference_day(sessions))
        return result

    def test_quorum_counts_only_valid_endpoints_and_retains_known_returns(self):
        result = self._checked(_sessions([90] * 9))
        self.assertEqual(result["decision_at"], "2026-01-02T11:02:00+09:00")
        self.assertEqual(result["observed"], 9)
        self.assertIsNone(result["median_return"])
        rows = result["symbols"]
        self.assertEqual({row["rank_state"] for row in rows.values()}, {"unresolved"})
        self.assertEqual({row["gated_state"] for row in rows.values()}, {"unresolved"})
        self.assertEqual({row["reason"] for row in rows.values()}, {"basket_below_quorum"})
        self.assertEqual({row["order_state"] for row in rows.values()}, {"retain"})
        self.assertEqual(rows[SYMBOLS[8]]["return"], "-1/10")
        self.assertIsNone(rows[SYMBOLS[9]]["return"])

    def test_quintile_boundary_breaks_ties_by_literal_code_and_median_zero_passes(self):
        closes = [90, 90, 90, 95, 99, 101, 102, 103, 104, 105]
        result = self._checked(_sessions(closes))
        self.assertEqual((result["observed"], result["median_return"]), (10, "0"))
        rows = result["symbols"]
        self.assertEqual([symbol for symbol, row in rows.items() if row["rank_state"] == "select"], list(SYMBOLS[:2]))
        self.assertEqual(rows[SYMBOLS[0]], {"return": "-1/10", "rank_state": "select", "gated_state": "select",
                                           "order_state": "ready", "reason": "order_ready", "limit": "90", "stop": "1"})
        self.assertEqual(rows[SYMBOLS[2]]["reason"], "outside_laggard_quintile")
        self.assertEqual(rows[SYMBOLS[10]]["reason"], "endpoints_unavailable")

    def test_negative_median_declines_only_gated_arm_and_keeps_control_bounds(self):
        result = self._checked(_sessions([90, 91, 92, 93, 94, 95, 96, 97, 98, 99, 100]))
        self.assertEqual(result["median_return"], "-1/20")
        selected = result["symbols"][SYMBOLS[0]]
        self.assertEqual((selected["rank_state"], selected["gated_state"], selected["order_state"]),
                         ("select", "decline", "ready"))
        self.assertEqual((selected["limit"], selected["stop"]), ("90", "1"))

    def test_strict_negative_and_ordering_below_binary_float_precision(self):
        tiny_lag = "99.99999999999999999999999999999999"
        deeper_tiny_lag = "99.99999999999999999999999999999998"
        closes = [tiny_lag, 100, deeper_tiny_lag] + [101] * 7
        rows = self._checked(_sessions(closes))["symbols"]
        self.assertEqual([symbol for symbol, row in rows.items() if row["rank_state"] == "select"],
                         [SYMBOLS[0], SYMBOLS[2]])
        self.assertEqual(rows[SYMBOLS[0]]["return"], "-1/10000000000000000000000000000000000")

        rows = self._checked(_sessions([99, 100] + [101] * 8))["symbols"]
        self.assertEqual(rows[SYMBOLS[1]]["reason"], "return_not_negative")
        self.assertEqual(rows[SYMBOLS[1]]["return"], "0")

    def test_full_roster_has_four_places_including_the_exact_boundary(self):
        rows = self._checked(_sessions([90] * 20))["symbols"]
        self.assertEqual([symbol for symbol, row in rows.items() if row["rank_state"] == "select"], list(SYMBOLS[:4]))
        self.assertEqual(rows[SYMBOLS[4]]["reason"], "outside_laggard_quintile")

    def test_missing_stop_and_equal_limit_do_not_select_replacement(self):
        sessions = _sessions([90, 91, 92] + [101] * 7)
        first, second = SYMBOLS[:2]
        sessions[first].minutes.pop(OPEN + 45 * MINUTE)
        for offset in range(45, 60):
            replacement = _minute(second, offset, 91)
            sessions[second].minutes[replacement.start_at] = replacement
        rows = self._checked(sessions)["symbols"]
        self.assertEqual((rows[first]["rank_state"], rows[first]["order_state"], rows[first]["reason"],
                          rows[first]["limit"], rows[first]["stop"]),
                         ("select", "unresolved", "stop_window_unavailable", "90", None))
        self.assertEqual((rows[second]["rank_state"], rows[second]["order_state"], rows[second]["reason"],
                          rows[second]["limit"], rows[second]["stop"]),
                         ("select", "decline", "limit_at_stop", "91", "91"))
        self.assertEqual(rows[SYMBOLS[2]]["reason"], "outside_laggard_quintile")

    def test_invalid_or_late_price_keeps_unknown_distinct_from_zero(self):
        latest = OPEN + 59 * MINUTE
        cases = ({"available_at": latest + 4 * MINUTE}, {"available_at": latest + 2 * MINUTE},
                 {"available_at": None}, {"price_state": "halt", "ohlc": None},
                 {"ohlc": tuple(map(Decimal, (101, 100, 99, 101)))}, {"symbol": SYMBOLS[11]},
                 {"ohlc": tuple(map(Decimal, (101, 101, 0, 101)))},
                 {"ohlc": tuple(map(Decimal, (101, 101, 100, "NaN")))})
        for changes in cases:
            with self.subTest(changes=changes):
                sessions = _sessions([90, 91] + [100] * 9)
                prices = sessions[SYMBOLS[10]].minutes
                prices[latest] = replace(prices[latest], **changes)
                result = self._checked(sessions)
                self.assertEqual(result["observed"], 10)
                self.assertEqual(result["symbols"][SYMBOLS[9]]["return"], "0")
                self.assertEqual(result["symbols"][SYMBOLS[10]]["reason"], "endpoints_unavailable")

    def test_selection_never_looks_at_other_prices_or_future_bars(self):
        sessions = _sessions([90, 91] + [101] * 8)
        for symbol, session in tuple(sessions.items()):
            offsets = (0, *range(45, 60)) if symbol in SYMBOLS[:2] else (0, 59)
            sessions[symbol] = replace(session, minutes=_BoundedPrices(session.minutes, offsets))
        self.assertEqual(self._checked(sessions)["observed"], 10)

    def test_rejects_incomplete_or_cross_date_roster_geometry(self):
        sessions = _sessions([100] * 20)
        for calculation in (laggards.select_day, laggards.reference_day):
            with self.assertRaisesRegex(ValueError, "exactly 20"):
                calculation({symbol: sessions[symbol] for symbol in SYMBOLS[:19]})
            with self.assertRaisesRegex(ValueError, "one opening and closing"):
                calculation({**sessions, SYMBOLS[0]: replace(sessions[SYMBOLS[0]], opening=OPEN + MINUTE)})
            with self.assertRaisesRegex(ValueError, "session identity"):
                calculation({**sessions, SYMBOLS[0]: sessions[SYMBOLS[1]]})

    def test_reference_detects_forged_producer_observation(self):
        sessions = _sessions([90, 91] + [101] * 9)
        symbol = SYMBOLS[0]
        at = OPEN + 59 * MINUTE
        original = laggards._minute
        forged = tuple(map(Decimal, (99, 99, 99, 99)))
        with patch.object(laggards, "_minute", side_effect=lambda session, when: forged
                          if (session.symbol, when) == (symbol, at) else original(session, when)):
            produced = laggards.select_day(sessions)
            audited = laggards.reference_day(sessions)
        self.assertEqual(produced["symbols"][symbol]["return"], "-1/100")
        self.assertEqual(audited["symbols"][symbol]["return"], "-1/10")
