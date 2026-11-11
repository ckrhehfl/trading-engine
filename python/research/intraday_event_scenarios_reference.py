"""Independent EL scene reconstruction from scheduled raw minute prefixes.

Shared production objects are serialization/input types and the declared design
contract only. Each family searches for its next onset, then reconstructs each
bounded episode prefix from scratch until the first terminal. No producer
detector, scale helper, candidate state or emitted value is an expected value.
The broad minute census is rebuilt separately from event admission and resets.
"""

from dataclasses import dataclass
from fractions import Fraction
from typing import Iterable

from research.intraday_event_scenarios import (
    CONTRACT, BroadObservation, EventFrame, MinuteBar, ScenarioProjection,
    SupportRow, Value,
)


FAMILIES = ("A_DOWNFAIL", "B_ORL30", "C_FIRSTPULLBACK")
LENGTHS = (CONTRACT["a"]["impulse_minutes"], 1, CONTRACT["c"]["impulse_minutes"])


def _history(rows: dict[int, MinuteBar], before: int) -> tuple[str, Fraction | None]:
    count = CONTRACT["scale_bars"]
    if before < count:
        return "WARMUP", None
    sequence = [rows.get(index) for index in range(before - count, before + 1)]
    if None in sequence:
        return "MISSING_SCALE_HISTORY", None
    # The range spans today's high/low and yesterday's completed close. This
    # equivalent span formula does not reuse the producer's max-of-three TR.
    observations = sorted(max(right.high, left.close) - min(right.low, left.close)
                          for left, right in zip(sequence, sequence[1:]))
    middle = count // 2
    scale = (observations[middle - 1] + observations[middle]) / 2
    return ("SUPPORTED", scale) if scale else ("ZERO_SCALE", None)


def _measure(rows, family, offset, length):
    status, scale = _history(rows, offset - length)
    if scale is None:
        return status, None
    recent = [rows.get(index) for index in range(offset - length + 1, offset + 1)]
    if None in recent:
        return "MISSING_IMPULSE", None
    origin = rows[offset - length].close
    displacement = origin - recent[-1].close if family == FAMILIES[0] else recent[-1].close - origin
    if displacement < 3 * scale:
        return "SUPPORTED_NO_ONSET", None
    return "BROAD_OBSERVATION", (scale, displacement, displacement * 10000 / origin)


def _broad(rows, clocks, family, length, series_id):
    """Group broad hits using previous-hit distance and intervening unknowns."""
    result, previous_hit, unknown_through, anchor = {}, None, -1, None
    for offset in range(len(clocks)):
        status, values = (_measure(rows, family, offset, length)
                          if offset in rows else ("MISSING_BAR", None))
        if values is None:
            if status != "SUPPORTED_NO_ONSET":
                unknown_through = offset
            continue
        if (previous_hit is None or unknown_through > previous_hit
                or offset - previous_hit > CONTRACT["reset_minutes"]):
            anchor = offset
        scale, move, bps = values
        result[offset] = BroadObservation(family, offset, clocks[offset],
            f"{series_id}:{family}:broad:{anchor}", scale, move, bps,
            bps >= CONTRACT["minimum_past_move_bps"])
        previous_hit = offset
    return result


def _broad_conditions(observation):
    if observation is None:
        return {}
    return {"broad_episode_id": observation.broad_episode_id,
            "past_move_bps": observation.past_move_bps,
            "minimum_past_move_bps": CONTRACT["minimum_past_move_bps"],
            "meets_minimum_past_move": observation.meets_minimum_past_move}


@dataclass(frozen=True)
class _Episode:
    family: str
    onset: int
    start: int
    scale: Fraction
    level: Fraction
    base: Fraction
    peak: Fraction
    context: tuple[tuple[str, Value], ...]


def _start(rows, family, length, offset, ended, opening, broad):
    observation = broad.get(offset)
    if observation is not None and not observation.meets_minimum_past_move:
        return "below_past_move_floor", None
    baseline = offset - length
    if ended is not None and (offset - ended < CONTRACT["reset_minutes"] or baseline < ended):
        return "RESET", None
    status, scale = _history(rows, baseline)
    if scale is None:
        return status, None
    recent = [rows.get(index) for index in range(baseline + 1, offset + 1)]
    if None in recent:
        return "MISSING_IMPULSE", None
    base = rows[baseline].close
    if family == FAMILIES[1]:
        if offset < CONTRACT["b"]["opening_minutes"]:
            return "LEVEL_WARMUP", None
        if opening is None:
            return "MISSING_OPENING_RANGE", None
        level, known, width = opening
        if offset <= known:
            return "LEVEL_NOT_PREKNOWN", None
        threshold = level - scale / 4
        onset = rows[offset].close < threshold <= base
        context = {"level_known_at": known, "opening_range_bps": width,
                   "opening_range_meets_66bps": width >= CONTRACT["minimum_past_move_bps"]}
    else:
        onset = observation is not None
        level = min(bar.low for bar in recent)
        context = _broad_conditions(observation)
    if not onset:
        return "SUPPORTED_NO_ONSET", None
    peak = max(bar.high for bar in recent)
    if family == FAMILIES[2]:
        level = peak
    return "ONSET", _Episode(family, offset, baseline + 1, scale, level, base,
                              peak, tuple(sorted(context.items())))


def _snapshot(episode, offset, rows):
    """Reduce an episode's raw prefix, without carrying mutable prior state."""
    present = offset in rows
    through = offset if present else offset - 1
    level = episode.level
    structure = min(rows[index].low for index in range(episode.start, through + 1))
    phase = "WAITING_PULLBACK" if episode.family == FAMILIES[2] else "WAITING"
    pullback, peak = None, episode.peak
    if episode.family == FAMILIES[2]:
        # Locate the FIRST pullback directly over the bounded observed prefix;
        # after that location the peak remains fixed, even if highs rise again.
        for index in range(episode.onset + 1, through + 1):
            running_high = max([episode.peak, *(rows[j].high for j in range(episode.onset + 1, index + 1))])
            if rows[index].close <= running_high - episode.scale / 2:
                pullback, peak = index, running_high
                break
        if pullback is None:
            peak = max([episode.peak, *(rows[j].high for j in range(episode.onset + 1, through + 1))])
        else:
            structure = min(rows[j].low for j in range(pullback, through + 1))
            phase = "PULLBACK"
        level = peak
    age = offset - episode.onset
    if not present:
        return phase, level, structure, "UNKNOWN_GAP", {"missing_offset": offset, "age": age}, False
    if offset == episode.onset:
        return phase, level, structure, None, {"age": 0, "base_close": episode.base,
                                                "impulse_close": rows[offset].close}, False
    conditions = {"age": age}
    if episode.family == FAMILIES[0]:
        failed = rows[offset].low < episode.level - episode.scale / 4
        retested = age >= CONTRACT["a"]["confirm_age_inclusive"][0] and any(
            rows[j].low <= episode.level + episode.scale / 4 for j in (offset - 2, offset - 1))
        reclaimed = age >= CONTRACT["a"]["confirm_age_inclusive"][0] and all(
            rows[offset].close > rows[j].high for j in (offset - 2, offset - 1))
        conditions.update(failed_structure=failed, prior_retest=retested, reclaimed_prior_highs=reclaimed)
        terminal = ("INVALIDATED" if failed else "CONFIRMED" if retested and reclaimed else
                    "EXPIRED_NO_CONFIRMATION" if age >= CONTRACT["a"]["confirm_age_inclusive"][1] else None)
        return phase, level, structure, terminal, conditions, False
    if episode.family == FAMILIES[1]:
        reclaimed = rows[offset].close >= episode.level + episode.scale / 4
        conditions["reclaimed_fixed_level"] = reclaimed
        terminal = ("CONFIRMED" if reclaimed else "EXPIRED_NO_CONFIRMATION"
                    if age >= CONTRACT["b"]["last_age"] else None)
        return phase, level, structure, terminal, conditions, False
    if pullback is None:
        conditions.update(pullback_started=False, running_peak=peak)
        terminal = "EXPIRED_NO_PULLBACK" if age >= CONTRACT["c"]["last_pullback_age"] else None
        return phase, level, structure, terminal, conditions, False
    pullback_age = offset - pullback
    retained = (episode.base + peak) / 2
    failed = rows[offset].low < retained
    resumed = pullback_age >= CONTRACT["c"]["resume_age_inclusive"][0] and all(
        rows[offset].close > rows[j].high for j in (offset - 2, offset - 1))
    conditions.update(pullback_at=pullback, pullback_age=pullback_age,
                      retained_advance_floor=retained, failed_structure=failed,
                      reclaimed_prior_highs=resumed)
    terminal = ("INVALIDATED" if failed else "CONFIRMED" if resumed else "EXPIRED_NO_CONFIRMATION"
                if pullback_age >= CONTRACT["c"]["resume_age_inclusive"][1] else None)
    return phase, level, structure, terminal, conditions, pullback == offset


def _family(rows, clocks, family, length, opening, broad, session_length, series_id):
    support, frames, offset, ended = [], [], 0, None
    while offset < len(clocks):
        if offset not in rows:
            status, episode = "MISSING_BAR", None
        else:
            status, episode = _start(rows, family, length, offset, ended, opening, broad)
        if episode is None:
            support.append(SupportRow(family, offset, clocks[offset], status, None,
                                      tuple(sorted(_broad_conditions(broad.get(offset)).items()))))
            offset += 1
            continue
        event_id = f"{series_id}:{family}:{offset}"
        # Search the bounded episode until its FIRST terminal, then resume onset
        # search. Every emitted snapshot is independently reduced from raw bars.
        while offset < len(clocks):
            phase, level, structure, terminal, conditions, first_pullback = _snapshot(episode, offset, rows)
            support_status = ("ONSET" if offset == episode.onset else
                              "ACTIVE_SUPPORTED" if offset in rows else "MISSING_BAR")
            support.append(SupportRow(family, offset, clocks[offset], support_status, event_id,
                                      tuple(sorted(_broad_conditions(broad.get(offset)).items()))))

            def frame(kind, state, is_terminal):
                return EventFrame(family, event_id, series_id, episode.onset, offset, clocks[offset],
                    episode.start, offset + 1, kind, state, is_terminal, episode.scale,
                    level, structure, tuple(sorted((dict(episode.context) | conditions).items())))

            at_onset = offset == episode.onset
            if at_onset:
                frames.append(frame("at_onset", phase, False))
            if terminal is None and offset + 1 == session_length:
                terminal = "SESSION_END"
            if terminal is not None:
                frames.append(frame("terminal", terminal, True))
                ended = offset
                offset += 1
                break
            if not at_onset and (first_pullback or (offset - episode.onset) % 3 == 0):
                frames.append(frame("first_pullback" if first_pullback else "scheduled", phase, False))
            offset += 1
    return support, frames


def reference_scan(bars: Iterable[MinuteBar], *, end_offset: int, session_length: int,
                   series_id: str = "invented") -> ScenarioProjection:
    """Rebuild all event frames, support rows and broad minute observations."""
    if (type(end_offset) is not int or type(session_length) is not int
            or session_length <= 0 or not 0 <= end_offset <= session_length):
        raise ValueError("EL reference requires a bounded scheduled prefix")
    if not isinstance(series_id, str) or not series_id.strip():
        raise ValueError("EL reference requires a fixed series identity")
    supplied = tuple(bars)
    if any(not isinstance(bar, MinuteBar) for bar in supplied):
        raise TypeError("EL reference accepts only MinuteBar values")
    offsets = [bar.offset for bar in supplied]
    if offsets != sorted(set(offsets)) or any(offset >= end_offset for offset in offsets):
        raise ValueError("EL reference requires unique ordered bars before the watermark")
    rows = {bar.offset: bar for bar in supplied}
    arrivals = [rows[i].available_offset if i in rows else i + CONTRACT["missing_deadline_lag_minutes"]
                for i in range(end_offset)]
    clocks = []
    for arrival in arrivals:
        clocks.append(max(arrival, clocks[-1] if clocks else 0))
    opening_count = CONTRACT["b"]["opening_minutes"]
    opening_rows = [rows.get(i) for i in range(opening_count)]
    opening = None
    if None not in opening_rows:
        low, high = min(bar.low for bar in opening_rows), max(bar.high for bar in opening_rows)
        opening = low, max(bar.available_offset for bar in opening_rows), (high - low) * 10000 / opening_rows[0].open
    frames, support, observations = [], [], []
    for family, length in zip(FAMILIES, LENGTHS):
        broad = _broad(rows, clocks, family, length, series_id) if family != FAMILIES[1] else {}
        family_support, family_frames = _family(rows, clocks, family, length, opening, broad, session_length, series_id)
        frames.extend(family_frames)
        support.extend(family_support)
        observations.extend(broad.values())
    order = lambda row: (row.offset, FAMILIES.index(row.family))
    return ScenarioProjection(tuple(sorted(frames, key=order)), tuple(sorted(support, key=order)),
                              tuple(sorted(observations, key=order)))


def verify(projection: ScenarioProjection, bars: Iterable[MinuteBar], *, end_offset: int,
           session_length: int, series_id: str = "invented") -> None:
    """Refuse any changed field, stream ordering, omitted row or extra event."""
    if type(projection) is not ScenarioProjection:
        raise TypeError("EL verifier requires a complete ScenarioProjection")
    expected = reference_scan(bars, end_offset=end_offset, session_length=session_length, series_id=series_id)
    for name in ("frames", "support", "broad_observations"):
        if getattr(projection, name) != getattr(expected, name):
            raise ValueError(f"EL independent {name} reconstruction differs")
