"""Training-only EI modeled fill/gap/gross payoffs from original minute paths.

Target is a path-label boundary and entry cap, NOT a target-touch profit order.
The reused fixed holding policy exits at the next open after an available
close <= stop, or at the original absolute expiry. It never reduces/reenters.
These are counterfactual nominal-unit modeled payoffs, not actual fills or
investment total returns. Source authentication belongs to the caller.
"""

from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from fractions import Fraction
from hashlib import sha256
from typing import Literal

from research import intraday_chart_cash as cash
from research.intraday_chart_inputs import _clock, _need, _session
from research.intraday_context_empirical import EmpiricalTrainingRow, weighted_training_rows
from research.intraday_context_entry import (
    PreparedContextEntry, context_entry_feature_sha256, fill_context_entry, prepare_context_entry,
)
from research.intraday_context_entry_values import HistoricalEntryPayoff
from research.intraday_context_features import ContextFeatures
from research.intraday_context_forecast_fold import _eligible
from research.intraday_context_model_inputs import encode
from research.intraday_context_snapshot import (
    LabeledCoordinate, ProjectedCoordinate, _validate_coordinate, _validate_labels, question_digest,
)
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, window
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_forecast_targets import training_outcome
from research.intraday_scenario_management_compare import ManagementReplay, _position_replay
from research.intraday_scenario_policy import Episode


SCHEMA = "ei-context-entry-training-draft-v1"
FIXED_POLICY = "available_close_stop_next_open_or_original_absolute_expiry_no_target_exit"
LabelReader = Callable[[str, str], LabeledCoordinate | None]
SessionReader = Callable[[str, str], SessionInputs | None]


@dataclass(frozen=True)
class TrainingPriceRead:
    """Source support before the training cutoff, not decision-time availability.

    The reused holding policy separately tests each close's availability at
    its decision clock. A complete payoff records the conservative maximum
    availability of valid price records checked, never an inferred timestamp.
    """
    at: datetime
    purpose: str
    minute: Minute | None
    supported: bool
    reason: str | None


@dataclass(frozen=True)
class TrainingEntryTrace:
    question_sha256: str
    feature_sha256: str
    status: Literal["excluded", "unknown", "no_fill", "closed"]
    reason: str
    horizon_support: tuple[tuple[int, str], ...] = ()
    prepared: PreparedContextEntry | None = None
    initial_episode: Episode | None = None
    initial_fill: cash.Decision | None = None
    holding: ManagementReplay | None = None
    prices: tuple[TrainingPriceRead, ...] = ()
    actual_mature_at: datetime | None = None


@dataclass(frozen=True)
class EntryTrainingCoordinate:
    original: ProjectedCoordinate
    cutoff_at: datetime
    book: cash.CashBook
    risk_budget: Fraction
    cost_bps: int
    source_sha256: str
    execution_policy_sha256: str
    parameters_sha256: str
    rows: tuple[HistoricalEntryPayoff, ...]
    traces: tuple[TrainingEntryTrace, ...]
    opened_inputs: tuple[str, ...]
    fixed_policy: str = FIXED_POLICY
    source_authenticated: bool = False
    contains_actual_fills: bool = False

    @property
    def counts(self) -> dict:
        return {"original_questions": len(self.original.questions), "original_frames": len(self.original.frames),
                "original_memberships": len(self.original.targets), "training_rows": len(self.rows),
                "label_supported_rows": sum(row.question.outcome is not None for row in self.rows),
                "statuses": dict(sorted(Counter(trace.status for trace in self.traces).items())),
                "reasons": dict(sorted(Counter(trace.reason for trace in self.traces).items()))}


def _digest(value: str) -> None:
    _need(type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value),
          "EI_entry_training_digest_required")


def _payload(value):
    if type(value) is Fraction:
        return [value.numerator, value.denominator]
    if type(value) is dict:
        return {key: _payload(item) for key, item in value.items()}
    if type(value) in (list, tuple):
        return [_payload(item) for item in value]
    return value


class _PriceReads(Mapping):
    """Only requested minutes are touched; no raw-session iteration is allowed."""
    def __init__(self, session: SessionInputs, cutoff: datetime):
        self.session, self.cutoff = session, cutoff
        self.reads: list[TrainingPriceRead] = []

    def read(self, at: datetime, purpose: str) -> Minute | None:
        _clock(at)
        if not self.session.opening <= at < self.session.closing:
            self.reads.append(TrainingPriceRead(at, purpose, None, False, "outside_session"))
            return None
        minute = self.session.minutes.get(at)
        if minute is None:
            self.reads.append(TrainingPriceRead(at, purpose, None, False, "missing_minute"))
            return None
        _need(type(minute) is Minute and minute.symbol == self.session.symbol and minute.start_at == at,
              "EI_entry_training_minute_substitution")
        if minute.available_at is None:
            self.reads.append(TrainingPriceRead(at, purpose, minute, False, "availability_missing"))
            return None
        _clock(minute.available_at)
        _need(minute.available_at >= at + 3 * MINUTE, "EI_entry_training_publication_before_declared_lag")
        if minute.available_at >= self.cutoff:
            self.reads.append(TrainingPriceRead(at, purpose, minute, False, "price_not_mature_before_cutoff"))
            return None
        single = SessionInputs(self.session.symbol, at, at + MINUTE, {at: minute})
        checked = window(single, 0, 1, minute.available_at)
        reason = checked.issues[0].reason if checked.issues else None
        self.reads.append(TrainingPriceRead(at, purpose, minute, not checked.issues, reason))
        return None if checked.issues else minute

    def __getitem__(self, at):
        return self.read(at, "holding_close")

    def __iter__(self) -> Iterator:
        raise TypeError("EI entry training requires point price reads")

    def __len__(self):
        raise TypeError("EI entry training does not enumerate the original session")

    def maturity(self) -> datetime:
        values = [item.minute.available_at for item in self.reads if item.supported]
        _need(bool(values), "EI_entry_training_missing_price_maturity")
        return max(values)


class _ModeledOpens(Mapping):
    def __init__(self, prices: _PriceReads):
        self.prices = prices

    def __getitem__(self, at):
        minute = self.prices.read(at, "modeled_open")
        return None if minute is None else Fraction(minute.ohlc[0])

    def __iter__(self) -> Iterator:
        raise TypeError("EI entry training opens are request-only")

    def __len__(self):
        raise TypeError("EI entry training does not enumerate future opens")


def _path_payoff(feature: ContextFeatures, empirical: EmpiricalTrainingRow, weight: Fraction,
                 support: tuple[tuple[int, str], ...], session: SessionInputs | None, *,
                 cutoff: datetime, book: cash.CashBook, risk: Fraction, cost: int,
                 source: str, policy: str) -> tuple[HistoricalEntryPayoff, TrainingEntryTrace]:
    question = feature.question
    trace = TrainingEntryTrace(question_digest(question), context_entry_feature_sha256(feature),
                               "unknown", "session_missing", support)
    row = HistoricalEntryPayoff(empirical, question.start_close, question.anchor_id[1].pre_peak_range,
                               None, None, None, None, weight, source, "session_missing")

    def unknown(reason: str):
        return replace(row, unsupported_reason=reason), replace(trace, status="unknown", reason=reason)

    if session is None:
        return row, trace
    prices = _PriceReads(session, cutoff)
    minute = prices.read(feature.forecast_at - 3 * MINUTE, "causal_close")
    trace = replace(trace, prices=tuple(prices.reads))
    if minute is None or minute.available_at != feature.forecast_at:
        return unknown("causal_close_unsupported")
    prepared = prepare_context_entry(feature, book, mode="baseline", visible_minute=minute,
        risk_budget=risk, cost_bps=cost, causal_source_sha256=trace.feature_sha256,
        execution_policy_sha256=policy, rule_enabled=True)
    trace = replace(trace, prepared=prepared)
    if prepared.status != "prepared" or prepared.step.action != "enter":
        return unknown("preparation_unsupported:" + prepared.reason)
    execute_at = prepared.step.episode.pending.execute_at
    opens = _ModeledOpens(prices)
    entry, filled = fill_context_entry(prepared, execute_at, opens.get(execute_at))
    trace = replace(trace, initial_episode=entry, initial_fill=filled, prices=tuple(prices.reads))
    if filled.outcome == "indeterminate":
        return unknown("initial_fill_unknown:" + filled.reason)
    if filled.outcome == "declined":
        maturity = prices.maturity()
        _need(question.path.start_at < maturity < cutoff, "EI_entry_training_no_fill_maturity_differs")
        return replace(row, filled=False, actual_mature_at=maturity, gross_in_R=Fraction(), gap_in_R=Fraction(),
                       unsupported_reason=None, no_fill_reason=filled.reason), replace(
                           trace, status="no_fill", reason=filled.reason, actual_mature_at=maturity)
    _need(filled.filled and filled.quantity > 0 and len(entry.book.lots) == 1,
          "EI_entry_training_confirmed_lot_required")
    holding = _position_replay(entry, replace(session, minutes=prices), opens, None)
    trace = replace(trace, holding=holding, prices=tuple(prices.reads))
    if holding.status != "closed" or holding.episode.book.indeterminate or holding.episode.book.lots:
        return unknown("holding_unresolved:" + holding.reason)
    lot = entry.book.lots[0]
    exits = tuple(leg for leg in holding.episode.book.realized_exits if leg.lot_id == lot.lot_id)
    _need(len(exits) == 1 and exits[0].quantity == lot.original_quantity == filled.quantity
          and exits[0].entry_basis == lot.entry_price * filled.quantity
          and exits[0].allocated_entry_cost == lot.original_entry_cost,
          "EI_entry_training_fixed_exit_cash_binding_differs")
    gross = exits[0].gross_pnl / filled.quantity / row.risk
    gap = (lot.entry_price - row.decision_close) / row.risk
    maturity = prices.maturity()
    _need(question.path.start_at < maturity < cutoff, "EI_entry_training_payoff_not_mature")
    return replace(row, filled=True, actual_mature_at=maturity, gross_in_R=gross, gap_in_R=gap,
                   unsupported_reason=None), replace(trace, status="closed", reason=holding.reason,
                                                     actual_mature_at=maturity)


def build_entry_training_coordinate(
    coordinate: ProjectedCoordinate, read_labels: LabelReader, read_session: SessionReader, *,
    expected_targets: tuple[TargetCoordinate, ...], cutoff_at: datetime, book: cash.CashBook,
    risk_budget: Fraction, cost_bps: int, source_sha256: str, execution_policy_sha256: str,
) -> EntryTrainingCoordinate:
    """Reconstruct all eligible historical questions before fitting entry values.

    Each question forks the same caller book and risk/cost settings. All mature
    label-supported questions in a symbol/session/stage share total weight one,
    BEFORE payoff missingness or economic priority is considered. No-request
    cases remain unsupported payoffs; only a known pending request can no-fill.
    Missing-label and unsupported-feature questions retain their original status.
    This is not an overlapping portfolio simulation or a selector entry point.
    """
    _clock(cutoff_at)
    for digest in (source_sha256, execution_policy_sha256):
        _digest(digest)
    _need(type(book) is cash.CashBook and not book.lots and not book.indeterminate
          and type(risk_budget) is Fraction and risk_budget > 0 and type(cost_bps) is int and cost_bps in (33, 43)
          and callable(read_labels) and callable(read_session), "EI_entry_training_parameters_differ")
    _validate_coordinate(coordinate, expected_targets)
    parameters = sha256(encode([SCHEMA, FIXED_POLICY, source_sha256, execution_policy_sha256,
                                _payload(asdict(book)), _payload(risk_budget), cost_bps])).hexdigest()
    traces: dict[str, TrainingEntryTrace] = {}
    candidates = []
    original_keys = tuple(question_digest(feature.question) for feature in coordinate.questions)
    blocked = (coordinate.opening is not None and (coordinate.opening >= cutoff_at
               or coordinate.closing + 2 * MINUTE > cutoff_at))
    for feature, key in zip(coordinate.questions, original_keys, strict=True):
        reason = "coordinate_not_before_training_cutoff" if blocked else _eligible(feature)
        if reason is not None:
            traces[key] = TrainingEntryTrace(key, context_entry_feature_sha256(feature), "excluded", reason)
        else:
            candidates.append(feature)
    opened = []
    labels = None
    if candidates:
        labels = read_labels(coordinate.session, coordinate.symbol)
        opened.append("labels")
        if labels is not None:
            _validate_labels(labels, coordinate)
            _need(all(label.cutoff_at <= cutoff_at for label in labels.labels),
                  "EI_entry_training_label_cutoff_differs")
    by_question = {} if labels is None else {label.question_id: label for label in labels.labels}
    empirical = []
    supports = {}
    for feature in candidates:
        key = question_digest(feature.question)
        label = by_question.get(key)
        support = tuple((horizon, "label_missing") for horizon in (5, 15, 30))
        outcome, reason = None, "training_label_missing"
        if label is not None:
            if not label.results:
                reason = "training_session_missing"
                support = tuple((horizon, "session_missing") for horizon in (5, 15, 30))
            else:
                support = tuple((horizon, result.status) for horizon, result in zip((5, 15, 30), label.results, strict=True))
                outcome = training_outcome(label.results, before=cutoff_at)
                first = next(((horizon, result) for horizon, result in zip((5, 15, 30), label.results, strict=True)
                              if result.status != "resolved"), None)
                reason = (f"joint_horizon_{first[0]}_{first[1].status}:{first[1].reason}" if first else
                          "outcome_not_mature_before_split" if outcome is None else None)
        question = feature.question
        empirical.append(EmpiricalTrainingRow(question.question_id, coordinate.symbol, coordinate.session,
            question.stage, question.episode_origin_context, feature.forecast_at, outcome, reason))
        supports[key] = support
    weights = {row.question_id: weight for row, weight in weighted_training_rows(empirical, cutoff_at=cutoff_at)}
    session = None
    if weights:
        session = read_session(coordinate.session, coordinate.symbol)
        opened.append("prices")
        if session is not None:
            _session(session)
            _need((session.symbol, session.opening, session.closing) == (coordinate.symbol, coordinate.opening,
                  coordinate.closing), "EI_entry_training_session_substitution")
    rows = []
    for feature, question in zip(candidates, empirical, strict=True):
        key = question_digest(feature.question)
        if question.outcome is None:
            reason = "label_unavailable:" + question.unavailable_reason
            rows.append(HistoricalEntryPayoff(question, feature.question.start_close,
                feature.question.anchor_id[1].pre_peak_range, None, None, None, None, None, source_sha256, reason))
            traces[key] = TrainingEntryTrace(key, context_entry_feature_sha256(feature), "unknown", reason, supports[key])
        else:
            row, trace = _path_payoff(feature, question, weights[question.question_id], supports[key], session,
                cutoff=cutoff_at, book=book, risk=risk_budget, cost=cost_bps, source=source_sha256,
                policy=execution_policy_sha256)
            rows.append(row)
            traces[key] = trace
    return EntryTrainingCoordinate(coordinate, cutoff_at, book, risk_budget, cost_bps, source_sha256,
        execution_policy_sha256, parameters, tuple(rows), tuple(traces[key] for key in original_keys), tuple(opened))
