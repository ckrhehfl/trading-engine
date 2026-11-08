"""Prospective EI chart projection, with no labels, fitting or input access.

The context extension is a representation of the same observed chart, not a
new external information source. Relative volume describes stored units only;
the source does not certify physical volume units or historical delivery times.
"""

from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction
from math import isfinite
from typing import Literal

from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_questions import ContextQuestion, QuestionId
from research.intraday_context_situations import ContextFrame, ContextMetrics, ContextMinute, ResistanceAnchor
from research.intraday_ed_inputs import MINUTE, STATES


SCHEMA = "ei-chart-context-features-draft-v1"
HISTORY_MINUTES = 30
MINUTE_NAMES = ("open_in_R", "high_in_R", "low_in_R", "close_in_R",
                "relative_stored_volume", "volume_observed", "volume_scale_positive")
COMMON_NAMES = ("breakout_stage", "elapsed_session_minutes", "band_in_R",
                "target_distance_from_close_in_R", "stop_distance_from_close_in_R",
                "question_expiry_minutes", "close_from_level_in_R")
CONTEXT_NAMES = ("origin_A", "origin_B", "origin_other", "current_A", "current_B", "current_other",
                 "advance_in_R", "pullback_depth_over_advance", "bars_since_peak", "retest_episodes")
CONTEXTS = ("A", "B", "other")
ExactSequence = tuple[tuple[Fraction, Fraction, Fraction, Fraction], ...]


@dataclass(frozen=True)
class NumericContextFeatures:
    question_id: QuestionId
    forecast_at: datetime
    mode: Literal["chart", "chart_context"]
    minute: tuple[tuple[float, ...], ...]
    static: tuple[float, ...]

    @property
    def static_names(self) -> tuple[str, ...]:
        return COMMON_NAMES + (CONTEXT_NAMES if self.mode == "chart_context" else ())

    @property
    def feature_names(self) -> tuple[str, ...]:
        return tuple(f"minute_{index:02d}_{name}" for index in range(HISTORY_MINUTES)
                     for name in MINUTE_NAMES) + self.static_names

    @property
    def flat(self) -> tuple[float, ...]:
        """The exact same numeric view flattened for a tree model."""
        return tuple(value for row in self.minute for value in row) + self.static


@dataclass(frozen=True)
class ContextFeatures:
    question: ContextQuestion
    forecast_at: datetime
    observation_end: datetime
    window_start: datetime
    status: Literal["ready", "unsupported"]
    reasons: tuple[str, ...]
    minute_available_at: tuple[datetime | None, ...]
    price_states: tuple[str, ...]
    volume_states: tuple[str, ...]
    prices_in_R: ExactSequence | None
    stored_volumes: tuple[Fraction | None, ...]
    volume_scale: Fraction | None
    common: tuple[Fraction, ...] | None
    context: tuple[Fraction, ...] | None

    @property
    def question_id(self) -> QuestionId:
        return self.question.question_id

    def numeric_view(self, *, with_context: bool) -> NumericContextFeatures:
        """Encode absent volume with a mask, never as an observed zero.

        The volume denominator is the mean of observed values in the available
        thirty-minute window at this question's clock. It is not a historical
        per-minute indicator computed at each earlier candle's clock.
        """
        _need(type(with_context) is bool and self.status == "ready" and not self.reasons
              and self.prices_in_R is not None and self.common is not None and self.context is not None,
              "EI_ready_features_required")
        _need(len(self.prices_in_R) == len(self.stored_volumes) == len(self.volume_states) == HISTORY_MINUTES
              and len(self.common) == len(COMMON_NAMES) and len(self.context) == len(CONTEXT_NAMES),
              "EI_feature_shape_differs")
        scale_positive = self.volume_scale is not None and self.volume_scale > 0
        rows = []
        for prices, volume, state in zip(self.prices_in_R, self.stored_volumes, self.volume_states, strict=True):
            observed = state == "observed"
            _need(observed == (volume is not None), "EI_feature_volume_mask_differs")
            ratio = volume / self.volume_scale if observed and scale_positive else Fraction(0)
            rows.append(tuple(_finite(value) for value in (*prices, ratio, Fraction(observed),
                                                           Fraction(scale_positive))))
        static = self.common + (self.context if with_context else ())
        return NumericContextFeatures(self.question_id, self.forecast_at,
            "chart_context" if with_context else "chart", tuple(rows), tuple(_finite(value) for value in static))


def _finite(value: Fraction) -> float:
    _need(type(value) is Fraction, "EI_exact_feature_required")
    try:
        encoded = float(value)
    except OverflowError as exc:
        raise ValueError("EI_feature_exceeds_finite_numeric_range") from exc
    _need(isfinite(encoded), "EI_feature_exceeds_finite_numeric_range")
    return encoded


def _prefix(frame: ContextFrame) -> tuple[ContextMinute, ...]:
    for at in (frame.opening, frame.observation_end, frame.available_at):
        _clock(at)
        _need(at.second == at.microsecond == 0, "EI_feature_clock_off_minute_grid")
    elapsed = (frame.observation_end - frame.opening) // MINUTE
    _need(elapsed >= 0 and frame.observation_end == frame.opening + elapsed * MINUTE
          and frame.available_at == frame.observation_end + 2 * MINUTE
          and type(frame.prefix) is tuple and len(frame.prefix) == elapsed,
          "EI_feature_prefix_clock_or_length_differs")
    on_grid = True
    for index, row in enumerate(frame.prefix):
        _need(isinstance(row, ContextMinute) and row.start_at == frame.opening + index * MINUTE,
              "EI_feature_prefix_identity_differs")
        if row.available_at is not None:
            _clock(row.available_at)
            _need(row.start_at + 3 * MINUTE <= row.available_at <= frame.available_at,
                  "EI_feature_prefix_availability_differs")
        for state, value, kind in ((row.price_state, row.prices, "price"),
                                   (row.volume_state, row.volume, "volume")):
            _need(type(state) is str and state in STATES | {"late"}, "EI_feature_observation_state_differs")
            _need(state == "observed" or value is None, "EI_feature_unknown_has_payload")
            if state == "observed":
                _need(row.available_at is not None, "EI_feature_observed_without_availability")
                if kind == "price":
                    _need(type(value) is tuple and len(value) == 4
                          and all(type(price) is Fraction and price > 0 for price in value),
                          "EI_feature_exact_prices_required")
                    op, high, low, close = value
                    _need(low <= min(op, close) <= max(op, close) <= high, "EI_feature_ohlc_order_differs")
                else:
                    _need(type(value) is Fraction and value >= 0, "EI_feature_exact_volume_required")
            if state == "late":
                _need(row.available_at is None, "EI_feature_late_payload_not_redacted")
        deadline = frame.opening + (max(30, (index // 5 + 1) * 5) + 2) * MINUTE
        on_grid = on_grid and row.price_state == "observed" and row.available_at <= deadline
    _need(type(frame.prefix_observed_on_grid) is bool and frame.prefix_observed_on_grid == on_grid,
          "EI_feature_original_grid_metadata_differs")
    return frame.prefix[-HISTORY_MINUTES:]


def _identity(frame: ContextFrame, question: ContextQuestion) -> ResistanceAnchor:
    _need(isinstance(frame, ContextFrame) and isinstance(question, ContextQuestion),
          "EI_typed_context_features_required")
    _need(type(frame.symbol) is str and bool(frame.symbol) and question.anchor_id[0] == frame.symbol
          and question.path.start_at == frame.available_at, "EI_feature_question_identity_differs")
    anchor = question.anchor_id[1]
    _need(isinstance(anchor, ResistanceAnchor) and frame.anchor == anchor
          and isinstance(frame.metrics, ContextMetrics), "EI_feature_anchor_identity_differs")
    _need(all(type(value) is Fraction for value in (anchor.start_price, anchor.price, anchor.pre_peak_range,
                                                   anchor.advance, anchor.band, question.start_close)),
          "EI_feature_exact_anchor_required")
    for at in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, question.episode_origin_at):
        _clock(at)
    _need(anchor.start_at == frame.opening and anchor.pre_peak_range > 0 and anchor.start_price > 0
          and anchor.advance == anchor.price - anchor.start_price > 0
          and anchor.start_at < anchor.peak_end < anchor.confirmed_at <= question.episode_origin_at
          <= frame.available_at and question.stage in ("approach", "breakout")
          and question.episode_origin_context in CONTEXTS and question.question_time_context in CONTEXTS
          and question.path.level == anchor.price and question.path.band == anchor.band,
          "EI_feature_anchor_or_context_differs")
    return anchor


def project_context_features(frame: ContextFrame, question: ContextQuestion) -> ContextFeatures:
    """Project only this causal frame; preserve support separately from labels.

    A ready feature row is not a supported future label or tradable opportunity.
    In particular, an outside-session thirty-minute question retains its
    features so a supported five/ fifteen-minute child is not silently removed.
    """
    anchor = _identity(frame, question)
    rows = _prefix(frame)
    reasons = []
    if len(rows) < HISTORY_MINUTES:
        reasons.append("not_yet_30_in_session_minutes")
    if any(row.price_state != "observed" for row in frame.prefix):
        reasons.append("context_price_prefix_unknown")
    if any(row.price_state != "observed" for row in rows):
        reasons.append("recent_price_window_unknown")
    if not frame.prefix_observed_on_grid:
        reasons.append("context_prefix_not_observed_on_original_grid")
    observed_volume = tuple(row.volume for row in rows if row.volume_state == "observed")
    scale = sum(observed_volume, Fraction(0)) / len(observed_volume) if observed_volume else None
    prices = common = context = None
    if not reasons:
        _need(frame.prefix[-1].prices[3] == question.start_close
              and question.question_time_context == frame.classification and question.phase == frame.phase,
              "EI_feature_question_state_differs")
        metrics = frame.metrics
        for at in (metrics.first_pullback_at, metrics.resumption_at):
            if at is not None:
                _clock(at)
                _need(anchor.confirmed_at <= at <= frame.available_at, "EI_feature_context_clock_differs")
        _need(type(metrics.advance_in_ranges) is Fraction and type(metrics.pullback_depth) is Fraction
              and metrics.advance_in_ranges == anchor.advance / anchor.pre_peak_range
              and metrics.pullback_depth >= 0 and type(metrics.bars_since_peak) is int
              and metrics.bars_since_peak >= 0 and type(metrics.retest_episodes) is int
              and metrics.retest_episodes >= 0 and metrics.invalidated_at is None,
              "EI_feature_context_metrics_differ")
        width, close = anchor.pre_peak_range, question.start_close
        prices = tuple(tuple((value - anchor.price) / width for value in row.prices) for row in rows)
        common = (Fraction(question.stage == "breakout"),
                  Fraction((frame.available_at - frame.opening) // MINUTE), anchor.band / width,
                  (question.path.target - close) / width, (close - question.path.stop) / width,
                  Fraction((question.path.end_at - question.path.start_at) // MINUTE),
                  (close - anchor.price) / width)
        context = (*tuple(Fraction(question.episode_origin_context == name) for name in CONTEXTS),
                   *tuple(Fraction(question.question_time_context == name) for name in CONTEXTS),
                   metrics.advance_in_ranges, metrics.pullback_depth,
                   Fraction(metrics.bars_since_peak), Fraction(metrics.retest_episodes))
    return ContextFeatures(question, frame.available_at, frame.observation_end,
        frame.observation_end - HISTORY_MINUTES * MINUTE, "unsupported" if reasons else "ready", tuple(reasons),
        tuple(row.available_at for row in rows), tuple(row.price_state for row in rows),
        tuple(row.volume_state for row in rows), prices, tuple(row.volume for row in rows), scale, common, context)
