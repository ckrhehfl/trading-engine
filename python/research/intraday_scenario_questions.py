"""Bind EH opening-range paths to the exact observations that raised them."""

from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import MINUTE, Issue, SessionInputs, _require, _session
from research.intraday_scenario_labels import PathQuestion
from research.intraday_scenario_state import OpeningRangeHighLevel, ScenarioEvent, _bar, _level_session


@dataclass(frozen=True)
class InitialPath:
    stage: Literal["approach", "breakout"]
    question: PathQuestion | None
    start_close: Fraction
    target_basis: Literal["prepared_band", "known_intraday_swing_high", "twice_stop_distance"] | None = None
    issues: tuple[Issue, ...] = ()


def opening_range_questions(session: SessionInputs, event: ScenarioEvent) -> tuple[InitialPath, ...]:
    """Use only an opening-range event's original decision and known breakout.

    A breakout's obstacle is the nearest higher confirmed 5-minute swing high
    already visible at that decision, looking only within this regular session.
    The swing needs a lower high on its left and a nonhigher high on its right.
    With a complete prefix and no such level, use twice the initial stop distance.
    """
    _session(session)
    _require(isinstance(event, ScenarioEvent) and isinstance(event.level, OpeningRangeHighLevel),
             "opening_range_event_required")
    _level_session(session, event.level)
    _require(event.kind in ("prepared", "breakout") and type(event.band) is Fraction and event.band > 0,
             "supported_opening_range_event_required")
    paths = []
    if event.kind == "prepared":
        at = event.origin.available_at
        close = Fraction(event.origin.close)
        bounds = event.prepared_bounds
        if bounds is None:
            paths.append(InitialPath("approach", None, close, issues=(
                Issue(session.symbol, at, "invalid_prepared_risk_boundary"),)))
        else:
            stop, target = bounds
            paths.append(InitialPath("approach", PathQuestion(at, at + 30 * MINUTE, event.level.price,
                         event.band, target, stop), close, "prepared_band"))
    if event.breakout is not None:
        paths.append(_breakout_path(session, event))
    return tuple(paths)


def _breakout_path(session: SessionInputs, event: ScenarioEvent) -> InitialPath:
    breakout = event.breakout
    at, close = breakout.available_at, Fraction(breakout.close)
    stop = event.breakout_stop
    if stop is None or stop >= close:
        return InitialPath("breakout", None, close, issues=(
            Issue(session.symbol, at, "invalid_breakout_risk_boundary"),))
    elapsed = (breakout.end_at - session.opening) // MINUTE
    _require(elapsed >= 20 and elapsed % 5 == 0 and breakout.end_at <= session.closing
             and at == breakout.end_at + 2 * MINUTE, "invalid_opening_breakout_clock")
    highs = []
    for start in range(0, elapsed, 5):
        bar, issues = _bar(session, start, at)
        if issues:
            return InitialPath("breakout", None, close, issues=issues)
        highs.append(Fraction(bar.high))
    swings = [middle for left, middle, right in zip(highs, highs[1:], highs[2:])
              if left < middle >= right and middle > close]
    if swings:
        target, basis = min(swings), "known_intraday_swing_high"
    else:
        target, basis = close + 2 * (close - stop), "twice_stop_distance"
    question = PathQuestion(at, at + 30 * MINUTE, event.level.price, event.band, target, stop)
    return InitialPath("breakout", question, close, basis)
