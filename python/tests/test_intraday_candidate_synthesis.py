"""Invented aggregates only; no saved research artifact is read."""
from copy import deepcopy
from fractions import Fraction
import unittest
from unittest.mock import patch

from research import intraday_candidate_synthesis as synthesis


def exact(value):
    value = Fraction(value)
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def cs_statistics(n=7, gross=Fraction(30)):
    return {"opportunities": n, "gross_sum_bps_exact": exact(n * gross),
        "gross_mean_bps_exact": exact(gross), "net_mean_bps_exact": exact(gross - 33),
        "net_sum_bps_exact": exact(n * (gross - 33)), "gross_mean_bps": float(gross),
        "net_mean_bps": float(gross - 33), "reference_sessions": 257, "reference_weeks": 56,
        "opportunities_per_week": n / 56, "opportunities_per_week_exact": exact(Fraction(n, 56))}


def group(n=7, unknown=1, mean=Fraction(3, 1000), *, failed=False, selected=False):
    return {"known_labels": n, "event_count": n + unknown, "unknown_labels": unknown,
        "gross_sum": exact(n * mean), "gross_mean": exact(mean), "net_mean": exact(mean - synthesis.COST),
        "assumed_cost": exact(synthesis.COST), "gross_positive_count": n, "net_positive_count": 0,
        "gross_positive_fraction": exact(1), "net_positive_fraction": exact(0),
        "source_own_label_dispersion": {"mde_bps": 40.9 if failed else 12.0,
            "requires_operator_choice": failed, "known_labels": n, "unknown_labels": unknown,
            "underpower_exception_applied": False, "reference_sessions": 257},
        "frequency": {"reference_sessions": 257, "reference_weeks": 56,
            "selected_per_reference_week" if selected else "signals_per_reference_week": exact(Fraction(n + unknown, 56)),
            "known_labels_per_reference_week": exact(Fraction(n, 56))},
        "descriptive_normal95_interval": [-.002, .008]}


def invented_publications():
    publications, pins = {}, {}
    for i, source in enumerate(("CS", "CW", "DA"), 1):
        study, status, mode = synthesis.IDENTITIES[source]
        pin = {"run_id": "invented-" + source, "report_sha256": str(i) * 64,
               "specification_sha256": str(i + 3) * 64}
        pins[source] = pin
        publications[source] = {"status": status, "run_id": pin["run_id"],
            "output_sha256": pin["report_sha256"],
            "record": {"study_id": study, "status": "completed", "run_id": pin["run_id"],
                "specification_sha256": pin["specification_sha256"],
                "result": {"output_sha256": pin["report_sha256"]},
                "parameters": {"mode": mode, "parameters": {"assumed_cost": exact(synthesis.COST),
                    "cohort": "each_original_or_selected_group_own_known_labels_no_common_complete_mask" if source == "CW"
                    else "original_signal_and_CY_feature_selected_or_rejected_before_own_label_availability_no_common_complete_mask"}},
                "window": dict(synthesis.WINDOW),
                "promotion_allowed": False, "promotion_trial_increment": 0},
            "verification": {"study_id": study, "report_sha256": pin["report_sha256"],
                "specification_sha256": pin["specification_sha256"],
                "verified" if source == "CS" else "verified_new_comparison_math": True}}
    cs = {"window": dict(synthesis.WINDOW), "reference_sessions": 257, "reference_weeks": 56,
        "assumed_cost_bps": 33, "uncertainty_recomputed": False, "new_performance_family": False,
        "cap_minute_open_30m": {"endpoint_convention": "entry_open_to_cap_minute_open; managed_exit_uses_its_original_triggered_open", "variants": {}},
        "close_endpoint_30m": {"endpoint_convention": "original_three_minute_entry_plus30m_terminal_minute_close_fixed_for_both_waits", "variants": {}},
        "source_publications": {"CR": {"retained_cq_sizing": {"mde_bps": 33.4,
            "requires_operator_choice": True, "underpower_exception_applied": False}}},
        "limitations": ["original_unknowns_retained"], "promotion_allowed": False,
        "strategy_qualified": False, "direction_closed": False, "actual_portfolio_pnl": False}
    for policy in synthesis.VARIANTS:
        for panel, arms in (("cap_minute_open_30m", ("baseline", "managed_exit", "context_filter")),
                            ("close_endpoint_30m", ("three_minute_wait", "one_minute_wait"))):
            cs[panel]["variants"][policy] = {}
            for family in synthesis.FAMILIES:
                cs[panel]["variants"][policy][family] = {
                    arm: cs_statistics(7, Fraction(25 + i)) for i, arm in enumerate(arms)}
                cs[panel]["variants"][policy][family]["retained_source_contrast"] = {
                    "descriptive_normal_95_interval": [-.002, .003], "uncertainty_recomputed": False}
        for source, arms in (("CW", ("15", "30")), ("DA", ("15",))):
            p = publications[source]
            p.update(reference_sessions=257, reference_weeks=56, current_requires_operator_choice=False,
                retained_target_count_per_policy=5140,
                flags={"observed_label_authorization_applied": True, "underpower_exception_applied": source == "DA",
                       "promotion_allowed": False, "actual_portfolio_pnl": False, "comparison_performed": True})
            p.setdefault("comparisons", {})[policy] = {"by_arm": {}}
            for arm in arms:
                p["comparisons"][policy]["by_arm"][arm] = {"groups": {
                    "original": group(7), "selected": group(4, failed=source == "DA", selected=True)},
                    "selected_minus_original": {"descriptive_normal95_interval": [-.003, .004],
                        "uncertainty": {"design_se": .002}}}
    publications["CS"]["synthesis"] = cs
    return publications, pins


class TestIntradayCandidateSynthesis(unittest.TestCase):
    def setUp(self):
        self.publications, self.pins = invented_publications()

    def run_synthesis(self):
        return synthesis.synthesize(self.publications, expected_pins=self.pins)

    def reject(self):
        with self.assertRaises(synthesis.SynthesisRefusal):
            self.run_synthesis()

    def test_complete_24_row_matrix_8_row_separate_appendix(self):
        report = self.run_synthesis()
        opened, closed = report["cap_minute_open_30m"]["rows"], report["close_endpoint_30m"]["rows"]
        self.assertEqual(len(opened), 24)
        self.assertEqual(len(closed), 8)
        self.assertEqual([sum(row["source"] == source for row in opened) for source in synthesis.SOURCES], [12, 8, 4])
        self.assertEqual({row["source"] for row in closed}, {"CS"})
        for policy in synthesis.VARIANTS:
            self.assertEqual(sum(row["policy"] == policy for row in opened), 12)

    def test_exact_cost_and_gross_mean_sum(self):
        for row in self.run_synthesis()["cap_minute_open_30m"]["rows"]:
            gross = synthesis._ratio(row["gross_mean_bps_exact"])
            self.assertEqual(synthesis._ratio(row["net_mean_bps_exact"]), gross - 33)
            self.assertEqual(synthesis._ratio(row["gross_sum_bps_exact"]), gross * row["known_labels"])

    def test_known_frequency_distinct_from_observed_signals_and_unknown(self):
        rows = self.run_synthesis()["cap_minute_open_30m"]["rows"]
        old, new = rows[0], next(row for row in rows if row["source"] == "CW")
        self.assertIsNone(old["observed_signals_per_week_exact"])
        self.assertIsNone(old["unknown_labels"])
        self.assertEqual(new["known_outcomes_per_week_exact"], exact(Fraction(7, 56)))
        self.assertEqual(new["observed_signals_per_week_exact"], exact(Fraction(8, 56)))
        self.assertEqual(new["unknown_labels"], 1)
        self.assertEqual(len({row["cohort"] for row in rows}), 3)

    def test_original_and_selected_published_frequency_contracts_are_distinct(self):
        for source in ("CW", "DA"):
            groups = self.publications[source]["comparisons"]["D+1"]["by_arm"]["15"]["groups"]
            self.assertNotIn("selected_per_reference_week", groups["original"]["frequency"])
            self.assertNotIn("signals_per_reference_week", groups["selected"]["frequency"])
        self.run_synthesis()

    def test_all_source_mde_flags_intervals_unknowns_retained_without_mutation(self):
        original = deepcopy(self.publications)
        report = self.run_synthesis()
        self.assertEqual(report["source_publications"], original)
        da = next(row for row in report["cap_minute_open_30m"]["rows"] if row["source"] == "DA" and row["arm"] == "selected")
        self.assertTrue(da["source_own_label_dispersion"]["requires_operator_choice"])
        self.assertEqual(da["source_statistics"]["descriptive_normal95_interval"], [-.002, .008])
        report["source_publications"]["CS"].clear()
        self.assertEqual(self.publications, original)

    def test_pure_no_io_and_no_new_claims(self):
        with patch("builtins.open", side_effect=AssertionError("no I/O")):
            report = self.run_synthesis()
        for key in ("new_labels", "new_performance_families", "promotion_trial_increment"):
            self.assertEqual(report[key], 0)
        for key in ("uncertainty_recomputed", "ranking_performed", "strategy_qualified", "direction_closed", "promotion_allowed"):
            self.assertIs(report[key], False)

    def test_retest_design_is_causal_unmeasured_and_uses_new_labels(self):
        design = synthesis.NEXT_HYPOTHESIS
        self.assertIs(design["same_bar_breakout_retest_allowed"], False)
        self.assertIs(design["frequency_measured"], False)
        self.assertIs(design["performance_access_in_this_synthesis"], False)
        self.assertIn("retest_bar_end_plus2", design["decision_cutoff_clock"])
        self.assertIn("new_raw_labels", design["future_labels"])
        self.assertIn("no_retry", design["invalidation"])

    def test_reject_each_identity_and_verification_mismatch(self):
        for source in synthesis.SOURCES:
            for field in ("run_id", "report_sha256", "specification_sha256"):
                with self.subTest(source=source, field=field):
                    self.publications, self.pins = invented_publications()
                    self.pins[source][field] = "x" if field == "run_id" else "f" * 64
                    self.reject()
            self.publications, self.pins = invented_publications()
            self.publications[source]["verification"]["verified" if source == "CS" else "verified_new_comparison_math"] = False
            self.reject()

    def test_reject_changed_cost_count_ratio_and_frequency(self):
        for key, value in (("assumed_cost", exact(Fraction(32, 10000))), ("event_count", 8),
                           ("gross_mean", exact(Fraction(1))), ("net_positive_count", 8)):
            self.publications, self.pins = invented_publications()
            cell = self.publications["DA"]["comparisons"]["D+1"]["by_arm"]["15"]["groups"]["selected"]
            cell[key] = value
            self.reject()
        self.publications, self.pins = invented_publications()
        self.publications["CW"]["comparisons"]["D+1"]["by_arm"]["30"]["groups"]["original"]["frequency"]["reference_weeks"] = 55
        self.reject()

    def test_reject_wrong_source_population_or_arm(self):
        self.publications["CW"]["comparisons"]["D+1"]["by_arm"]["60"] = {}
        self.reject()
        self.publications, self.pins = invented_publications()
        self.publications.pop("DA")
        self.reject()

    def test_reject_lost_source_authorization_and_nonfinite(self):
        self.publications["DA"]["flags"]["underpower_exception_applied"] = False
        self.reject()
        self.publications, self.pins = invented_publications()
        self.publications["CS"]["synthesis"]["nonfinite"] = float("nan")
        self.reject()

    def test_reject_cs_saved_arithmetic_and_bool_count(self):
        cell = self.publications["CS"]["synthesis"]["cap_minute_open_30m"]["variants"]["D+1"]["opening_range_breakout"]["baseline"]
        cell["gross_mean_bps_exact"] = exact(999)
        self.reject()
        self.publications, self.pins = invented_publications()
        cell = self.publications["CS"]["synthesis"]["close_endpoint_30m"]["variants"]["D+1"]["post_drop_recovery"]["one_minute_wait"]
        cell["opportunities"] = True
        self.reject()


if __name__ == "__main__":
    unittest.main()
