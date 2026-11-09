from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime, timedelta
from decimal import Decimal
import unittest
from unittest.mock import patch

from research import intraday_candidate_late_leader_selection as study
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2025-11-13T10:00:00+09:00")
CLOSE = datetime.fromisoformat("2025-11-13T16:30:00+09:00")
DECISION = CLOSE - 76 * MINUTE
STARTS = tuple(DECISION - offset * MINUTE for offset in range(17, 2, -1))
SYMBOLS = tuple(str(value) for value in range(100000, 100020))
RANKS = {symbol: rank for rank, symbol in enumerate(reversed(SYMBOLS), 1)}
BY_RANK = {rank: symbol for symbol, rank in RANKS.items()}


def _bar(symbol, at, open_price, close, *, low=None):
    open_price, close = Decimal(open_price), Decimal(close)
    low = min(open_price, close) if low is None else Decimal(low)
    return Minute(symbol, at, at + 3 * MINUTE, (open_price, max(open_price, close), low, close), None,
                  volume_state="missing")


def _sessions(closes, *, zero_width=()):
    sessions = {}
    for symbol, rank in sorted(RANKS.items()):
        close = closes.get(rank)
        bars = {}
        if close is not None:
            for index, at in enumerate(STARTS):
                price = 10000 if rank in zero_width else 10020
                op = 10000 if index == 0 else price
                cl = close if index == 14 else price
                bars[at] = _bar(symbol, at, op, cl, low=9970 if index == 7 and rank not in zero_width else None)
        sessions[symbol] = SessionInputs(symbol, OPEN, CLOSE, bars)
    return sessions


class _BoundedPrices(Mapping):
    def __init__(self, prices):
        self.prices = prices

    def __getitem__(self, at):
        if at not in STARTS:
            raise AssertionError("read an unrequested minute")
        return self.prices[at]

    def __iter__(self):
        raise AssertionError("enumerated unrequested prices")

    def __len__(self):
        return len(self.prices)


class LateLeaderSelectionTest(unittest.TestCase):
    def _checked(self, sessions):
        produced = study.select_day(sessions, RANKS)
        self.assertEqual(produced, study.reference_day(sessions, RANKS))
        return produced

    def test_rank_not_symbol_order_defines_leaders_and_target_bounds(self):
        result = self._checked(_sessions({1: 10010, 2: 10060, 3: 10050, 4: 10040, 5: 10020,
                                          6: 10000, 7: 10033, 8: 10034, 9: 9999}))
        self.assertEqual(result["decision_at"], "2025-11-13T15:14:00+09:00")
        self.assertEqual((result["observed_leaders"], result["leader_median_return"], result["leader_state"]),
                         (5, "1/250", "select"))
        rows = result["symbols"]
        for rank in range(1, 6):
            row = rows[BY_RANK[rank]]
            self.assertEqual((row["rank"], row["rank_state"], row["gated_state"], row["order_state"]),
                             (rank, "decline", "decline", "retain"))
            self.assertIsNotNone(row["return"])
        self.assertEqual(rows[BY_RANK[6]], {"rank": 6, "return": "0", "rank_state": "select",
                                           "gated_state": "select", "order_state": "ready", "limit": "10000",
                                           "stop": "9970", "reason": "order_ready"})
        self.assertEqual(rows[BY_RANK[7]]["return"], "33/10000")
        self.assertEqual(rows[BY_RANK[7]]["gated_state"], "select")
        for rank in (8, 9):
            self.assertEqual(rows[BY_RANK[rank]]["reason"], "return_outside_band")
            self.assertEqual(rows[BY_RANK[rank]]["gated_state"], "decline")

    def test_four_leader_median_at_cost_is_known_decline(self):
        result = self._checked(_sessions({1: 10020, 2: 10032, 4: 10034, 5: 10090, 6: 10033}))
        self.assertEqual((result["observed_leaders"], result["leader_median_return"], result["leader_state"]),
                         (4, "33/10000", "decline"))
        row = result["symbols"][BY_RANK[6]]
        self.assertEqual((row["rank_state"], row["gated_state"], row["order_state"]),
                         ("select", "decline", "ready"))

    def test_gate_and_target_bounds_are_exact_below_binary_float_precision(self):
        above = "10033.00000000000000000000000000000001"
        result = self._checked(_sessions({1: 10020, 2: 10033, 4: above, 5: 10090, 6: above, 7: 10033}))
        self.assertEqual(result["leader_state"], "select")
        rows = result["symbols"]
        self.assertEqual(rows[BY_RANK[6]]["rank_state"], "decline")
        self.assertEqual(rows[BY_RANK[7]]["rank_state"], "select")

    def test_missing_leader_quorum_does_not_borrow_target_and_preserves_control(self):
        result = self._checked(_sessions({1: 10090, 2: 10090, 5: 10090, 6: 10020, 7: 10200}))
        self.assertEqual((result["observed_leaders"], result["leader_median_return"], result["leader_state"]),
                         (3, None, "unresolved"))
        rows = result["symbols"]
        self.assertEqual((rows[BY_RANK[6]]["rank_state"], rows[BY_RANK[6]]["gated_state"]),
                         ("select", "unresolved"))
        self.assertEqual(rows[BY_RANK[7]]["gated_state"], "decline")
        self.assertEqual(rows[BY_RANK[8]]["gated_state"], "unresolved")
        self.assertEqual(rows[BY_RANK[4]]["reason"], "leader_not_target")

    def test_all_bars_required_and_invalid_observations_remain_unknown(self):
        cases = ({"available_at": STARTS[-1] + 4 * MINUTE}, {"available_at": STARTS[-1] + 2 * MINUTE},
                 {"available_at": None}, {"price_state": "conflict"}, {"ohlc": None},
                 {"ohlc": tuple(map(Decimal, (10020, 9999, 9970, 10020)))}, {"symbol": BY_RANK[19]})
        for changes in cases:
            with self.subTest(changes=changes):
                sessions = _sessions({1: 10040, 2: 10050, 3: 10060, 4: 10070, 6: 10020, 7: 10000})
                sessions[BY_RANK[4]].minutes.pop(STARTS[7])
                prices = sessions[BY_RANK[6]].minutes
                prices[STARTS[-1]] = replace(prices[STARTS[-1]], **changes)
                result = self._checked(sessions)
                self.assertEqual(result["observed_leaders"], 3)
                rows = result["symbols"]
                self.assertEqual(rows[BY_RANK[6]]["reason"], "window_unavailable")
                self.assertIsNone(rows[BY_RANK[6]]["return"])
                self.assertEqual(rows[BY_RANK[7]]["return"], "0")
        sessions[BY_RANK[7]].minutes.pop(STARTS[7])
        self.assertEqual(self._checked(sessions)["symbols"][BY_RANK[7]]["reason"], "window_unavailable")

    def test_zero_width_keeps_signal_and_declines_order(self):
        sessions = _sessions({1: 10040, 2: 10040, 3: 10040, 4: 10040, 6: 10000}, zero_width=(6,))
        row = self._checked(sessions)["symbols"][BY_RANK[6]]
        self.assertEqual((row["rank_state"], row["gated_state"], row["order_state"], row["reason"]),
                         ("select", "select", "decline", "limit_at_stop"))
        self.assertEqual((row["limit"], row["stop"]), ("10000", "10000"))

    def test_reads_only_registered_bars_available_at_decision(self):
        sessions = _sessions({1: 10050, 2: 10050, 3: 10050, 4: 10050, 6: 10020})
        sessions = {symbol: replace(session, minutes=_BoundedPrices(session.minutes))
                    for symbol, session in sessions.items()}
        self.assertEqual(self._checked(sessions)["leader_state"], "select")

    def test_rejects_malformed_roster_ranks_and_session_clocks(self):
        sessions = _sessions({})
        symbol = BY_RANK[1]
        for calculate in (study.select_day, study.reference_day):
            with self.assertRaisesRegex(ValueError, "exactly 20"):
                calculate({key: session for key, session in sessions.items() if key != symbol}, RANKS)
            with self.assertRaisesRegex(ValueError, "ranks 1 through 20"):
                calculate(sessions, {**RANKS, symbol: True})
            with self.assertRaisesRegex(ValueError, "ranks 1 through 20"):
                calculate(sessions, {**RANKS, symbol: 2})
            with self.assertRaisesRegex(ValueError, "session identity"):
                calculate({**sessions, symbol: sessions[BY_RANK[2]]}, RANKS)
            with self.assertRaisesRegex(ValueError, "one opening and closing"):
                calculate({**sessions, symbol: replace(sessions[symbol], closing=CLOSE - MINUTE)}, RANKS)
            with self.assertRaisesRegex(ValueError, "session geometry"):
                calculate({key: replace(session, closing=CLOSE + timedelta(days=1))
                           for key, session in sessions.items()}, RANKS)

    def test_reference_detects_forged_producer_quote(self):
        sessions = _sessions({1: 10050, 2: 10050, 3: 10050, 4: 10050, 6: 10020})
        original = study._minute
        symbol = BY_RANK[6]
        forged = tuple(map(Decimal, (10080, 10080, 10080, 10080)))
        with patch.object(study, "_minute", side_effect=lambda session, at: forged
                          if (session.symbol, at) == (symbol, STARTS[-1]) else original(session, at)):
            produced = study.select_day(sessions, RANKS)
            audited = study.reference_day(sessions, RANKS)
        self.assertEqual(produced["symbols"][symbol]["rank_state"], "decline")
        self.assertEqual(audited["symbols"][symbol]["rank_state"], "select")
