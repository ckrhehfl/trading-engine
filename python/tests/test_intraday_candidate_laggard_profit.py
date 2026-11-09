"""Invented immediate receipts and independent profit-exit reconstructions."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import unittest
from unittest.mock import patch

from research import intraday_candidate_laggard_execution as trade
from research import intraday_candidate_laggard_profit as profit
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


SYMBOL = "invented"
OPENING = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
DECISION = OPENING + 62 * MINUTE
ENTRY = OPENING + 63 * MINUTE


def _bar(offset, op=10_000, close=None, *, lag=3):
    at = ENTRY + offset * MINUTE
    close = op if close is None else close
    return Minute(SYMBOL, at, at + lag * MINUTE,
                  tuple(map(Decimal, (op, max(op, close), min(op, close), close))), None)


def _session():
    minutes = {ENTRY + offset * MINUTE: _bar(offset) for offset in range(31)}
    minutes[ENTRY + 30 * MINUTE] = _bar(30, 10_030)
    return SessionInputs(SYMBOL, OPENING, OPENING + 390 * MINUTE, minutes)


def _row(session):
    plan = {"rank_state": "select", "order_state": "ready", "limit": "10000", "stop": "9700"}
    old = trade.execute_trade(SYMBOL, DECISION, Fraction(10_000), Fraction(9700), session)
    return {"symbol": SYMBOL, "session": OPENING.date().isoformat(), "fold": 1,
            "questions": [{"status": "opportunity", "decision_at": DECISION.isoformat(), "rank_decision": plan,
                           "baseline": old, "candidate": {"status": "no_entry"}}]}


class TestLaggardProfit(unittest.TestCase):
    def _project(self, row, session):
        source = deepcopy(row)
        projected = profit.project(row, session)
        self.assertEqual(projected, profit.reference_project(row, session))
        self.assertEqual(row, source)
        self.assertEqual(projected["questions"][0]["baseline"], source["questions"][0]["baseline"])
        return projected["questions"][0]

    def test_inclusive_threshold_and_next_open_preserve_position(self):
        session = _session()
        session.minutes[ENTRY] = _bar(0, close=10_065)
        session.minutes[ENTRY + MINUTE] = _bar(1, close=10_066)
        session.minutes[ENTRY + 5 * MINUTE] = _bar(5, 10_040)
        session.minutes[ENTRY + 6 * MINUTE] = _bar(6, 10_100)
        q = self._project(_row(session), session)
        arm = q["candidate"]
        self.assertEqual((arm["selected_trigger"], arm["trigger_close"], arm["exit_price"]), ("profit66", "10066", "10040"))
        self.assertEqual(arm["exit_request_at"], (ENTRY + 4 * MINUTE).isoformat())
        self.assertEqual(arm["exit_at"], (ENTRY + 5 * MINUTE).isoformat())
        self.assertEqual((arm["fill"], arm["entry_notional"], arm["cost"]),
                         tuple(q["baseline"][key] for key in ("fill", "entry_notional", "cost")))
        self.assertEqual(arm["net"], str(7 * arm["fill"]["quantity"]))
        self.assertEqual(q["exit_capacity"], {"earlier_opens": 26, "missing_opens": 0,
                                               "best_open": "10100", "best_net_bps": "67"})

    def test_same_request_keeps_existing_exit_without_tie_close(self):
        session = _session()
        row = _row(session)
        tied = ENTRY + 26 * MINUTE
        session.minutes[tied] = _bar(26, close=10_200)
        q = self._project(row, session)
        self.assertEqual(q["candidate"], q["baseline"])
        del session.minutes[tied]
        q = self._project(row, session)
        self.assertEqual(q["candidate"], q["baseline"])
        self.assertEqual(q["exit_capacity"]["missing_opens"], 1)

    def test_earliest_structural_sale_needs_no_new_session(self):
        session = _session()
        session.minutes[ENTRY] = _bar(0, close=9700)
        row = _row(session)
        self.assertFalse(profit.needs_session(row))
        self.assertFalse(profit.needs_reference_session(row))
        q = self._project(row, None)
        self.assertEqual(q["candidate"], q["baseline"])
        self.assertEqual(q["exit_capacity"], {"earlier_opens": 0, "missing_opens": 0,
                                               "best_open": "10000", "best_net_bps": "-33"})

    def test_missing_earlier_close_does_not_select_later_trigger(self):
        session = _session()
        row = _row(session)
        self.assertTrue(profit.needs_session(row))
        self.assertTrue(profit.needs_reference_session(row))
        session.minutes[ENTRY + MINUTE] = _bar(1, lag=4)
        session.minutes[ENTRY + 2 * MINUTE] = _bar(2, close=10_066)
        q = self._project(row, session)
        arm = q["candidate"]
        self.assertEqual((arm["status"], arm["reason"], arm["exit_execute_at"], arm["net"]),
                         ("unresolved", "first_profit_close_unknown", None, None))
        self.assertEqual(arm["fill"], q["baseline"]["fill"])
        self.assertEqual(arm["unknown_at"], (ENTRY + MINUTE).isoformat())
        self.assertEqual(q["exit_capacity"]["best_open"], "10030")

    def test_missing_new_open_retains_request_and_modeled_position(self):
        session = _session()
        session.minutes[ENTRY] = _bar(0, close=10_066)
        row = _row(session)
        del session.minutes[ENTRY + 4 * MINUTE]
        q = self._project(row, session)
        arm = q["candidate"]
        self.assertEqual((arm["status"], arm["reason"], arm["selected_trigger"], arm["exit_at"], arm["net"]),
                         ("unresolved", "profit_exit_price_unknown", "profit66", None, None))
        self.assertEqual(arm["exit_execute_at"], (ENTRY + 4 * MINUTE).isoformat())
        self.assertEqual(arm["fill"], q["baseline"]["fill"])
        self.assertEqual(q["exit_capacity"], {"earlier_opens": 26, "missing_opens": 1,
                                               "best_open": None, "best_net_bps": None})

    def test_parent_no_fill_is_retained_and_old_other_arm_is_unused(self):
        session = _session()
        session.minutes[ENTRY] = _bar(0, 10_001)
        row = _row(session)
        self.assertFalse(profit.needs_session(row))
        self.assertFalse(profit.needs_reference_session(row))
        q = self._project(row, None)
        self.assertEqual(q["candidate"], q["baseline"])
        self.assertEqual(q["candidate"]["status"], "no_fill")
        self.assertIsNone(q["exit_capacity"])

    def test_rejects_receipt_fee_and_archive_open_mismatches(self):
        session = _session()
        row = _row(session)
        wrong = deepcopy(row)
        wrong["questions"][0]["baseline"]["cost"] = "0"
        for project in (profit.project, profit.reference_project):
            with self.assertRaisesRegex(ValueError, "original payoff differs"):
                project(wrong, session)
        session.minutes[ENTRY + 30 * MINUTE] = _bar(30, 10_031)
        for project in (profit.project, profit.reference_project):
            with self.assertRaisesRegex(ValueError, "archived original open differs"):
                project(row, session)

    def test_reference_is_independent_of_production_quote_access(self):
        session = _session()
        session.minutes[ENTRY] = _bar(0, close=10_066)
        row = _row(session)
        with patch.object(profit.rule, "_minute", return_value=None):
            changed = profit.project(row, session)
            independent = profit.reference_project(row, session)
        self.assertEqual(changed["questions"][0]["candidate"]["status"], "unresolved")
        self.assertEqual(independent["questions"][0]["candidate"]["selected_trigger"], "profit66")
