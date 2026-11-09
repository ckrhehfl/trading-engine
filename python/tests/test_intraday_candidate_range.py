from datetime import datetime
from fractions import Fraction
import unittest
from unittest.mock import patch

from research import intraday_candidate_range as range_gate
from research.intraday_candidate_room import MINUTE


def _coordinate(index=0):
    session = "2026-01-26"
    roster = {"session": session, "symbol": str(100000 + index)}
    status = {18: "overlap_suppressed", 19: "question_unsupported"}.get(index, "opportunity")
    outcome = ("closed", "closed", "closed", "no_entry", "no_fill", "unsupported", "unresolved")[index % 7]
    row = {"questions": [{"decision_at": session + "T09:32:00+09:00", "status": status,
                          "episode_origin_context": "A", "baseline": {"status": outcome}}]}
    return range_gate.invented_decision(row, roster)[0]


def _feature(coordinate):
    return coordinate["roster"]["questions"][0]["feature"]


def _choice(coordinate):
    choices = range_gate.selections(coordinate)
    range_gate.verify_selections(coordinate, choices)
    return next(iter(choices.values()))


class TestRangeExpansion(unittest.TestCase):
    def test_requires_both_conditions_with_an_inclusive_range_boundary(self):
        cases = (
            (0, "select", "expanded_range_and_breakout", "2", "1"),
            (1, "decline", "range_below_twice_prior_mean", "1", "1"),
            (6, "decline", "close_not_above_prior_high", "2", "0"),
        )
        for index, state, reason, ratio, distance in cases:
            with self.subTest(index=index):
                choice = _choice(_coordinate(index))
                self.assertEqual((choice["state"], choice["reason"], choice["range_ratio"],
                                  choice["breakout_distance_in_R"]), (state, reason, ratio, distance))

    def test_prior_high_uses_the_entire_first_25_not_just_the_last_prior_close(self):
        coordinate = _coordinate()
        bars = _feature(coordinate)["prices_in_R"]
        close = Fraction(bars[-1][3])
        bars[0][1] = str(close + 1)
        bars[-1][2] = str(Fraction(bars[-1][2]) - 1)
        choice = _choice(coordinate)
        self.assertEqual((choice["state"], choice["reason"], choice["breakout_distance_in_R"]),
                         ("decline", "close_not_above_prior_high", "-1"))

    def test_both_boundaries_use_exact_fraction_arithmetic(self):
        tiny = Fraction(1, 10**25)
        coordinate = _coordinate()
        latest = _feature(coordinate)["prices_in_R"][-1]
        latest[2] = str(Fraction(latest[2]) + tiny)
        choice = _choice(coordinate)
        self.assertEqual((choice["state"], choice["range_ratio"]), ("decline", str(2 - tiny / 5)))

        coordinate = _coordinate(6)
        prices = _feature(coordinate)["prices_in_R"]
        prices[:25] = [[str(Fraction(value) - tiny) for value in bar] for bar in prices[:25]]
        choice = _choice(coordinate)
        self.assertEqual((choice["state"], choice["range_ratio"], choice["breakout_distance_in_R"]),
                         ("select", "2", str(tiny)))

    def test_zero_prior_average_is_unresolved_but_known_zero_bars_are_valid(self):
        coordinate = _coordinate()
        bars = _feature(coordinate)["prices_in_R"]
        bars[0] = [bars[0][3]] * 4
        self.assertEqual(_choice(coordinate)["range_ratio"], "25/12")
        bars[:25] = [[bar[3]] * 4 for bar in bars[:25]]
        choice = _choice(coordinate)
        self.assertEqual((choice["state"], choice["reason"], choice["range_ratio"]),
                         ("unresolved", "nonpositive_prior_range", None))

        coordinate = _coordinate()
        bars = _feature(coordinate)["prices_in_R"]
        bars[-5:] = [[bar[3]] * 4 for bar in bars[-5:]]
        choice = _choice(coordinate)
        self.assertEqual((choice["state"], choice["range_ratio"]), ("decline", "0"))

    def test_missing_or_invalid_window_is_unresolved(self):
        for case in ("future", "gap", "opening", "window", "end", "state", "shape", "unknown",
                     "noncanonical", "inverted", "detached_close", "nonpositive_price", "bad_scale"):
            with self.subTest(case=case):
                coordinate = _coordinate()
                feature = _feature(coordinate)
                clocks = feature["minute_available_at"]
                bars = feature["prices_in_R"]
                if case == "future":
                    clocks[-1] = (datetime.fromisoformat(clocks[-1]) + MINUTE).isoformat()
                elif case == "gap":
                    clocks[0] = clocks[1]
                elif case == "opening":
                    coordinate["opening"] = clocks[0]
                elif case == "window":
                    feature["window_start"] = clocks[0]
                elif case == "end":
                    feature["observation_end"] = clocks[-1]
                elif case == "state":
                    feature["price_states"][0] = "missing"
                elif case == "shape":
                    bars.pop(0)
                elif case == "unknown":
                    bars[0] = None
                elif case == "noncanonical":
                    bars[0][3] = "-2/4"
                elif case == "inverted":
                    bars[0][1] = "-10"
                elif case == "detached_close":
                    bars[-1] = ["0"] * 4
                elif case == "nonpositive_price":
                    bars[0][2] = "-1000"
                else:
                    feature["question"]["episode_id"][0][1]["pre_peak_range"] = "0"
                choice = _choice(coordinate)
                self.assertEqual((choice["state"], choice["reason"], choice["range_ratio"],
                                  choice["breakout_distance_in_R"]),
                                 ("unresolved", "range_window_unavailable", None, None))

    def test_preserves_original_non_requests_without_requiring_their_price_windows(self):
        for index, state, reason in ((2, "unresolved", "invalid_pre_entry_geometry"),
                                     (3, "retain", "no_entry"), (5, "retain", "unsupported"),
                                     (18, "retain", "overlap_suppressed"),
                                     (19, "retain", "question_unsupported")):
            with self.subTest(index=index):
                coordinate = _coordinate(index)
                del _feature(coordinate)["prices_in_R"]
                choice = _choice(coordinate)
                self.assertEqual((choice["state"], choice["reason"], choice["range_ratio"]), (state, reason, None))

    def test_does_not_read_volume_selected_decision_or_future_outcomes(self):
        coordinate = _coordinate()
        expected = range_gate.selections(coordinate)
        _feature(coordinate).update(volume_states=["missing"] * 30, stored_volumes=[None] * 30)
        coordinate["decisions"][0]["selected"] = {"outcome": "future"}
        coordinate["modeled_opens"] = [["future", "999999", None]]
        self.assertEqual(range_gate.selections(coordinate), expected)
        range_gate.verify_selections(coordinate, expected)

    def test_reference_rejects_a_forged_support_result_or_mutated_decision(self):
        coordinate = _coordinate()
        original = range_gate.selections(coordinate)
        key = next(iter(original))
        altered = {key: {**original[key], "state": "decline"}}
        with self.assertRaisesRegex(ValueError, "sealed selection differs"):
            range_gate.verify_selections(coordinate, altered)

        feature = _feature(coordinate)
        valid = range_gate._observed_window(feature, datetime.fromisoformat(feature["forecast_at"]),
                                            datetime.fromisoformat(coordinate["opening"]))
        feature["minute_available_at"][0] = feature["minute_available_at"][1]
        with patch.object(range_gate, "_observed_window", return_value=valid):
            self.assertEqual(range_gate.selections(coordinate), original)
            with self.assertRaisesRegex(ValueError, "sealed selection differs"):
                range_gate.verify_selections(coordinate, original)
