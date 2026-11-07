"""Project the observed EH chart and position at one held decision clock."""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_chart_inputs import _aggregate
from research.intraday_ed_inputs import MINUTE, OHLC, Issue, SessionInputs, _clock, _require
from research.intraday_scenario_decision_inputs import WINDOW_MINUTES, PriceVector, _window_start
from research.intraday_scenario_policy import Episode
from research.intraday_scenario_state import _observed


@dataclass(frozen=True)
class HeldDecisionInputs:
    symbol: str
    decision_at: datetime
    entered_at: datetime
    planning_cap: datetime
    attempt: Literal[1, 2]
    status: Literal["ready", "unknown"]
    entry_in_bands: Fraction
    stop_in_bands: Fraction
    half_reduced: bool
    remaining_fraction: Fraction
    minutes_held: int
    minutes_remaining: int
    minutes: tuple[PriceVector, ...] = ()
    five_minute_bars: tuple[PriceVector, ...] = ()
    issues: tuple[Issue, ...] = ()


def held_decision_inputs(
    session: SessionInputs, episode: Episode, *, level: Fraction, band: Fraction,
    decision_at: datetime, planning_cap: datetime,
) -> HeldDecisionInputs:
    """Observe a still-held lot independently of the initial path question."""
    start = _window_start(session, decision_at)
    _require(type(episode) is Episode and type(level) is Fraction and type(band) is Fraction
             and level > 0 and band > 0, "eh_held_input_invalid_basis_or_episode")
    entered_at, stop = episode.entered_at, episode.stop
    _require(episode.symbol == session.symbol and len(episode.book.lots) == 1
             and type(episode.attempts) is int and episode.attempts in (1, 2)
             and entered_at is not None and type(stop) is Fraction and episode.closed_at is None
             and episode.pending is None and not episode.book.indeterminate and not episode.book.inventory_uncertain,
             "eh_held_input_requires_confirmed_position")
    for clock in (entered_at, episode.start_at, episode.last_at, episode.end_at, decision_at, planning_cap):
        _clock(clock)
        _require(not (clock - session.opening) % MINUTE, "eh_held_input_clock_off_minute_grid")
    _require(session.opening <= episode.start_at < entered_at <= decision_at - 3 * MINUTE
             and entered_at <= episode.last_at < decision_at < planning_cap <= episode.end_at < session.closing
             and planning_cap == min(entered_at + 30 * MINUTE, episode.end_at), "eh_held_input_clock_or_cap_differs")
    lot, = episode.book.lots
    _require(lot.symbol == episode.symbol and lot.lot_id == episode.first_lot_id + episode.attempts - 1
             and 0 < stop < lot.entry_price, "eh_held_input_lot_identity_or_basis_differs")

    def relative(ohlc: OHLC) -> PriceVector:
        op, high, low, close = ((Fraction(price) - level) / band for price in ohlc)
        return op, high, low, close

    rows, issues = _observed(session, start, WINDOW_MINUTES, decision_at)
    minutes: tuple[PriceVector, ...] = ()
    coarse: tuple[PriceVector, ...] = ()
    if not issues:
        minutes = tuple(relative(row.ohlc) for row in rows)
        coarse = tuple(relative(_aggregate(rows[index:index + 5])) for index in range(0, WINDOW_MINUTES, 5))
    return HeldDecisionInputs(
        symbol=session.symbol, decision_at=decision_at, entered_at=entered_at, planning_cap=planning_cap,
        attempt=episode.attempts, status="unknown" if issues else "ready",
        entry_in_bands=(lot.entry_price - level) / band, stop_in_bands=(stop - level) / band,
        half_reduced=lot.half_reduced, remaining_fraction=Fraction(lot.quantity, lot.original_quantity),
        minutes_held=(decision_at - entered_at) // MINUTE, minutes_remaining=(planning_cap - decision_at) // MINUTE,
        minutes=minutes, five_minute_bars=coarse, issues=issues,
    )
