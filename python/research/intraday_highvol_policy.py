"""Compare fixed 30-minute EG entry policies over sealed chronological inputs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import isfinite
import re

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_campaign_readiness import _week
from research.intraday_chart_evaluation import COST_BPS, _estimate
from research.intraday_chart_folds import Key
from research.intraday_ed_inputs import FIVE, MINUTE, _require
from research.intraday_highvol_events import OFFSETS
from research.intraday_highvol_models import ARMS
from research.intraday_highvol_scenarios import CONFIRMATION_WINDOW
from research.intraday_highvol_snapshot import PRIMARY_HORIZON, TIMINGS, _fraction
from research.intraday_pattern_calibration import session_close, session_open


RULE = "rule"
WEEKLY_TARGET = 15
BRANCHES = ("A", "B")
CHART_ARMS = ("LGB_CHART", "TCN_CHART")
CONTRASTS = (*((arm, RULE) for arm in ARMS), ("TCN_CHART_PEER", "TCN_CHART"),
             ("TCN_CHART", "LGB_CHART"))
DecisionKey = tuple[str, str, int, int]
ForecastKey = tuple[str, str, str, DecisionKey]


@dataclass(frozen=True)
class EntryForecast:
    gross30_pct: float | None = None
    target_first_probability: float | None = None
    training_target_rate: Fraction | float | None = None
    replay_ambiguous_costs: frozenset[int] = frozenset()
    unsupported_reason: str | None = None


@dataclass(frozen=True)
class _Clock:
    entry: datetime
    exit: datetime
    supported: bool


@dataclass(frozen=True)
class _Event:
    key: Key
    timing: str
    branch: str | None
    state: str
    support: str
    immediate: _Clock
    scenario: _Clock | None
    forecasts: tuple[EntryForecast | None, ...]
    decisions: tuple[tuple[str, ...], ...]


@dataclass(frozen=True)
class _Outcome:
    status: str
    gross: Fraction | None
    first_touch: str | None = None


@dataclass(frozen=True)
class _Row:
    event: _Event
    immediate: _Outcome
    scenario: _Outcome | None


def _forecast(value: EntryForecast) -> None:
    _require(type(value) is EntryForecast, "typed_eg_entry_forecast_required")
    gross, prob, rate = value.gross30_pct, value.target_first_probability, value.training_target_rate
    _require((gross is None or type(gross) is float and isfinite(gross))
             and (prob is None or type(prob) is float and isfinite(prob) and 0 <= prob <= 1)
             and (rate is None or type(rate) in (Fraction, float) and 0 <= rate <= 1)
             and type(value.replay_ambiguous_costs) is frozenset
             and all(type(cost) is int and cost in COST_BPS for cost in value.replay_ambiguous_costs)
             and (not value.replay_ambiguous_costs or gross is not None)
             and (value.unsupported_reason is None if gross is not None else
                  type(value.unsupported_reason) is str and bool(value.unsupported_reason)),
             "invalid_eg_entry_forecast")


def entry_decision(forecast: EntryForecast | None, cost: int) -> str:
    """Apply the registered cost hurdle without using target probability or outcomes."""
    _require(type(cost) is int and cost in COST_BPS, "invalid_eg_entry_cost")
    if forecast is not None:
        _forecast(forecast)
    if forecast is None or forecast.gross30_pct is None:
        return "prediction_unavailable"
    if cost in forecast.replay_ambiguous_costs:
        return "replay_ambiguous"
    return "selected" if forecast.gross30_pct > cost / 100 else "declined"


def _clock(decision: datetime, closing: datetime) -> _Clock:
    entry = decision + MINUTE
    exit_at = entry + PRIMARY_HORIZON * MINUTE
    return _Clock(entry, exit_at, exit_at < closing - 15 * MINUTE)


def _events(features, source_dates, test_dates, forecasts):
    previous, used = None, set()
    events = []
    for frame in features:
        day, symbol, offset = key = tuple(frame["key"])
        _require(day in source_dates and type(symbol) is str and re.fullmatch(r"KRX:[0-9A-Z]{6}", symbol)
                 is not None and type(offset) is int and offset in OFFSETS, "invalid_eg_entry_event_key")
        order = day, offset, symbol
        _require(previous is None or order > previous, "duplicate_or_unordered_eg_entry_event_key")
        previous = order
        if day not in test_dates:
            continue
        timings = frame["timings"]
        _require(type(timings) is list and timings == [item for item in TIMINGS if item in timings]
                 and bool(timings), "invalid_eg_entry_event_timings")
        opening, closing = session_open(day), session_close(day)
        high_at = opening + offset * MINUTE
        immediate = _clock(high_at + 2 * MINUTE, closing)
        control = frame["immediate"]
        _require((control["decision_at"], control["entry_at"], control["exit_at"]) ==
                 tuple(clock.isoformat() for clock in (high_at + 2 * MINUTE, immediate.entry, immediate.exit))
                 and control["status"] == ("supported" if immediate.supported else "outside_clock"),
                 "eg_immediate_entry_clock_changed")
        attempt, context = frame["attempt"], frame["entry"]
        branch, state = attempt["scenario"], attempt["state"]
        _require(branch in (*BRANCHES, None) and state in
                 ("confirmed", "no_setup", "unsupported", "outside_clock", "expired", "invalidated"),
                 "invalid_eg_entry_attempt_state")
        scenario = None
        if state == "confirmed":
            _require(branch in BRANCHES and type(context) is dict, "eg_confirmation_context_missing")
            confirmation = attempt["confirmation"]
            decision = datetime.fromisoformat(context["decision_at"])
            _require(decision.utcoffset() == opening.utcoffset(), "invalid_eg_entry_decision_timezone")
            elapsed = decision - high_at - 2 * MINUTE
            scenario = _clock(decision, closing)
            _require(FIVE <= elapsed <= CONFIRMATION_WINDOW and not elapsed % FIVE
                     and decision - 2 * MINUTE <= closing and context["decision_at"] == decision.isoformat()
                     and confirmation["decision_at"] == context["decision_at"]
                     and confirmation["intended_entry_at"] == scenario.entry.isoformat()
                     and context["primary_clock_supported"] is scenario.supported,
                     "eg_scenario_entry_clock_changed")
        else:
            _require(context is attempt["confirmation"] is None, "eg_unconfirmed_entry_context")
        for timing in timings:
            support = state
            if scenario is not None:
                chart, peer = context["chart"]["status"], context["peers"][timing]["status"]
                _require(chart in ("available", "unavailable") and peer in ("available", "unavailable"),
                         "invalid_eg_entry_support")
                if not scenario.supported:
                    support = "outside_clock"
                elif chart != "available":
                    support = "chart_unavailable"
                else:
                    support = "common" if peer == "available" else "chart_only"
            scores = []
            for arm in ARMS:
                forecast_key = branch, timing, arm, (*key, 0)
                eligible = support == "common" or (support == "chart_only" and arm in CHART_ARMS)
                score = forecasts.get(forecast_key) if eligible else None
                if eligible and forecast_key in forecasts:
                    _require(score is not None, "typed_eg_entry_forecast_required")
                    used.add(forecast_key)
                scores.append(score)
            decisions = tuple(tuple(entry_decision(score, cost) for score in scores) for cost in COST_BPS)
            events.append(_Event(key, timing, branch, state, support, immediate, scenario, tuple(scores), decisions))
    _require(len(used) == len(forecasts), "eg_forecast_outside_sealed_evaluation_support")
    return events


def _label(frame, clock: _Clock, *, immediate: bool) -> _Outcome:
    _require(type(frame) is dict and (frame["entry_at"], frame["exit_at"]) ==
             (clock.entry.isoformat(), clock.exit.isoformat()), "eg_entry_outcome_clock_changed")
    status = frame["status"]
    gross = frame["gross" if immediate else "fixed_gross"]
    touch = None if immediate or frame["first_touch"] is None else frame["first_touch"]["status"]
    _require(touch in (None, "target_first", "stop_first", "time", "same_minute_unknown", "unavailable"),
             "eg_entry_first_touch_status_changed")
    if immediate:
        _require(status in ("outside_clock", "available", "unavailable")
                 and (status == "available") == (gross is not None), "eg_immediate_outcome_status_changed")
        if status == "unavailable":
            status = "entry_unavailable" if frame["entry_open"] is None else "fixed_exit_unavailable"
    else:
        _require(status in ("outside_clock", "admitted", "entry_unavailable", "entry_refused")
                 and (status == "admitted") == (touch is not None)
                 and (gross is None or status == "admitted"), "eg_scenario_outcome_status_changed")
        if status == "admitted":
            status = "fixed_exit_unavailable" if gross is None else "available"
    _require(clock.supported == (status != "outside_clock"), "eg_outcome_prospective_clock_disagrees")
    return _Outcome(status, None if gross is None else _fraction(gross), touch)


def _rows(events, labels_at):
    current_key, frame = None, None
    for event in events:
        if event.key != current_key:
            current_key = event.key
            frame = labels_at(current_key)
            _require(type(frame) is dict and tuple(frame["key"]) == current_key, "eg_entry_outcome_key_changed")
        immediate = _label(frame["immediate"], event.immediate, immediate=True)
        scenario = None if event.scenario is None else _label(frame["primary"], event.scenario, immediate=False)
        yield _Row(event, immediate, scenario)


def _execution(decision: str, outcome: _Outcome) -> str:
    return outcome.status if decision == "selected" else decision


def _nonoverlap(selected):
    previous = {}
    for key, clock in sorted(selected, key=lambda item: (item[1].entry, item[0][1], item[0][2])):
        identity = key[:2]
        if identity not in previous or clock.entry >= previous[identity]:
            previous[identity] = clock.exit
            yield key


def _frequency(rows, decisions, control, weeks):
    def key(row):
        return row.event.key

    selected = [(key(row), row.event.immediate if control else row.event.scenario)
                for row, _, decision in decisions if decision == "selected"]
    opportunities = Counter(_week(key(row)[0]) for row in rows)
    nonoverlap = Counter(_week(item[0]) for item in _nonoverlap(selected))
    requested = Counter(_week(item[0][0]) for item in selected)
    modeled = Counter(_week(key(row)[0]) for row, status, _ in decisions
                      if status in ("available", "fixed_exit_unavailable"))
    known = Counter(_week(key(row)[0]) for row, status, _ in decisions if status == "available")
    unknown = Counter(_week(key(row)[0]) for row, status, _ in decisions if status in
                      ("prediction_unavailable", "replay_ambiguous", "entry_unavailable", "fixed_exit_unavailable"))
    return {week: {"reference_sessions": days, "opportunities": opportunities[week],
                   "selected": requested[week], "same_symbol_nonoverlapping_selected": nonoverlap[week],
                   "modeled_entries": modeled[week], "known_returns": known[week], "unknown": unknown[week],
                   "at_least_15_opportunities": opportunities[week] >= WEEKLY_TARGET,
                   "at_least_15_selected": requested[week] >= WEEKLY_TARGET,
                   "at_least_15_nonoverlapping_selected": nonoverlap[week] >= WEEKLY_TARGET}
            for week, days in weeks.items()}


def _concentration(rows, observed, index, cost):
    opportunities = Counter(row.event.key[index] for row in rows)
    counts, losses = Counter(), Counter()
    for row, gross in observed:
        group = row.event.key[index]
        counts[group] += 1
        losses[group] += max(Fraction(0), Fraction(cost, 10000) - gross)
    loss_mass = sum(losses.values())
    return {"largest_known_return_share": max(counts.values()) / len(observed) if observed else None,
            "largest_net_loss_mass_share": float(max(losses.values()) / loss_mass) if loss_mass else None,
            "counts": {group: {"opportunities": n, "known_returns": counts[group],
                               "net_loss_mass_pp": float(100 * losses[group])}
                       for group, n in sorted(opportunities.items())}}


def _reliability(rows, index, sessions):
    bins = [[0, 0.0, 0] for _ in range(5)]
    losses, baseline, paired, paired_model = [], [], [], []
    statuses = Counter()
    for row in rows:
        forecast, touch = row.event.forecasts[index], row.scenario.first_touch
        prob = forecast.target_first_probability if forecast is not None else None
        if prob is None:
            statuses["probability_unavailable"] += 1
            continue
        if touch not in ("target_first", "stop_first", "time"):
            statuses["target_unavailable"] += 1
            statuses[f"target_{touch if touch is not None else row.scenario.status}"] += 1
            continue
        actual, day = int(touch == "target_first"), row.event.key[0]
        bucket = bins[min(int(5 * prob), 4)]
        bucket[0] += 1
        bucket[1] += prob
        bucket[2] += actual
        loss = (prob - actual) ** 2
        losses.append((day, loss))
        rate = forecast.training_target_rate
        if rate is None:
            statuses["training_prevalence_unavailable"] += 1
        else:
            reference = (float(rate) - actual) ** 2
            baseline.append((day, reference))
            paired_model.append((day, loss))
            paired.append((day, loss - reference))
    return {"statuses": dict(statuses), "brier": _estimate(losses, sessions),
            "model_brier_same_pairs": _estimate(paired_model, sessions),
            "training_prevalence_brier_same_pairs": _estimate(baseline, sessions),
            "model_minus_training_prevalence_brier_same_pairs": _estimate(paired, sessions),
            "bins": [{"lower": i / 5, "upper": (i + 1) / 5, "upper_inclusive": i == 4,
                      "rows": count, "mean_prediction": total / count if count else None,
                      "observed_frequency": hits / count if count else None}
                     for i, (count, total, hits) in enumerate(bins)]}


def _summarize(rows, decisions, sessions, weeks, cost, *, control=False):
    outcomes = [(row, _execution(decision, row.immediate if control else row.scenario), decision)
                for row, decision in zip(rows, decisions, strict=True)]
    observed = [(row, (row.immediate if control else row.scenario).gross)
                for row, status, _ in outcomes if status == "available"]
    hurdle = Fraction(cost, 10000)
    return {"decision_counts": dict(Counter(decisions)),
            "outcome_counts": dict(Counter(status for _, status, _ in outcomes)),
            "gross_pp": _estimate([(row.event.key[0], float(100 * value)) for row, value in observed], sessions),
            "net_pp": _estimate([(row.event.key[0], float(100 * (value - hurdle)))
                                 for row, value in observed], sessions),
            "known_cost_exceedance_count": sum(value > hurdle for _, value in observed),
            "weeks": _frequency(rows, outcomes, control, weeks),
            "concentration": {"session": _concentration(rows, observed, 0, cost),
                              "symbol": _concentration(rows, observed, 1, cost)}}


def _contribution(row, decision, cost):
    if decision == "declined" or (decision == "selected" and row.scenario.status == "entry_refused"):
        return Fraction(0)
    if decision == "selected" and row.scenario.gross is not None:
        return row.scenario.gross - Fraction(cost, 10000)
    return None


def _contrast(rows, left, right, sessions, cost):
    def paired(hurdle):
        for row, a, b in zip(rows, left, right, strict=True):
            lhs, rhs = _contribution(row, a, hurdle), _contribution(row, b, hurdle)
            if lhs is not None and rhs is not None:
                yield row.event.key[0], float(100 * lhs), float(100 * rhs)

    def estimate(points):
        return {"left": _estimate([(day, lhs) for day, lhs, _ in points], sessions),
                "right": _estimate([(day, rhs) for day, _, rhs in points], sessions),
                "left_minus_right": _estimate([(day, lhs - rhs) for day, lhs, rhs in points], sessions)}

    net, gross = list(paired(cost)), list(paired(0))
    return {"common_opportunities": len(rows), "known_matched_opportunities": len(net),
            "unknown_matched_opportunities": len(rows) - len(net),
            "gross_pp": estimate(gross), "net_pp": estimate(net)}


def _group(rows, arms, sessions, weeks):
    def decisions_at(cost_index):
        decisions = {RULE: ["selected"] * len(rows)}
        for arm in arms:
            index = ARMS.index(arm)
            decisions[arm] = [row.event.decisions[cost_index][index] for row in rows]
        return decisions

    def at_cost(decisions, cost):
        return {
            "arms": {arm: _summarize(rows, scores, sessions, weeks, cost) for arm, scores in decisions.items()},
            "contrasts": {f"{left}_minus_{right}": _contrast(rows, decisions[left], decisions[right], sessions, cost)
                          for left, right in CONTRASTS if left in decisions and right in decisions},
        }

    primary = decisions_at(0)
    reports = {"33": at_cost(primary, 33), "43": at_cost(decisions_at(1), 43)}
    fixed_selection_stress = at_cost(primary, 43)
    forecast_statuses = {}
    for arm in arms:
        scores = [row.event.forecasts[ARMS.index(arm)] for row in rows]
        forecast_statuses[arm] = dict(Counter("absent_forecast" if score is None else
                                             score.unsupported_reason or "scored" for score in scores))
    return {"opportunities": len(rows), "first_hour_onsets": sum(row.event.key[2] <= 60 for row in rows),
            "forecast_statuses": forecast_statuses,
            "target_first_reliability": {arm: _reliability(rows, ARMS.index(arm), sessions) for arm in arms},
            "costs": reports, "fixed_selection_stress": {"selection_bps": 33, "outcome_bps": 43,
                                                        **fixed_selection_stress}}


def _timing_attribution(rows, sessions):
    pairs = [(row.event.key[0], float(100 * (row.scenario.gross - row.immediate.gross))) for row in rows
             if row.scenario.gross is not None and row.immediate.gross is not None]
    return {"claim": "posthoc matched outcome timing attribution only; both trades pay the same cost",
            "prospective_common_scenario_opportunities": len(rows),
            "known_matched_outcomes": len(pairs), "unknown_matched_outcomes": len(rows) - len(pairs),
            "scenario_minus_immediate_pp": _estimate(pairs, sessions)}


def _candidate_screen(groups):
    candidates = []
    for branch in BRANCHES:
        for arm in (RULE, *ARMS):
            timings = {}
            for timing in TIMINGS:
                cell = groups[branch][timing]["common"]["costs"]["43"]["arms"][arm]
                estimate = cell["net_pp"]
                interval = estimate["normal95_exploratory"]
                passes = (estimate["contributing_sessions"] >= 10 and estimate["mean"] is not None
                          and estimate["mean"] > 0 and interval is not None and interval[0] > 0)
                timings[timing] = {"passes": passes, "mean_net43_pp": estimate["mean"],
                    "interval_lower_pp": None if interval is None else interval[0],
                    "contributing_sessions": estimate["contributing_sessions"],
                    "nonoverlapping_15_selection_weeks": sum(
                        row["at_least_15_nonoverlapping_selected"] for row in cell["weeks"].values())}
            candidates.append({"branch": branch, "arm": arm, "timings": timings,
                               "screen_passes": all(row["passes"] for row in timings.values())})
    return candidates


def compare_entry_policies(
    source_dates: Sequence[str], features: Iterable[dict], *, labels_at: Callable[[Key], dict],
    forecasts: Mapping[ForecastKey, EntryForecast], include_chart_only: bool = False,
) -> dict:
    """Compare prospective common-support entries and unconditional high controls.

    The caller binds audited, immutable snapshot frames and fold forecasts.
    Feature and forecast checks complete before any evaluation label request.
    All 157 fixed-calendar test sessions count, including weeks with zero
    selections. The overlap count is a fixed-clock diagnostic over prospective
    selections, without using modeled fills or changing policy decisions.
    """
    _require(type(include_chart_only) is bool and isinstance(forecasts, Mapping), "invalid_eg_policy_scope")
    calendar = fixed_calendar(source_dates)
    sessions = calendar.evaluation_reference_sessions
    weeks = calendar.evaluation_week_session_counts
    events = _events(features, set(calendar.source_reference_sessions), set(sessions), forecasts)
    rows = list(_rows(events, labels_at))
    groups = {branch: {} for branch in BRANCHES}
    immediate = {}
    for timing in TIMINGS:
        original = [row for row in rows if row.event.timing == timing]
        controls = ["selected" if row.event.immediate.supported else "outside_clock" for row in original]
        immediate[timing] = {"observed_clusters": len(original),
                             "first_hour_onsets": sum(row.event.key[2] <= 60 for row in original),
                             "attempt_states": dict(Counter(row.event.state for row in original)),
                             "costs": {str(cost): _summarize(original, controls, sessions, weeks, cost, control=True)
                                       for cost in COST_BPS}}
        for branch in BRANCHES:
            population = [row for row in original if row.event.branch == branch]
            common = [row for row in population if row.event.support == "common"]
            report = {"attempt_states": dict(Counter(row.event.state for row in population)),
                      "support_states": dict(Counter(row.event.support for row in population)),
                      "common": _group(common, ARMS, sessions, weeks),
                      "matched_timing_attribution": _timing_attribution(common, sessions)}
            if include_chart_only:
                wider = [row for row in population if row.event.support in ("common", "chart_only")]
                report["wider_chart_diagnostic"] = _group(wider, CHART_ARMS, sessions, weeks)
            groups[branch][timing] = report
    return {"schema": "EG-entry-policy-comparison-v1", "reference_sessions": list(sessions),
            "evaluation_boundary_week_flags": {key: dict(value) for key, value in calendar.evaluation_boundary_week_flags.items()},
            "groups": groups, "immediate": immediate, "candidate_screen": _candidate_screen(groups),
            "claim": "stored OPEN price diagnostics, without account trades, broker fills or promotion evidence"}
