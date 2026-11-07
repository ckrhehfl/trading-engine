"""Join a sealed feature roster to the fixed chronological EH management comparison."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_ed_inputs import SessionInputs, _require
from research.intraday_scenario_analogue_roster import ChartAnalogueRoster, HeldAnalogueRoster, HistoricalChartDecision
from research.intraday_scenario_management_day import ManagementDay, baseline_holding_inputs, run_management_day
from research.intraday_scenario_management_report import ManagementReport, summarize_management
from research.intraday_scenario_snapshot import ProjectedDay, _validate_day


@dataclass(frozen=True)
class ManagementCampaign:
    scenario: str
    days: tuple[ManagementDay, ...]
    report: ManagementReport
    management_mode: Literal["pending_question", "held"]
    downside_penalty: Fraction


def run_management_campaign(
    projected: Iterable[ProjectedDay], reference_sessions: tuple[str, ...], *, scenario: str,
    read_session: Callable[[str, str], SessionInputs | None], risk_budget: Fraction, cost_bps: int,
    downside_penalty: Fraction, fresh_reentry: bool, held_comparison: bool = False,
    checkpoint: Callable[[str], object] | None = None,
) -> ManagementCampaign:
    """Replay one population and cost profile, with a prior-only roster for each fold.

    The caller authenticates the sealed projection, reference calendar, and source
    sessions. This adapter has no file or input-authority surface.
    """
    _require(scenario in ("S1", "S2") and type(reference_sessions) is tuple and callable(read_session)
             and type(risk_budget) is Fraction and risk_budget > 0 and type(cost_bps) is int and cost_bps in (33, 43)
             and type(downside_penalty) is Fraction and downside_penalty >= 0
             and type(fresh_reentry) is bool and type(held_comparison) is bool
             and (checkpoint is None or callable(checkpoint)), "eh_management_campaign_scope_differs")
    calendar = fixed_calendar(reference_sessions)
    remaining_folds = iter(calendar.folds)
    fold = next(remaining_folds)
    roster = None
    held_roster = None
    history = []
    held_history = []
    training_end = len(calendar.folds[-1].training_sessions)
    population = {}
    results = []
    observations = {}

    for index, (day, saved) in enumerate(zip(reference_sessions, projected, strict=True)):
        _require(type(saved) is ProjectedDay and saved.day == day, "eh_management_campaign_feature_date_differs")
        if checkpoint is not None:
            checkpoint("eh_management_feature_day")
        _validate_day(saved)
        membership = sorted((member.rank, item.symbol) for item in saved.coordinates
                            for member in item.targets if member.scenario == scenario)
        _require(tuple(rank for rank, _ in membership) == tuple(range(1, 21)),
                 "eh_management_campaign_original_ranks_differ")
        symbols = tuple(symbol for _, symbol in membership)
        population[day] = symbols

        if fold is not None and day in fold.test_sessions:
            if roster is None:
                _require(day == fold.test_sessions[0] and reference_sessions[:index] == fold.training_sessions,
                         "eh_management_campaign_fold_boundary_differs")
                roster = ChartAnalogueRoster(history, fold=fold)
                held_roster = HeldAnalogueRoster(held_history, fold=fold) if held_comparison else None
                prior_days = frozenset(fold.training_sessions)

            def scoped_session(source_day: str, symbol: str) -> SessionInputs | None:
                _require((source_day == day or source_day in prior_days) and symbol in population[source_day],
                         "eh_management_campaign_source_coordinate_outside_scope")
                return read_session(source_day, symbol)

            evaluated = run_management_day(saved, scenario=scenario, roster=roster, read_session=scoped_session,
                risk_budget=risk_budget, cost_bps=cost_bps, downside_penalty=downside_penalty,
                fresh_reentry=fresh_reentry, checkpoint=checkpoint, held_roster=held_roster)
            _require(evaluated.day == day and evaluated.scenario == scenario and evaluated.symbols == symbols
                     and set(evaluated.observations) == {(day, symbol) for symbol in symbols},
                     "eh_management_campaign_evaluated_roster_differs")
            results.append(evaluated)
            observations.update(evaluated.observations)
            if day == fold.test_sessions[-1]:
                fold = next(remaining_folds, None)
                roster = None

        if index < training_end and held_comparison:
            def baseline_session(source_day: str, symbol: str) -> SessionInputs | None:
                _require(source_day == day and symbol in population[day],
                         "eh_management_campaign_baseline_coordinate_outside_scope")
                return read_session(source_day, symbol)

            held_history.extend(baseline_holding_inputs(saved, scenario=scenario, read_session=baseline_session,
                                     risk_budget=risk_budget, cost_bps=cost_bps, checkpoint=checkpoint))
        elif index < training_end:
            history.extend(HistoricalChartDecision("breakout", row.frame.chart) for row in saved.rows
                           if scenario in row.scenarios and row.frame.initial.stage == "breakout"
                           and row.frame.chart is not None and row.frame.chart.status == "ready")

    _require(fold is None and tuple(result.day for result in results) == calendar.evaluation_reference_sessions,
             "eh_management_campaign_evaluation_dates_differ")
    evaluated_population = {day: population[day] for day in calendar.evaluation_reference_sessions}
    report = summarize_management(evaluated_population, observations, cost_bps=cost_bps, include_reentry=fresh_reentry)
    return ManagementCampaign(scenario, tuple(results), report, "held" if held_comparison else "pending_question",
                              downside_penalty)
