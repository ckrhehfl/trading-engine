"""Invented receipts for the EJ laggard's fixed execution and independent check."""

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import unittest
from unittest.mock import patch

from research import intraday_candidate_laggard_execution as trade
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


SYMBOL = "invented"
OPENING = datetime(2026, 1, 5, 9, tzinfo=timezone(timedelta(hours=9)))
DECISION = OPENING + 62 * MINUTE
ENTRY = DECISION + MINUTE
EXPIRY = ENTRY + 30 * MINUTE
DELAYED_REQUEST = OPENING + 67 * MINUTE
DELAYED_ENTRY = OPENING + 68 * MINUTE
LATE_CLOSING = OPENING + 450 * MINUTE
LATE_DECISION = LATE_CLOSING - 76 * MINUTE
LATE_ENTRY = LATE_CLOSING - 75 * MINUTE
LATE_EXPIRY = LATE_CLOSING - 45 * MINUTE
LIMIT = Fraction(10_000)
STOP = Fraction(9_700)


def _bar(at, op=9900, close=None, *, lag=3, low=None):
    close = op if close is None else close
    low = min(op, close) if low is None else low
    return Minute(SYMBOL, at, at + lag * MINUTE,
                  tuple(map(Decimal, (op, max(op, close), low, close))), None)


def _bars():
    values = {ENTRY + offset * MINUTE: _bar(ENTRY + offset * MINUTE) for offset in range(26)}
    values[EXPIRY] = _bar(EXPIRY, 10100)
    return values


class _Reads(dict):
    def __init__(self, rows, allowed):
        super().__init__(rows)
        self.allowed = allowed
        self.seen = set()

    def get(self, at, default=None):
        if at not in self.allowed:
            raise AssertionError(f"unexpected price read at {at}")
        self.seen.add(at)
        return super().get(at, default)


def _session(rows):
    return SessionInputs(SYMBOL, OPENING, OPENING + 390 * MINUTE, rows)


class TestLaggardExecution(unittest.TestCase):
    def _check(self, rows, *, limit=LIMIT, stop=STOP, delayed=False):
        session = _session(rows)
        execute = trade.execute_delayed_trade if delayed else trade.execute_trade
        reference = trade.reference_delayed_trade if delayed else trade.reference_trade
        actual = execute(SYMBOL, DECISION, limit, stop, session)
        self.assertEqual(actual, reference(SYMBOL, DECISION, limit, stop, session))
        return actual

    def test_sizes_at_limit_and_charges_once_on_actual_entry_notional(self):
        actual = self._check(_bars())
        self.assertEqual(actual["requested_quantity"], 150)
        self.assertEqual(actual["fill"], {"outcome": "modeled", "quantity": 150, "cost": "9801/2"})
        self.assertEqual(actual["entry_request_at"], DECISION.isoformat())
        self.assertEqual(actual["entry_at"], ENTRY.isoformat())
        self.assertEqual(actual["entry_price"], "9900")
        self.assertEqual(actual["exit_request_at"], (EXPIRY - MINUTE).isoformat())
        self.assertEqual(actual["exit_at"], EXPIRY.isoformat())
        self.assertEqual(actual["selected_trigger"], "time")
        self.assertEqual(tuple(actual[k] for k in ("status", "gross", "net", "entry_notional")),
                         ("closed", "30000", "50199/2", "1485000"))

    def test_uses_existing_order_cap_and_accepts_exact_limit(self):
        rows = {at: _bar(at, 10_000) for at in _bars()}
        actual = self._check(rows, stop=Fraction(9_999))
        self.assertEqual((actual["status"], actual["entry_price"], actual["requested_quantity"]),
                         ("closed", "10000", 200))
        self.assertEqual((actual["entry_notional"], actual["cost"], actual["net"]),
                         ("2000000", "6600", "-6600"))

    def test_first_structural_close_sells_on_later_open_at_gapped_price(self):
        sale = ENTRY + 4 * MINUTE
        rows = _Reads({ENTRY: _bar(ENTRY, 9900, 9700), sale: _bar(sale, 9500)}, {ENTRY, sale})
        actual = self._check(rows)
        self.assertEqual(rows.seen, {ENTRY, sale})
        self.assertEqual(actual["selected_trigger"], "structural")
        self.assertEqual(actual["trigger_start_at"], ENTRY.isoformat())
        self.assertEqual(actual["trigger_close"], "9700")
        self.assertEqual(actual["exit_request_at"], (ENTRY + 3 * MINUTE).isoformat())
        self.assertEqual(actual["exit_at"], sale.isoformat())
        self.assertEqual((actual["exit_price"], actual["net"]), ("9500", "-129801/2"))

    def test_intrabar_low_does_not_trigger_and_expiry_tie_needs_no_close(self):
        rows = _bars()
        rows[ENTRY] = _bar(ENTRY, low=9500)
        tied = ENTRY + 26 * MINUTE
        rows[tied] = _bar(tied, 9900, 9700)
        watched = _Reads(rows, set(rows) - {tied})
        actual = self._check(watched)
        self.assertEqual(actual["selected_trigger"], "time")
        self.assertEqual(actual["exit_price"], "10100")
        self.assertNotIn(tied, watched.seen)

    def test_known_entry_decline_does_not_read_holding_prices(self):
        for outside in (9_700, 10_001):
            with self.subTest(outside=outside):
                rows = _Reads({ENTRY: _bar(ENTRY, outside)}, {ENTRY})
                actual = self._check(rows)
                self.assertEqual((actual["status"], actual["reason"], actual["fill"], actual["entry_at"]),
                                 ("no_fill", "open_outside_frozen_entry_bounds",
                                  {"outcome": "declined", "quantity": 0, "cost": "0"}, None))
                self.assertEqual([actual[k] for k in ("gross", "cost", "net", "entry_notional")], ["0"] * 4)
                self.assertEqual(rows.seen, {ENTRY})

    def test_risk_size_zero_does_not_read_even_entry_open(self):
        rows = _Reads({}, set())
        actual = self._check(rows, limit=Fraction(60_000), stop=Fraction(1))
        self.assertEqual((actual["status"], actual["reason"], actual["requested_quantity"]),
                         ("no_fill", "insufficient_risk_or_cash_for_one_unit", 0))
        self.assertIsNone(actual["entry_execute_at"])
        self.assertIsNone(actual["fill"])

    def test_missing_or_late_entry_is_unresolved_without_tail_access(self):
        for rows in ({}, {ENTRY: _bar(ENTRY, lag=4)}, {ENTRY: replace(_bar(ENTRY), available_at=None)}):
            with self.subTest(rows=rows):
                watched = _Reads(rows, {ENTRY})
                actual = self._check(watched)
                self.assertEqual((actual["status"], actual["reason"], actual["unknown_at"]),
                                 ("unresolved", "entry_price_unknown", ENTRY.isoformat()))
                self.assertEqual(actual["fill"], {"outcome": "indeterminate", "quantity": 0, "cost": None})
                self.assertEqual([actual[k] for k in ("gross", "cost", "net", "entry_notional")], [None] * 4)

    def test_earlier_missing_close_preserves_entry_and_leaves_first_stop_unknown(self):
        unknown = ENTRY + MINUTE
        rows = _Reads({ENTRY: _bar(ENTRY), EXPIRY: _bar(EXPIRY)}, {ENTRY, unknown})
        actual = self._check(rows)
        self.assertEqual((actual["status"], actual["reason"], actual["unknown_at"], actual["selected_trigger"]),
                         ("unresolved", "first_structural_close_unknown", unknown.isoformat(), "unknown"))
        self.assertEqual(actual["fill"]["outcome"], "modeled")
        self.assertIsNone(actual["exit_execute_at"])

    def test_missing_requested_stop_or_expiry_open_keeps_schedule_without_fallback(self):
        for expected, initial, rows in (("structural", ENTRY + 4 * MINUTE, {ENTRY: _bar(ENTRY, 9900, 9700)}),
                                         ("time", EXPIRY, _bars())):
            with self.subTest(expected=expected):
                rows.pop(initial, None)
                actual = self._check(_Reads(rows, set(rows) | {initial}))
                self.assertEqual((actual["status"], actual["reason"], actual["selected_trigger"],
                                  actual["exit_execute_at"], actual["unknown_at"]),
                                 ("unresolved", "exit_price_unknown", expected,
                                  initial.isoformat(), initial.isoformat()))
                self.assertIsNone(actual["exit_at"])
                self.assertIsNone(actual["net"])

    def test_reference_does_not_reuse_producer_price_admission(self):
        session = _session(_bars())
        with patch.object(trade, "_minute", return_value=None):
            self.assertEqual(trade.execute_trade(SYMBOL, DECISION, LIMIT, STOP, session)["status"], "unresolved")
            self.assertEqual(trade.reference_trade(SYMBOL, DECISION, LIMIT, STOP, session)["status"], "closed")

    def test_invalid_seal_cannot_move_request_or_exit_clock(self):
        session = _session(_Reads({}, set()))
        for function in (trade.execute_trade, trade.reference_trade,
                         trade.execute_delayed_trade, trade.reference_delayed_trade):
            for symbol, decision, limit, stop in (("other", DECISION, LIMIT, STOP),
                                                  (SYMBOL, DECISION + MINUTE, LIMIT, STOP),
                                                  (SYMBOL, DECISION, LIMIT, LIMIT)):
                with self.subTest(function=function.__name__, symbol=symbol, decision=decision, stop=stop):
                    with self.assertRaises(ValueError):
                        function(symbol, decision, limit, stop, session)

    def test_delayed_request_keeps_size_and_absolute_expiry(self):
        rows = _bars()
        rows[DELAYED_ENTRY] = _bar(DELAYED_ENTRY, 9950)
        immediate = self._check(rows)
        allowed = {at for at in rows if at >= DELAYED_ENTRY}
        watched = _Reads(rows, allowed)
        delayed = self._check(watched, delayed=True)

        self.assertEqual(delayed["requested_quantity"], immediate["requested_quantity"])
        self.assertEqual(delayed["fill"], {"outcome": "modeled", "quantity": 150, "cost": "19701/4"})
        self.assertEqual(delayed["entry_request_at"], DELAYED_REQUEST.isoformat())
        self.assertEqual(delayed["entry_execute_at"], DELAYED_ENTRY.isoformat())
        self.assertEqual(delayed["entry_at"], DELAYED_ENTRY.isoformat())
        self.assertEqual(delayed["entry_price"], "9950")
        self.assertEqual(delayed["exit_at"], immediate["exit_at"])
        self.assertEqual(delayed["net"], "70299/4")
        self.assertEqual(watched.seen, allowed)

    def test_delayed_exit_uses_first_structural_close_after_fill(self):
        sale = DELAYED_ENTRY + 4 * MINUTE
        rows = _Reads({ENTRY: _bar(ENTRY, 9900, 9600),
                       DELAYED_ENTRY: _bar(DELAYED_ENTRY, 9900, 9700), sale: _bar(sale, 9500)},
                      {DELAYED_ENTRY, sale})
        delayed = self._check(rows, delayed=True)

        self.assertEqual(delayed["selected_trigger"], "structural")
        self.assertEqual(delayed["trigger_start_at"], DELAYED_ENTRY.isoformat())
        self.assertEqual(delayed["exit_at"], sale.isoformat())
        self.assertEqual(delayed["exit_price"], "9500")
        self.assertEqual(rows.seen, {DELAYED_ENTRY, sale})

    def test_delayed_expiry_tie_needs_no_close_at_tied_bar(self):
        rows = _bars()
        tied = EXPIRY - 4 * MINUTE
        rows[tied] = _bar(tied, 9900, 9700)
        watched = _Reads(rows, {at for at in rows if at >= DELAYED_ENTRY and at != tied})
        delayed = self._check(watched, delayed=True)

        self.assertEqual(delayed["selected_trigger"], "time")
        self.assertEqual(delayed["exit_at"], EXPIRY.isoformat())
        self.assertNotIn(tied, watched.seen)

    def test_missing_delayed_open_is_unresolved_even_when_immediate_open_exists(self):
        rows = _Reads({ENTRY: _bar(ENTRY)}, {DELAYED_ENTRY})
        delayed = self._check(rows, delayed=True)

        self.assertEqual((delayed["status"], delayed["reason"], delayed["unknown_at"]),
                         ("unresolved", "entry_price_unknown", DELAYED_ENTRY.isoformat()))
        self.assertEqual(delayed["fill"], {"outcome": "indeterminate", "quantity": 0, "cost": None})

    def test_missing_delayed_exit_open_keeps_original_expiry(self):
        rows = {at: bar for at, bar in _bars().items() if DELAYED_ENTRY <= at < EXPIRY}
        watched = _Reads(rows, set(rows) | {EXPIRY})
        delayed = self._check(watched, delayed=True)

        self.assertEqual((delayed["status"], delayed["reason"], delayed["selected_trigger"],
                          delayed["exit_execute_at"], delayed["unknown_at"]),
                         ("unresolved", "exit_price_unknown", "time", EXPIRY.isoformat(), EXPIRY.isoformat()))
        self.assertEqual(delayed["fill"]["outcome"], "modeled")
        self.assertIsNone(delayed["exit_at"])


class TestLateExecution(unittest.TestCase):
    def _check(self, rows, *, stop=STOP):
        session = SessionInputs(SYMBOL, OPENING, LATE_CLOSING, rows)
        actual = trade.execute_late_trade(SYMBOL, LATE_DECISION, LIMIT, stop, session)
        self.assertEqual(actual, trade.reference_late_trade(SYMBOL, LATE_DECISION, LIMIT, stop, session))
        return actual

    def test_special_closing_sets_late_clock_and_preserves_cap_and_cost(self):
        starts = [LATE_ENTRY + offset * MINUTE for offset in range(26)]
        rows = {at: _bar(at, 9999) for at in starts}
        rows[LATE_EXPIRY] = _bar(LATE_EXPIRY, 10_100)
        watched = _Reads(rows, set(rows))
        actual = self._check(watched, stop=Fraction(9998))
        self.assertEqual(watched.seen, set(rows))
        self.assertEqual((actual["status"], actual["selected_trigger"], actual["requested_quantity"]),
                         ("closed", "time", 200))
        self.assertEqual(actual["entry_request_at"], LATE_DECISION.isoformat())
        self.assertEqual(actual["entry_execute_at"], LATE_ENTRY.isoformat())
        self.assertEqual(actual["entry_at"], LATE_ENTRY.isoformat())
        self.assertEqual(actual["exit_request_at"], (LATE_EXPIRY - MINUTE).isoformat())
        self.assertEqual(actual["exit_execute_at"], LATE_EXPIRY.isoformat())
        self.assertEqual(actual["exit_at"], LATE_EXPIRY.isoformat())
        self.assertEqual(actual["fill"], {"outcome": "modeled", "quantity": 200, "cost": "329967/50"})
        self.assertEqual(tuple(actual[k] for k in ("entry_notional", "gross", "cost", "net")),
                         ("1999800", "20200", "329967/50", "680033/50"))

    def test_late_structural_close_triggers_next_open_without_reentry(self):
        sale = LATE_ENTRY + 4 * MINUTE
        watched = _Reads({LATE_ENTRY: _bar(LATE_ENTRY, 9900, 9700), sale: _bar(sale, 9500)}, {LATE_ENTRY, sale})
        actual = self._check(watched)
        self.assertEqual(watched.seen, {LATE_ENTRY, sale})
        self.assertEqual((actual["selected_trigger"], actual["trigger_start_at"], actual["exit_request_at"],
                          actual["exit_at"], actual["exit_price"], actual["net"]),
                         ("structural", LATE_ENTRY.isoformat(), (LATE_ENTRY + 3 * MINUTE).isoformat(),
                          sale.isoformat(), "9500", "-129801/2"))

    def test_late_rejects_wrong_decision_before_reading_prices(self):
        session = SessionInputs(SYMBOL, OPENING, LATE_CLOSING, _Reads({}, set()))
        for function in (trade.execute_late_trade, trade.reference_late_trade):
            for decision in (DECISION, LATE_DECISION - 60 * MINUTE, LATE_DECISION + MINUTE):
                with self.subTest(function=function.__name__, decision=decision):
                    with self.assertRaises(ValueError):
                        function(SYMBOL, decision, LIMIT, STOP, session)


class TestShockExecution(unittest.TestCase):
    def _check(self, decision, rows):
        session = _session(rows)
        actual = trade.execute_shock_trade(SYMBOL, decision, LIMIT, STOP, session)
        self.assertEqual(actual, trade.reference_shock_trade(SYMBOL, decision, LIMIT, STOP, session))
        return actual

    def test_each_registered_decision_uses_next_open_and_exact_payoff(self):
        for offset in (62, 122, 182):
            with self.subTest(offset=offset):
                decision = OPENING + offset * MINUTE
                entry = decision + MINUTE
                expiry = entry + 30 * MINUTE
                starts = [entry + step * MINUTE for step in range(26)]
                rows = {at: _bar(at) for at in starts}
                rows[expiry] = _bar(expiry, 10_100)
                watched = _Reads(rows, set(rows))
                actual = self._check(decision, watched)

                self.assertEqual(watched.seen, set(rows))
                self.assertEqual(actual["status"], "closed")
                self.assertEqual(actual["selected_trigger"], "time")
                self.assertEqual(actual["entry_request_at"], decision.isoformat())
                self.assertEqual(actual["entry_execute_at"], entry.isoformat())
                self.assertEqual(actual["entry_at"], entry.isoformat())
                self.assertEqual(actual["exit_request_at"], (expiry - MINUTE).isoformat())
                self.assertEqual(actual["exit_execute_at"], expiry.isoformat())
                self.assertEqual(actual["exit_at"], expiry.isoformat())
                self.assertEqual(actual["fill"], {"outcome": "modeled", "quantity": 150, "cost": "9801/2"})
                self.assertEqual(tuple(actual[k] for k in ("entry_notional", "gross", "cost", "net")),
                                 ("1485000", "30000", "9801/2", "50199/2"))

    def test_other_decisions_and_expiry_at_close_are_rejected_before_reads(self):
        for function in (trade.execute_shock_trade, trade.reference_shock_trade):
            for offset in (61, 63, 121, 123, 181, 183, 314):
                with self.subTest(function=function.__name__, offset=offset):
                    with self.assertRaises(ValueError):
                        function(SYMBOL, OPENING + offset * MINUTE, LIMIT, STOP, _session(_Reads({}, set())))

            decision = OPENING + 182 * MINUTE
            short_session = SessionInputs(SYMBOL, OPENING, decision + 31 * MINUTE, _Reads({}, set()))
            with self.assertRaises(ValueError):
                function(SYMBOL, decision, LIMIT, STOP, short_session)

    def test_structural_trigger_waits_three_minutes_then_sells_at_next_open(self):
        decision = OPENING + 122 * MINUTE
        entry = decision + MINUTE
        sale = entry + 4 * MINUTE
        rows = _Reads({entry: _bar(entry, 9900, 9700), sale: _bar(sale, 9500)}, {entry, sale})
        actual = self._check(decision, rows)

        self.assertEqual(rows.seen, {entry, sale})
        self.assertEqual((actual["selected_trigger"], actual["trigger_start_at"], actual["exit_request_at"],
                          actual["exit_at"], actual["exit_price"], actual["net"]),
                         ("structural", entry.isoformat(), (entry + 3 * MINUTE).isoformat(),
                          sale.isoformat(), "9500", "-129801/2"))

    def test_missing_entry_or_earliest_close_preserves_unknown_price(self):
        decision = OPENING + 182 * MINUTE
        entry = decision + MINUTE
        next_close = entry + MINUTE
        for rows, missing, reason, fill_outcome in (({}, entry, "entry_price_unknown", "indeterminate"),
                                                      ({entry: _bar(entry)}, next_close,
                                                       "first_structural_close_unknown", "modeled")):
            with self.subTest(reason=reason):
                watched = _Reads(rows, set(rows) | {missing})
                actual = self._check(decision, watched)

                self.assertEqual(watched.seen, set(rows) | {missing})
                self.assertEqual((actual["status"], actual["reason"], actual["unknown_at"]),
                                 ("unresolved", reason, missing.isoformat()))
                self.assertEqual(actual["fill"]["outcome"], fill_outcome)
                self.assertIsNone(actual["exit_at"])
                self.assertIsNone(actual["net"])


class TestOpeningBreadthExecution(unittest.TestCase):
    def test_fixed_clock_and_exact_payoff_before_closing_auction(self):
        for close in (OPENING + 390 * MINUTE, LATE_CLOSING):
            decision = close - 46 * MINUTE
            entry, expiry = close - 45 * MINUTE, close - 15 * MINUTE
            rows = {entry + step * MINUTE: _bar(entry + step * MINUTE) for step in range(26)}
            rows[expiry] = _bar(expiry, 10_100)
            for run in (trade.execute_open_late_trade, trade.reference_open_late_trade):
                with self.subTest(closing=close, calculator=run.__name__):
                    watched = _Reads(rows, set(rows))
                    session = SessionInputs(SYMBOL, OPENING, close, watched)
                    with self.assertRaises(ValueError):
                        run(SYMBOL, decision - MINUTE, LIMIT, STOP, session)
                    self.assertEqual(watched.seen, set())

                    arm = run(SYMBOL, decision, LIMIT, STOP, session)
                    self.assertEqual(watched.seen, set(rows))
                    self.assertEqual((arm["entry_at"], arm["exit_at"]), (entry.isoformat(), expiry.isoformat()))
                    self.assertEqual((arm["gross"], arm["cost"], arm["net"]), ("30000", "9801/2", "50199/2"))


class TestOpeningRetryExecution(unittest.TestCase):
    def _check(self, rows, *, limit=LIMIT, stop=STOP):
        session = _session(rows)
        result = trade.execute_retry_trade(SYMBOL, DECISION, limit, stop, session)
        self.assertEqual(result, trade.reference_retry_trade(SYMBOL, DECISION, limit, stop, session))
        return result

    def test_quantity_reuses_original_caps_without_reading_any_price(self):
        for limit, stop, expected in ((LIMIT, STOP, 150), (LIMIT, Fraction(9999), 200),
                                      (Fraction(60_000), Fraction(1), 0)):
            with self.subTest(limit=limit, stop=stop):
                untouched = _Reads({}, set())
                session = _session(untouched)
                self.assertEqual(trade.requested_quantity(SYMBOL, DECISION, limit, stop, session), expected)
                self.assertEqual(trade.reference_requested_quantity(SYMBOL, DECISION, limit, stop, session), expected)
                self.assertEqual(untouched.seen, set())

                first = _session({ENTRY: _bar(ENTRY, 60_001)})
                original = trade.execute_trade(SYMBOL, DECISION, limit, stop, first)
                self.assertEqual(original["requested_quantity"], expected)
                if not expected:
                    self.assertEqual(self._check(untouched, limit=limit, stop=stop), original)

    def test_retry_uses_original_absolute_expiry_and_does_not_read_tied_close(self):
        expiry = OPENING + 123 * MINUTE
        tied = expiry - 4 * MINUTE
        rows = {DELAYED_ENTRY + offset * MINUTE: _bar(DELAYED_ENTRY + offset * MINUTE, 10_000)
                for offset in range(51)}
        rows[tied] = _bar(tied, 9900, 9700)
        rows[expiry] = _bar(expiry, 10_100)
        watched = _Reads(rows, set(rows) - {tied})
        result = self._check(watched)

        self.assertEqual(watched.seen, watched.allowed)
        self.assertEqual((result["selected_trigger"], result["entry_request_at"], result["entry_at"],
                          result["exit_request_at"], result["exit_at"]),
                         ("time", DELAYED_REQUEST.isoformat(), DELAYED_ENTRY.isoformat(),
                          (expiry - MINUTE).isoformat(), expiry.isoformat()))
        self.assertEqual((result["requested_quantity"], result["gross"], result["cost"], result["net"]),
                         (150, "15000", "4950", "10050"))

    def test_retry_uses_first_eligible_close_and_next_open(self):
        sale = DELAYED_ENTRY + 4 * MINUTE
        rows = _Reads({DELAYED_ENTRY: _bar(DELAYED_ENTRY, 9900, 9700), sale: _bar(sale, 9500)},
                      {DELAYED_ENTRY, sale})
        result = self._check(rows)
        self.assertEqual(rows.seen, {DELAYED_ENTRY, sale})
        self.assertEqual((result["selected_trigger"], result["trigger_start_at"], result["exit_at"],
                          result["exit_price"], result["net"]),
                         ("structural", DELAYED_ENTRY.isoformat(), sale.isoformat(), "9500", "-129801/2"))

    def test_retry_distinguishes_missing_and_outside_bounds(self):
        for bar, status, reason, outcome in (
            (None, "unresolved", "entry_price_unknown", "indeterminate"),
            (_bar(DELAYED_ENTRY, lag=4), "unresolved", "entry_price_unknown", "indeterminate"),
            (_bar(DELAYED_ENTRY, 9700), "no_fill", "open_outside_frozen_entry_bounds", "declined"),
            (_bar(DELAYED_ENTRY, 10001), "no_fill", "open_outside_frozen_entry_bounds", "declined"),
        ):
            with self.subTest(bar=bar):
                rows = _Reads({DELAYED_ENTRY: bar} if bar is not None else {}, {DELAYED_ENTRY})
                result = self._check(rows)
                self.assertEqual(rows.seen, {DELAYED_ENTRY})
                self.assertEqual((result["status"], result["reason"], result["fill"]["outcome"]),
                                 (status, reason, outcome))
                self.assertIsNone(result["entry_at"])

    def test_retry_clock_and_expiry_are_fixed_before_price_reads(self):
        rows = _Reads({}, set())
        for execute in (trade.execute_retry_trade, trade.reference_retry_trade):
            with self.subTest(execute=execute.__name__):
                with self.assertRaises(ValueError):
                    execute(SYMBOL, DELAYED_REQUEST, LIMIT, STOP, _session(rows))
                too_short = SessionInputs(SYMBOL, OPENING, OPENING + 123 * MINUTE, rows)
                with self.assertRaises(ValueError):
                    execute(SYMBOL, DECISION, LIMIT, STOP, too_short)
        self.assertEqual(rows.seen, set())


class TestOpeningHold60Execution(unittest.TestCase):
    def _check(self, rows):
        session = _session(rows)
        result = trade.execute_opening_hold60(SYMBOL, DECISION, LIMIT, STOP, session)
        self.assertEqual(result, trade.reference_opening_hold60(SYMBOL, DECISION, LIMIT, STOP, session))
        return result

    def test_opening_request_and_sixty_minute_expiry_keep_original_size_and_cost(self):
        expiry = OPENING + 123 * MINUTE
        tied = expiry - 4 * MINUTE
        rows = {ENTRY + offset * MINUTE: _bar(ENTRY + offset * MINUTE) for offset in range(56)}
        rows[tied] = _bar(tied, 9900, 9700)
        rows[expiry] = _bar(expiry, 10_100)
        watched = _Reads(rows, set(rows) - {tied})

        result = self._check(watched)

        self.assertEqual(watched.seen, watched.allowed)
        self.assertEqual((result["status"], result["selected_trigger"], result["entry_request_at"],
                          result["entry_execute_at"], result["entry_at"], result["exit_request_at"],
                          result["exit_execute_at"], result["exit_at"]),
                         ("closed", "time", DECISION.isoformat(), ENTRY.isoformat(), ENTRY.isoformat(),
                          (expiry - MINUTE).isoformat(), expiry.isoformat(), expiry.isoformat()))
        self.assertEqual(result["requested_quantity"],
                         trade.requested_quantity(SYMBOL, DECISION, LIMIT, STOP, _session(watched)))
        self.assertEqual((result["requested_quantity"], result["gross"], result["cost"], result["net"]),
                         (150, "30000", "9801/2", "50199/2"))

    def test_first_structural_close_sells_at_next_open_even_below_stop(self):
        structural = ENTRY + MINUTE
        sale = structural + 4 * MINUTE
        rows = {ENTRY: _bar(ENTRY, low=9500), structural: _bar(structural, 9900, 9700), sale: _bar(sale, 9500)}
        watched = _Reads(rows, set(rows))

        result = self._check(watched)

        self.assertEqual(watched.seen, set(rows))
        self.assertEqual((result["selected_trigger"], result["trigger_start_at"], result["exit_request_at"],
                          result["exit_at"], result["exit_price"], result["net"]),
                         ("structural", structural.isoformat(), (sale - MINUTE).isoformat(),
                          sale.isoformat(), "9500", "-129801/2"))

    def test_rejects_wrong_decision_or_expiry_at_close_before_reading_prices(self):
        watched = _Reads({}, set())
        short = SessionInputs(SYMBOL, OPENING, OPENING + 123 * MINUTE, watched)
        for run in (trade.execute_opening_hold60, trade.reference_opening_hold60):
            with self.subTest(run=run.__name__):
                with self.assertRaises(ValueError):
                    run(SYMBOL, DECISION + MINUTE, LIMIT, STOP, _session(watched))
                with self.assertRaises(ValueError):
                    run(SYMBOL, DECISION, LIMIT, STOP, short)
        self.assertEqual(watched.seen, set())
