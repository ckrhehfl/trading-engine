"""Independent EI training execution audit over caller-authenticated originals.

The caller must supply the complete sealed coordinate and labels already audited
by the snapshot reference, and its authenticated raw session and policy metadata.
This component checks their binding and reconstructs execution, not their source
authentication or the snapshot science. Shared DTOs, scalar serialization and
the existing independent raw-price validator do not supply execution answers.
No producer preparation, fill, policy, replay or validation function is called.
"""

from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict, fields, is_dataclass, replace
from datetime import datetime
from fractions import Fraction
from hashlib import sha256

from research import intraday_chart_cash as cash
from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_entry import PreparedContextEntry
from research.intraday_context_entry_training import (
    EntryTrainingCoordinate, TrainingEntryTrace, TrainingPriceRead,
)
from research.intraday_context_entry_values import HistoricalEntryPayoff
from research.intraday_context_model_inputs import encode
from research.intraday_context_snapshot import LabeledCoordinate, ProjectedCoordinate, _feature
from research.intraday_context_snapshot_reference import _original_roster, _question_key
from research.intraday_ed_inputs import MINUTE, InputRefusal, Minute, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_management_compare import ManagementReplay
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import EntryBounds, Episode, Request, Step
from research.intraday_scenario_rollout import ReplayEvent


SCHEMA = "ei-context-entry-training-reference-v1"
_PRODUCER_SCHEMA = "ei-context-entry-training-draft-v1"
_POLICY = "available_close_stop_next_open_or_original_absolute_expiry_no_target_exit"
_ORDER_FRACTION = Fraction(1, 50)
_HORIZONS = (5, 15, 30)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_entry_training_reference_" + reason)


def _same(actual, expected) -> None:
    _require(type(actual) is type(expected), "type_differs")
    if is_dataclass(expected):
        for field in fields(expected):
            _same(getattr(actual, field.name), getattr(expected, field.name))
    elif type(expected) is tuple:
        _require(len(actual) == len(expected), "length_differs")
        for left, right in zip(actual, expected, strict=True):
            _same(left, right)
    else:
        _require(actual == expected, "value_differs")


def _clock(value) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None
             and value.second == value.microsecond == 0, "minute_clock_required")


def _digest(value) -> None:
    _require(type(value) is str and len(value) == 64
             and all(c in "0123456789abcdef" for c in value), "digest_required")


def _payload(value):
    if type(value) is Fraction:
        return [value.numerator, value.denominator]
    if type(value) is dict:
        return {key: _payload(item) for key, item in value.items()}
    if type(value) in (tuple, list):
        return [_payload(item) for item in value]
    return value


def _feature_digest(feature) -> str:
    # _feature is only the shared wire codec; it neither decides nor validates.
    return sha256(encode(_feature(feature))).hexdigest()


def _scope(coordinate: ProjectedCoordinate, targets: tuple[TargetCoordinate, ...]) -> None:
    _require(type(coordinate) is ProjectedCoordinate, "original_coordinate_required")
    roster = _original_roster(targets)
    _require(len(roster) == 1 and (coordinate.session, coordinate.symbol) in roster,
             "original_roster_differs")
    _same(coordinate.targets, roster[coordinate.session, coordinate.symbol])
    keys = tuple(_question_key(item.question) for item in coordinate.questions)
    _require(len(set(keys)) == len(keys)
             and keys == tuple(key for frame in coordinate.frames for key in frame.question_ids),
             "original_question_bijection_differs")
    if coordinate.opening is None:
        _require(coordinate.status == "session_missing" and coordinate.closing is None
                 and not coordinate.questions and not coordinate.frames, "missing_coordinate_differs")
    else:
        _clock(coordinate.opening)
        _clock(coordinate.closing)
        _require(coordinate.opening < coordinate.closing and coordinate.status == "observed_session",
                 "original_session_bounds_differ")


def _eligible(feature) -> str | None:
    if feature.status != "ready":
        return "feature_unsupported:" + (feature.reasons[0] if feature.reasons else "unspecified")
    question, path = feature.question, feature.question.path
    if "unsupported_price_geometry" in question.unsupported_reasons or not (
            0 < path.stop <= path.level - path.band < path.level + path.band < path.target
            and path.stop < question.start_close < path.target):
        return "unsupported_price_geometry"
    return None


def _labels(original: LabeledCoordinate | None, coordinate: ProjectedCoordinate,
            cutoff: datetime) -> dict:
    if original is None:
        return {}
    _require(type(original) is LabeledCoordinate
             and (original.session, original.symbol) == (coordinate.session, coordinate.symbol)
             and original.status in ("observed", "session_missing"), "label_scope_differs")
    _same(tuple(row.question for row in original.labels), tuple(row.question for row in coordinate.questions))
    result = {}
    for label in original.labels:
        _clock(label.cutoff_at)
        _require(label.cutoff_at == coordinate.closing + 2 * MINUTE <= cutoff
                 and label.status == original.status, "label_cutoff_differs")
        _require((not label.results) if label.status == "session_missing" else len(label.results) == 3,
                 "label_horizons_differ")
        for horizon, outcome in zip(_HORIZONS, label.results):
            path = replace(label.question.path, end_at=label.question.path.start_at + horizon * MINUTE)
            _same(outcome.question, path)
            _require(outcome.status in ("resolved", "pending", "unknown", "unsupported")
                     and path.start_at <= outcome.observed_through <= path.end_at
                     and type(outcome.departed) is bool and type(outcome.retested) is bool
                     and (not outcome.retested or outcome.departed), "label_geometry_differs")
            if outcome.status == "resolved":
                _clock(outcome.mature_at)
                _require(outcome.path in ("P1", "P2", "P3", "P4")
                         and outcome.observed_through + 2 * MINUTE <= outcome.mature_at <= label.cutoff_at,
                         "label_maturity_differs")
            else:
                _require(outcome.path is None and type(outcome.reason) is str and bool(outcome.reason),
                         "unresolved_label_differs")
        result[_question_key(label.question)] = label
    return result


def _outcome(label, cutoff):
    if label is None:
        return None, "training_label_missing", tuple((h, "label_missing") for h in _HORIZONS)
    if not label.results:
        return None, "training_session_missing", tuple((h, "session_missing") for h in _HORIZONS)
    support = tuple((h, item.status) for h, item in zip(_HORIZONS, label.results, strict=True))
    for horizon, item in zip(_HORIZONS, label.results, strict=True):
        if item.status != "resolved":
            return None, f"joint_horizon_{horizon}_{item.status}:{item.reason}", support
    chosen, previous_end = None, label.question.path.start_at
    for horizon, item in zip(_HORIZONS, label.results, strict=True):
        if chosen is None and item.path != "P4":
            _require(item.observed_through > previous_end, "terminal_before_prior_timeout")
            chosen = item.path, horizon, item.observed_through, item.mature_at
        if chosen is None:
            _require(item.observed_through == item.question.end_at, "unfinished_timeout")
        else:
            _require((item.path, item.observed_through, item.mature_at) == (chosen[0], chosen[2], chosen[3]),
                     "terminal_changed_between_horizons")
        previous_end = item.question.end_at
    mature = max(item.mature_at for item in label.results)
    if mature >= cutoff:
        return None, "outcome_not_mature_before_split", support
    index = 9 if chosen is None else _HORIZONS.index(chosen[1]) * 3 + ("P1", "P2", "P3").index(chosen[0])
    return TrainingOutcome(index, mature), None, support


class _OriginalPrices:
    """Read each original minute once; retain every logical use in its trace."""

    def __init__(self, session, cutoff):
        self.session, self.cutoff = session, cutoff
        self.cache = {}

    def read(self, at, purpose, records):
        session = self.session
        row, reason = None, None
        if not session.opening <= at < session.closing:
            reason = "outside_session"
        else:
            if at not in self.cache:
                self.cache[at] = session.minutes.get(at)
            row = self.cache[at]
            if row is None:
                reason = "missing_minute"
            else:
                _require(type(row) is Minute and (row.symbol, row.start_at) == (session.symbol, at),
                         "original_minute_substitution")
                if row.available_at is None:
                    reason = "availability_missing"
                else:
                    _clock(row.available_at)
                    _require(row.available_at >= at + 3 * MINUTE, "original_publication_before_lag")
                    if row.available_at >= self.cutoff:
                        reason = "price_not_mature_before_cutoff"
                    else:
                        # The independent decoder receives this already-read row.
                        single = SessionInputs(session.symbol, at, at + MINUTE, {at: row})
                        _, reason = _price_row(single, at, row.available_at)
        records.append(TrainingPriceRead(at, purpose, row, reason is None, reason))
        return row if reason is None else None


def _frozen(book, reason):
    return replace(book, indeterminate_reasons=book.indeterminate_reasons + (reason,), inventory_uncertain=True)


def _holding(initial: Episode, prices: _OriginalPrices, records: list) -> ManagementReplay:
    """Independent chronological sale search; never inspect highs for profit exits."""
    cap = min(initial.end_at, initial.entered_at + 30 * MINUTE)
    episode, events = initial, []
    unreviewed = initial.entered_at
    at = initial.entered_at + MINUTE
    while at < cap:
        missing, reason = None, None
        while unreviewed <= at - 3 * MINUTE:
            row = prices.read(unreviewed, "holding_close", records)
            unreviewed += MINUTE
            if row is None:
                missing = missing or "missing_minute"
            elif row.available_at > at:
                missing = missing or "minute_available_after_cutoff"
            elif Fraction(row.ohlc[3]) <= initial.stop:
                reason = "structural_close_failure"
                break
        if reason is None and at + MINUTE == cap:
            reason = "fixed_episode_end"
        if reason is None and missing is not None:
            issue = "holding_close_unknown:" + missing
            events.append(ReplayEvent(at, "unresolved", issue))
            return ManagementReplay(episode, cap, "unresolved", issue, tuple(events))
        if reason is None:
            episode = replace(episode, last_at=at)
            at += MINUTE
            continue
        lot = episode.book.lots[0]
        executed = at + MINUTE
        events.append(ReplayEvent(at, "exit", reason, executed, lot.quantity))
        row = prices.read(executed, "modeled_open", records)
        price = None if row is None else Fraction(row.ohlc[0])
        if price is None:
            book = _frozen(episode.book, f"missing_exit_price:{episode.book.strategy}:{episode.symbol}")
            episode = replace(episode, book=book, last_at=executed)
            events.append(ReplayEvent(executed, "exit", "exit_price_unknown", quantity=0,
                                      fill_status="indeterminate"))
            return ManagementReplay(episode, cap, "unresolved", "exit_price_unknown", tuple(events))
        sale = cash.RealizedExit(lot.lot_id, lot.symbol, lot.quantity, lot.quantity * lot.entry_price,
                                 lot.original_entry_cost, lot.quantity * price)
        book = replace(episode.book, cash=episode.book.cash + sale.gross_proceeds, lots=(),
                       realized_exits=episode.book.realized_exits + (sale,))
        episode = replace(episode, book=book, last_at=executed, stop=None, entered_at=None, closed_at=executed)
        events.append(ReplayEvent(executed, "exit", "exited", quantity=lot.quantity,
                                  fill_status="modeled", open_price=price))
        return ManagementReplay(episode, cap, "closed", "exited", tuple(events))
    raise InputRefusal("EI_entry_training_reference_invalid_holding_cap")


def _execution(feature, empirical, weight, support, prices, *, book, risk, cost, source, policy):
    question, path = feature.question, feature.question.path
    feature_digest = _feature_digest(feature)
    trace = TrainingEntryTrace(_question_key(question), feature_digest, "unknown", "session_missing", support)
    row = HistoricalEntryPayoff(empirical, question.start_close, question.anchor_id[1].pre_peak_range,
                               None, None, None, None, weight, source, "session_missing")

    def unknown(reason):
        return replace(row, unsupported_reason=reason), replace(trace, reason=reason)

    if prices is None:
        return row, trace
    records = []
    visible = prices.read(feature.forecast_at - 3 * MINUTE, "causal_close", records)
    trace = replace(trace, prices=tuple(records))
    if visible is None or visible.available_at != feature.forecast_at:
        return unknown("causal_close_unsupported")
    _require(Fraction(visible.ohlc[3]) == question.start_close and (feature.prices_in_R is None
             or feature.prices_in_R[-1] == tuple((Fraction(p) - path.level) / row.risk for p in visible.ohlc)),
             "causal_feature_price_differs")
    at = feature.forecast_at
    episode = Episode(question.anchor_id[0], at, path.end_at, risk, cost, book, book.next_lot_id, at)
    step = None
    if question.unsupported_reasons:
        reason = "question_unsupported: " + ", ".join(question.unsupported_reasons)
    elif not question.economic_priority:
        reason = "lower_same_clock_question_priority"
    else:
        limit = min(question.start_close + path.band, path.target)
        bounds = EntryBounds(path.stop, limit)
        rate = Fraction(cost, 10_000)
        ceiling = min(book.cash * _ORDER_FRACTION, book.cash / (1 + rate))
        quantity = min((risk / 2) // (limit * (1 + rate) - path.stop), ceiling // limit)
        if quantity:
            request = Request("enter", at + MINUTE, quantity, bounds, (book.cash, book.cash))
            step = Step(replace(episode, attempts=1, pending=request), "enter", "higher_value_of_entry")
        else:
            step = Step(episode, "wait", "insufficient_risk_or_cash_for_one_unit")
        reason = step.reason
    prepared = PreparedContextEntry("baseline", feature, feature_digest, book, visible, risk, cost,
        feature_digest, policy, None, None, True, "abstain" if step is None else "prepared", reason, step)
    trace = replace(trace, prepared=prepared)
    if step is None or step.action != "enter":
        return unknown("preparation_unsupported:" + reason)
    episode = step.episode
    executed = episode.pending.execute_at
    opened = prices.read(executed, "modeled_open", records)
    price = None if opened is None else Fraction(opened.ohlc[0])
    if price is None:
        changed = _frozen(book, f"missing_entry_price:{book.strategy}:{episode.symbol}")
        fill = cash.Decision(changed, "entry_price_unknown", "indeterminate")
    elif price >= path.target:
        fill = cash.Decision(book, "entry_at_or_above_target")
    elif not path.stop < price <= limit:
        fill = cash.Decision(book, "open_outside_frozen_entry_bounds")
    else:
        paid = quantity * price * rate
        delta = -quantity * price - paid
        lot = cash.Lot(book.next_lot_id, episode.symbol, quantity, quantity, price, paid)
        changed = replace(book, cash=book.cash + delta, lots=(lot,), next_lot_id=book.next_lot_id + 1)
        fill = cash.Decision(changed, "entered", "modeled", quantity, delta, paid, ceiling)
    episode = replace(episode, book=fill.book, last_at=executed, pending=None,
                      stop=path.stop if fill.filled else None, entered_at=executed if fill.filled else None)
    trace = replace(trace, initial_episode=episode, initial_fill=fill, prices=tuple(records))
    if fill.outcome == "indeterminate":
        return unknown("initial_fill_unknown:" + fill.reason)
    if not fill.filled:
        mature = max(item.minute.available_at for item in records if item.supported)
        return replace(row, filled=False, actual_mature_at=mature, gross_in_R=Fraction(), gap_in_R=Fraction(),
                       unsupported_reason=None, no_fill_reason=fill.reason), replace(
                           trace, status="no_fill", reason=fill.reason, actual_mature_at=mature)
    holding = _holding(episode, prices, records)
    trace = replace(trace, holding=holding, prices=tuple(records))
    if holding.status != "closed":
        return unknown("holding_unresolved:" + holding.reason)
    sale = holding.episode.book.realized_exits[-1]
    mature = max(item.minute.available_at for item in records if item.supported)
    return replace(row, filled=True, actual_mature_at=mature, gross_in_R=sale.gross_pnl / quantity / row.risk,
                   gap_in_R=(price - row.decision_close) / row.risk, unsupported_reason=None), replace(
                       trace, status="closed", reason=holding.reason, actual_mature_at=mature)


def verify_entry_training_coordinate(
    result: EntryTrainingCoordinate, original_coordinate: ProjectedCoordinate,
    original_labels: LabeledCoordinate | None, original_session: SessionInputs | None, *,
    expected_targets: tuple[TargetCoordinate, ...], cutoff_at: datetime, book: cash.CashBook,
    risk_budget: Fraction, cost_bps: int, source_sha256: str, execution_policy_sha256: str,
) -> dict:
    """Audit a saved result against external originals, with point-only price access.

    The original snapshot and source/policy bindings must be authenticated outside
    this pure function. A matching digest alone never authenticates those facts.
    Excluded coordinates access neither labels nor raw minutes. Missing labels
    access no prices. The receipt distinguishes raw reads from logical price uses.
    """
    _clock(cutoff_at)
    _digest(source_sha256)
    _digest(execution_policy_sha256)
    _require(type(book) is cash.CashBook and not book.lots and not book.indeterminate
             and type(risk_budget) is Fraction and risk_budget > 0
             and type(cost_bps) is int and cost_bps in (33, 43), "caller_parameters_differ")
    _scope(original_coordinate, expected_targets)
    coordinate = original_coordinate
    parameters = sha256(encode([_PRODUCER_SCHEMA, _POLICY, source_sha256, execution_policy_sha256,
                                _payload(asdict(book)), _payload(risk_budget), cost_bps])).hexdigest()
    # Authenticate every producer binding against caller metadata before raw access.
    _require(type(result) is EntryTrainingCoordinate, "typed_result_required")
    for actual, expected in ((result.original, coordinate), (result.cutoff_at, cutoff_at),
            (result.book, book), (result.risk_budget, risk_budget), (result.cost_bps, cost_bps),
            (result.source_sha256, source_sha256), (result.execution_policy_sha256, execution_policy_sha256),
            (result.parameters_sha256, parameters), (result.fixed_policy, _POLICY),
            (result.source_authenticated, False), (result.contains_actual_fills, False)):
        _same(actual, expected)
    blocked = coordinate.opening is not None and (coordinate.opening >= cutoff_at
                                                  or coordinate.closing + 2 * MINUTE > cutoff_at)
    traces, candidates = {}, []
    keys = tuple(_question_key(feature.question) for feature in coordinate.questions)
    for feature, key in zip(coordinate.questions, keys, strict=True):
        reason = "coordinate_not_before_training_cutoff" if blocked else _eligible(feature)
        if reason is None:
            candidates.append(feature)
        else:
            traces[key] = TrainingEntryTrace(key, _feature_digest(feature), "excluded", reason)
    labels = _labels(original_labels, coordinate, cutoff_at) if candidates else {}
    opened = ["labels"] if candidates else []
    empirical, supports = [], []
    for feature in candidates:
        outcome, reason, support = _outcome(labels.get(_question_key(feature.question)), cutoff_at)
        question = feature.question
        empirical.append(EmpiricalTrainingRow(question.question_id, coordinate.symbol, coordinate.session,
            question.stage, question.episode_origin_context, feature.forecast_at, outcome, reason))
        supports.append(support)
    counts = Counter((item.symbol, item.session, item.stage) for item in empirical if item.outcome is not None)
    prices = None
    if counts:
        opened.append("prices")
        if original_session is not None:
            _require(type(original_session) is SessionInputs and isinstance(original_session.minutes, Mapping)
                     and (original_session.symbol, original_session.opening, original_session.closing)
                     == (coordinate.symbol, coordinate.opening, coordinate.closing), "original_session_substitution")
            prices = _OriginalPrices(original_session, cutoff_at)
    _same(result.opened_inputs, tuple(opened))
    rows = []
    for feature, empirical_row, support in zip(candidates, empirical, supports, strict=True):
        key = _question_key(feature.question)
        if empirical_row.outcome is None:
            reason = "label_unavailable:" + empirical_row.unavailable_reason
            rows.append(HistoricalEntryPayoff(empirical_row, feature.question.start_close,
                feature.question.anchor_id[1].pre_peak_range, None, None, None, None, None, source_sha256, reason))
            traces[key] = TrainingEntryTrace(key, _feature_digest(feature), "unknown", reason, support)
        else:
            weight = Fraction(1, counts[empirical_row.symbol, empirical_row.session, empirical_row.stage])
            row, trace = _execution(feature, empirical_row, weight, support, prices, book=book, risk=risk_budget,
                                   cost=cost_bps, source=source_sha256, policy=execution_policy_sha256)
            rows.append(row)
            traces[key] = trace
    _same(result.rows, tuple(rows))
    expected_traces = tuple(traces[key] for key in keys)
    _same(result.traces, expected_traces)
    return {"schema": SCHEMA, "verified": True, "original_questions": len(keys),
            "original_frames": len(coordinate.frames), "original_memberships": len(coordinate.targets),
            "training_rows": len(rows), "label_supported_rows": sum(counts.values()),
            "complete_payoff_rows": sum(row.filled is not None for row in rows),
            "known_no_fill_rows": sum(row.filled is False for row in rows),
            "unknown_payoff_rows": sum(row.filled is None for row in rows),
            "opened_inputs": tuple(opened), "original_price_reads": 0 if prices is None else len(prices.cache),
            "logical_price_uses": sum(len(trace.prices) for trace in expected_traces),
            "parameters_sha256": parameters, "source_authenticated": False,
            "snapshot_science_reconstructed": False, "market_payoffs_reconstructed": True,
            "contains_actual_fills": False, "maximum_absolute_difference": 0}
