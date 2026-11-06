"""Independently recheck EG decisions, cash arithmetic and reported comparison totals."""

from collections import Counter, defaultdict
from fractions import Fraction
from math import fsum, isclose, sqrt

from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_cash_book import INITIAL_CAPITAL, ORDER_FRACTION
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_readiness import _week
from research.intraday_chart_evaluation import MIN_INTERVAL_SESSIONS, NORMAL_95, NUMERICAL_ZERO
from research.intraday_ed_inputs import _require
from research.intraday_highvol_snapshot import HOLD_STEPS, PRIMARY_HORIZON, _fraction


TIMINGS = ("S1", "S2")
BRANCHES = ("A", "B")
ARMS = ("LGB_CHART", "TCN_CHART", "TCN_CHART_PEER")
COSTS = (33, 43)


def _known(value):
    return None if value is None else _fraction(value)


def _close_number(left, right):
    if left is None or right is None:
        return left is right
    return type(left) in (int, float) and isclose(left, right, abs_tol=2e-12, rel_tol=2e-12)


def _statistics(saved, pairs, sessions):
    totals, counts = defaultdict(list), Counter()
    for day, value in pairs:
        counts[day] += 1
        totals[day].append(float(value))
    n, days = len(pairs), len(sessions)
    center = fsum(float(value) for _, value in pairs) / n if n else None
    cluster = None
    if n and days > 1:
        deviations = [fsum(value - center for value in totals[day]) for day in sessions]
        cluster = sqrt(fsum(value ** 2 for value in deviations) * days / (days - 1)) / n
    interval = None if cluster is None or cluster <= NUMERICAL_ZERO or len(counts) < MIN_INTERVAL_SESSIONS else (
        center - NORMAL_95 * cluster, center + NORMAL_95 * cluster)
    _require(saved["observations"] == n and saved["reference_sessions"] == days
             and saved["contributing_sessions"] == len(counts) and _close_number(saved["mean"], center)
             and _close_number(saved["session_cluster_se"], cluster)
             and _close_number(saved["largest_session_observation_share"], max(counts.values()) / n if n else None)
             and (saved["normal95_exploratory"] is None if interval is None else
                  len(saved["normal95_exploratory"] or ()) == 2 and
                  all(_close_number(a, b) for a, b in zip(saved["normal95_exploratory"], interval, strict=True))),
             "eg_independent_evaluation_statistic_differs")


def _select(forecasts, branch, timing, arm, key, cost):
    if arm == "rule":
        return "selected"
    result = forecasts.get((branch, timing, arm, (*key, 0)))
    if result is None or result.gross30_pct is None:
        return "prediction_unavailable"
    if cost in result.replay_ambiguous_costs:
        return "replay_ambiguous"
    return "selected" if result.gross30_pct > cost / 100 else "declined"


def _supported(frame, timing):
    entry = frame["entry"]
    if entry is None or not entry["primary_clock_supported"] or entry["chart"]["status"] != "available":
        return None
    return "common" if entry["peers"][timing]["status"] == "available" else "chart_only"


def _return(frame, *, immediate):
    start = _known(frame["entry_open"])
    finish = _known(frame["exit_open" if immediate else "fixed_exit_open"])
    status = frame["status"]
    if status in ("outside_clock", "entry_refused", "entry_unavailable"):
        return status, None
    if status == "unavailable" and start is None:
        return "entry_unavailable", None
    if finish is None:
        return "fixed_exit_unavailable", None
    gross = finish / start - 1
    _require(_known(frame["gross" if immediate else "fixed_gross"]) == gross,
             "eg_independent_evaluation_quote_return_differs")
    return "available", gross


def _policy_line(saved, rows, cost, sessions, weeks):
    decisions, statuses = Counter(), Counter()
    by_week, known = defaultdict(Counter), []
    for key, decision, result, gross in rows:
        decisions[decision] += 1
        status = result if decision == "selected" else decision
        statuses[status] += 1
        week = by_week[_week(key[0])]
        week["opportunities"] += 1
        week["selected"] += decision == "selected"
        week["modeled_entries"] += status in ("available", "fixed_exit_unavailable")
        week["unknown"] += status in ("prediction_unavailable", "replay_ambiguous", "entry_unavailable",
                                      "fixed_exit_unavailable")
        if status == "available":
            known.append((key[0], gross))
            week["known_returns"] += 1
    charge = Fraction(cost, 10_000)
    _require(saved["decision_counts"] == dict(decisions) and saved["outcome_counts"] == dict(statuses)
             and saved["known_cost_exceedance_count"] == sum(gross > charge for _, gross in known)
             and set(saved["weeks"]) == set(weeks), "eg_independent_evaluation_policy_counts_differ")
    for week, sessions_count in weeks.items():
        row = saved["weeks"][week]
        _require(row["reference_sessions"] == sessions_count and all(row[name] == by_week[week][name] for name in (
            "opportunities", "selected", "modeled_entries", "known_returns", "unknown")),
            "eg_independent_evaluation_policy_week_differs")
    _statistics(saved["gross_pp"], [(day, 100 * gross) for day, gross in known], sessions)
    _statistics(saved["net_pp"], [(day, 100 * (gross - charge)) for day, gross in known], sessions)
    return len(known)


def _policy_contrasts(saved, by_arm, cost, sessions):
    def contribution(row, charge):
        _, decision, status, gross = row
        if decision == "declined" or decision == "selected" and status == "entry_refused":
            return Fraction()
        if decision == "selected" and gross is not None:
            return 100 * (gross - charge)
        return None

    expected = {f"{arm}_minus_rule" for arm in by_arm if arm != "rule"}
    expected.add("TCN_CHART_minus_LGB_CHART")
    if "TCN_CHART_PEER" in by_arm:
        expected.add("TCN_CHART_PEER_minus_TCN_CHART")
    _require(set(saved) == expected, "eg_independent_evaluation_contrast_roster_differs")
    for label, report in saved.items():
        left, right = label.split("_minus_")
        for name, charge in (("gross_pp", Fraction()), ("net_pp", Fraction(cost, 10_000))):
            known = []
            for lhs, rhs in zip(by_arm[left], by_arm[right], strict=True):
                _require(lhs[0] == rhs[0], "eg_independent_evaluation_contrast_keys_differ")
                a, b = contribution(lhs, charge), contribution(rhs, charge)
                if a is not None and b is not None:
                    known.append((lhs[0][0], a, b))
            _require(report["common_opportunities"] == len(by_arm[left])
                     and report["known_matched_opportunities"] == len(known)
                     and report["unknown_matched_opportunities"] == len(by_arm[left]) - len(known),
                     "eg_independent_evaluation_contrast_counts_differ")
            _statistics(report[name]["left"], [(day, a) for day, a, _ in known], sessions)
            _statistics(report[name]["right"], [(day, b) for day, _, b in known], sessions)
            _statistics(report[name]["left_minus_right"], [(day, a - b) for day, a, b in known], sessions)


def _policy(inputs, report, frames, calendar, checkpoint):
    sessions, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    forecasts = inputs.entry_forecasts()
    groups, controls = defaultdict(list), defaultdict(list)
    for key, frame in frames.items():
        branch = frame["attempt"]["scenario"]
        for timing in frame["timings"]:
            control = "selected" if frame["immediate"]["status"] == "supported" else "outside_clock"
            controls[timing].append((key, control))
            support = _supported(frame, timing)
            if support is not None:
                groups[branch, timing, support].append((key, {cost: {arm: _select(forecasts, branch, timing,
                    arm, key, cost) for arm in (*ARMS, "rule")} for cost in COSTS}))
    with inputs.open_labels(tuple(frames)) as label_at:
        labels = {key: label_at(key) for key in frames}
    known_count = 0
    for timing in TIMINGS:
        control = report["immediate"][timing]
        _require(control["observed_clusters"] == len(controls[timing]), "eg_independent_evaluation_control_differs")
        rows = [(key, decision, *_return(labels[key]["immediate"], immediate=True))
                for key, decision in controls[timing]]
        for cost in COSTS:
            known_count += _policy_line(control["costs"][str(cost)], rows, cost, sessions, weeks)
        for branch in BRANCHES:
            common = groups[branch, timing, "common"]
            saved = report["groups"][branch][timing]
            for population, name, arms in ((common, "common", (*ARMS, "rule")),
                    (common + groups[branch, timing, "chart_only"], "wider_chart_diagnostic", (*ARMS[:2], "rule"))):
                view = saved[name]
                _require(view["opportunities"] == len(population), "eg_independent_evaluation_opportunities_differ")
                _require(view["fixed_selection_stress"]["selection_bps"] == 33
                         and view["fixed_selection_stress"]["outcome_bps"] == 43,
                         "eg_independent_evaluation_cost_stress_differs")
                for selection, cost, cell in ((33, 33, view["costs"]["33"]), (43, 43, view["costs"]["43"]),
                                              (33, 43, view["fixed_selection_stress"])):
                    checkpoint("eg_reference_policy_cell")
                    _require(set(cell["arms"]) == set(arms), "eg_independent_evaluation_arm_roster_differs")
                    base = [(key, decisions, _return(labels[key]["primary"], immediate=False))
                            for key, decisions in population]
                    by_arm = {arm: [(key, decisions[selection][arm], *outcome) for key, decisions, outcome in base]
                              for arm in arms}
                    for arm, rows in by_arm.items():
                        known_count += _policy_line(cell["arms"][arm], rows, cost, sessions, weeks)
                    _policy_contrasts(cell["contrasts"], by_arm, cost, sessions)
    checked_candidates = set()
    for row in report["candidate_screen"]:
        branch, arm = row["branch"], row["arm"]
        _require((branch, arm) not in checked_candidates and set(row["timings"]) == set(TIMINGS),
                 "eg_independent_evaluation_candidate_roster_differs")
        checked_candidates.add((branch, arm))
        flags = []
        for timing in TIMINGS:
            cell = report["groups"][branch][timing]["common"]["costs"]["43"]["arms"][arm]
            stats = cell["net_pp"]
            lower = stats["normal95_exploratory"][0] if stats["normal95_exploratory"] is not None else None
            flag = (lower is not None and lower > 0 and stats["mean"] > 0 and stats["contributing_sessions"] >= 10)
            flags.append(flag)
            _require(row["timings"][timing] == {"passes": flag, "mean_net43_pp": stats["mean"],
                "interval_lower_pp": lower, "contributing_sessions": stats["contributing_sessions"],
                "nonoverlapping_15_selection_weeks": sum(week["at_least_15_nonoverlapping_selected"]
                    for week in cell["weeks"].values())}, "eg_independent_evaluation_candidate_cells_differ")
        _require(row["screen_passes"] == all(flags), "eg_independent_evaluation_candidate_decision_differs")
    _require(checked_candidates == {(branch, arm) for branch in BRANCHES for arm in ("rule", *ARMS)},
             "eg_independent_evaluation_candidate_roster_differs")
    return {"events": len(frames), "checked_arm_outcomes": known_count,
            "prospective_outcomes": _key_evidence(tuple(frames))}


def _lot_path(frame, label, timing, arm, key, holding, cost):
    primary = label["primary"]
    if primary["status"] != "admitted":
        return primary["status"], None, None
    price = _known(primary["entry_open"])
    charge = Fraction(cost, 10_000)
    quantity = min(INITIAL_CAPITAL * ORDER_FRACTION, INITIAL_CAPITAL / (1 + charge)) // price
    if not quantity:
        return "nominal_entry_declined", None, None
    cap = _known(primary["fixed_exit_open"])
    fixed = None if cap is None else (cap / price - 1, Fraction(PRIMARY_HORIZON))
    remaining, paid, time = quantity, Fraction(), 0
    reduced, previous_nonpositive = False, False
    branch, stop = frame["attempt"]["scenario"], _known(frame["attempt"]["confirmation"]["stop"])
    for step, state, quote in zip(HOLD_STEPS, frame["holding"], label["holding"], strict=True):
        close = _known(state["last_close"])
        score = holding.get((branch, timing, arm, (*key, step)))
        support = (state["chart"]["status"] == "available" and
                   (arm in ARMS[:2] or state["peers"][timing]["status"] == "available"))
        nonpositive = (close is not None and close > stop and support and score is not None and
                       not score.replay_ambiguous and score.hold_advantage_pct is not None and
                       score.hold_advantage_pct <= 0)
        units = 0
        if close is not None and close <= stop or nonpositive and previous_nonpositive:
            units = remaining
        elif nonpositive and not reduced:
            units, reduced = remaining // 2, True
        previous_nonpositive = nonpositive
        if units:
            next_open = _known(quote["next_open"])
            if next_open is None:
                return "modeled_entry", fixed, None
            paid += units * next_open
            time += units * step
            remaining -= units
            if not remaining:
                break
    if remaining and cap is None:
        return "modeled_entry", fixed, None
    if remaining:
        paid += remaining * cap
        time += remaining * PRIMARY_HORIZON
    return "modeled_entry", fixed, (paid / (quantity * price) - 1, Fraction(time, quantity))


def _management(inputs, report, frames, calendar, checkpoint):
    sessions, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    entry, holding = inputs.entry_forecasts(), inputs.holding_forecasts()
    cases, selected = defaultdict(list), set()
    for key, frame in frames.items():
        branch = frame["attempt"]["scenario"]
        for timing in frame["timings"]:
            if _supported(frame, timing) != "common":
                continue
            for cost in COSTS:
                for arm in ARMS:
                    decision = _select(entry, branch, timing, arm, key, cost)
                    cases[branch, timing, cost, arm].append((key, decision))
                    if decision == "selected":
                        selected.add(key)
    selected_keys = tuple(key for key in frames if key in selected)
    with inputs.open_labels(selected_keys) as label_at:
        labels = {key: label_at(key) for key in selected_keys}
    known_count = 0
    for branch in BRANCHES:
        for timing in TIMINGS:
            for cost in COSTS:
                for arm in ARMS:
                    checkpoint("eg_reference_management_cell")
                    saved = report["groups"][branch][timing][str(cost)][arm]
                    statuses, entered, matched = Counter(), [], []
                    for key, decision in cases[branch, timing, cost, arm]:
                        if decision != "selected":
                            statuses[decision] += 1
                            continue
                        status, fixed, managed = _lot_path(frames[key], labels[key], timing, arm, key, holding, cost)
                        statuses[status] += 1
                        if status == "modeled_entry":
                            entered.append((key[0], fixed, managed))
                            if fixed is not None and managed is not None:
                                matched.append((key[0], 100 * (managed[0] - fixed[0])))
                    _require(saved["entry_status_counts"] == dict(statuses) and saved["known_matched"] == len(matched)
                             and saved["unresolved_matched"] == len(entered) - len(matched)
                             and set(saved["weeks"]) == set(weeks)
                             and saved["known_matched_improvements"] == sum(delta > 0 for _, delta in matched)
                             and saved["known_matched_worsenings"] == sum(delta < 0 for _, delta in matched),
                             "eg_independent_evaluation_management_counts_differ")
                    _statistics(saved["paired_managed_minus_fixed_net_pp"], matched, sessions)
                    for slot, name in ((1, "fixed"), (2, "managed")):
                        known = [(row[0], row[slot]) for row in entered if row[slot] is not None]
                        _statistics(saved[name]["gross_pp"], [(day, 100 * path[0]) for day, path in known], sessions)
                        _statistics(saved[name]["net_pp"], [(day, 100 * (path[0] - Fraction(cost, 10_000)))
                                                            for day, path in known], sessions)
                        _statistics(saved[name]["average_hold_minutes"], [(day, path[1]) for day, path in known], sessions)
                    known_count += len(matched)
    return {"events": len(frames), "outcome_events": len(selected_keys), "checked_matched_paths": known_count,
            "prospective_outcomes": _key_evidence(selected_keys)}


def _key_evidence(keys):
    return {"keys": len(keys), "sha256": wire.digest(wire.encode(keys))}


def verify_evaluation(unit_id, inputs, report, checkpoint):
    """Use authenticated features/forecasts and independent scalar outcome arithmetic."""
    calendar = fixed_calendar(inputs.source_dates)
    sessions = set(calendar.evaluation_reference_sessions)
    frames = {tuple(frame["key"]): frame for frame in inputs.features() if frame["key"][0] in sessions}
    _require(tuple(frames) == inputs.evaluation_keys and report["reference_sessions"] ==
             list(calendar.evaluation_reference_sessions), "eg_independent_evaluation_calendar_differs")
    if unit_id == "EG-POLICY-COMPARISON-1":
        _require(report["schema"] == "EG-entry-policy-comparison-v1", "eg_independent_evaluation_policy_schema_differs")
        result = _policy(inputs, report, frames, calendar, checkpoint)
    else:
        _require(unit_id == "EG-MANAGEMENT-1" and report["schema"] == "EG-saved-management-v1",
                 "eg_independent_evaluation_unit_differs")
        result = _management(inputs, report, frames, calendar, checkpoint)
    return {"schema": "EG-evaluation-reference-v1", "complete": True, **result}
