"""Exact episode-level EH management and reentry contrasts on a fixed roster."""

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from research.intraday_campaign_readiness import KST, _week
from research.intraday_ed_inputs import _clock, _require
from research.intraday_scenario_management_compare import ManagementComparison
from research.intraday_scenario_management_metrics import ArmMetrics, management_metrics


EntryStatus = Literal["no_event", "no_entry", "declined", "unconfirmed", "unsupported", "confirmed"]
Coordinate = tuple[str, str]
_ENTRY_STATUSES = ("no_event", "no_entry", "declined", "unconfirmed", "unsupported", "confirmed")
_WEEKLY_OBJECTIVE = 15


@dataclass(frozen=True)
class ManagementObservation:
    entry_status: EntryStatus
    comparison: ManagementComparison | None = None


@dataclass(frozen=True)
class WeeklyEntries:
    """Original coverage, confirmed fills, and missingness at each replay stage."""

    week: str
    original_symbol_days: int
    confirmed_initial_entries: int
    confirmed_fresh_entries: int
    unconfirmed_initial_entries: int
    unsupported_initial_entries: int
    unresolved_outcomes: int
    unsupported_after_entry: int

    @property
    def confirmed_entries(self) -> int:
        return self.confirmed_initial_entries + self.confirmed_fresh_entries

    @property
    def known_outcomes(self) -> int:
        return self.confirmed_initial_entries - self.unresolved_outcomes - self.unsupported_after_entry


@dataclass(frozen=True)
class ArmSummary:
    name: str
    known_outcomes: int
    unresolved_outcomes: int
    unsupported_after_entry: int
    weeks: tuple[WeeklyEntries, ...]
    weeks_at_least_15: int


@dataclass(frozen=True)
class EventTotals:
    gross_pnl: Fraction
    net_pnl: Fraction
    paid_cost: Fraction
    losing_events: int
    net_loss: Fraction
    worst_event_net_pnl: Fraction
    mean_event_gross_bps: Fraction
    mean_event_net_bps: Fraction
    mean_entry_basis_exposure: Fraction
    gross_turnover: Fraction


@dataclass(frozen=True)
class PairContribution:
    group: str
    both_known_events: int
    second_minus_first_net_pnl: Fraction | None


@dataclass(frozen=True)
class ManagementContrast:
    first_arm: str
    second_arm: str
    both_known_events: int
    first: EventTotals | None
    second: EventTotals | None
    by_week: tuple[PairContribution, ...]
    by_symbol: tuple[PairContribution, ...]


@dataclass(frozen=True)
class ManagementReport:
    cost_bps: int
    original_sessions: int
    original_symbol_days: int
    initial_status_counts: dict[str, int]
    arms: tuple[ArmSummary, ...]
    contrasts: tuple[ManagementContrast, ...]


def _case_metrics(comparison: ManagementComparison, day: str, symbol: str,
                  cost_bps: int) -> tuple[Fraction, dict[str, ArmMetrics]]:
    _require(type(comparison) is ManagementComparison, "typed_eh_management_comparison_required")
    initial = comparison.initial
    _clock(initial.entered_at)
    _require(initial.symbol == symbol and initial.entered_at.astimezone(KST).date().isoformat() == day
             and initial.cost_bps == cost_bps and initial.attempts == 1 and initial.pending is None and initial.closed_at is None
             and not initial.book.indeterminate and len(initial.book.lots) == 1,
             "eh_management_report_initial_identity_or_cost_differs")
    lot, = initial.book.lots
    _require(lot.symbol == symbol and lot.lot_id == initial.first_lot_id and lot.quantity == lot.original_quantity
             and not lot.half_reduced,
             "eh_management_report_initial_lot_differs")
    _require(lot.original_entry_cost == lot.remaining_entry_basis * Fraction(cost_bps, 10_000),
             "eh_management_report_initial_paid_cost_differs")
    arms = (comparison.fixed, comparison.dynamic)
    if comparison.reentry is not None:
        arms += (comparison.reentry,)
    _require(all((item.episode.symbol, item.episode.start_at, item.episode.end_at, item.episode.first_lot_id,
                  item.episode.cost_bps, item.episode.book.initial_capital)
                 == (symbol, initial.start_at, initial.end_at, initial.first_lot_id, cost_bps, initial.book.initial_capital)
                 for item in arms), "eh_management_report_arm_identity_or_cost_differs")
    return lot.remaining_entry_basis, {item.name: item for item in management_metrics(comparison)}


def _totals(rows: tuple[tuple[Fraction, ArmMetrics], ...]) -> EventTotals | None:
    if not rows:
        return None
    nets = tuple(item.net_pnl for _, item in rows)
    _require(all(item.gross_pnl is not None and item.net_pnl is not None and item.paid_cost is not None
                 and item.entry_basis_exposure is not None and item.gross_turnover is not None for _, item in rows),
             "eh_management_report_known_metrics_missing")
    losses = tuple(-value for value in nets if value < 0)
    return EventTotals(
        sum((item.gross_pnl for _, item in rows), Fraction()), sum(nets, Fraction()),
        sum((item.paid_cost for _, item in rows), Fraction()), len(losses), sum(losses, Fraction()), min(nets),
        10_000 * sum((item.gross_pnl / basis for basis, item in rows), Fraction()) / len(rows),
        10_000 * sum((item.net_pnl / basis for basis, item in rows), Fraction()) / len(rows),
        sum((item.entry_basis_exposure for _, item in rows), Fraction()) / len(rows),
        sum((item.gross_turnover for _, item in rows), Fraction()),
    )


def _contrast(first: str, second: str, rows: list[tuple[str, str, Fraction, ArmMetrics, ArmMetrics]],
              weeks: tuple[str, ...]) -> ManagementContrast:
    week_counts, symbol_counts = Counter(), Counter()
    week_pnl, symbol_pnl = Counter(), Counter()
    for week, symbol, _, left, right in rows:
        delta = right.net_pnl - left.net_pnl
        week_counts[week] += 1
        symbol_counts[symbol] += 1
        week_pnl[week] += delta
        symbol_pnl[symbol] += delta
    return ManagementContrast(
        first, second, len(rows), _totals(tuple((basis, left) for _, _, basis, left, _ in rows)),
        _totals(tuple((basis, right) for _, _, basis, _, right in rows)),
        tuple(PairContribution(week, week_counts[week], week_pnl.get(week)) for week in weeks),
        tuple(PairContribution(symbol, symbol_counts[symbol], symbol_pnl[symbol]) for symbol in sorted(symbol_counts)),
    )


def summarize_management(
    calendar: Mapping[str, tuple[str, ...]], observations: Mapping[Coordinate, ManagementObservation], *,
    cost_bps: int, include_reentry: bool,
) -> ManagementReport:
    """Summarize one original population and one nominal cost assumption.

    Event returns divide by that event's shared initial notional. Net loss is
    the positive sum of negative event PnL magnitudes on the matched sample.
    Weekly counts retain confirmed entries with unresolved terminal paths and
    include overlapping positions. All original evaluation weeks are kept,
    with separate coverage counts before and after a confirmed initial fill.
    The caller supplies the frozen calendar and all its coordinates. These
    metrics describe independent episode replays.
    """
    _require(isinstance(calendar, Mapping) and 0 < len(calendar) <= 157 and isinstance(observations, Mapping)
             and type(cost_bps) is int and cost_bps in (33, 43) and type(include_reentry) is bool,
             "eh_management_report_calendar_and_profile_required")
    for symbols in calendar.values():
        _require(type(symbols) is tuple and 0 < len(symbols) <= 20
                 and all(type(symbol) is str and bool(symbol) for symbol in symbols)
                 and len(set(symbols)) == len(symbols), "eh_management_report_original_roster_differs")
    week_by_day = {day: _week(day) for day in calendar}
    keys = {(day, symbol) for day, symbols in calendar.items() for symbol in symbols}
    _require(len(observations) == len(keys) and keys == set(observations), "eh_management_report_coordinates_differ")
    week_sizes = Counter(week_by_day[day] for day, _ in keys)
    weeks = tuple(sorted(week_sizes))
    names = ("fixed", "dynamic", "reentry") if include_reentry else ("fixed", "dynamic")
    initial_status = Counter(dict.fromkeys(_ENTRY_STATUSES, 0))
    entry_by_week = Counter()
    outcome_by_week = Counter()
    fresh_entries = Counter()
    pairs = {pair: [] for pair in zip(names, names[1:])}
    for day, symbol in sorted(keys):
        row = observations[day, symbol]
        _require(type(row) is ManagementObservation and row.entry_status in _ENTRY_STATUSES
                 and (row.entry_status == "confirmed" or row.comparison is None),
                 "eh_management_report_entry_status_differs")
        week = week_by_day[day]
        initial_status[row.entry_status] += 1
        entry_by_week[week, row.entry_status] += 1
        if row.entry_status != "confirmed":
            continue
        basis, metrics = ((None, {}) if row.comparison is None
                          else _case_metrics(row.comparison, day, symbol, cost_bps))
        known = {}
        for name in names:
            item = metrics.get(name)
            if item is None:
                outcome_by_week[name, week, "unsupported"] += 1
                continue
            _require(item.confirmed_entries in ((1, 2) if name == "reentry" else (1,)),
                     "eh_management_report_confirmed_entry_count_differs")
            fresh_entries[name, week] += item.confirmed_entries - 1
            if item.status == "unresolved":
                outcome_by_week[name, week, "unresolved"] += 1
            else:
                _require(item.status in ("closed", "not_reentered") and item.net_pnl is not None,
                         "eh_management_report_terminal_state_differs")
                known[name] = item
        for (first, second), pair in pairs.items():
            if first in known and second in known:
                pair.append((week, symbol, basis, known[first], known[second]))

    arms = []
    for name in names:
        weekly = tuple(WeeklyEntries(
            week, week_sizes[week], entry_by_week[week, "confirmed"], fresh_entries[name, week],
            entry_by_week[week, "unconfirmed"], entry_by_week[week, "unsupported"],
            outcome_by_week[name, week, "unresolved"], outcome_by_week[name, week, "unsupported"],
        ) for week in weeks)
        arms.append(ArmSummary(
            name, sum(item.known_outcomes for item in weekly), sum(item.unresolved_outcomes for item in weekly),
            sum(item.unsupported_after_entry for item in weekly), weekly,
            sum(item.confirmed_entries >= _WEEKLY_OBJECTIVE for item in weekly),
        ))
    return ManagementReport(cost_bps, len(calendar), len(keys), dict(initial_status), tuple(arms),
                            tuple(_contrast(*pair, rows, weeks) for pair, rows in pairs.items()))
