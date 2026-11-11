"""Independent EL chart/peer feature and information-clock reconstruction.

Only immutable input types and declared feature names/dimensions are shared
with production. Raw receipt-time observations supply every expected value;
no producer feature, normalization, availability or grouping helper is called.
"""

from fractions import Fraction
from itertools import groupby
from statistics import median
from typing import Iterable, Mapping

from research.intraday_event_features import CHANNELS, STATIC, STEPS, VolumeObservation
from research.intraday_event_scenarios import EventFrame, MinuteBar


def _price_view(bars: Mapping[int, MinuteBar], decision: int) -> dict[int, MinuteBar]:
    admitted = {}
    for offset, bar in bars.items():
        if offset < 0 or offset >= decision or bar is None:
            continue
        if not isinstance(bar, MinuteBar) or bar.offset != offset:
            raise ValueError("EL reference bar coordinate differs")
        if bar.available_offset <= decision:
            admitted[offset] = bar
    return admitted


def _volume_values(volumes, offsets: Iterable[int], decision: int):
    values = []
    for offset in offsets:
        observation = volumes.get(offset) if offset >= 0 else None
        if observation is None:
            values.append(None)
        else:
            if not isinstance(observation, VolumeObservation) or observation.offset != offset:
                raise ValueError("EL reference volume coordinate differs")
            values.append(observation.value if observation.available_offset <= decision else None)
    return values


def _positive_median(values):
    if len(values) != 20 or any(value is None for value in values):
        return None
    center = median(values)
    return center if center > 0 else None


def _quotient(numerator, denominator):
    return numerator / denominator if denominator else None


def _text(value):
    return str(Fraction(value)) if value is not None else None


def chart_features(frame: EventFrame, bars: Mapping[int, MinuteBar], *,
                   session_length: int, volumes=None):
    """Reconstruct 64 scheduled price/volume rows and the 16 static inputs."""
    if (not isinstance(frame, EventFrame) or type(session_length) is not int
            or not 0 <= frame.onset_offset <= frame.offset < frame.decision_offset < session_length
            or not isinstance(frame.scale, Fraction) or frame.scale <= 0
            or frame.level <= 0 or frame.structure <= 0):
        raise ValueError("EL reference causal feature geometry differs")
    prices = _price_view(bars, frame.decision_offset)
    if not prices:
        return None
    volumes = {} if volumes is None else volumes
    last = max(prices)
    origin, scale = prices[last].close, frame.scale
    sequence = []
    # Build by named channels, then serialize in the public column order. This
    # avoids treating producer array positions as feature values or masks.
    for index in range(last + 1 - STEPS, last + 1):
        current, previous = prices.get(index), prices.get(index - 1)
        values = dict.fromkeys(CHANNELS)
        values.update(price_known=int(current is not None), previous_known=int(previous is not None),
                      pre_session=int(index < 0))
        if current is not None:
            for name in ("open", "high", "low", "close"):
                values[name + "_distance"] = (getattr(current, name) - origin) / scale
            values.update(body=(current.close - current.open) / scale,
                          range=(current.high - current.low) / scale,
                          close_change=None if previous is None else (current.close - previous.close) / scale)
        volume = _volume_values(volumes, (index,), frame.decision_offset)[0]
        baseline = (_positive_median(_volume_values(volumes, range(index - 20, index), frame.decision_offset))
                    if index >= 20 else None)
        values.update(reported_volume_ratio=None if volume is None or baseline is None else volume / baseline,
                      volume_known=int(volume is not None), volume_baseline_known=int(baseline is not None))
        sequence.append([values[name] for name in CHANNELS])
    static = {"clock": Fraction(frame.decision_offset, session_length),
              "event_age": Fraction(frame.offset - frame.onset_offset),
              "scale_fraction": scale / origin, "level_distance": (origin - frame.level) / scale,
              "structure_distance": (origin - frame.structure) / scale,
              "reference_age": Fraction(frame.decision_offset - last),
              "known_fraction": Fraction(len(set(prices).intersection(range(last + 1 - STEPS, last + 1))), STEPS)}
    complete = {}
    for width in (5, 15):
        keys = tuple(range(last - width, last + 1))
        complete[width] = tuple(prices[i] for i in keys) if all(i in prices for i in keys) else None
        selected = complete[width]
        static["net" + str(width)] = (None if selected is None else (origin - selected[0].close) / scale)
        static["range" + str(width)] = (None if selected is None else
            (max(bar.high for bar in selected[1:]) - min(bar.low for bar in selected[1:])) / scale)
    recent15 = complete[15]
    if recent15 is None:
        static.update(efficiency15=None, position15=None, above_level_fraction15=None)
    else:
        changes = [recent15[i + 1].close - recent15[i].close for i in range(15)]
        lower = min(bar.low for bar in recent15[1:])
        upper = max(bar.high for bar in recent15[1:])
        static.update(efficiency15=_quotient(abs(sum(changes, Fraction())), sum(map(abs, changes), Fraction())),
                      position15=_quotient(origin - lower, upper - lower),
                      above_level_fraction15=Fraction(len([bar for bar in recent15[1:] if bar.close >= frame.level]), 15))
    recent5 = complete[5]
    static["compression5"] = (None if recent5 is None else
        sum((max(right.high, left.close) - min(right.low, left.close)
             for left, right in zip(recent5, recent5[1:])), Fraction()) / (5 * scale))
    baseline = (_positive_median(_volume_values(volumes, range(last - 24, last - 4), frame.decision_offset))
                if last >= 24 else None)
    recent_volumes = _volume_values(volumes, range(max(0, last - 4), last + 1), frame.decision_offset)
    static["volume5_vs_prior20"] = (sum(recent_volumes, Fraction()) / (5 * baseline)
        if baseline is not None and len(recent_volumes) == 5 and all(v is not None for v in recent_volumes) else None)
    return {"schema": "EL-causal-chart-features-v1", "family": frame.family, "state": frame.state,
        "event_id": frame.event_id, "decision_offset": frame.decision_offset,
        "reference_offset": last, "reference_close": str(origin),
        "volume_semantics": "within-session reported numeric ratios; physical share units unverified",
        "sequence_names": list(CHANNELS), "sequence": [[_text(value) for value in row] for row in sequence],
        "static_names": list(STATIC), "static": [_text(static[name]) for name in STATIC]}


def peer_features(symbol, peers, *, decision_offset: int, quorum: int = 5):
    """Rebuild complete same-clock observed peers, with exact median returns."""
    if type(quorum) is not int or quorum < 1 or type(decision_offset) is not int:
        raise ValueError("EL reference peer support contract differs")
    last = decision_offset - 3
    intervals, names = [], []
    for name in sorted(peers):
        if name == symbol or last < 15:
            continue
        selected = []
        for offset in range(last - 15, last + 1):
            bar = peers[name].get(offset)
            if bar is not None and (not isinstance(bar, MinuteBar) or bar.offset != offset):
                raise ValueError("EL reference bar coordinate differs")
            selected.append(bar if bar is not None and bar.available_offset <= decision_offset else None)
        if any(bar is None for bar in selected):
            continue
        close = selected[-1].close
        intervals.append((close / selected[10].close - 1, close / selected[0].close - 1))
        names.append(name)
    values = None
    if len(names) >= quorum:
        values = {"median5": str(median(row[0] for row in intervals)),
                  "median15": str(median(row[1] for row in intervals)),
                  "positive15_fraction": str(Fraction(len([row for row in intervals if row[1] > 0]), len(names)))}
    return {"observed": len(names), "quorum": quorum, "reference_offset": last,
            "members": names, "values": values,
            "scope": "observed original peers; not a market-wide breadth statistic"}


def coalesce_frames(frames):
    """Group full histories, reject every conflicting rank, then choose latest."""
    supplied = tuple(frames)
    if any(not isinstance(frame, EventFrame) for frame in supplied):
        raise TypeError("EL reference frame required")
    identity = lambda frame: (frame.decision_offset, frame.overlap_group_id, frame.family)
    rank = lambda frame: (frame.offset, frame.onset_offset, frame.terminal,
                          frame.kind == "terminal", frame.kind == "first_pullback")
    selected = []
    for _, history in groupby(sorted(supplied, key=identity), key=identity):
        ordered = sorted(history, key=rank)
        for _, versions in groupby(ordered, key=rank):
            values = tuple(versions)
            if any(frame != values[0] for frame in values[1:]):
                raise ValueError("EL reference conflicting states at the same information timestamp")
        selected.append(ordered[-1])
    return tuple(selected)


def verify_chart_features(result, frame, bars, *, session_length, volumes=None):
    expected = chart_features(frame, bars, session_length=session_length, volumes=volumes)
    if result != expected:
        raise ValueError("EL independent chart features differ")


def verify_peer_features(result, symbol, peers, *, decision_offset, quorum=5):
    expected = peer_features(symbol, peers, decision_offset=decision_offset, quorum=quorum)
    if result != expected:
        raise ValueError("EL independent peer features differ")


def verify_coalesced_frames(result, frames):
    if result != coalesce_frames(frames):
        raise ValueError("EL independent coalesced frames differ")
