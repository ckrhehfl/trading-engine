"""Invented aggregate publications only; no saved market artifact is opened."""
from copy import deepcopy
from fractions import Fraction
import json
import unittest
from unittest.mock import patch

from research import intraday_candidate_update as update


def exact(value):
    value = Fraction(value)
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def historical_row(source, policy, family, arm):
    n = 7 if arm in ("baseline", "original", "three_minute_wait") else 4
    observed = None if source == "CS" else n + 1
    gross = Fraction(53, 7)
    return {"source": source, "policy": policy, "family": family, "arm": arm,
        "cohort": source + "_original_cohort", "known_labels": n,
        "observed_signals": observed, "unknown_labels": None if observed is None else 1,
        "gross_sum_bps_exact": exact(gross * n), "gross_mean_bps_exact": exact(gross),
        "net_mean_bps_exact": exact(gross - 33), "gross_mean_bps": float(gross),
        "net_mean_bps": float(gross - 33), "assumed_cost_bps": 33,
        "known_outcomes_per_week_exact": exact(Fraction(n, 56)),
        "observed_signals_per_week_exact": None if observed is None else exact(Fraction(observed, 56)),
        "source_statistics": {"historical_statistic": "retained", "interval": {"lower": -.004, "upper": .007}},
        "source_own_label_dispersion": {"mde_bps": 40.9, "requires_operator_choice": True,
            "underpower_exception_applied": False}}


def statistics(n, *, modern=False):
    gross = Fraction(17, 7000)
    result = {"gross_sum": exact(n * gross), "net_sum": exact(n * (gross - update.COST)),
        "gross_mean": exact(gross), "net_mean": exact(gross - update.COST),
        "gross_median": exact(gross), "net_median": exact(gross - update.COST),
        "assumed_cost": exact(update.COST),
        "gross_descriptive_interval": {"lower": -.0013, "upper": .0071},
        "net_descriptive_interval": {"lower": -.0046, "upper": .0038}}
    if modern:
        result.update(known_labels=n, unknown_labels=0, gross_positive_count=n,
            net_positive_count=0, gross_positive_fraction=exact(1), net_positive_fraction=exact(0),
            endpoint_gross_strictly_above_cost_count=0, endpoint_gross_strictly_above_cost_fraction=exact(0))
    else:
        result.update(event_count=n, gross_wins=n, net_wins=0, cost_exceeding=0,
            gross_win_fraction=exact(1), net_win_fraction=exact(0), cost_exceedance_fraction=exact(0))
    return result


def invented_publications():
    publications, pins = {}, {}
    weeks = ["invented-week-" + str(i) for i in range(56)]
    for index, source in enumerate(update.SOURCES):
        study, status, mode, verification_flag = update.IDENTITIES[source]
        pin = {"run_id": "invented-" + source, "report_sha256": format(index + 1, "x") * 64,
            "specification_sha256": format(index + 7, "x") * 64}
        pins[source] = pin
        publication = {"status": status, "run_id": pin["run_id"], "output_sha256": pin["report_sha256"],
            "record": {"status": "completed", "study_id": study, "run_id": pin["run_id"],
                "specification_sha256": pin["specification_sha256"], "window": dict(update.WINDOW),
                "parameters": {"mode": mode, "parameters": {"assumed_cost": exact(update.COST)}},
                "result": {"output_sha256": pin["report_sha256"]},
                "promotion_allowed": False, "promotion_trial_increment": 0},
            "verification": {verification_flag: True, "study_id": study,
                "report_sha256": pin["report_sha256"], "specification_sha256": pin["specification_sha256"],
                "verified_reference_sessions": 257, "verified_reference_weeks": 56},
            "retained_receipt": {"exception_scope": "historical_only", "numeric_negative_zero": -0.0}}
        publications[source] = publication
        if source == "DS":
            # The original DS verifier authenticates its study through its
            # compact_report; unlike the other five it has no study_id field.
            publication["verification"].pop("study_id")
        if source == "DB":
            body = {"window": dict(update.WINDOW), "reference_sessions": 257, "reference_weeks": 56,
                "assumed_cost_bps": 33, "new_labels": 0, "new_performance_families": 0,
                "promotion_trial_increment": 0, "source_publications": {"CR": {
                    "retained_sizing": {"mde_bps": 33.4, "requires_operator_choice": True}}},
                **dict.fromkeys(("uncertainty_recomputed", "ranking_performed", "strategy_qualified",
                    "direction_closed", "promotion_allowed", "actual_portfolio_pnl"), False)}
            for panel in ("cap_minute_open_30m", "close_endpoint_30m"):
                rows = []
                for policy in update.VARIANTS:
                    for family in ("opening_range_breakout", "post_drop_recovery"):
                        arms = ("baseline", "managed_exit", "context_filter") if panel == "cap_minute_open_30m" else ("three_minute_wait", "one_minute_wait")
                        rows.extend(historical_row("CS", policy, family, arm) for arm in arms)
                    if panel == "cap_minute_open_30m":
                        rows.extend(historical_row("CW", policy, "opening_range_" + length, arm)
                            for length in ("15", "30") for arm in ("original", "selected"))
                        rows.extend(historical_row("DA", policy, "volume_ready_opening_range_15", arm)
                            for arm in ("original", "selected"))
                body[panel] = {"rows": rows}
            publication["synthesis"] = body
            continue
        body = {"variants": {}, "comparison_performed": True,
            "requires_operator_choice_before_comparison": False, "comparison_not_run_reasons": []}
        for policy in update.VARIANTS:
            variant = {"by_arm": {}, "comparison": {"by_arm": {}, "retained_contrast": {
                "uncertainty": {"design_se": .002, "bootstrap_seed": 0},
                "descriptive_normal95_interval": {"lower": -.004, "upper": .006}}},
                "cohorts": {"common_outcome_complete": {"by_arm": {}}},
                "population": {"targets": 5140, "source_unknowns": 2609,
                    "excluded_unknown_features": 3 if source == "DS" else 0}}
            for index, arm in enumerate(update.ARMS[source]):
                n = 84 if index == 0 else 28
                own = {"events": n, "known_labels": n, "unknown_labels": 0,
                    "reference_sessions": 257, "mde_bps": 12.7, "design_se": .0004,
                    "bootstrap_draws": 2000, "bootstrap_seed": 0}
                weekly = {week: n if i == 0 else 0 for i, week in enumerate(weeks)}
                frequency = {"reference_sessions": 257, "reference_weeks": 56,
                    "signals_per_reference_week": exact(Fraction(n, 56)),
                    "known_labels_per_reference_week": exact(Fraction(n, 56)),
                    "by_week": weekly, "events": n,
                    "mean_per_all_reference_weeks": exact(Fraction(n, 56))}
                cell = {"own_label_dispersion": own, "sizing_passed": True,
                    "comparison_choice_reasons": [], "frequency": frequency}
                if source in ("DN", "DS"):
                    cell.update(statistics(n, modern=True), events=n, by_week=weekly)
                    variant["comparison"].pop("by_arm", None)
                else:
                    variant["comparison"]["by_arm"][arm] = statistics(n)
                variant["by_arm"][arm] = cell
                variant["cohorts"]["common_outcome_complete"]["by_arm"][arm] = {
                    "signal_counts": {"signal": n, "unknown": 0, "known_no_signal": 0},
                    "label_counts": {"known": n, "unknown": 0}, "frequency": frequency}
            body["variants"][policy] = variant
        if source in ("DN", "DS"):
            body.update(study_id=study, mode=mode, window=dict(update.WINDOW),
                reference_sessions=["invented-session-" + str(i) for i in range(257)],
                reference_weeks=weeks, promotion_allowed=False, underpower_exception_applied=False)
            publication["diagnostic"] = body
            publication["verification"]["compact_report"] = deepcopy(body)
        else:
            publication.update(body, claims={"promotion_allowed": False, "underpower_exception_applied": False})
            publication["verification"].update(deepcopy(body))
    return publications, pins


class TestIntradayCandidateUpdate(unittest.TestCase):
    def setUp(self):
        self.publications, self.pins = invented_publications()
        self.hypothesis = {"id": "invented-fixed-next-pattern-v1", "frequency_measured": False,
            "underpower_exception_inherited": False}

    def run_update(self):
        return update.synthesize(self.publications, expected_pins=self.pins, next_hypothesis=self.hypothesis)

    def resign(self, source):
        publication = self.publications[source]
        if source in ("DN", "DS"):
            publication["verification"]["compact_report"] = deepcopy(publication["diagnostic"])
        elif source != "DB":
            publication["verification"]["variants"] = deepcopy(publication["variants"])

    def reject(self):
        with self.assertRaises(update.SynthesisRefusal):
            self.run_update()

    def test_complete_matrix_preserves_endpoint_and_source_cohorts(self):
        report = self.run_update()
        opened, closed = report["cap_minute_open_30m"]["rows"], report["close_endpoint_30m"]["rows"]
        self.assertEqual(len(opened), 44)
        self.assertEqual(len(closed), 8)
        self.assertEqual([sum(row["source"] == source for row in opened) for source in update.SOURCES], [24, 4, 4, 4, 4, 4])
        self.assertEqual({row["source"] for row in closed}, {"DB"})
        self.assertEqual({row["endpoint_convention"] for row in closed}, {
            "original_three_minute_entry_plus30m_terminal_minute_close_fixed_for_both_waits"})
        self.assertEqual({row["published_source"] for row in opened if row["source"] == "DB"}, {"CS", "CW", "DA"})
        managed = next(row for row in opened if row["source"] == "DK" and row["arm"] == "managed")
        self.assertIn("triggered_minute_open", managed["endpoint_convention"])
        self.assertEqual(len({row["cohort"] for row in opened}), 8)

    def test_exact_unit_conversion_cost_and_distinct_denominators(self):
        rows = self.run_update()["cap_minute_open_30m"]["rows"]
        for row in rows:
            with self.subTest(source=row["source"], arm=row["arm"]):
                gross = update._ratio(row["gross_mean_bps_exact"])
                self.assertEqual(update._ratio(row["net_mean_bps_exact"]), gross - 33)
                self.assertEqual(update._ratio(row["gross_sum_bps_exact"]), gross * row["known_labels"])
                self.assertEqual(update._ratio(row["known_outcomes_per_week_exact"]), Fraction(row["known_labels"], 56))
        old = next(row for row in rows if row["source"] == "DB" and row["published_source"] == "CS")
        self.assertIsNone(old["observed_signals_per_week_exact"])
        self.assertIsNone(old["unknown_labels"])
        old_volume = next(row for row in rows if row["source"] == "DB" and row["published_source"] == "DA")
        self.assertEqual(update._ratio(old_volume["observed_signals_per_week_exact"]), Fraction(old_volume["known_labels"] + 1, 56))
        selected = next(row for row in rows if row["source"] == "DI" and row["arm"] == "volatility_selected")
        self.assertEqual(update._ratio(selected["gross_mean_bps_exact"]), Fraction(170, 7))
        self.assertEqual(update._ratio(selected["observed_signals_per_week_exact"]), Fraction(1, 2))

    def test_complete_evidence_is_independent_copy_with_original_float_bits(self):
        before = json.dumps(self.publications, sort_keys=True)
        report = self.run_update()
        self.assertEqual(json.dumps(report["source_publications"], sort_keys=True), before)
        self.assertEqual(report["source_pins"], self.pins)
        self.assertEqual(report["next_hypothesis"], self.hypothesis)
        old = report["cap_minute_open_30m"]["rows"][0]
        self.assertEqual(old["source_own_label_dispersion"]["mde_bps"], 40.9)
        self.assertTrue(old["source_own_label_dispersion"]["requires_operator_choice"])
        self.assertEqual(old["source_statistics"], self.publications["DB"]["synthesis"]["cap_minute_open_30m"]["rows"][0])
        report["source_publications"]["DS"]["diagnostic"]["variants"]["D+1"]["population"]["source_unknowns"] = 0
        old["source_statistics"]["source_statistics"]["interval"]["lower"] = 99
        report["next_hypothesis"]["id"] = "changed"
        self.assertEqual(json.dumps(self.publications, sort_keys=True), before)
        self.assertEqual(self.hypothesis["id"], "invented-fixed-next-pattern-v1")

    def test_scope_has_no_new_measurement_or_ranking_and_requires_no_io(self):
        self.assertNotIn("study_id", self.publications["DS"]["verification"])
        with patch("builtins.open", side_effect=AssertionError("unexpected I/O")):
            report = self.run_update()
        for key in ("new_labels", "new_performance_families", "promotion_trial_increment"):
            self.assertEqual(report[key], 0)
        for key in ("uncertainty_recomputed", "ranking_performed", "pooled_estimation_performed",
                "strategy_qualified", "direction_closed", "promotion_allowed", "actual_portfolio_pnl", "next_hypothesis_measured"):
            self.assertIs(report[key], False)
        json.dumps(report, allow_nan=False)

    def test_all_six_identity_and_completion_pins_fail_closed(self):
        original = deepcopy(self.publications)
        for source in update.SOURCES:
            for target, key, value in (("record", "status", "failed"), ("record", "promotion_allowed", True),
                    ("record", "promotion_trial_increment", True), ("verification", "report_sha256", "f" * 64),
                    ("verification", "specification_sha256", "f" * 64),
                    ("verification", update.IDENTITIES[source][3], False)):
                with self.subTest(source=source, key=key):
                    self.publications = deepcopy(original)
                    self.publications[source][target][key] = value
                    self.reject()

    def test_verified_summary_and_exact_aggregate_mutations_are_rejected(self):
        original = deepcopy(self.publications)
        for source in update.SOURCES[1:]:
            for mutation in ("mean", "sum", "net", "cost", "frequency", "count", "mde", "week", "arm"):
                with self.subTest(source=source, mutation=mutation):
                    self.publications = deepcopy(original)
                    publication = self.publications[source]
                    body = publication["diagnostic"] if source in ("DN", "DS") else publication
                    variant = body["variants"]["D+1"]
                    arm = update.ARMS[source][0]
                    cell = variant["by_arm"][arm]
                    stats = cell if source in ("DN", "DS") else variant["comparison"]["by_arm"][arm]
                    frequency = variant["cohorts"]["common_outcome_complete"]["by_arm"][arm]["frequency"] if source == "DH" else cell["frequency"]
                    if mutation in ("mean", "sum", "net", "cost"):
                        key = {"mean": "gross_mean", "sum": "gross_sum", "net": "net_sum", "cost": "assumed_cost"}[mutation]
                        stats[key] = exact(Fraction(1, 999))
                    elif mutation == "frequency":
                        key = "mean_per_all_reference_weeks" if source in ("DN", "DS") else "signals_per_reference_week"
                        frequency[key] = exact(2)
                    elif mutation == "count":
                        cell["own_label_dispersion"]["known_labels"] += 1
                    elif mutation == "mde":
                        cell["own_label_dispersion"]["mde_bps"] = 34.0
                    elif mutation == "week":
                        frequency["reference_weeks"] = 55
                    else:
                        variant["by_arm"]["invented_extra_arm"] = deepcopy(cell)
                    self.resign(source)
                    self.reject()
        self.publications = deepcopy(original)
        self.publications["DH"]["verification"]["variants"]["D+1"]["comparison"]["by_arm"]["basic"]["gross_mean"] = exact(0)
        self.reject()

    def test_DB_population_unknowns_and_arithmetic_mutations_are_rejected(self):
        original = deepcopy(self.publications)
        for mutation in ("duplicate", "missing", "endpoint", "count", "frequency", "cost", "net", "missingness"):
            with self.subTest(mutation=mutation):
                self.publications = deepcopy(original)
                rows = self.publications["DB"]["synthesis"]["cap_minute_open_30m"]["rows"]
                row = rows[0]
                if mutation == "duplicate": rows[-1] = deepcopy(row)
                elif mutation == "missing": rows.pop()
                elif mutation == "endpoint": row["arm"] = "one_minute_wait"
                elif mutation == "count": row["known_labels"] = True
                elif mutation == "frequency": row["known_outcomes_per_week_exact"] = exact(2)
                elif mutation == "cost": row["assumed_cost_bps"] = 32
                elif mutation == "net": row["net_mean_bps_exact"] = exact(0)
                else: row["unknown_labels"] = 0
                self.reject()

    def test_malformed_shapes_and_nonfinite_evidence_are_rejected(self):
        original = deepcopy(self.publications)
        for mutation in ("source", "pin", "ratio", "nan", "bool", "empty_next"):
            with self.subTest(mutation=mutation):
                self.publications = deepcopy(original)
                if mutation == "source": self.publications["EXTRA"] = {}
                elif mutation == "pin": self.pins["DB"]["report_sha256"] = "not-a-hash"
                elif mutation == "ratio":
                    self.publications["DB"]["synthesis"]["cap_minute_open_30m"]["rows"][0]["gross_mean_bps_exact"] = {"numerator": "1", "denominator": "0"}
                elif mutation == "nan": self.publications["DS"]["retained_receipt"]["unread_estimate"] = float("nan")
                elif mutation == "bool": self.publications["DI"]["verification"]["verified_reference_weeks"] = True
                else: self.hypothesis = {}
                self.reject()
                self.publications, self.pins = invented_publications()


if __name__ == "__main__":
    unittest.main()
