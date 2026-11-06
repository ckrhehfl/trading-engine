"""Independently recheck EG decisions, cash arithmetic and reported comparison totals."""

from collections import Counter, defaultdict
from fractions import Fraction
from math import fsum, isclose, sqrt
from statistics import stdev

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
    naive = stdev(float(value) for _, value in pairs) / sqrt(n) if n > 1 else None
    _require(saved["observations"] == n and saved["reference_sessions"] == days
             and saved["contributing_sessions"] == len(counts) and _close_number(saved["mean"], center)
             and _close_number(saved["session_cluster_se"], cluster)
             and _close_number(saved["naive_se_diagnostic_only"], naive)
             and _close_number(saved["cluster_to_naive_se_ratio"], cluster / naive if cluster is not None and
                               naive is not None and naive > NUMERICAL_ZERO else None)
             and saved["precision"] == ("estimated" if interval is not None else
                                       "insufficient_precision" if n else "no_observations")
             and _close_number(saved["largest_session_observation_share"], max(counts.values()) / n if n else None)
             and (saved["normal95_exploratory"] is None if interval is None else
                  len(saved["normal95_exploratory"] or ()) == 2 and
                  all(_close_number(a, b) for a, b in zip(saved["normal95_exploratory"], interval, strict=True))),
             "eg_independent_evaluation_statistic_differs")
    return interval


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


def _policy_line(saved, rows, cost, sessions, weeks, clocks):
    decisions, statuses = Counter(), Counter()
    by_week, known, selected = defaultdict(Counter), [], []
    for key, decision, result, gross in rows:
        decisions[decision] += 1
        status = result if decision == "selected" else decision
        statuses[status] += 1
        week = by_week[_week(key[0])]
        week["opportunities"] += 1
        week["selected"] += decision == "selected"
        if decision == "selected":
            selected.append(key)
        week["modeled_entries"] += status in ("available", "fixed_exit_unavailable")
        week["unknown"] += status in ("prediction_unavailable", "replay_ambiguous", "entry_unavailable",
                                      "fixed_exit_unavailable")
        if status == "available":
            known.append((key, gross))
            week["known_returns"] += 1
    nonoverlap, exits = Counter(), {}
    for key in sorted(selected, key=lambda item: (clocks[item][0], item[1], item[2])):
        start, end = clocks[key]
        identity = key[:2]
        if identity not in exits or start >= exits[identity]:
            nonoverlap[_week(key[0])] += 1
            exits[identity] = end
    charge = Fraction(cost, 10_000)
    _require(saved["decision_counts"] == dict(decisions) and saved["outcome_counts"] == dict(statuses)
             and saved["known_cost_exceedance_count"] == sum(gross > charge for _, gross in known)
             and set(saved["weeks"]) == set(weeks), "eg_independent_evaluation_policy_counts_differ")
    for week, sessions_count in weeks.items():
        row = saved["weeks"][week]
        _require(row["reference_sessions"] == sessions_count and all(row[name] == by_week[week][name] for name in (
            "opportunities", "selected", "modeled_entries", "known_returns", "unknown"))
            and row["same_symbol_nonoverlapping_selected"] == nonoverlap[week]
            and row["at_least_15_opportunities"] is (by_week[week]["opportunities"] >= 15)
            and row["at_least_15_selected"] is (by_week[week]["selected"] >= 15)
            and row["at_least_15_nonoverlapping_selected"] is (nonoverlap[week] >= 15),
            "eg_independent_evaluation_policy_week_differs")
    _statistics(saved["gross_pp"], [(key[0], 100 * gross) for key, gross in known], sessions)
    _statistics(saved["net_pp"], [(key[0], 100 * (gross - charge)) for key, gross in known], sessions)
    for field, index in (("session", 0), ("symbol", 1)):
        population = Counter(row[0][index] for row in rows)
        distribution = Counter(key[index] for key, _ in known)
        loss = defaultdict(Fraction)
        for key, gross in known:
            loss[key[index]] += max(Fraction(), charge - gross)
        total_loss = sum(loss.values())
        concentration = saved["concentration"][field]
        _require(set(concentration["counts"]) == set(population)
                 and _close_number(concentration["largest_known_return_share"],
                                   max(distribution.values()) / len(known) if known else None)
                 and _close_number(concentration["largest_net_loss_mass_share"],
                                   float(max(loss.values()) / total_loss) if total_loss else None),
                 "eg_independent_evaluation_concentration_differs")
        for value, count in population.items():
            line = concentration["counts"][value]
            _require(line["opportunities"] == count and line["known_returns"] == distribution[value]
                     and _close_number(line["net_loss_mass_pp"], float(100 * loss[value])),
                     "eg_independent_evaluation_concentration_counts_differ")
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


def _reliability(saved, population, labels, forecasts, branch, timing, arm, sessions):
    statuses, bins, brier, model, baseline, paired = Counter(), defaultdict(list), [], [], [], []
    for key, _ in population:
        forecast = forecasts.get((branch, timing, arm, (*key, 0)))
        probability = None if forecast is None else forecast.target_first_probability
        if probability is None:
            statuses["probability_unavailable"] += 1
            continue
        label = labels[key]["primary"]
        touch = label["first_touch"]
        state = label["status"] if touch is None else touch["status"]
        if state not in ("target_first", "stop_first", "time"):
            statuses["target_unavailable"] += 1
            statuses[f"target_{state}"] += 1
            continue
        observed = int(state == "target_first")
        index = min(4, int(probability * 5))
        bins[index].append((probability, observed))
        error = (probability - observed) ** 2
        brier.append((key[0], error))
        if forecast.training_target_rate is None:
            statuses["training_prevalence_unavailable"] += 1
        else:
            base = (float(forecast.training_target_rate) - observed) ** 2
            model.append((key[0], error))
            baseline.append((key[0], base))
            paired.append((key[0], error - base))
    _require(saved["statuses"] == dict(statuses) and len(saved["bins"]) == 5,
             "eg_independent_evaluation_calibration_counts_differ")
    for index, row in enumerate(saved["bins"]):
        points = bins[index]
        n = len(points)
        _require(row["lower"] == index / 5 and row["upper"] == (index + 1) / 5
                 and row["upper_inclusive"] is (index == 4) and row["rows"] == n
                 and _close_number(row["mean_prediction"], fsum(p for p, _ in points) / n if n else None)
                 and _close_number(row["observed_frequency"], sum(y for _, y in points) / n if n else None),
                 "eg_independent_evaluation_calibration_bin_differs")
    for name, points in (("brier", brier), ("model_brier_same_pairs", model),
                         ("training_prevalence_brier_same_pairs", baseline),
                         ("model_minus_training_prevalence_brier_same_pairs", paired)):
        _statistics(saved[name], points, sessions)


def _opening(saved, keys, frames):
    first = [key for key in keys if key[2] <= 60]
    left = sum(frames[key]["first_high"]["left_censored"] for key in first)
    _require(saved["first_hour_observed_clusters"] == len(first)
             and saved["first_hour_confirmed_onsets"] == len(first) - left
             and saved["first_hour_left_censored_clusters"] == left,
             "eg_independent_evaluation_opening_counts_differ")


def _policy(inputs, report, frames, calendar, checkpoint):
    sessions, weeks = calendar.evaluation_reference_sessions, calendar.evaluation_week_session_counts
    forecasts = inputs.entry_forecasts()
    groups, controls = defaultdict(list), defaultdict(list)
    attempts, support_states, control_states = defaultdict(Counter), defaultdict(Counter), defaultdict(Counter)
    for key, frame in frames.items():
        branch, state = frame["attempt"]["scenario"], frame["attempt"]["state"]
        for timing in frame["timings"]:
            control = "selected" if frame["immediate"]["status"] == "supported" else "outside_clock"
            controls[timing].append((key, control))
            control_states[timing][state] += 1
            support = _supported(frame, timing)
            if branch in BRANCHES:
                attempts[branch, timing][state] += 1
                if state != "confirmed":
                    support_states[branch, timing][state] += 1
                elif not frame["entry"]["primary_clock_supported"]:
                    support_states[branch, timing]["outside_clock"] += 1
                else:
                    support_states[branch, timing][support or "chart_unavailable"] += 1
            if support is not None:
                groups[branch, timing, support].append((key, {cost: {arm: _select(forecasts, branch, timing,
                    arm, key, cost) for arm in (*ARMS, "rule")} for cost in COSTS}))
    with inputs.open_labels(tuple(frames)) as label_at:
        labels = {key: label_at(key) for key in frames}
    clocks = {kind: {key: (label[kind]["entry_at"], label[kind]["exit_at"]) for key, label in labels.items()
                     if label[kind] is not None} for kind in ("immediate", "primary")}
    known_count = 0
    for timing in TIMINGS:
        control = report["immediate"][timing]
        _require(control["observed_clusters"] == len(controls[timing])
                 and control["attempt_states"] == dict(control_states[timing]), "eg_independent_evaluation_control_differs")
        _opening(control, [key for key, _ in controls[timing]], frames)
        rows = [(key, decision, *_return(labels[key]["immediate"], immediate=True))
                for key, decision in controls[timing]]
        for cost in COSTS:
            known_count += _policy_line(control["costs"][str(cost)], rows, cost, sessions, weeks, clocks["immediate"])
        for branch in BRANCHES:
            common = groups[branch, timing, "common"]
            saved = report["groups"][branch][timing]
            _require(saved["attempt_states"] == dict(attempts[branch, timing])
                     and saved["support_states"] == dict(support_states[branch, timing]),
                     "eg_independent_evaluation_attempt_support_differs")
            for population, name, arms in ((common, "common", (*ARMS, "rule")),
                    (common + groups[branch, timing, "chart_only"], "wider_chart_diagnostic", (*ARMS[:2], "rule"))):
                view = saved[name]
                _require(view["opportunities"] == len(population), "eg_independent_evaluation_opportunities_differ")
                _opening(view, [key for key, _ in population], frames)
                _require(view["fixed_selection_stress"]["selection_bps"] == 33
                         and view["fixed_selection_stress"]["outcome_bps"] == 43,
                         "eg_independent_evaluation_cost_stress_differs")
                for arm in arms:
                    if arm != "rule":
                        statuses = Counter("absent_forecast" if score is None else score.unsupported_reason or "scored"
                                           for key, _ in population
                                           for score in (forecasts.get((branch, timing, arm, (*key, 0))),))
                        _require(view["forecast_statuses"][arm] == dict(statuses),
                                 "eg_independent_evaluation_forecast_states_differ")
                        _reliability(view["target_first_reliability"][arm], population, labels, forecasts,
                                     branch, timing, arm, sessions)
                for selection, cost, cell in ((33, 33, view["costs"]["33"]), (43, 43, view["costs"]["43"]),
                                              (33, 43, view["fixed_selection_stress"])):
                    checkpoint("eg_reference_policy_cell")
                    _require(set(cell["arms"]) == set(arms), "eg_independent_evaluation_arm_roster_differs")
                    base = [(key, decisions, _return(labels[key]["primary"], immediate=False))
                            for key, decisions in population]
                    by_arm = {arm: [(key, decisions[selection][arm], *outcome) for key, decisions, outcome in base]
                              for arm in arms}
                    for arm, rows in by_arm.items():
                        known_count += _policy_line(cell["arms"][arm], rows, cost, sessions, weeks, clocks["primary"])
                    _policy_contrasts(cell["contrasts"], by_arm, cost, sessions)
            timing_pairs = []
            for key, _ in common:
                immediate = _return(labels[key]["immediate"], immediate=True)[1]
                scenario = _return(labels[key]["primary"], immediate=False)[1]
                if immediate is not None and scenario is not None:
                    timing_pairs.append((key[0], 100 * (scenario - immediate)))
            attribution = saved["matched_timing_attribution"]
            _require(attribution["prospective_common_scenario_opportunities"] == len(common)
                     and attribution["known_matched_outcomes"] == len(timing_pairs)
                     and attribution["unknown_matched_outcomes"] == len(common) - len(timing_pairs),
                     "eg_independent_evaluation_timing_attribution_differs")
            _statistics(attribution["scenario_minus_immediate_pp"], timing_pairs, sessions)
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
        return primary["status"], None, None, Counter(), Counter()
    price = _known(primary["entry_open"])
    charge = Fraction(cost, 10_000)
    quantity = min(INITIAL_CAPITAL * ORDER_FRACTION, INITIAL_CAPITAL / (1 + charge)) // price
    if not quantity:
        return "nominal_entry_declined", None, None, Counter(), Counter()
    cap = _known(primary["fixed_exit_open"])
    fixed = None if cap is None else (cap / price - 1, Fraction(PRIMARY_HORIZON))
    remaining, paid, time = quantity, Fraction(), 0
    reduced, previous_nonpositive = False, False
    updates, legs = Counter(), Counter()
    branch, stop = frame["attempt"]["scenario"], _known(frame["attempt"]["confirmation"]["stop"])
    for step, state, quote in zip(HOLD_STEPS, frame["holding"], label["holding"], strict=True):
        close = _known(state["last_close"])
        score = holding.get((branch, timing, arm, (*key, step)))
        chart = state["chart"]["status"] == "available"
        peer = arm in ARMS[:2] or state["peers"][timing]["status"] == "available"
        support = chart and peer
        nonpositive = (close is not None and close > stop and support and score is not None and
                       not score.replay_ambiguous and score.hold_advantage_pct is not None and
                       score.hold_advantage_pct <= 0)
        units = 0
        if close is not None and close <= stop:
            units, reason = remaining, "structural_close_failure"
        elif close is None:
            reason = "structural_close_unavailable"
        elif not support:
            reason = "holding_chart_unavailable" if not chart else "holding_peer_unavailable"
        elif score is None:
            reason = "holding_forecast_absent"
        elif score.replay_ambiguous:
            reason = "holding_replay_ambiguous"
        elif score.hold_advantage_pct is None:
            reason = score.unsupported_reason
        elif nonpositive and previous_nonpositive:
            units, reason = remaining, "consecutive_nonpositive_hold_gain"
        elif nonpositive and not reduced:
            units, reduced = remaining // 2, True
            reason = "first_nonpositive_hold_gain"
            if not units:
                legs["declined"] += 1
        else:
            reason = "no_exit_signal"
        updates[reason] += 1
        previous_nonpositive = nonpositive
        if units:
            next_open = _known(quote["next_open"])
            if next_open is None:
                legs["indeterminate"] += 1
                return "modeled_entry", fixed, None, updates, legs
            legs["modeled"] += 1
            paid += units * next_open
            time += units * step
            remaining -= units
            if not remaining:
                break
    if remaining and cap is None:
        legs["indeterminate"] += 1
        return "modeled_entry", fixed, None, updates, legs
    if remaining:
        legs["modeled"] += 1
        paid += remaining * cap
        time += remaining * PRIMARY_HORIZON
    return "modeled_entry", fixed, (paid / (quantity * price) - 1, Fraction(time, quantity)), updates, legs


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
                    updates, legs, by_symbol, by_week = Counter(), Counter(), Counter(), defaultdict(Counter)
                    for key, decision in cases[branch, timing, cost, arm]:
                        weekly = by_week[_week(key[0])]
                        weekly["common_opportunities"] += 1
                        if decision != "selected":
                            statuses[decision] += 1
                            continue
                        weekly["selected"] += 1
                        status, fixed, managed, reasons, leg_states = _lot_path(
                            frames[key], labels[key], timing, arm, key, holding, cost)
                        statuses[status] += 1
                        updates.update(reasons)
                        legs.update(leg_states)
                        if status == "modeled_entry":
                            weekly["modeled_entries"] += 1
                            entered.append((key[0], fixed, managed))
                            if fixed is not None and managed is not None:
                                weekly["known_matched"] += 1
                                by_symbol[key[1]] += 1
                                matched.append((key[0], 100 * (managed[0] - fixed[0])))
                    _require(saved["entry_status_counts"] == dict(statuses) and saved["known_matched"] == len(matched)
                             and saved["unresolved_matched"] == len(entered) - len(matched)
                             and set(saved["weeks"]) == set(weeks)
                             and saved["update_reasons"] == dict(updates) and saved["managed_leg_statuses"] == dict(legs)
                             and saved["known_matched_by_symbol"] == dict(by_symbol)
                             and _close_number(saved["largest_symbol_known_matched_share"],
                                               max(by_symbol.values()) / len(matched) if matched else None)
                             and saved["known_matched_improvements"] == sum(delta > 0 for _, delta in matched)
                             and saved["known_matched_worsenings"] == sum(delta < 0 for _, delta in matched),
                             "eg_independent_evaluation_management_counts_differ")
                    for week, days in weeks.items():
                        _require(saved["weeks"][week] == {"reference_sessions": days, **{
                            field: by_week[week][field] for field in ("common_opportunities", "selected", "modeled_entries",
                                                                      "known_matched")}},
                            "eg_independent_evaluation_management_week_differs")
                    _statistics(saved["paired_managed_minus_fixed_net_pp"], matched, sessions)
                    for slot, name in ((1, "fixed"), (2, "managed")):
                        known = [(row[0], row[slot]) for row in entered if row[slot] is not None]
                        states = Counter("unavailable" if row[slot] is None else "closed" for row in entered)
                        _require(saved[name]["status_counts"] == dict(states), "eg_independent_evaluation_path_status_differs")
                        _statistics(saved[name]["gross_pp"], [(day, 100 * path[0]) for day, path in known], sessions)
                        _statistics(saved[name]["net_pp"], [(day, 100 * (path[0] - Fraction(cost, 10_000)))
                                                            for day, path in known], sessions)
                        _statistics(saved[name]["average_hold_minutes"], [(day, path[1]) for day, path in known], sessions)
                    known_count += len(matched)
    return {"events": len(frames), "outcome_events": len(selected_keys), "checked_matched_paths": known_count,
            "prospective_outcomes": _key_evidence(selected_keys)}


def _key_evidence(keys):
    return {"keys": len(keys), "sha256": wire.digest(wire.encode(keys))}


def _scalar_frame(frame):
    """The original feature rows can contain large minute tensors; keep decision facts only."""
    def support(block):
        return {"chart": {"status": block["chart"]["status"]},
                "peers": {clock: {"status": values["status"]} for clock, values in block["peers"].items()}}

    entry = frame["entry"]
    return {"key": frame["key"], "timings": frame["timings"], "first_high": frame["first_high"],
            "attempt": frame["attempt"], "immediate": frame["immediate"],
            "entry": None if entry is None else {**support(entry), "primary_clock_supported": entry["primary_clock_supported"]},
            "holding": [{**support(row), "last_close": row["last_close"]} for row in frame.get("holding", ())]}


def verify_evaluation(unit_id, inputs, report, checkpoint):
    """Use authenticated features/forecasts and independent scalar outcome arithmetic."""
    calendar = fixed_calendar(inputs.source_dates)
    sessions = set(calendar.evaluation_reference_sessions)
    frames = {tuple(frame["key"]): _scalar_frame(frame) for frame in inputs.features() if frame["key"][0] in sessions}
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
