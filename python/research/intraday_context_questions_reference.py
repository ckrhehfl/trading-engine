"""Independent reconstruction of EI's causal approach and breakout questions.

The caller supplies the independent context-frame stream. No producer scanner,
producer helper, future label, input archive or economic outcome is consulted.
Only immutable record types and the absolute path-question type are shared.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from itertools import zip_longest

from research.intraday_context_questions import ContextQuestion, QuestionFrame
from research.intraday_context_situations import ContextFrame, ContextMinute, ResistanceAnchor
from research.intraday_ed_inputs import InputRefusal
from research.intraday_scenario_labels import PathQuestion


_MINUTE = timedelta(minutes=1)
_PRICE_STATES = {"observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown", "late"}


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_question_reference_" + reason)


def _clock(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None
             and value.second == value.microsecond == 0, "invalid_minute_clock")


def _ohlc(rows: tuple[ContextMinute, ...]) -> tuple[Fraction, Fraction, Fraction, Fraction]:
    _require(len(rows) == 5 and all(row.price_state == "observed" and row.prices is not None for row in rows),
             "completed_five_minutes_required")
    prices = tuple(row.prices for row in rows)
    return prices[0][0], max(row[1] for row in prices), min(row[2] for row in prices), prices[-1][3]


class _FrameAudit:
    """Check each source observation once and retain original-deadline failures."""

    def __init__(self, closing: datetime) -> None:
        _clock(closing)
        self.closing = closing
        self.previous: ContextFrame | None = None
        self.failed_original_deadline = False
        self.counts: Counter[str] = Counter()

    def accept(self, frame: ContextFrame) -> None:
        _require(isinstance(frame, ContextFrame), "typed_frame_required")
        for at in (frame.opening, frame.observation_end, frame.available_at):
            _clock(at)
        _require(type(frame.symbol) is str and bool(frame.symbol), "invalid_symbol")
        elapsed = (frame.observation_end - frame.opening) // _MINUTE
        _require(elapsed >= 30 and elapsed % 5 == 0
                 and frame.observation_end == frame.opening + elapsed * _MINUTE
                 and frame.available_at == frame.observation_end + 2 * _MINUTE
                 and frame.opening.date() == self.closing.date() and frame.available_at < self.closing,
                 "invalid_session_grid")
        prior = self.previous
        _require(frame.available_at == (frame.opening + 32 * _MINUTE if prior is None
                                        else prior.available_at + 5 * _MINUTE), "missing_duplicate_or_reordered_clock")
        _require(prior is None or (frame.symbol, frame.opening) == (prior.symbol, prior.opening),
                 "changed_session_identity")
        _require(type(frame.prefix) is tuple and len(frame.prefix) == elapsed, "prefix_length")
        start = 0 if prior is None else len(prior.prefix)
        changed = []
        if prior is not None and frame.prefix[:start] != prior.prefix:
            for index in range(start):
                old, new = prior.prefix[index], frame.prefix[index]
                if old == new:
                    continue
                _require(isinstance(new, ContextMinute) and old.price_state == old.volume_state == "late"
                         and old.available_at is None and new.available_at is not None, "changed_observed_past")
                _clock(new.available_at)
                _require(new.available_at > prior.available_at, "backdated_recovered_observation")
                self.counts[old.price_state] -= 1
                if not self.counts[old.price_state]:
                    del self.counts[old.price_state]
                changed.append((index, new))
        for index, row in (*changed, *enumerate(frame.prefix[start:], start)):
            _require(isinstance(row, ContextMinute) and row.start_at == frame.opening + index * _MINUTE,
                     "minute_identity")
            _require(type(row.price_state) is str and row.price_state in _PRICE_STATES, "price_state")
            if row.available_at is not None:
                _clock(row.available_at)
                _require(row.start_at + 3 * _MINUTE <= row.available_at <= frame.available_at, "availability")
            if row.price_state == "observed":
                values = row.prices
                _require(row.available_at is not None and type(values) is tuple and len(values) == 4
                         and all(type(value) is Fraction and value > 0 for value in values), "exact_prices")
                _require(values[2] <= values[0] <= values[1] and values[2] <= values[3] <= values[1], "ohlc_order")
            else:
                _require(row.prices is None, "unknown_price_payload")
                if row.price_state == "late":
                    _require(row.available_at is None, "late_payload_visibility")
            deadline = frame.opening + (max(30, 5 * (index // 5 + 1)) + 2) * _MINUTE
            self.failed_original_deadline |= row.price_state != "observed" or row.available_at > deadline
            self.counts[row.price_state] += 1
        support = frame.prefix_support
        _require(support.minutes == support.in_session_minutes == elapsed
                 and support.price_counts == tuple(sorted(self.counts.items())), "prefix_support")
        _require(type(frame.prefix_observed_on_grid) is bool
                 and frame.prefix_observed_on_grid is not self.failed_original_deadline, "original_deadline_metadata")
        _require(frame.classification in {"A", "B", "other", "unclassified", "unknown"}
                 and (frame.classification == "unknown") == self.failed_original_deadline, "context_support_state")
        if frame.anchor is None:
            _require(frame.metrics is None and frame.classification in {"unclassified", "unknown"}, "absent_anchor")
        else:
            anchor = frame.anchor
            _require(isinstance(anchor, ResistanceAnchor) and frame.metrics is not None
                     and frame.classification in {"A", "B", "other"}, "anchor_context")
            for at in (anchor.start_at, anchor.peak_end, anchor.confirmed_at):
                _clock(at)
            width = anchor.pre_peak_range
            peak_minutes = (anchor.peak_end - frame.opening) // _MINUTE
            _require(all(type(value) is Fraction for value in
                         (anchor.start_price, anchor.price, width, anchor.advance, anchor.band))
                     and width > 0 and anchor.start_at == frame.opening
                     and anchor.start_price == frame.prefix[0].prices[0]
                     and anchor.advance == anchor.price - anchor.start_price and anchor.band == width / 4
                     and peak_minutes >= 20 and peak_minutes % 5 == 0
                     and anchor.peak_end == frame.opening + peak_minutes * _MINUTE
                     and anchor.peak_end + 5 * _MINUTE <= frame.observation_end
                     and anchor.confirmed_at == frame.opening + (max(30, peak_minutes + 5) + 2) * _MINUTE
                     and anchor.confirmed_at <= frame.available_at, "anchor_geometry")
            if frame.metrics.invalidated_at is not None:
                _clock(frame.metrics.invalidated_at)
                _require(anchor.confirmed_at <= frame.metrics.invalidated_at <= frame.available_at,
                         "invalidation_clock")
        self.previous = frame


def reference_question_frames(frames: Iterable[ContextFrame], *, closing: datetime) -> Iterator[QuestionFrame]:
    """Reconstruct every clock from independently reconstructed context frames.

    A below-band bar can be consumed once by a later touch. Episode zero starts
    when the fixed anchor becomes known. Every new touch after a below-band bar
    creates a new episode, and each episode can contain only its first closing
    crossing. Prior questions remain independent fixed identities, even when
    another episode begins before their expiry. No inventory claim follows.
    """
    audit = _FrameAudit(closing)
    fixed_anchor = episode = origin = None
    episode_number = 0
    below_bar_end = consumed_below_end = None
    crossed_episodes: set[int] = set()
    invalidated = None
    history: list[ContextQuestion] = []
    for frame in frames:
        audit.accept(frame)
        found: tuple[ContextQuestion, ...] = ()
        if audit.failed_original_deadline:
            status, reason = "unknown", "required_history_unknown"
        elif frame.anchor is None:
            _require(fixed_anchor is None, "anchor_disappeared")
            status, reason = "no_anchor", frame.reason
        else:
            if fixed_anchor is None:
                fixed_anchor = frame.anchor
                _require(fixed_anchor.confirmed_at == frame.available_at, "late_anchor_identity")
                episode, origin = ((frame.symbol, fixed_anchor), 0), frame
                right = (fixed_anchor.peak_end - frame.opening) // _MINUTE + 5
                if right < len(frame.prefix) and _ohlc(frame.prefix[right - 5:right])[1] < fixed_anchor.price - fixed_anchor.band:
                    below_bar_end = frame.opening + right * _MINUTE
            _require(frame.anchor == fixed_anchor, "fixed_anchor_changed")
            if invalidated is not None:
                _require(frame.metrics.invalidated_at == invalidated, "invalidation_repaired")
            invalidated = frame.metrics.invalidated_at
            if invalidated is not None:
                status, reason = "invalidated", "anchor_invalidated"
            else:
                _, high, low, close = _ohlc(frame.prefix[-5:])
                previous_close = frame.prefix[-6].prices[3]
                lower, upper = fixed_anchor.price - fixed_anchor.band, fixed_anchor.price + fixed_anchor.band
                stages = []
                if below_bar_end is not None and below_bar_end != consumed_below_end and low <= upper and high >= lower:
                    episode_number += 1
                    episode, origin = ((frame.symbol, fixed_anchor), episode_number), frame
                    consumed_below_end = below_bar_end
                    stages.append("approach")
                if episode_number not in crossed_episodes and previous_close <= upper < close:
                    crossed_episodes.add(episode_number)
                    stages.append("breakout")
                if high < lower:
                    below_bar_end = frame.observation_end
                if stages:
                    width = fixed_anchor.pre_peak_range
                    path = PathQuestion(frame.available_at, frame.available_at + 30 * _MINUTE,
                                        fixed_anchor.price, width / 4, fixed_anchor.price + 2 * width,
                                        fixed_anchor.price - width)
                    reasons = tuple(reason for valid, reason in (
                        (path.stop > 0 and path.stop < close < path.target, "unsupported_price_geometry"),
                        (path.end_at <= closing, "outside_session")) if not valid)
                    prior_ids = tuple(item.question_id for item in history if item.path.end_at > frame.available_at)
                    found = tuple(ContextQuestion(episode, stage, path, close, origin.classification,
                        origin.available_at, frame.classification, frame.phase, reasons,
                        stage == ("breakout" if "breakout" in stages else "approach"),
                        prior_ids + tuple((episode, other, path.question_id) for other in stages if other != stage))
                        for stage in stages)
                    history.extend(found)
                status, reason = ("questions", "common_observed_event") if found else ("no_event", "no_common_event")
        yield QuestionFrame(frame.symbol, frame.opening, frame.available_at, frame.classification,
                            status, reason, episode, found)


def _same(expected, actual) -> bool:
    if type(expected) is not type(actual):
        return False
    if is_dataclass(expected):
        return all(_same(getattr(expected, field.name), getattr(actual, field.name)) for field in fields(expected))
    if isinstance(expected, tuple):
        return len(expected) == len(actual) and all(_same(left, right) for left, right in zip(expected, actual, strict=True))
    return expected == actual


def verify_question_frames(frames: Iterable[ContextFrame], produced: Iterable[QuestionFrame], *, closing: datetime) -> None:
    """Compare the entire supplied prefix, including no-event and unknown clocks."""
    missing = object()
    for expected, actual in zip_longest(reference_question_frames(frames, closing=closing), produced, fillvalue=missing):
        _require(expected is not missing and actual is not missing, "trace_length_differs")
        _require(type(actual) is QuestionFrame and _same(expected, actual), "frame_or_question_differs")
