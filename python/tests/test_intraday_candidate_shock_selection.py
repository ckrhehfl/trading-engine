from collections.abc import Mapping
from dataclasses import replace
from datetime import datetime
from decimal import Decimal
import unittest

from research import intraday_candidate_shock_selection as shock
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs


OPEN = datetime.fromisoformat("2026-01-02T09:00:00+09:00")
CLOSE = datetime.fromisoformat("2026-01-02T15:30:00+09:00")
SYMBOL = "000660"


def _at(offset):
    return OPEN + offset * MINUTE


def _prices(offset, *, fifth="9934", latest="9956"):
    prices = {}
    for index in range(10):
        at = _at(offset - 12 + index)
        value = Decimal(fifth if index == 4 else latest if index == 9 else "10000")
        low = Decimal("9900") if index == 6 else value
        prices[at] = Minute(SYMBOL, at, at + 3 * MINUTE, (value, value, low, value),
                            None, volume_state="missing")
    return prices


def _volumes(offset, *, baseline="10", event="20"):
    shares = {}
    for distance in range(8, 38):
        at = _at(offset - distance)
        value = Decimal(event if distance <= 12 else baseline)
        shares[at] = Minute(SYMBOL, at, at + 3 * MINUTE, None, value, price_state="missing")
    return shares


class _BoundedMinutes(Mapping):
    def __init__(self, minutes, offsets):
        self.minutes = minutes
        self.allowed = {_at(offset) for offset in offsets}
        self.requested = set()

    def __getitem__(self, at):
        if at not in self.allowed:
            raise AssertionError("read an unrequested minute")
        self.requested.add(at)
        return self.minutes[at]

    def __iter__(self):
        raise AssertionError("enumerated minutes instead of requesting coordinates")

    def __len__(self):
        return len(self.minutes)


class ShockSelectionTest(unittest.TestCase):
    def _price(self, minutes):
        session = SessionInputs(SYMBOL, OPEN, CLOSE, minutes)
        plan = shock.select_price(session)
        self.assertEqual(plan, shock.reference_price(session))
        return plan

    def _volume(self, plan, minutes):
        session = None if minutes is None else SessionInputs(SYMBOL, OPEN, CLOSE, minutes)
        result = shock.apply_volume(plan, session)
        self.assertEqual(result, shock.reference_volume(plan, session))
        return result

    def test_exact_shock_and_recovery_boundaries_choose_first_without_reading_later_prices(self):
        minutes = _BoundedMinutes(_prices(62) | _prices(122), range(50, 60))
        plan = self._price(minutes)
        self.assertEqual(plan, {"decision_at": _at(62).isoformat(), "rank_state": "select",
                                "order_state": "ready", "limit": "9956", "stop": "9900",
                                "reason": "order_ready", "unknown_offsets": []})
        self.assertEqual(minutes.requested, minutes.allowed)

    def test_near_threshold_and_full_recovery_are_observed_declines(self):
        plan = self._price(_prices(62, fifth="9934.00000000000000000000000001")
                           | _prices(122, latest="9955.99999999999999999999999999")
                           | _prices(182, latest="10000"))
        self.assertEqual(plan, {"decision_at": None, "rank_state": "decline", "order_state": "retain",
                                "limit": None, "stop": None, "reason": "shock_recovery_not_observed",
                                "unknown_offsets": []})

    def test_missing_or_late_quote_is_retained_when_later_event_is_selected(self):
        earlier = _prices(62)
        earlier[_at(54)] = replace(earlier[_at(54)], available_at=_at(58))
        middle = _prices(122)
        del middle[_at(110)]
        plan = self._price(earlier | middle | _prices(182))
        self.assertEqual((plan["rank_state"], plan["decision_at"], plan["unknown_offsets"]),
                         ("select", _at(182).isoformat(), [62, 122]))

    def test_no_observed_event_with_bad_quote_is_unresolved(self):
        middle = _prices(122)
        middle[_at(114)] = replace(middle[_at(114)], ohlc=(Decimal(1),) * 2 + (Decimal(2), Decimal(1)))
        plan = self._price(_prices(62, latest="10000") | middle | _prices(182, fifth="10000"))
        self.assertEqual(plan, {"decision_at": None, "rank_state": "unresolved", "order_state": "retain",
                                "limit": None, "stop": None, "reason": "price_window_unavailable",
                                "unknown_offsets": [122]})
        self.assertEqual(self._volume(plan, None)["gated_state"], "unresolved")

    def test_volume_exact_ratio_uses_only_selected_window_and_keeps_price_plan(self):
        plan = self._price(_prices(62))
        baseline_with_zero = _volumes(62)
        baseline_with_zero[_at(25)] = replace(baseline_with_zero[_at(25)], shares=Decimal(0))
        baseline_with_zero[_at(26)] = replace(baseline_with_zero[_at(26)], shares=Decimal(20))
        minutes = _BoundedMinutes(baseline_with_zero | _volumes(122), range(25, 55))
        result = self._volume(plan, minutes)
        self.assertEqual(result, {**plan, "gated_state": "select", "volume_ratio": "2",
                                  "volume_reason": "volume_ratio_at_least_two"})
        self.assertEqual(minutes.requested, minutes.allowed)
        self.assertNotIn("gated_state", plan)

    def test_low_or_missing_volume_does_not_replace_earlier_price_event(self):
        plan = self._price(_prices(62) | _prices(122))
        low = _volumes(62, event="19")
        later = _volumes(122)
        result = self._volume(plan, low | later)
        self.assertEqual((result["gated_state"], result["volume_ratio"], result["decision_at"]),
                         ("decline", "19/10", _at(62).isoformat()))
        del low[_at(54)]
        result = self._volume(plan, low | later)
        self.assertEqual((result["gated_state"], result["volume_ratio"], result["decision_at"]),
                         ("unresolved", None, _at(62).isoformat()))
        self.assertEqual(self._volume(plan, None), result)

    def test_zero_reference_and_unknown_volume_are_distinct_from_known_zero_shock(self):
        plan = self._price(_prices(62))
        self.assertEqual(self._volume(plan, _volumes(62, baseline="0"))["volume_reason"], "volume_reference_zero")
        result = self._volume(plan, _volumes(62, event="0"))
        self.assertEqual((result["gated_state"], result["volume_ratio"]), ("decline", "0"))

        volume = _volumes(62)
        for changes in ({"available_at": _at(57) + MINUTE}, {"shares": Decimal("-1")},
                        {"shares": Decimal("NaN")}, {"volume_state": "missing"}, {"symbol": "005930"}):
            with self.subTest(changes=changes):
                invalid = volume | {_at(54): replace(volume[_at(54)], **changes)}
                result = self._volume(plan, invalid)
                self.assertEqual((result["gated_state"], result["volume_reason"]),
                                 ("unresolved", "volume_window_unavailable"))

    def test_unselected_volume_mirrors_plan_and_selected_rejects_different_decision(self):
        no_event = self._price({offset_at: value for offset in shock.DECISION_OFFSETS
                                for offset_at, value in _prices(offset, latest="10000").items()})
        self.assertEqual(self._volume(no_event, None), {**no_event, "gated_state": "decline",
                                                       "volume_ratio": None, "volume_reason": "volume_not_applicable"})
        plan = self._price(_prices(62)) | {"decision_at": _at(63).isoformat()}
        session = SessionInputs(SYMBOL, OPEN, CLOSE, _BoundedMinutes({}, ()))
        for verifier in (shock.apply_volume, shock.reference_volume):
            with self.assertRaisesRegex(ValueError, "differs from frozen price event"):
                verifier(plan, session)
