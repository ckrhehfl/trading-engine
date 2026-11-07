"""Pure first-touch labels and observable prefixes for fixed EH questions."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from research.intraday_ed_inputs import MINUTE, STATES, Minute, SessionInputs, _clock, _require, _session


HORIZONS_MINUTES = (5, 15, 30)
_DEFAULT_AVAILABILITY_LAG = 2 * MINUTE
ExactPrice = Decimal | Fraction | int
Path = Literal["P1", "P2", "P3", "P4"]
PathStatus = Literal["resolved", "pending", "unknown", "unsupported"]
QuestionId = tuple[datetime, Fraction, Fraction, Fraction, Fraction, datetime]


def _price(value: ExactPrice) -> Fraction:
    _require(type(value) in (int, Fraction) or type(value) is Decimal and value.is_finite(),
             "exact_finite_price_required")
    return Fraction(value)


@dataclass(frozen=True)
class PathQuestion:
    """Absolute path coordinates in a single caller-declared price basis."""

    start_at: datetime
    end_at: datetime
    level: ExactPrice
    band: ExactPrice
    target: ExactPrice
    stop: ExactPrice

    def __post_init__(self) -> None:
        for name in ("start_at", "end_at"):
            at = getattr(self, name)
            _clock(at)
            at = at.astimezone(UTC)
            _require(at.second == at.microsecond == 0, "question_clock_requires_minute_grid")
            object.__setattr__(self, name, at)
        _require(self.start_at < self.end_at, "question_end_must_follow_start")
        for name in ("level", "band", "target", "stop"):
            object.__setattr__(self, name, _price(getattr(self, name)))

    @property
    def question_id(self) -> QuestionId:
        return (self.start_at, Fraction(self.level), Fraction(self.band), Fraction(self.target),
                Fraction(self.stop), self.end_at)

    def forecast_id(self, observed_at: datetime) -> tuple[QuestionId, datetime]:
        _clock(observed_at)
        _require(observed_at >= self.start_at, "forecast_precedes_question")
        return self.question_id, observed_at.astimezone(UTC)


@dataclass(frozen=True)
class PathResult:
    question: PathQuestion
    status: PathStatus
    observed_through: datetime
    path: Path | None = None
    mature_at: datetime | None = None
    reason: str | None = None
    departed: bool = False
    retested: bool = False


def _minute(session: SessionInputs, at: datetime, cutoff_at: datetime) -> tuple[Minute | None, str | None]:
    row = session.minutes.get(at)
    if row is None:
        return None, "minute_not_observed_at_cutoff"
    _require(isinstance(row, Minute) and row.symbol == session.symbol and row.start_at == at,
             "minute_identity_changed")
    _clock(row.available_at)
    _require(row.available_at >= at + MINUTE, "minute_available_before_completion")
    if row.available_at > cutoff_at:
        return None, "minute_not_observed_at_cutoff"
    _require(type(row.price_state) is str and row.price_state in STATES, "invalid_observation_state")
    if row.price_state != "observed":
        _require(row.ohlc is None, "unknown_observation_has_payload")
        return None, f"price_{row.price_state}"
    prices = row.ohlc
    _require(type(prices) is tuple and len(prices) == 4, "observed_price_requires_ohlc")
    _require(all(type(price) is Decimal and price.is_finite() and price > 0 for price in prices),
             "invalid_exact_price")
    op, high, low, close = prices
    _require(low <= min(op, close) <= max(op, close) <= high, "invalid_ohlc_order")
    return row, None


def _touch(row: Minute, *, target: Fraction, stop: Fraction, lower: Fraction, upper: Fraction,
           departed: bool, retested: bool) -> tuple[Path | None, str | None, bool]:
    op, high, low, _ = row.ohlc
    if op <= stop:
        return "P3", None, retested
    if op >= target:
        return ("P2" if retested else "P1"), None, retested
    if high >= target and low <= stop:
        return None, "same_minute_target_stop_order_unknown", retested
    if low <= stop:
        return "P3", None, retested

    retest_now = departed and low <= upper and high >= lower
    if high >= target:
        if retest_now and not retested and not lower <= op <= upper:
            return None, "same_minute_retest_target_order_unknown", False
        retested = retested or retest_now
        return ("P2" if retested else "P1"), None, retested
    return None, None, retested or retest_now


def _labels(questions: tuple[PathQuestion, ...], start_close: ExactPrice | None,
            session: SessionInputs, cutoff_at: datetime, availability_lag: timedelta) -> tuple[PathResult, ...]:
    _session(session)
    _clock(cutoff_at)
    base = questions[0]
    start, level, band, target, stop, _ = base.question_id
    _require(cutoff_at >= start, "cutoff_precedes_question")
    _require(type(availability_lag) is timedelta and availability_lag >= timedelta(0),
             "nonnegative_availability_lag_required")
    lower, upper = level - band, level + band
    close = _price(start_close) if start_close is not None else None
    if close is None:
        return tuple(PathResult(question, "unsupported", start, reason="missing_start_close")
                     for question in questions)
    if not (band > 0 and 0 < stop <= lower < upper < target and stop < close < target):
        return tuple(PathResult(question, "unsupported", start, reason="unsupported_price_geometry")
                     for question in questions)

    through, latest = start, start
    departed, retested = close > upper, False
    stopped = None
    results = []
    for question in questions:
        if not session.opening <= start < question.end_at <= session.closing:
            results.append(PathResult(question, "unsupported", start, reason="outside_session"))
            continue
        while stopped is None and through < question.end_at:
            if through + MINUTE + availability_lag > cutoff_at:
                stopped = PathResult(question, "pending", through, reason="minute_not_due_at_cutoff",
                                     departed=departed, retested=retested)
                break
            row, issue = _minute(session, through, cutoff_at)
            if issue is not None:
                stopped = PathResult(question, "unknown", through, reason=issue, departed=departed,
                                     retested=retested)
                break

            through += MINUTE
            latest = max(latest, row.available_at, through + availability_lag)
            path, issue, retested = _touch(row, target=target, stop=stop, lower=lower, upper=upper,
                                           departed=departed, retested=retested)
            if path is not None or issue is not None:
                stopped = PathResult(question, "resolved" if path else "unknown", through, path, latest,
                                     issue, departed, retested)
                break
            departed = departed or row.ohlc[3] > upper

        if stopped is None:
            results.append(PathResult(question, "resolved", through, "P4", latest,
                                      departed=departed, retested=retested))
        else:
            results.append(replace(stopped, question=question))
    return tuple(results)


def label_path(question: PathQuestion, start_close: ExactPrice | None, session: SessionInputs, *,
               cutoff_at: datetime, availability_lag: timedelta = _DEFAULT_AVAILABILITY_LAG) -> PathResult:
    """Scan consecutive complete bars starting at the question's start.

    The start close is caller-authenticated at the start. A close strictly
    above the band can arm the retest, including that initial close. An armed
    retest must occur on a later minute. A terminal touch or ambiguous order
    stops the scan. The declared observation schedule defaults to minute end
    plus two minutes. A minute not yet due leaves a pending prefix without a
    lookup. Once due, a missing or unpublished row blocks first order with
    the same status and reason. Touches do not imply fills.
    """
    return _labels((question,), start_close, session, cutoff_at, availability_lag)[0]


def label_horizons(question: PathQuestion, start_close: ExactPrice | None, session: SessionInputs, *,
                   cutoff_at: datetime,
                   availability_lag: timedelta = _DEFAULT_AVAILABILITY_LAG) -> tuple[PathResult, ...]:
    """Cut the same 30-minute path at 5, 15 and 30 minutes in one scan."""
    _require(question.end_at - question.start_at == 30 * MINUTE, "expected_thirty_minute_question")
    questions = tuple(replace(question, end_at=question.start_at + minutes * MINUTE)
                      for minutes in HORIZONS_MINUTES)
    return _labels(questions, start_close, session, cutoff_at, availability_lag)
