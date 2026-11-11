"""Pure EL price-scene projection over an immutable scheduled-minute prefix.

No loading, selector, outcome, fill, return or order machinery lives here.
Numeric definitions are research design assumptions, not validated trader rules.
``end_offset`` is an exclusive input watermark: an omitted earlier minute is
unknown at its assumed start+3 deadline, not evidence that no trade occurred.
Later insertion/revision of a past coordinate is a new input version, never an
update to this projection. Late supplied bars delay the ordered projection;
decision offsets are the cumulative maximum availability of the observed prefix.
An unfinished prefix does not terminate an active episode.
"""

from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Iterable


FAMILIES = ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK")
LAG = 3
SCALE_BARS = 20
RESET = 5
QUARTER = Fraction(1, 4)
HALF = Fraction(1, 2)
MINIMUM_PAST_MOVE_BPS = 66
CONTRACT = {
    "version": "el-price-scenes-v1",
    "status": "proposed_design_values_not_validated_rules",
    "input": "immutable_scheduled_minute_prefix",
    "missing_deadline_lag_minutes": LAG,
    "scale": "median_20_true_ranges_ending_before_impulse_start",
    "scale_bars": SCALE_BARS,
    "minimum_past_move_bps": MINIMUM_PAST_MOVE_BPS,
    "past_move_floor": "past_impulse_size_only_not_future_payoff_room",
    "broad_candidates": "all_3_scale_minute_observations; groups_after_5_no_hit_minutes_or_unknown",
    "a": {"impulse_minutes": 3, "minimum_scales": 3,
          "confirm_age_inclusive": [3, 12], "low_tolerance": "1/4"},
    "b": {"opening_minutes": 30, "breach": "close < level - scale/4",
          "reclaim": "close >= level + scale/4", "last_age": 20,
          "breach_start": "strictly_after_level_known_at",
          "fresh_cross": "previous_close >= level - scale/4"},
    "c": {"impulse_minutes": 5, "minimum_scales": 3,
          "pullback_scales": "1/2", "last_pullback_age": 10,
          "retained_advance": "1/2", "resume_age_inclusive": [2, 10]},
    "frames": "onset, every_3_scheduled_minutes, first_pullback, terminal",
    "reset_minutes": RESET,
    "failure_precedes_confirmation": True,
    "session_end": "only_at_explicit_complete_session_watermark",
    "overlap": "conservative_shared_series_group_and_causal_intervals",
}


def _exact(value: Fraction | Decimal | int) -> Fraction:
    if isinstance(value, bool) or not isinstance(value, (Fraction, Decimal, int)):
        raise TypeError("Prices require Fraction, finite Decimal, or int; no floats")
    if isinstance(value, Decimal) and not value.is_finite():
        raise ValueError("Non-finite price")
    return Fraction(value)


@dataclass(frozen=True)
class MinuteBar:
    offset: int
    open: Fraction | Decimal | int
    high: Fraction | Decimal | int
    low: Fraction | Decimal | int
    close: Fraction | Decimal | int
    available_offset: int

    def __post_init__(self) -> None:
        if type(self.offset) is not int or self.offset < 0:
            raise ValueError("offset must be a nonnegative integer")
        if (type(self.available_offset) is not int
                or self.available_offset < self.offset + 1):
            raise ValueError("A complete minute cannot be available before its end")
        for name in ("open", "high", "low", "close"):
            object.__setattr__(self, name, _exact(getattr(self, name)))
        if not (0 < self.low <= min(self.open, self.close)
                <= max(self.open, self.close) <= self.high):
            raise ValueError("Invalid positive OHLC geometry")


Value = Fraction | int | bool | str | None


def _json_value(value: Value) -> str | int | bool | None:
    return str(value) if isinstance(value, Fraction) else value


@dataclass(frozen=True)
class EventFrame:
    family: str
    event_id: str
    overlap_group_id: str
    onset_offset: int
    offset: int
    decision_offset: int
    interval_start: int
    interval_end: int
    kind: str
    state: str
    terminal: bool
    scale: Fraction
    level: Fraction
    structure: Fraction
    conditions: tuple[tuple[str, Value], ...]

    def as_dict(self) -> dict[str, object]:
        result = dict(self.__dict__)
        for key in ("scale", "level", "structure"):
            result[key] = str(result[key])
        result["conditions"] = {key: _json_value(value)
                                for key, value in self.conditions}
        return result


@dataclass(frozen=True)
class SupportRow:
    family: str
    offset: int
    decision_offset: int
    status: str
    event_id: str | None
    conditions: tuple[tuple[str, Value], ...] = ()


@dataclass(frozen=True)
class BroadObservation:
    """One 3-scale minute observation, not one independent trading event."""
    family: str
    offset: int
    decision_offset: int
    broad_episode_id: str
    scale: Fraction
    past_move: Fraction
    past_move_bps: Fraction
    meets_minimum_past_move: bool


@dataclass(frozen=True)
class ScenarioProjection:
    frames: tuple[EventFrame, ...]
    support: tuple[SupportRow, ...]
    broad_observations: tuple[BroadObservation, ...] = ()


@dataclass(frozen=True)
class _OpeningRange:
    low: Fraction
    known_at: int
    range_bps: Fraction


@dataclass
class _Candidate:
    family: str
    event_id: str
    onset: int
    interval_start: int
    scale: Fraction
    level: Fraction
    minimum: Fraction
    base: Fraction
    peak: Fraction
    phase: str = "WAITING"
    pullback_at: int | None = None
    context: tuple[tuple[str, Value], ...] = ()


def _scale(bars: dict[int, MinuteBar], baseline: int) -> tuple[str, Fraction | None]:
    # Twenty TRs need twenty-one consecutive closes, including the baseline.
    start = baseline - SCALE_BARS + 1
    if start < 1:
        return "WARMUP", None
    if any(index not in bars for index in range(start - 1, baseline + 1)):
        return "MISSING_SCALE_HISTORY", None
    ranges = sorted(max(bars[index].high - bars[index].low,
                        abs(bars[index].high - bars[index - 1].close),
                        abs(bars[index].low - bars[index - 1].close))
                    for index in range(start, baseline + 1))
    value = (ranges[9] + ranges[10]) / 2
    return ("SUPPORTED", value) if value > 0 else ("ZERO_SCALE", None)


def _broad_measure(
    family: str, offset: int, bars: dict[int, MinuteBar],
) -> tuple[str, tuple[Fraction, Fraction, Fraction] | None]:
    length = 3 if family == "A_DOWNFAIL" else 5
    baseline = offset - length
    status, scale = _scale(bars, baseline)
    if scale is None:
        return status, None
    if any(index not in bars for index in range(baseline + 1, offset + 1)):
        return "MISSING_IMPULSE", None
    base = bars[baseline].close
    move = base - bars[offset].close if family == "A_DOWNFAIL" else bars[offset].close - base
    if move < 3 * scale:
        return "SUPPORTED_NO_ONSET", None
    return "BROAD_OBSERVATION", (scale, move, move * 10000 / base)


def _onset(family: str, offset: int, bars: dict[int, MinuteBar],
           opening: _OpeningRange | None, last_end: int | None,
           series_id: str, broad: BroadObservation | None) -> tuple[str, _Candidate | None]:
    length = 3 if family == "A_DOWNFAIL" else 5 if family == "C_FIRSTPULLBACK" else 1
    baseline = offset - length
    if broad is not None and not broad.meets_minimum_past_move:
        return "below_past_move_floor", None
    if last_end is not None and (offset < last_end + RESET
                                 or baseline + 1 <= last_end):
        return "RESET", None
    status, scale = _scale(bars, baseline)
    if scale is None:
        return status, None
    if any(index not in bars for index in range(baseline + 1, offset + 1)):
        return "MISSING_IMPULSE", None
    bar = bars[offset]
    base = bars[baseline].close
    if family == "B_ORL30":
        if offset < 30:
            return "LEVEL_WARMUP", None
        if opening is None:
            return "MISSING_OPENING_RANGE", None
        level = opening.low
        if offset <= opening.known_at:
            return "LEVEL_NOT_PREKNOWN", None
        threshold = level - QUARTER * scale
        triggered = base >= threshold and bar.close < threshold
    else:
        level = min(bars[index].low for index in range(baseline + 1, offset + 1))
        move = base - bar.close if family == "A_DOWNFAIL" else bar.close - base
        triggered = move >= 3 * scale
    if not triggered:
        return "SUPPORTED_NO_ONSET", None
    minimum = min(bars[index].low for index in range(baseline + 1, offset + 1))
    peak = max(bars[index].high for index in range(baseline + 1, offset + 1))
    candidate = _Candidate(family, f"{series_id}:{family}:{offset}", offset,
                           baseline + 1, scale, level, minimum, base, peak)
    if family == "C_FIRSTPULLBACK":
        candidate.level = peak
        candidate.phase = "WAITING_PULLBACK"
    if family == "B_ORL30":
        assert opening is not None
        candidate.context = tuple(sorted({
            "level_known_at": opening.known_at,
            "opening_range_bps": opening.range_bps,
            "opening_range_meets_66bps": opening.range_bps >= MINIMUM_PAST_MOVE_BPS,
        }.items()))
    else:
        assert broad is not None
        candidate.context = tuple(sorted({
            "broad_episode_id": broad.broad_episode_id,
            "past_move_bps": broad.past_move_bps,
            "minimum_past_move_bps": MINIMUM_PAST_MOVE_BPS,
            "meets_minimum_past_move": broad.meets_minimum_past_move,
        }.items()))
    return "ONSET", candidate


def _advance(candidate: _Candidate, offset: int,
             bars: dict[int, MinuteBar]) -> tuple[str | None, dict[str, Value], bool]:
    """Return terminal state, conditions and whether first pullback began."""
    bar = bars[offset]
    age = offset - candidate.onset
    candidate.minimum = min(candidate.minimum, bar.low)
    conditions: dict[str, Value] = {"age": age}
    if candidate.family == "A_DOWNFAIL":
        failed = bar.low < candidate.level - QUARTER * candidate.scale
        retest = (age >= 3 and min(bars[offset - 2].low, bars[offset - 1].low)
                  <= candidate.level + QUARTER * candidate.scale)
        reclaimed = age >= 3 and bar.close > max(bars[offset - 2].high,
                                                bars[offset - 1].high)
        conditions.update(failed_structure=failed, prior_retest=retest,
                          reclaimed_prior_highs=reclaimed)
        terminal = ("INVALIDATED" if failed else "CONFIRMED" if retest and reclaimed
                    else "EXPIRED_NO_CONFIRMATION" if age >= 12 else None)
        return terminal, conditions, False
    if candidate.family == "B_ORL30":
        reclaimed = bar.close >= candidate.level + QUARTER * candidate.scale
        conditions["reclaimed_fixed_level"] = reclaimed
        return ("CONFIRMED" if reclaimed else "EXPIRED_NO_CONFIRMATION"
                if age >= 20 else None), conditions, False

    pullback_started = False
    if candidate.pullback_at is None:
        candidate.peak = max(candidate.peak, bar.high)
        candidate.level = candidate.peak
        if bar.close <= candidate.peak - HALF * candidate.scale:
            candidate.pullback_at = offset
            candidate.minimum = bar.low
            candidate.phase = "PULLBACK"
            pullback_started = True
        else:
            conditions.update(pullback_started=False, running_peak=candidate.peak)
            return ("EXPIRED_NO_PULLBACK" if age >= 10 else None), conditions, False
    pullback_age = offset - candidate.pullback_at
    floor = candidate.base + HALF * (candidate.peak - candidate.base)
    failed = bar.low < floor
    resumed = (pullback_age >= 2 and bar.close > max(bars[offset - 2].high,
                                                    bars[offset - 1].high))
    conditions.update(pullback_at=candidate.pullback_at, pullback_age=pullback_age,
                      retained_advance_floor=floor, failed_structure=failed,
                      reclaimed_prior_highs=resumed)
    terminal = ("INVALIDATED" if failed else "CONFIRMED" if resumed
                else "EXPIRED_NO_CONFIRMATION" if pullback_age >= 10 else None)
    return terminal, conditions, pullback_started


def _frame(candidate: _Candidate, offset: int, decision: int, series_id: str,
           kind: str, state: str, terminal: bool,
           conditions: dict[str, Value]) -> EventFrame:
    return EventFrame(candidate.family, candidate.event_id, series_id,
                      candidate.onset, offset, decision, candidate.interval_start,
                      offset + 1, kind, state, terminal, candidate.scale,
                      candidate.level, candidate.minimum,
                      tuple(sorted((dict(candidate.context) | conditions).items())))


def scan_scenarios(bars: Iterable[MinuteBar], *, end_offset: int,
                   session_length: int, series_id: str = "invented") -> ScenarioProjection:
    """Project three scenes; retain three support rows for every scheduled minute.

    ``bars`` must be a unique, ascending, unrevised prefix wholly before the
    exclusive ``end_offset`` watermark. ``session_length`` is a pre-known calendar
    boundary. Fractions remain exact; JSON conversion represents them as strings.
    Overlap groups deliberately group the whole series, never falsely certify
    separate events as independent. Per-frame causal intervals permit downstream
    overlap accounting without future-dependent renaming of an emitted event.
    """
    if (type(end_offset) is not int or type(session_length) is not int
            or not 0 <= end_offset <= session_length or session_length < 1):
        raise ValueError("Require 0 <= end_offset <= positive session_length")
    if not isinstance(series_id, str) or not series_id.strip():
        raise ValueError("series_id must identify one fixed symbol/session/version")
    supplied = tuple(bars)
    if any(not isinstance(bar, MinuteBar) for bar in supplied):
        raise TypeError("Only MinuteBar input is supported")
    offsets = [bar.offset for bar in supplied]
    if offsets != sorted(set(offsets)) or any(index >= end_offset for index in offsets):
        raise ValueError("Require unique ascending bars before the input watermark")
    by_offset = {bar.offset: bar for bar in supplied}
    active: dict[str, _Candidate] = {}
    last_end: dict[str, int] = {}
    frames: list[EventFrame] = []
    support: list[SupportRow] = []
    broad_observations: list[BroadObservation] = []
    broad_groups: dict[str, tuple[str, int]] = {}
    opening: _OpeningRange | None = None
    decision = 0
    for offset in range(end_offset):
        bar = by_offset.get(offset)
        decision = max(decision, bar.available_offset if bar else offset + LAG)
        if offset == 29 and all(index in by_offset for index in range(30)):
            low = min(by_offset[index].low for index in range(30))
            high = max(by_offset[index].high for index in range(30))
            opening = _OpeningRange(low,
                                    max(by_offset[index].available_offset for index in range(30)),
                                    (high - low) * 10000 / by_offset[0].open)
        for family in FAMILIES:
            broad: BroadObservation | None = None
            if family != "B_ORL30":
                broad_status, measurement = (_broad_measure(family, offset, by_offset)
                                              if bar is not None else ("MISSING_BAR", None))
                if measurement is not None:
                    group = broad_groups.get(family)
                    if group is None or offset - group[1] > RESET:
                        group = (f"{series_id}:{family}:broad:{offset}", offset)
                    broad_groups[family] = (group[0], offset)
                    scale, move, bps = measurement
                    broad = BroadObservation(family, offset, decision, group[0], scale,
                                             move, bps, bps >= MINIMUM_PAST_MOVE_BPS)
                    broad_observations.append(broad)
                elif broad_status != "SUPPORTED_NO_ONSET":
                    broad_groups.pop(family, None)
            candidate = active.get(family)
            terminal: str | None = None
            conditions: dict[str, Value] = {}
            changed = False
            if bar is None:
                status = "MISSING_BAR"
                if candidate is not None:
                    terminal = "UNKNOWN_GAP"
                    conditions = {"missing_offset": offset, "age": offset - candidate.onset}
            elif candidate is not None:
                status = "ACTIVE_SUPPORTED"
                terminal, conditions, changed = _advance(candidate, offset, by_offset)
            else:
                status, candidate = _onset(family, offset, by_offset, opening,
                                           last_end.get(family), series_id, broad)
                if candidate is not None:
                    active[family] = candidate
                    conditions = {"age": 0, "base_close": candidate.base,
                                  "impulse_close": bar.close}
            support_conditions: dict[str, Value] = {}
            if broad is not None:
                support_conditions = {"broad_episode_id": broad.broad_episode_id,
                                      "past_move_bps": broad.past_move_bps,
                                      "minimum_past_move_bps": MINIMUM_PAST_MOVE_BPS,
                                      "meets_minimum_past_move": broad.meets_minimum_past_move}
            support.append(SupportRow(family, offset, decision, status,
                                      candidate.event_id if candidate else None,
                                      tuple(sorted(support_conditions.items()))))
            if candidate is None:
                continue
            is_onset = offset == candidate.onset
            if is_onset:
                frames.append(_frame(candidate, offset, decision, series_id,
                                     "at_onset", candidate.phase, False, conditions))
            if terminal is None and offset == session_length - 1:
                terminal = "SESSION_END"
            if terminal is not None:
                frames.append(_frame(candidate, offset, decision, series_id,
                                     "terminal", terminal, True, conditions))
                active.pop(family)
                last_end[family] = offset
            elif not is_onset and (changed or (offset - candidate.onset) % 3 == 0):
                kind = "first_pullback" if changed else "scheduled"
                frames.append(_frame(candidate, offset, decision, series_id,
                                     kind, candidate.phase, False, conditions))
    return ScenarioProjection(tuple(frames), tuple(support), tuple(broad_observations))
