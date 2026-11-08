"""Prospective EI questions from causal context frames, without future access.

This draft is separate from the support census and grants no actual-run
authority. Stages share an anchor and episode but retain distinct question
identities. A question, including its economic priority, never implies a fill.
"""

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_situations import (
    ContextFrame, ContextMetrics, ContextMinute, OHLC, ResistanceAnchor, WindowSupport,
)
from research.intraday_ed_inputs import MINUTE, STATES
from research.intraday_scenario_labels import PathQuestion, QuestionId as PathQuestionId


SCHEME_ID = "EI-common-approach-breakout-draft-v1"
FIRST_ENDPOINT_MINUTES = 30
BAR_MINUTES = 5
AVAILABILITY_LAG_MINUTES = 2
BAND_IN_R = Fraction(1, 4)
STOP_BELOW_LEVEL_IN_R = Fraction(1)
TARGET_ABOVE_LEVEL_IN_R = Fraction(2)
QUESTION_HORIZON_MINUTES = 30
ECONOMIC_STAGE_PRIORITY = ("breakout", "approach")

Stage = Literal["approach", "breakout"]
AnchorId = tuple[str, ResistanceAnchor]
EpisodeId = tuple[AnchorId, int]
QuestionId = tuple[EpisodeId, Stage, PathQuestionId]


@dataclass(frozen=True)
class ContextQuestion:
    episode_id: EpisodeId
    stage: Stage
    path: PathQuestion
    start_close: Fraction
    episode_origin_context: Literal["A", "B", "other"]
    episode_origin_at: datetime
    question_time_context: Literal["A", "B", "other"]
    phase: str
    unsupported_reasons: tuple[str, ...]
    economic_priority: bool
    overlapping_question_ids: tuple[QuestionId, ...]

    @property
    def question_id(self) -> QuestionId:
        return self.episode_id, self.stage, self.path.question_id

    @property
    def anchor_id(self) -> AnchorId:
        return self.episode_id[0]


@dataclass(frozen=True)
class QuestionFrame:
    symbol: str
    opening: datetime
    available_at: datetime
    context: str
    status: Literal["no_anchor", "unknown", "invalidated", "no_event", "questions"]
    reason: str
    episode_id: EpisodeId | None
    questions: tuple[ContextQuestion, ...] = ()


def _bar(rows: tuple[ContextMinute, ...]) -> OHLC:
    _need(len(rows) == BAR_MINUTES and all(row.price_state == "observed" and row.prices is not None
                                         for row in rows), "EI_question_bar_requires_observed_prices")
    return rows[0].prices[0], max(row.prices[1] for row in rows), min(row.prices[2] for row in rows), rows[-1].prices[3]


class _PrefixValidation:
    """Validate unchanged past tuples in C; inspect only new or recovered rows."""

    def __init__(self) -> None:
        self.counts: Counter[str] = Counter()
        self.on_grid = True

    def validate(self, frame: ContextFrame, previous: ContextFrame | None, elapsed: int) -> None:
        _need(type(frame.prefix) is tuple and len(frame.prefix) == elapsed,
              "EI_question_prefix_length_differs")
        start = 0 if previous is None else len(previous.prefix)
        changed = []
        if previous is not None and frame.prefix[:start] != previous.prefix:
            for index, (old, row) in enumerate(zip(previous.prefix, frame.prefix)):
                if old != row:
                    _need(isinstance(row, ContextMinute) and old.price_state == old.volume_state == "late"
                          and old.available_at is None and row.available_at is not None,
                          "EI_question_observed_past_prefix_changed")
                    _clock(row.available_at)
                    _need(row.available_at > previous.available_at, "EI_question_observed_past_prefix_changed")
                    changed.append((index, row))
                    self.counts[old.price_state] -= 1
                    if self.counts[old.price_state] == 0:
                        del self.counts[old.price_state]
        for index, row in (*changed, *enumerate(frame.prefix[start:], start)):
            at = frame.opening + index * MINUTE
            _need(isinstance(row, ContextMinute) and row.start_at == at, "EI_question_prefix_grid_differs")
            _need(type(row.price_state) is str and row.price_state in STATES | {"late"},
                  "EI_question_invalid_price_state")
            self.counts[row.price_state] += 1
            if row.available_at is not None:
                _clock(row.available_at)
                _need(at + (1 + AVAILABILITY_LAG_MINUTES) * MINUTE <= row.available_at <= frame.available_at,
                      "EI_question_price_availability_differs")
            if row.price_state == "observed":
                prices = row.prices
                _need(row.available_at is not None and type(prices) is tuple and len(prices) == 4
                      and all(type(value) is Fraction and value > 0 for value in prices),
                      "EI_question_exact_observed_prices_required")
                op, high, low, close = prices
                _need(low <= min(op, close) <= max(op, close) <= high, "EI_question_invalid_ohlc_order")
            else:
                _need(row.prices is None, "EI_question_unknown_price_has_payload")
            if row.price_state == "late":
                _need(row.available_at is None, "EI_question_late_payload_is_not_visible")
            deadline = frame.opening + (max(FIRST_ENDPOINT_MINUTES, (index // BAR_MINUTES + 1)
                                           * BAR_MINUTES) + AVAILABILITY_LAG_MINUTES) * MINUTE
            self.on_grid = self.on_grid and row.price_state == "observed" and row.available_at <= deadline
        support = frame.prefix_support
        _need(isinstance(support, WindowSupport) and support.minutes == support.in_session_minutes == elapsed
              and support.price_counts == tuple(sorted(self.counts.items())), "EI_question_prefix_support_differs")
        _need(type(frame.prefix_observed_on_grid) is bool and frame.prefix_observed_on_grid == self.on_grid,
              "EI_question_observed_grid_metadata_differs")
        _need((frame.classification == "unknown") == (not self.on_grid), "EI_question_unknown_status_differs")


def _validate_frame(frame: ContextFrame, previous: ContextFrame | None, closing: datetime,
                    prefix: _PrefixValidation) -> None:
    _need(isinstance(frame, ContextFrame), "EI_question_typed_context_frame_required")
    for at in (frame.opening, frame.observation_end, frame.available_at):
        _clock(at)
    _need(type(frame.symbol) is str and bool(frame.symbol), "EI_question_invalid_symbol")
    elapsed = (frame.observation_end - frame.opening) // MINUTE
    _need(elapsed >= FIRST_ENDPOINT_MINUTES and elapsed % BAR_MINUTES == 0
          and frame.observation_end == frame.opening + elapsed * MINUTE
          and frame.available_at == frame.observation_end + AVAILABILITY_LAG_MINUTES * MINUTE
          and frame.opening < frame.available_at < closing
          and frame.opening.date() == closing.date(), "EI_question_invalid_session_grid")
    expected = (frame.opening + (FIRST_ENDPOINT_MINUTES + AVAILABILITY_LAG_MINUTES) * MINUTE
                if previous is None else previous.available_at + BAR_MINUTES * MINUTE)
    _need(frame.available_at == expected, "EI_question_requires_complete_ordered_grid_from_E30")
    _need(previous is None or (frame.symbol, frame.opening) == (previous.symbol, previous.opening),
          "EI_question_session_identity_changed")
    _need(frame.classification in {"A", "B", "other", "unclassified", "unknown"},
          "EI_question_invalid_context")
    prefix.validate(frame, previous, elapsed)
    if frame.anchor is None:
        _need(frame.metrics is None and frame.classification in {"unclassified", "unknown"},
              "EI_question_anchor_context_differs")
        return
    anchor = frame.anchor
    _need(isinstance(anchor, ResistanceAnchor) and isinstance(frame.metrics, ContextMetrics)
          and frame.classification in {"A", "B", "other"}, "EI_question_anchor_context_differs")
    for value in (anchor.start_at, anchor.peak_end, anchor.confirmed_at):
        _clock(value)
    for value in (anchor.start_price, anchor.price, anchor.pre_peak_range, anchor.advance, anchor.band):
        _need(type(value) is Fraction, "EI_question_exact_anchor_required")
    peak_minutes = (anchor.peak_end - frame.opening) // MINUTE
    _need(anchor.start_at == frame.opening and anchor.start_price == frame.prefix[0].prices[0]
          and peak_minutes >= 4 * BAR_MINUTES and peak_minutes % BAR_MINUTES == 0
          and anchor.peak_end == frame.opening + peak_minutes * MINUTE
          and anchor.peak_end + BAR_MINUTES * MINUTE <= frame.observation_end
          and anchor.confirmed_at == frame.opening + (max(FIRST_ENDPOINT_MINUTES, peak_minutes + BAR_MINUTES)
                                                      + AVAILABILITY_LAG_MINUTES) * MINUTE
          and anchor.confirmed_at <= frame.available_at and anchor.pre_peak_range > 0
          and anchor.advance == anchor.price - anchor.start_price
          and anchor.band == BAND_IN_R * anchor.pre_peak_range, "EI_question_anchor_geometry_differs")
    if frame.metrics.invalidated_at is not None:
        _clock(frame.metrics.invalidated_at)
        _need(anchor.confirmed_at <= frame.metrics.invalidated_at <= frame.available_at,
              "EI_question_invalidation_clock_differs")


def _question(frame: ContextFrame, episode_id: EpisodeId, stage: Stage, close: Fraction,
              stages: list[Stage], earlier: tuple[QuestionId, ...], closing: datetime,
              origin: ContextFrame) -> ContextQuestion:
    anchor = episode_id[0][1]
    width = anchor.pre_peak_range
    path = PathQuestion(frame.available_at, frame.available_at + QUESTION_HORIZON_MINUTES * MINUTE,
                        anchor.price, BAND_IN_R * width, anchor.price + TARGET_ABOVE_LEVEL_IN_R * width,
                        anchor.price - STOP_BELOW_LEVEL_IN_R * width)
    reasons = () if path.stop > 0 and path.stop < close < path.target else ("unsupported_price_geometry",)
    if path.end_at > closing:
        reasons += ("outside_session",)
    priority = next(item for item in ECONOMIC_STAGE_PRIORITY if item in stages)
    overlapping = earlier + tuple((episode_id, other, path.question_id) for other in stages if other != stage)
    return ContextQuestion(episode_id, stage, path, close, origin.classification, origin.available_at,
                           frame.classification, frame.phase,
                           reasons, stage == priority, overlapping)


def scan_context_questions(frames: Iterable[ContextFrame], *, closing: datetime) -> Iterator[QuestionFrame]:
    """Consume a complete ordered E30+ frame prefix, emitting every input clock.

    A wholly below-band completed bar arms the next overlapping bar. That bar
    opens a new episode; a close crossing above the upper band can create one
    breakout per episode, including episode zero. Same-bar approach/breakout
    are two questions, with breakout economic priority and no intrabar order.
    Priority applies only at that clock, not as permission for a second entry.

    Episode-origin context is frozen when the anchor first becomes known for
    episode zero, and at each later approach for its new episode. Question-time
    context is separately frozen at each question clock. Neither is relabelled
    from later frames; a future comparison must register which context it uses.

    Earlier unexpired questions remain explicit overlap dependencies. Their
    presence does not inhibit a new episode and their absence never establishes
    statistical independence. No path label, payoff or future-data reader is
    accepted. Unsupported questions keep their original absolute expiry.
    """
    _clock(closing)
    previous = None
    anchor = None
    episode_id = None
    episode_origin = None
    episode_number = 0
    armed = broken = history_unknown = False
    invalidated_at = None
    prefix = _PrefixValidation()
    active: list[ContextQuestion] = []
    for frame in frames:
        _validate_frame(frame, previous, closing, prefix)
        previous = frame
        active = [question for question in active if question.path.end_at > frame.available_at]
        if frame.classification == "unknown":
            history_unknown = True
        questions: tuple[ContextQuestion, ...] = ()
        if history_unknown:
            status, reason = "unknown", "required_history_unknown"
        elif frame.anchor is None:
            _need(anchor is None, "EI_question_fixed_anchor_disappeared")
            status, reason = "no_anchor", frame.reason
        else:
            if anchor is None:
                anchor = frame.anchor
                _need(anchor.confirmed_at == frame.available_at, "EI_question_anchor_identified_late")
                episode_id = ((frame.symbol, anchor), episode_number)
                episode_origin = frame
                # At the first E32 decision the confirming right bar may already
                # precede the current bar. It may arm, never itself be a retest.
                right_end = (anchor.peak_end - frame.opening) // MINUTE + BAR_MINUTES
                if right_end < len(frame.prefix):
                    armed = _bar(frame.prefix[right_end - BAR_MINUTES:right_end])[1] < anchor.price - anchor.band
            _need(frame.anchor == anchor, "EI_question_fixed_anchor_changed")
            if invalidated_at is not None:
                _need(frame.metrics.invalidated_at == invalidated_at, "EI_question_anchor_invalidation_repaired")
            invalidated_at = frame.metrics.invalidated_at
            if invalidated_at is not None:
                status, reason = "invalidated", "anchor_invalidated"
            else:
                _, high, low, close = _bar(frame.prefix[-BAR_MINUTES:])
                prior_close = _bar(frame.prefix[-2 * BAR_MINUTES:-BAR_MINUTES])[3]
                lower, upper = anchor.price - anchor.band, anchor.price + anchor.band
                stages: list[Stage] = []
                if armed and low <= upper and high >= lower:
                    episode_number += 1
                    episode_id = ((frame.symbol, anchor), episode_number)
                    episode_origin = frame
                    armed, broken = False, False
                    stages.append("approach")
                if not broken and prior_close <= upper < close:
                    stages.append("breakout")
                    broken = True
                if high < lower:
                    armed = True
                if stages:
                    earlier = tuple(question.question_id for question in active)
                    questions = tuple(_question(frame, episode_id, stage, close, stages, earlier, closing, episode_origin)
                                      for stage in stages)
                    active.extend(questions)
                status, reason = ("questions", "common_observed_event") if questions else ("no_event", "no_common_event")
        yield QuestionFrame(frame.symbol, frame.opening, frame.available_at, frame.classification,
                            status, reason, episode_id, questions)
