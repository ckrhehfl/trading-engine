"""Summarize exact EI entry outcomes on the original evaluation denominator."""

from collections import Counter, defaultdict
from fractions import Fraction

from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_readiness import _week
from research.intraday_context_economic_fold import EconomicFoldResult
from research.intraday_context_entry_compare import EntryComparison
from research.intraday_ed_inputs import _require


def _arm_report(rows, mode, cost_bps):
    arms = tuple(getattr(row, mode) for row in rows)
    fills = sum(arm.fill is not None and arm.fill.filled for arm in arms)
    closed = tuple(arm.payoff for arm in arms if arm.status == "closed")
    _require(len(closed) <= fills and all(payoff is not None and payoff.entry_notional > 0
                 and payoff.cost == payoff.entry_notional * Fraction(cost_bps, 10000) for payoff in closed),
             "EI_entry_report_closed_basis_differs")
    gross = sum((payoff.gross for payoff in closed), Fraction())
    notional = sum((payoff.entry_notional for payoff in closed), Fraction())
    return {"status_counts": dict(sorted(Counter(arm.status for arm in arms).items())),
            "modeled_fills": fills, "resolved_fills": len(closed), "fills_with_unknown_outcome": fills - len(closed),
            "closed_gross_pnl": gross if closed else None,
            "closed_net_pnl": sum((payoff.net for payoff in closed), Fraction()) if closed else None,
            "closed_assumed_cost": sum((payoff.cost for payoff in closed), Fraction()) if closed else None,
            "mean_closed_trade_net_bps": (sum((payoff.net_return for payoff in closed), Fraction()) * 10000
                                          / len(closed)) if closed else None,
            "closed_fixed_notional_break_even_cost_bps": 10000 * gross / notional if closed else None}


def _comparison(rows, cost_bps):
    baseline = _arm_report(rows, "baseline", cost_bps)
    selected = _arm_report(rows, "selected", cost_bps)
    paired = tuple(row for row in rows if row.net_delta is not None)
    count = baseline["modeled_fills"]
    return {"original_opportunities": len(rows), "baseline": baseline, "selected": selected,
            "modeled_fill_reduction_fraction": Fraction(count - selected["modeled_fills"], count) if count else None,
            "both_known_opportunities": len(paired), "unpaired_opportunities": len(rows) - len(paired),
            "paired_selected_minus_baseline_net_pnl": (sum((row.net_delta for row in paired), Fraction())
                                                       if paired else None),
            "paired_baseline_modeled_fills": sum(row.baseline.fill is not None and row.baseline.fill.filled for row in paired),
            "paired_selected_modeled_fills": sum(row.selected.fill is not None and row.selected.fill.filled for row in paired)}


def summarize_entry_comparisons(comparisons: tuple[EntryComparison, ...], *,
                               expected_folds: tuple[ChronologicalFold, ...]) -> dict:
    """Pool complete disjoint evaluation folds of one scenario/cost profile.

    The caller independently verifies original roster, preparation and replay
    first. Conditional trade return uses closed fills. Paired opportunity P&L
    includes known skips and confirmed no-fills. Unknowns stay outside the paired numerator.
    Weekly counts retain zero-opportunity and entirely missing coordinates.
    Nominal event totals do not describe concurrent capital or account returns.
    """
    _require(type(comparisons) is tuple and comparisons and all(type(item) is EntryComparison for item in comparisons)
             and type(expected_folds) is tuple and all(type(fold) is ChronologicalFold for fold in expected_folds)
             and tuple(item.prepared.fold for item in comparisons) == expected_folds,
             "EI_entry_report_expected_folds_differ")
    first = comparisons[0].prepared
    profile = (first.scenario, first.cost_bps, first.feature_sha256, first.execution_policy_sha256,
               first.original_union_coordinates)
    dates = tuple(day for fold in expected_folds for day in fold.test_sessions)
    _require(dates and tuple(sorted(set(dates))) == dates, "EI_entry_report_evaluation_dates_overlap")
    day_rows = {day: [] for day in dates}
    day_coordinates = Counter()
    coordinates, questions = Counter(), Counter()
    origin_rows = defaultdict(list)
    for result in comparisons:
        prepared = result.prepared
        _require((prepared.scenario, prepared.cost_bps, prepared.feature_sha256, prepared.execution_policy_sha256,
                  prepared.original_union_coordinates) == profile
                 and tuple(row.prepared for row in result.coordinates) == prepared.coordinates,
                 "EI_entry_report_profile_or_coordinate_roster_differs")
        keys = tuple((row.prepared.roster.session, row.prepared.roster.symbol) for row in result.coordinates)
        _require(tuple(sorted(set(keys))) == keys and {day for day, _ in keys} == set(prepared.fold.test_sessions),
                 "EI_entry_report_coordinate_dates_differ")
        for row in result.coordinates:
            roster = row.prepared.roster
            day_coordinates[roster.session] += 1
            coordinates[roster.coordinate_status] += 1
            _require(tuple(pair.decisions for pair in row.outcomes) == row.prepared.decisions,
                     "EI_entry_report_original_decisions_differ")
            for pair in row.outcomes:
                opportunity = pair.decisions.opportunity
                questions[opportunity.status] += 1
                if opportunity.status == "opportunity":
                    day_rows[roster.session].append(pair)
                    origin_rows[opportunity.feature.question.episode_origin_context].append(pair)
    weeks = {}
    day_pairs = []
    rows = []
    for day, daily in day_rows.items():
        rows.extend(daily)
        week = weeks.setdefault(_week(day), {"sessions": 0, "original_coordinates": 0, "original_opportunities": 0,
                                              "baseline_modeled_fills": 0, "selected_modeled_fills": 0})
        week["sessions"] += 1
        week["original_coordinates"] += day_coordinates[day]
        week["original_opportunities"] += len(daily)
        paired = [pair.net_delta for pair in daily if pair.net_delta is not None]
        day_pairs.append({"session": day, "opportunities": len(daily), "both_known_opportunities": len(paired),
                          "paired_net_delta": sum(paired, Fraction()) if paired else None})
        for mode in ("baseline", "selected"):
            fills = (getattr(pair, mode).fill for pair in daily)
            week[f"{mode}_modeled_fills"] += sum(fill is not None and fill.filled for fill in fills)
    supported_days = [row for row in day_pairs if row["both_known_opportunities"]]
    return {"scenario": first.scenario, "cost_bps": first.cost_bps, "original_evaluation_sessions": len(dates),
            "original_evaluation_coordinates": sum(day_coordinates.values()), "coordinate_statuses": dict(sorted(coordinates.items())),
            "question_statuses": dict(sorted(questions.items())), "overall": _comparison(rows, first.cost_bps),
            "by_episode_origin": {context: _comparison(origin_rows[context], first.cost_bps) for context in ("A", "B", "other")},
            "weekly": tuple({"week": key, **values} for key, values in weeks.items()),
            "date_contributions": tuple(day_pairs), "dates_with_paired_opportunities": len(supported_days),
            "mean_of_daily_paired_opportunity_net_deltas": (sum((row["paired_net_delta"] / row["both_known_opportunities"]
                                                                for row in supported_days), Fraction()) / len(supported_days)
                                                            if supported_days else None),
            "claim": "nominal_event_level_discovery"}


def _held_payoff(case, mode, verified_net):
    if verified_net is None:
        return None
    book = getattr(case, mode).episode.book
    original = case.initial.book
    exits = book.realized_exits[len(original.realized_exits):]
    _require(book.realized_exits[:len(original.realized_exits)] == original.realized_exits
             and exits and not book.lots and not book.indeterminate,
             "EI_held_report_closed_ledger_differs")
    gross = sum((leg.gross_pnl for leg in exits), Fraction())
    cost = sum((leg.allocated_entry_cost for leg in exits), Fraction())
    _require(gross - cost == verified_net, "EI_held_report_verified_net_differs")
    return gross, cost, verified_net


def summarize_matched_holding(results: tuple[EconomicFoldResult, ...], *,
                              expected_folds: tuple[ChronologicalFold, ...], include_reentry: bool) -> dict:
    """Report same-initial-fill differences with unknown outcomes retained."""
    _require(type(results) is tuple and results and all(type(result) is EconomicFoldResult for result in results)
             and type(include_reentry) is bool and tuple(result.entry.prepared.fold for result in results) == expected_folds,
             "EI_held_report_expected_folds_differ")
    entry = summarize_entry_comparisons(tuple(result.entry for result in results), expected_folds=expected_folds)
    modes = ("fixed", "chart", "chart_context") + (("reentry",) if include_reentry else ())
    contrasts = (("fixed", "chart"), ("fixed", "chart_context"), ("chart", "chart_context"))
    if include_reentry:
        contrasts += (("chart_context", "reentry"),)
    rows = []
    for result in results:
        for coordinate, cases, audits in zip(result.entry.coordinates, result.management,
                                              result.management_audits, strict=True):
            for pair, case, audit in zip(coordinate.outcomes, cases, audits, strict=True):
                selected = pair.selected
                filled = selected.fill is not None and selected.fill.filled
                _require((case is not None) is (audit is not None) is filled, "EI_held_report_selected_fill_missing")
                if not filled:
                    continue
                _require(case.initial == selected.initial and case.initial_fill == selected.fill
                         and case.fixed == selected.holding and audit["discretionary_values_recomputed"] is True
                         and audit["reentry_reset_verified"] is include_reentry and set(audit["net_by_arm"]) == set(modes),
                         "EI_held_report_same_entry_or_audit_differs")
                rows.append((coordinate.prepared.roster.session, {
                    mode: _held_payoff(case, mode, audit["net_by_arm"][mode]) for mode in modes},
                    bool(case.reentry and case.reentry.fill and case.reentry.fill.filled)))
    _require(len(rows) == entry["overall"]["selected"]["modeled_fills"], "EI_held_report_modeled_fill_count_differs")
    arms = {}
    for mode in modes:
        known = [values[mode] for _, values, _ in rows if values[mode] is not None]
        arms[mode] = {"known": len(known), "unknown": len(rows) - len(known),
                      **{key: sum((row[index] for row in known), Fraction()) if known else None
                         for index, key in enumerate(("gross", "assumed_cost", "net"))}}
    comparisons = []
    for first, second in contrasts:
        by_date = defaultdict(list)
        for day, values, _ in rows:
            left, right = values[first], values[second]
            if left is not None and right is not None:
                by_date[day].append(right[2] - left[2])
        paired = sum(map(len, by_date.values()))
        comparisons.append({"first": first, "second": second, "paired": paired, "unpaired": len(rows) - paired,
                            "paired_net_delta": sum((sum(values, Fraction()) for values in by_date.values()),
                                                    Fraction()) if paired else None,
                            "date_contributions": tuple({"session": day, "count": len(values),
                                                         "net_delta": sum(values, Fraction())}
                                                        for day, values in sorted(by_date.items()))})
    return {"entry": entry, "holding": {"same_modeled_initial_fills": len(rows), "arms": arms,
            "contrasts": tuple(comparisons), "extra_modeled_reentry_fills": sum(reentered for _, _, reentered in rows),
            "claim": "nominal_event_level_discovery"}}
