"""New EL causal chart inputs; no loader, labels, fitted state or order path.

Fractions survive serialization. Conversion to a numerical learner belongs to
the statistical boundary. Missing observations have explicit flags, never a
fabricated flat candle. The caller seals these rows before any payoff targets.
"""

from fractions import Fraction
from dataclasses import dataclass
from typing import Mapping

from research.intraday_event_scenarios import EventFrame, MinuteBar


STEPS = 64
CHANNELS = ("open_distance", "high_distance", "low_distance", "close_distance",
            "body", "range", "close_change", "price_known", "previous_known", "pre_session",
            "reported_volume_ratio", "volume_known", "volume_baseline_known")
STATIC = ("clock", "event_age", "scale_fraction", "level_distance", "structure_distance",
          "reference_age", "known_fraction", "net5", "net15", "range5", "range15",
          "efficiency15", "compression5", "position15", "above_level_fraction15", "volume5_vs_prior20")


@dataclass(frozen=True)
class VolumeObservation:
    offset: int
    value: Fraction
    available_offset: int

    def __post_init__(self):
        if (type(self.offset) is not int or self.offset < 0 or type(self.available_offset) is not int
                or self.available_offset < self.offset + 1 or not isinstance(self.value, (Fraction, int))
                or isinstance(self.value, bool) or self.value < 0):
            raise ValueError("Exact nonnegative reported volume and causal clock required")
        object.__setattr__(self, "value", Fraction(self.value))


def _volume(volumes, offset, decision):
    value = volumes.get(offset)
    if value is None:
        return None
    if not isinstance(value, VolumeObservation) or value.offset != offset:
        raise ValueError("EL volume coordinate differs")
    return value.value if value.available_offset <= decision else None


def _volume_baseline(volumes, end, decision):
    if end < 19:
        return None
    values = [_volume(volumes, i, decision) for i in range(end - 19, end + 1)]
    if any(value is None for value in values):
        return None
    values.sort()
    median = (values[9] + values[10]) / 2
    return median if median > 0 else None


def coalesce_frames(frames):
    """One latest known state per family and information timestamp.

    At equal receipt time, retain the latest event-time update, including a
    terminal state. This does not itself permit entry or label waiting as zero.
    An old confirmed episode is not replayed as another trade at the same clock.
    """
    grouped, seen = {}, {}
    for frame in frames:
        if not isinstance(frame, EventFrame):
            raise TypeError("EL frame required")
        key = frame.overlap_group_id, frame.family, frame.decision_offset
        rank = (frame.offset, frame.onset_offset, frame.terminal,
                frame.kind == "terminal", frame.kind == "first_pullback")
        identity = key, rank
        if identity in seen and frame != seen[identity]:
            raise ValueError("Conflicting states at the same information timestamp")
        seen[identity] = frame
        old = grouped.get(key)
        if old is None or rank > old[0]:
            grouped[key] = rank, frame
    return tuple(value[1] for key, value in sorted(grouped.items(), key=lambda item: (item[0][2], item[0][0], item[0][1])))


def _known(bars: Mapping[int, MinuteBar], offset: int, decision: int):
    bar = bars.get(offset)
    if bar is None:
        return None
    if not isinstance(bar, MinuteBar) or bar.offset != offset:
        raise ValueError("EL bar coordinate differs")
    return bar if bar.available_offset <= decision else None


def _complete(bars, start, end, decision):
    if start < 0:
        return None
    selected = tuple(_known(bars, offset, decision) for offset in range(start, end + 1))
    return selected if all(bar is not None for bar in selected) else None


def _ratio(value, denominator):
    return None if denominator == 0 else value / denominator


def chart_features(frame: EventFrame, bars: Mapping[int, MinuteBar], *, session_length: int, volumes=None):
    """Build a receipt-time prefix, independently of later session coverage.

    If a delayed event update arrives, other observations already received at
    that clock may enter its chart. No late bar is backdated. Unknown future
    label support and full-session census masks are not accepted as inputs.
    """
    if (not isinstance(frame, EventFrame) or type(session_length) is not int
            or not 0 <= frame.onset_offset <= frame.offset < frame.decision_offset < session_length
            or not isinstance(frame.scale, Fraction) or frame.scale <= 0
            or frame.level <= 0 or frame.structure <= 0):
        raise ValueError("EL causal feature geometry differs")
    volumes = {} if volumes is None else volumes
    candidates = [offset for offset in bars if 0 <= offset < frame.decision_offset
                  and _known(bars, offset, frame.decision_offset) is not None]
    if not candidates:
        return None
    last = max(candidates)
    reference = bars[last].close
    rows = []
    for offset in range(last - STEPS + 1, last + 1):
        bar = _known(bars, offset, frame.decision_offset) if offset >= 0 else None
        previous = _known(bars, offset - 1, frame.decision_offset) if offset > 0 else None
        if bar is None:
            rows.append([None] * 7 + [0, int(previous is not None), int(offset < 0)])
        else:
            rows.append([(getattr(bar, field) - reference) / frame.scale
                         for field in ("open", "high", "low", "close")]
                        + [(bar.close - bar.open) / frame.scale,
                           (bar.high - bar.low) / frame.scale,
                           None if previous is None else (bar.close - previous.close) / frame.scale,
                           1, int(previous is not None), 0])
        volume = _volume(volumes, offset, frame.decision_offset) if offset >= 0 else None
        baseline = _volume_baseline(volumes, offset - 1, frame.decision_offset) if offset >= 0 else None
        rows[-1].extend([None if volume is None or baseline is None else volume / baseline,
                         int(volume is not None), int(baseline is not None)])
    spans = {width: _complete(bars, last - width, last, frame.decision_offset) for width in (5, 15)}
    stats = {}
    for width, selected in spans.items():
        stats["net" + str(width)] = None if selected is None else (selected[-1].close - selected[0].close) / frame.scale
        stats["range" + str(width)] = None if selected is None else (max(b.high for b in selected[1:]) - min(b.low for b in selected[1:])) / frame.scale
    recent = spans[15]
    if recent is None:
        stats.update(dict.fromkeys(("efficiency15", "position15", "above_level_fraction15")))
    else:
        variation = sum((abs(b.close - a.close) for a, b in zip(recent, recent[1:])), Fraction())
        low, high = min(b.low for b in recent[1:]), max(b.high for b in recent[1:])
        stats.update(efficiency15=_ratio(abs(recent[-1].close - recent[0].close), variation),
                     position15=_ratio(recent[-1].close - low, high - low),
                     above_level_fraction15=Fraction(sum(b.close >= frame.level for b in recent[1:]), 15))
    recent5 = spans[5]
    stats["compression5"] = None if recent5 is None else sum((max(b.high - b.low, abs(b.high - a.close), abs(b.low - a.close))
        for a, b in zip(recent5, recent5[1:])), Fraction()) / (5 * frame.scale)
    volume_base = _volume_baseline(volumes, last - 5, frame.decision_offset)
    volume5 = [_volume(volumes, i, frame.decision_offset) for i in range(max(0, last - 4), last + 1)]
    stats["volume5_vs_prior20"] = (sum(volume5, Fraction()) / (5 * volume_base)
        if volume_base is not None and len(volume5) == 5 and all(v is not None for v in volume5) else None)
    static = {"clock": Fraction(frame.decision_offset, session_length),
        "event_age": Fraction(frame.offset - frame.onset_offset), "scale_fraction": frame.scale / reference,
        "level_distance": (reference - frame.level) / frame.scale,
        "structure_distance": (reference - frame.structure) / frame.scale,
        "reference_age": Fraction(frame.decision_offset - last),
        "known_fraction": Fraction(sum(row[7] for row in rows), STEPS), **stats}
    encode = lambda value: None if value is None else str(Fraction(value))
    return {"schema": "EL-causal-chart-features-v1", "family": frame.family, "state": frame.state,
        "event_id": frame.event_id, "decision_offset": frame.decision_offset,
        "reference_offset": last, "reference_close": str(reference),
        "volume_semantics": "within-session reported numeric ratios; physical share units unverified",
        "sequence_names": list(CHANNELS), "sequence": [[encode(value) for value in row] for row in rows],
        "static_names": list(STATIC), "static": [encode(static[name]) for name in STATIC]}


def peer_features(symbol, peers, *, decision_offset: int, quorum: int = 5):
    """Same scheduled close endpoints, excluding self; observed peers only.

    This is not a complete-market index. Missing peers never become zero return.
    The same-price clocks use the registered minute-start+3 availability model.
    """
    if type(quorum) is not int or quorum < 1 or type(decision_offset) is not int:
        raise ValueError("EL peer support contract differs")
    last = decision_offset - 3
    observed = []
    for name, bars in sorted(peers.items()):
        if name == symbol:
            continue
        selected = _complete(bars, last - 15, last, decision_offset)
        if selected is None:
            continue
        observed.append((name, selected[-1].close / selected[-6].close - 1,
                         selected[-1].close / selected[0].close - 1))
    value = None
    if len(observed) >= quorum:
        medians = []
        for column in (1, 2):
            ordered = sorted(row[column] for row in observed)
            n = len(ordered)
            medians.append((ordered[(n - 1) // 2] + ordered[n // 2]) / 2)
        value = {"median5": str(medians[0]), "median15": str(medians[1]),
                 "positive15_fraction": str(Fraction(sum(row[2] > 0 for row in observed), len(observed)))}
    return {"observed": len(observed), "quorum": quorum, "reference_offset": last,
            "members": [row[0] for row in observed], "values": value,
            "scope": "observed original peers; not a market-wide breadth statistic"}
