"""Independent exact and numeric EI feature reconstruction.

Call with the independent context and question streams. The implementation
shares only immutable feature records and field names with the producer; it
does not invoke its projection, conversion helpers or numeric-view method.
No input archive, future label or fitted model is read here.
"""

from dataclasses import fields, is_dataclass
from datetime import datetime, timedelta
from fractions import Fraction
from math import isfinite

from research.intraday_context_features import (
    COMMON_NAMES, CONTEXT_NAMES, MINUTE_NAMES, ContextFeatures, NumericContextFeatures,
)
from research.intraday_context_questions import ContextQuestion
from research.intraday_context_situations import ContextFrame, ContextMinute, ResistanceAnchor
from research.intraday_context_support_reference import _classify
from research.intraday_ed_inputs import InputRefusal


_MINUTE = timedelta(minutes=1)
_STATES = {"observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown", "late"}
_CONTEXTS = ("A", "B", "other")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_feature_reference_" + reason)


def _clock(value: datetime) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None
             and value.second == value.microsecond == 0, "minute_clock")


def _same(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_same(getattr(left, field.name), getattr(right, field.name)) for field in fields(left))
    if isinstance(left, tuple):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _question_identity(frame: ContextFrame, question: ContextQuestion) -> ResistanceAnchor:
    _require(type(frame) is ContextFrame and type(question) is ContextQuestion, "typed_frame_question")
    episode = question.episode_id
    _require(type(episode) is tuple and len(episode) == 2 and type(episode[1]) is int and episode[1] >= 0
             and type(episode[0]) is tuple and len(episode[0]) == 2, "episode_identity")
    symbol, anchor = episode[0]
    _require(type(symbol) is str and symbol and symbol == frame.symbol and type(anchor) is ResistanceAnchor
             and _same(anchor, frame.anchor) and frame.metrics is not None, "anchor_identity")
    _require(question.stage in ("approach", "breakout") and type(question.economic_priority) is bool
             and question.episode_origin_context in _CONTEXTS and question.question_time_context in _CONTEXTS,
             "question_context")
    for at in (anchor.start_at, anchor.peak_end, anchor.confirmed_at, question.episode_origin_at):
        _clock(at)
    _require(all(type(value) is Fraction for value in (anchor.start_price, anchor.price, anchor.pre_peak_range,
                anchor.advance, anchor.band, question.start_close)), "exact_anchor_prices")
    _require(anchor.start_at == frame.opening and anchor.start_price > 0 and anchor.pre_peak_range > 0
             and anchor.advance == anchor.price - anchor.start_price > 0
             and anchor.start_at < anchor.peak_end < anchor.confirmed_at <= question.episode_origin_at
             <= frame.available_at and question.path.start_at == frame.available_at
             and question.path.level == anchor.price and question.path.band == anchor.band, "question_clock_geometry")
    return anchor


def _visible_rows(frame: ContextFrame) -> tuple[ContextMinute, ...]:
    for at in (frame.opening, frame.observation_end, frame.available_at):
        _clock(at)
    count = (frame.observation_end - frame.opening) // _MINUTE
    _require(count >= 0 and frame.observation_end == frame.opening + count * _MINUTE
             and frame.available_at == frame.observation_end + 2 * _MINUTE
             and type(frame.prefix) is tuple and len(frame.prefix) == count, "prefix_clock_or_length")
    first_attempt_known = True
    for index, row in enumerate(frame.prefix):
        _require(type(row) is ContextMinute and row.start_at == frame.opening + index * _MINUTE, "minute_identity")
        if row.available_at is not None:
            _clock(row.available_at)
            _require(row.start_at + 3 * _MINUTE <= row.available_at <= frame.available_at, "minute_availability")
        _require(type(row.price_state) is str and row.price_state in _STATES
                 and type(row.volume_state) is str and row.volume_state in _STATES, "observation_state")
        if row.price_state == "observed":
            prices = row.prices
            _require(row.available_at is not None and type(prices) is tuple and len(prices) == 4
                     and all(type(value) is Fraction and value > 0 for value in prices), "exact_ohlc")
            _require(prices[2] <= prices[0] <= prices[1] and prices[2] <= prices[3] <= prices[1], "ohlc_order")
        else:
            _require(row.prices is None, "unobserved_price_payload")
        if row.volume_state == "observed":
            _require(row.available_at is not None and type(row.volume) is Fraction and row.volume >= 0,
                     "exact_stored_volume")
        else:
            _require(row.volume is None, "unobserved_volume_payload")
        if "late" in (row.price_state, row.volume_state):
            _require(row.available_at is None, "late_row_is_visible")
        original_due = frame.opening + (max(30, 5 * (1 + index // 5)) + 2) * _MINUTE
        if row.price_state != "observed" or row.available_at > original_due:
            first_attempt_known = False
    _require(type(frame.prefix_observed_on_grid) is bool
             and frame.prefix_observed_on_grid is first_attempt_known, "original_deadline_metadata")
    return frame.prefix[max(0, len(frame.prefix) - 30):]


def reference_features(frame: ContextFrame, question: ContextQuestion) -> ContextFeatures:
    """Rebuild exact values before any lossy floating-point conversion.

    The volume scale is the observed-only arithmetic mean at this question's
    clock, including actual zeros. Missing volume cannot enter its denominator.
    An unsupported future horizon does not remove usable present features.
    """
    anchor = _question_identity(frame, question)
    rows = _visible_rows(frame)
    reasons = tuple(reason for condition, reason in (
        (len(rows) < 30, "not_yet_30_in_session_minutes"),
        (any(row.price_state != "observed" for row in frame.prefix), "context_price_prefix_unknown"),
        (any(row.price_state != "observed" for row in rows), "recent_price_window_unknown"),
        (not frame.prefix_observed_on_grid, "context_prefix_not_observed_on_original_grid"),
    ) if condition)
    volume_sum, volume_count = Fraction(0), 0
    for row in rows:
        if row.volume_state == "observed":
            volume_sum += row.volume
            volume_count += 1
    scale = volume_sum / volume_count if volume_count else None
    exact_prices = common = context = None
    if not reasons:
        _require(frame.observation_end > frame.opening and len(frame.prefix) % 5 == 0, "completed_bar_grid")
        expected_anchor, metrics, classification, phase, _ = _classify(frame.prefix, frame.opening)
        _require(_same(expected_anchor, anchor) and _same(metrics, frame.metrics)
                 and classification == frame.classification == question.question_time_context
                 and phase == frame.phase == question.phase, "independent_context_state")
        close, width = rows[-1].prices[3], anchor.pre_peak_range
        _require(close == question.start_close and metrics.invalidated_at is None, "question_price_state")
        exact_prices = tuple(tuple((price - anchor.price) / width for price in row.prices) for row in rows)
        common = (
            Fraction(1 if question.stage == "breakout" else 0),
            (frame.available_at - frame.opening) // _MINUTE,
            anchor.band / width,
            (question.path.target - close) / width,
            (close - question.path.stop) / width,
            (question.path.end_at - question.path.start_at) // _MINUTE,
            (close - anchor.price) / width,
        )
        common = tuple(Fraction(value) for value in common)
        context = tuple(Fraction(value) for value in (
            *(question.episode_origin_context == name for name in _CONTEXTS),
            *(question.question_time_context == name for name in _CONTEXTS),
            (anchor.price - anchor.start_price) / width,
            metrics.pullback_depth, metrics.bars_since_peak, metrics.retest_episodes,
        ))
    return ContextFeatures(question, frame.available_at, frame.observation_end,
        frame.observation_end - 30 * _MINUTE, "unsupported" if reasons else "ready", reasons,
        tuple(row.available_at for row in rows), tuple(row.price_state for row in rows),
        tuple(row.volume_state for row in rows), exact_prices, tuple(row.volume for row in rows), scale, common, context)


def _number(value: Fraction) -> float:
    _require(type(value) is Fraction, "exact_numeric_input")
    try:
        converted = float(value)
    except OverflowError as exc:
        raise InputRefusal("EI_feature_reference_nonfinite_numeric") from exc
    _require(isfinite(converted), "nonfinite_numeric")
    return converted


def reference_numeric_view(features: ContextFeatures, *, with_context: bool) -> NumericContextFeatures:
    """Encode masks and a float64 view without using the producer method."""
    _require(type(features) is ContextFeatures and type(with_context) is bool
             and features.status == "ready" and features.reasons == ()
             and type(features.prices_in_R) is tuple and len(features.prices_in_R) == 30
             and len(features.stored_volumes) == len(features.volume_states) == 30
             and type(features.common) is tuple and len(features.common) == len(COMMON_NAMES)
             and type(features.context) is tuple and len(features.context) == len(CONTEXT_NAMES), "ready_feature_shape")
    values = tuple(value for value in features.stored_volumes if value is not None)
    _require(all(type(value) is Fraction and value >= 0 for value in values), "stored_volume_numeric_type")
    expected_scale = sum(values, Fraction(0)) / len(values) if values else None
    _require(_same(features.volume_scale, expected_scale), "volume_scale")
    positive_scale = expected_scale is not None and expected_scale > 0
    sequence = []
    for prices, volume, state in zip(features.prices_in_R, features.stored_volumes, features.volume_states, strict=True):
        _require(type(prices) is tuple and len(prices) == 4 and type(state) is str and state in _STATES
                 and (state == "observed") == (volume is not None), "minute_shape_or_mask")
        ratio = Fraction(0) if volume is None or not positive_scale else volume / expected_scale
        exact = (*prices, ratio, Fraction(volume is not None), Fraction(positive_scale))
        _require(len(exact) == len(MINUTE_NAMES), "minute_names")
        sequence.append(tuple(_number(value) for value in exact))
    static = features.common + (features.context if with_context else ())
    return NumericContextFeatures(features.question.question_id, features.forecast_at,
                                  "chart_context" if with_context else "chart", tuple(sequence),
                                  tuple(_number(value) for value in static))


def verify_features(frame: ContextFrame, question: ContextQuestion, produced: ContextFeatures, *,
                    chart: NumericContextFeatures | None = None,
                    chart_context: NumericContextFeatures | None = None) -> None:
    """Audit exact identity and optional numeric views against source values."""
    expected = reference_features(frame, question)
    _require(_same(expected, produced), "exact_projection_differs")
    for actual, contextual in ((chart, False), (chart_context, True)):
        if actual is not None:
            _require(_same(reference_numeric_view(expected, with_context=contextual), actual), "numeric_projection_differs")
