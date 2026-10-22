"""Invented publication fixtures only; no actual research input is opened."""

from copy import deepcopy
from datetime import date, timedelta
from fractions import Fraction
import hashlib
import json
import math
import unittest
from unittest.mock import patch

from research import intraday_result_synthesis as synthesis


def exact(value: Fraction) -> dict:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def arm(n: int, mean: Fraction) -> dict:
    return {"events": n, "sum_price_change": exact(mean * n), "mean_price_change": float(mean),
            "mean_after_assumed_cost": float(mean - Fraction(33, 10000)),
            "gross_positive_events": n, "cost_exceeding_events": n if mean > Fraction(33, 10000) else 0}


def own(n: int) -> dict:
    return {"measured_labels": n, "reference_sessions": 257, "requires_operator_choice": False,
            "mde_bps": 12.0, "underpower_exception_applied": False}


def paired(n: int, difference: Fraction) -> dict:
    return {"events": n, "sum_change": exact(n * difference), "mean_change": float(difference),
            "improved_events": n if difference > 0 else 0, "equal_events": n if not difference else 0,
            "worse_events": n if difference < 0 else 0,
            "uncertainty": {"reference_sessions": 257, "design_se": .001},
            "descriptive_normal_95_interval": [float(difference) - .002, float(difference) + .002]}


def counts(n: int, m: int) -> dict:
    return {"original_events": n, "filtered_events": m, "removed_events": n - m,
            "retention_fraction": m / n if n else None}


def weeks() -> list[str]:
    start, end = date(2025, 9, 1), date(2026, 9, 21)
    result = []
    while start <= end:
        year, week, _ = start.isocalendar()
        result.append(f"{year}-W{week:02d}")
        start += timedelta(days=7)
    return result


def refresh_summaries(publications: dict) -> None:
    for publication in publications.values():
        raw = (json.dumps(publication["summaries"], sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, allow_nan=False) + "\n").encode()
        publication["summaries_sha256"] = hashlib.sha256(raw).hexdigest()


def invented_publications() -> tuple[dict, dict]:
    """Small invented event counts over the already-fixed reference calendar."""
    identities = {
        "CL": ("intraday-entry-delay-comparison-v1", "entry_delay_compared_within_budget",
               "fixed-exit-clock-one-vs-three-minute-entry-comparison"),
        "CN": ("intraday-exit-policy-comparison-v1", "exit_policy_compared_within_budget",
               "fixed-three-minute-entry-time-only-vs-close-trigger-managed-exit-comparison"),
        "CP": ("intraday-peer-context-comparison-v1", "peer_context_compared_within_budget",
               "fixed-opening-breakout-positive-original-peer-context-conditional-per-entry-comparison"),
        "CR": ("intraday-recovery-context-comparison-v1", "recovery_context_compared_within_budget",
               "fixed-post-drop-recovery-negative-original-event-context-exploratory-conditional-per-entry-comparison"),
    }
    publication, pins = {}, {}
    common_evidence = {"retained_unknowns": 7, "assumed_availability": True}
    cm_evidence = {"upstream_report_sha256": "a" * 64, "calibration_input_evidence": common_evidence}
    for index, (source, (study, status, mode)) in enumerate(identities.items(), start=1):
        pin = {"run_id": f"invented-{source}", "report_sha256": str(index) * 64,
               "specification_sha256": str(index + 4) * 64}
        pins[source] = pin
        parameters = {"cost_floor_bps": "33",
            "cost": "subtract_exact_33_over_10000_from_each_event_both_arms_same_assumption"}
        if source == "CL":
            parameters.update(price_label="terminal_minute_close_divided_by_entry_minute_open_minus_one_all_intervening_minutes_valid",
                              exit="original_current_entry_plus_original_horizon_unchanged_for_both_labels")
        elif source == "CN":
            parameters.update(time_cap_minutes=30, time_only="preplanned_at_original_decision_exit_open_at_entry_plus_30minutes")
        else:
            parameters = {"cost": "subtract_exact33_over10000_per_event_same_assumed_cost_cancels_in_difference",
                          "entry_exit": "same_original_three_minute_wait_entry_open_and_time_only_entry_plus30minute_exit_open"}
        evidence = (deepcopy(cm_evidence) if source in ("CL", "CN") else
                    {"upstream_report_sha256": "b" * 64, "calibration_input_evidence": deepcopy(cm_evidence)})
        result = {"output_sha256": pin["report_sha256"], "input_evidence": deepcopy(evidence),
                  "requires_operator_choice": False}
        if source == "CR":
            result.update(underpower_exception_applied=True, qualified_cells_power_prerequisite_met=False,
                          retained_cq_sizing={"mde_bps": 33.4, "requires_operator_choice": True,
                                              "underpower_exception_applied": False})
        verification = {"verified": True, "study_id": study, "report_sha256": pin["report_sha256"],
                        "specification_sha256": pin["specification_sha256"], "input_evidence": evidence,
                        "selected_complete": {}, "requires_operator_choice": False,
                        "source_runtime_authenticated": False}
        if source in ("CP", "CR"):
            verification.update(original_complete={}, reference_sessions=257)
        publication[source] = {"status": status, "run_id": pin["run_id"], "output_sha256": pin["report_sha256"],
            "calibration_only": False, "requires_operator_choice": False, "summaries": {},
            "record": {"study_id": study, "status": "completed", "run_id": pin["run_id"],
                "specification_sha256": pin["specification_sha256"], "result": result,
                "parameters": {"mode": mode, "parameters": parameters}, "promotion_allowed": False,
                "promotion_trial_increment": 0,
                "window": {"symbol": "KRX:*", "interval": "1m", "start": "2025-09-04", "end": "2026-09-23"}},
            "verification": verification}
        if source == "CR":
            publication[source].update(underpower_exception_applied=True, qualified_cells_power_prerequisite_met=False)
    for variant_index, variant in enumerate(("D+1", "D+2")):
        for source in ("CL", "CN"):
            publication[source]["summaries"][variant] = {}
            publication[source]["verification"]["selected_complete"][variant] = {}
        for family_index, (family, filtered_source) in enumerate((("opening_range_breakout", "CP"), ("post_drop_recovery", "CR"))):
            n, m = 4 + variant_index - family_index, 3 + variant_index - family_index
            baseline, managed = arm(n, Fraction(3, 1000)), arm(n, Fraction(4, 1000))
            current, earlier = arm(n, Fraction(2, 1000)), arm(n, Fraction(15, 10000))
            cn = {"selected_complete": n, "retained_unknowns": 7, "by_arm": {},
                "time_only": baseline, "managed": managed, "paired": paired(n, Fraction(1, 1000)),
                "old_close_bridge": {"old_close": deepcopy(current)}}
            for name in ("time_only", "managed"):
                cn["by_arm"][name] = {"selected_complete": n, "known_labels": n, "unknown_labels": 0,
                                       "requires_operator_choice": False, "own_label_dispersion": own(n)}
            cell = {"selected_complete": n, "earlier_known_labels": n, "earlier_unknown_labels": 0,
                "earlier_own_power": own(n), "current_own_power": own(n), "current_power_prerequisite_met": True,
                "requires_operator_choice": False, "current": current, "earlier": earlier,
                "paired": paired(n, Fraction(-5, 10000))}
            horizons = ("5", "15", "30", "60", "120") if family_index == 0 else ("5", "15", "30")
            cl = {"selected_complete": n, "retained_unknowns": 7,
                  "by_horizon": {horizon: deepcopy(cell) for horizon in horizons}}
            for source, summary in (("CL", cl), ("CN", cn)):
                publication[source]["summaries"][variant][family] = summary
                publication[source]["verification"]["selected_complete"][variant][family] = n
            selected = arm(m, Fraction(35, 10000))
            selected["cost_exceeding_fraction"] = 1.0
            if filtered_source == "CR":
                selected["gross_positive_fraction"] = 1.0
            selection = {**counts(n, m), "by_week": {week: counts(n if index == 0 else 0, m if index == 0 else 0)
                         for index, week in enumerate(weeks())}}
            if filtered_source == "CR":
                selection.update(reference_weeks=56, original_opportunities_per_week=n / 56,
                                 filtered_opportunities_per_week=m / 56, removed_opportunities_per_week=(n - m) / 56)
            difference = {"mean_change": .0005, "descriptive_normal_95_interval": [-.001, .002],
                          "uncertainty": {"reference_sessions": 257, "original_events": n, "filtered_events": m}}
            publication[filtered_source]["summaries"][variant] = {
                "original": deepcopy(baseline), "filtered": selected, "selection": selection, "difference": difference}
            publication[filtered_source]["verification"]["original_complete"][variant] = n
            publication[filtered_source]["verification"]["selected_complete"][variant] = m
    refresh_summaries(publication)
    return publication, pins


class TestIntradayResultSynthesis(unittest.TestCase):
    def setUp(self):
        self.publications, self.pins = invented_publications()

    def run_synthesis(self):
        return synthesis.synthesize(self.publications, expected_pins=self.pins)

    def reject(self, reason=None, *, refresh=True):
        if refresh:
            refresh_summaries(self.publications)
        with self.assertRaises(synthesis.SynthesisRefusal) as caught:
            self.run_synthesis()
        if reason:
            self.assertEqual(str(caught.exception), reason)

    def test_exact_gross_net_cost_and_separate_endpoint_panels(self):
        result = self.run_synthesis()
        opened = result["cap_minute_open_30m"]["variants"]["D+1"]["opening_range_breakout"]
        closed = result["close_endpoint_30m"]["variants"]["D+1"]["opening_range_breakout"]
        self.assertEqual(opened["baseline"]["gross_mean_bps_exact"], exact(Fraction(30)))
        self.assertEqual(opened["baseline"]["net_mean_bps_exact"], exact(Fraction(-3)))
        self.assertEqual(opened["managed_exit"]["net_mean_bps_exact"], exact(Fraction(7)))
        self.assertEqual(opened["context_filter"]["net_mean_bps_exact"], exact(Fraction(2)))
        self.assertEqual(opened["managed_minus_baseline"]["mean_change_bps_exact"], exact(Fraction(10)))
        self.assertEqual(opened["filtered_minus_original"]["mean_change_bps_exact"], exact(Fraction(5)))
        self.assertEqual(closed["one_minus_three_minute_wait"]["mean_change_bps_exact"], exact(Fraction(-5)))
        self.assertEqual(closed["three_minute_wait"]["net_mean_bps"], -13)
        self.assertFalse(result["new_performance_family"])
        self.assertFalse(result["uncertainty_recomputed"])
        self.assertFalse(result["promotion_allowed"])

    def test_frequency_uses_all_56_weeks_including_zero_and_count_change(self):
        result = self.run_synthesis()
        panel = result["cap_minute_open_30m"]["variants"]["D+1"]["opening_range_breakout"]
        self.assertEqual(panel["baseline"]["opportunities_per_week_exact"], exact(Fraction(4, 56)))
        self.assertEqual(panel["filtered_minus_original"]["opportunities_per_week_change_exact"], exact(Fraction(-1, 56)))
        weekly = panel["source_selection"]["by_week"]
        self.assertEqual(len(weekly), 56)
        self.assertEqual(sum(row["original_events"] == 0 for row in weekly.values()), 55)
        self.assertIn("not_realized_portfolio_trades", result["frequency_basis"])

    def test_source_intervals_full_other_horizons_and_failed_cq_evidence_survive(self):
        result = self.run_synthesis()
        for source in ("CL", "CN", "CP", "CR"):
            self.assertEqual(result["source_publications"][source]["summaries"], self.publications[source]["summaries"])
        original = self.publications["CN"]["summaries"]["D+1"]["opening_range_breakout"]["paired"]
        contrast = result["cap_minute_open_30m"]["variants"]["D+1"]["opening_range_breakout"]["managed_minus_baseline"]
        self.assertEqual(contrast["source_contrast"], original)
        failed = result["source_publications"]["CR"]["record"]["result"]["retained_cq_sizing"]
        self.assertTrue(failed["requires_operator_choice"])
        self.assertFalse(failed["underpower_exception_applied"])
        self.assertFalse(result["cap_minute_open_30m"]["variants"]["D+1"]["post_drop_recovery"]["own_power_prerequisite_met"])

    def test_pure_no_file_io_and_no_shared_mutable_state(self):
        original = deepcopy(self.publications)
        with patch("builtins.open", side_effect=AssertionError("no file IO")):
            result = self.run_synthesis()
        self.assertEqual(original, self.publications)
        result["source_publications"]["CR"]["summaries"].clear()
        self.assertEqual(original, self.publications)

    def test_reject_source_identity_and_expected_pin_mismatches(self):
        paths = (("record", "study_id"), ("run_id",), ("record", "run_id"),
                 ("verification", "study_id"), ("verification", "report_sha256"),
                 ("record", "specification_sha256"), ("verification", "specification_sha256"))
        for path in paths:
            with self.subTest(path=path):
                self.publications, self.pins = invented_publications()
                target = self.publications["CN"]
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = "wrong"
                self.reject("source_identity_mismatch")
        self.publications, self.pins = invented_publications()
        self.pins["CL"]["report_sha256"] = "f" * 64
        self.reject("source_identity_mismatch")

    def test_reject_self_declared_but_unverified_source(self):
        self.publications["CR"]["verification"]["verified"] = False
        self.reject("unverified_source")

    def test_reject_changed_fixed_family_or_horizon_population(self):
        self.publications["CN"]["summaries"]["D+1"]["new_family"] = {}
        self.reject("family_population_mismatch")
        self.publications, self.pins = invented_publications()
        del self.publications["CL"]["summaries"]["D+1"]["opening_range_breakout"]["by_horizon"]["120"]
        self.reject("horizon_population_mismatch")

    def test_reject_summary_hash_mismatch(self):
        self.publications["CL"]["summaries"]["D+1"]["opening_range_breakout"]["selected_complete"] = 8
        self.reject("source_summaries_hash_mismatch", refresh=False)

    def test_reject_wrong_cost_or_exit_convention(self):
        mutations = (("CN", "cost_floor_bps", "32"), ("CN", "time_cap_minutes", 15),
                     ("CL", "price_label", "cap_open"), ("CP", "entry_exit", "terminal_close"),
                     ("CR", "cost", "no_cost"))
        for source, field, value in mutations:
            with self.subTest(source=source, field=field):
                self.publications, self.pins = invented_publications()
                self.publications[source]["record"]["parameters"]["parameters"][field] = value
                self.reject()

    def test_reject_gross_net_inconsistency_even_if_cost_metadata_agrees(self):
        self.publications["CN"]["summaries"]["D+1"]["opening_range_breakout"]["managed"]["mean_after_assumed_cost"] = .004
        self.reject("source_arithmetic_mismatch")

    def test_reject_source_count_or_common_cm_sum_mismatch(self):
        self.publications["CP"]["summaries"]["D+1"]["original"] = arm(4, Fraction(31, 10000))
        self.reject("common_cm_baseline_mismatch")
        self.publications, self.pins = invented_publications()
        self.publications["CP"]["verification"]["original_complete"]["D+1"] = 5
        self.reject("source_count_mismatch")

    def test_reject_common_cm_provenance_mismatch(self):
        evidence = self.publications["CR"]["verification"]["input_evidence"]
        evidence["calibration_input_evidence"]["upstream_report_sha256"] = "c" * 64
        self.publications["CR"]["record"]["result"]["input_evidence"] = deepcopy(evidence)
        self.reject("common_cm_provenance_mismatch")

    def test_reject_missing_zero_week_or_changed_week_population(self):
        selection = self.publications["CP"]["summaries"]["D+1"]["selection"]
        del selection["by_week"][weeks()[-1]]
        self.reject("zero_inclusive_week_population_mismatch")

    def test_reject_weekly_counts_disagree_with_total_and_filtered_population(self):
        selection = self.publications["CR"]["summaries"]["D+1"]["selection"]
        selection["by_week"][weeks()[0]] = counts(4, 2)
        self.reject("weekly_count_mismatch")

    def test_reject_bad_source_interval_or_paired_sum(self):
        self.publications["CN"]["summaries"]["D+1"]["opening_range_breakout"]["paired"]["descriptive_normal_95_interval"] = [.01, .02]
        self.reject("invalid_source_interval")
        self.publications, self.pins = invented_publications()
        self.publications["CL"]["summaries"]["D+1"]["opening_range_breakout"]["by_horizon"]["30"]["paired"]["sum_change"] = exact(Fraction(1))
        self.reject("source_arithmetic_mismatch")

    def test_reject_lost_recovery_failed_power_and_unknown_or_failed_new_sizing(self):
        self.publications["CR"]["qualified_cells_power_prerequisite_met"] = True
        self.reject("recovery_failed_power_not_retained")
        self.publications, self.pins = invented_publications()
        self.publications["CN"]["summaries"]["D+1"]["opening_range_breakout"]["by_arm"]["managed"]["unknown_labels"] = 1
        self.reject("source_sizing_mismatch")

    def test_reject_nonfinite_values_and_malformed_counts_ratios_sources(self):
        for value in (math.inf, -math.inf, math.nan):
            with self.subTest(value=value):
                self.publications, self.pins = invented_publications()
                self.publications["CL"]["record"]["unrelated_float"] = value
                self.reject("nonfinite_source", refresh=False)
        mutations = (
            lambda p: p["CP"]["summaries"]["D+1"]["original"].update(events=True),
            lambda p: p["CP"]["summaries"]["D+1"]["original"].update(events=0),
            lambda p: p["CP"]["summaries"]["D+1"]["original"].update(sum_price_change={"numerator": "1", "denominator": "0"}),
            lambda p: p["CP"]["summaries"].pop("D+2"),
            lambda p: p["CN"].pop("verification"),
            lambda p: p.update(EXTRA={}),
        )
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                self.publications, self.pins = invented_publications()
                mutate(self.publications)
                self.reject(refresh="EXTRA" not in self.publications)


if __name__ == "__main__":
    unittest.main()
