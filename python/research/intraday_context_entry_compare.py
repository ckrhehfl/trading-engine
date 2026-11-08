"""Paired EI take/skip and fixed-exit comparison on the original fold roster.

The caller authenticates the original roster, forecast readback and training
value model under the exact execution policy, including the cost and risk basis.
Prepare the fold with causal point reads, seal the decisions, then supply the
same authenticated test-session source for replay. Each opportunity uses the
same nominal book and fixed exit in both arms.
"""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from fractions import Fraction

from research import intraday_chart_cash as cash
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_chart_inputs import _clock, _need
from research.intraday_context_entry import (
    PreparedContextEntry, context_entry_feature_sha256, fill_context_entry, prepare_context_entry,
)
from research.intraday_context_entry_roster import CoordinateOpportunities, EntryRoster, Opportunity, _opportunities
from research.intraday_context_entry_values import (
    EntryValueModel, EntryValueQuery, _digest, assess_entry_value, forecast_probability_boundary,
)
from research.intraday_context_forecast_fold import MODEL_KEYS, _validate_fold
from research.intraday_context_forecast_seal import StoredForecast
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs, _session
from research.intraday_scenario_management_compare import ManagementReplay, _position_replay
from research.intraday_scenario_open_reference import _price_row
from research.intraday_scenario_policy import Episode, start_episode


ReadVisible = Callable[[str, str, datetime], Minute | None]
ReadSession = Callable[[str, str], SessionInputs | None]


@dataclass(frozen=True)
class EntryDecisionPair:
    opportunity: Opportunity
    baseline: PreparedContextEntry | None
    selected: PreparedContextEntry | None
    baseline_unavailable: str | None = None
    selected_unavailable: str | None = None


@dataclass(frozen=True)
class PreparedEntryCoordinate:
    roster: CoordinateOpportunities
    opening: datetime | None
    closing: datetime | None
    decisions: tuple[EntryDecisionPair, ...]


@dataclass(frozen=True)
class PreparedEntryComparison:
    scenario: str
    fold: ChronologicalFold
    feature_sha256: str
    forecast_sha256: str
    value_model_sha256: str
    execution_policy_sha256: str
    cost_bps: int
    original_union_coordinates: int
    coordinates: tuple[PreparedEntryCoordinate, ...]


@dataclass(frozen=True)
class EventPayoff:
    gross: Fraction
    cost: Fraction
    entry_notional: Fraction

    @property
    def net(self) -> Fraction:
        return self.gross - self.cost

    @property
    def net_return(self) -> Fraction | None:
        return self.net / self.entry_notional if self.entry_notional else None


_NO_TRADE = EventPayoff(Fraction(), Fraction(), Fraction())


@dataclass(frozen=True)
class EntryArmOutcome:
    status: str
    reason: str
    payoff: EventPayoff | None = None
    initial: Episode | None = None
    fill: cash.Decision | None = None
    holding: ManagementReplay | None = None


@dataclass(frozen=True)
class EntryPairOutcome:
    decisions: EntryDecisionPair
    baseline: EntryArmOutcome
    selected: EntryArmOutcome

    @property
    def net_delta(self) -> Fraction | None:
        if self.baseline.payoff is None or self.selected.payoff is None:
            return None
        return self.selected.payoff.net - self.baseline.payoff.net


@dataclass(frozen=True)
class EntryCoordinateOutcome:
    prepared: PreparedEntryCoordinate
    outcomes: tuple[EntryPairOutcome, ...]
    modeled_opens: tuple[tuple[datetime, Fraction | None, str | None], ...]


@dataclass(frozen=True)
class EntryComparison:
    prepared: PreparedEntryComparison
    coordinates: tuple[EntryCoordinateOutcome, ...]


def _prepare_pair(opportunity, prediction, visible, *, book, risk, cost, policy, model, forecast_sha):
    if opportunity.status != "opportunity":
        return EntryDecisionPair(opportunity, None, None)
    features = opportunity.feature
    at = features.forecast_at
    start = at - 3 * MINUTE
    minute = visible(prediction.session, prediction.symbol, start)
    if minute is not None:
        _need(type(minute) is Minute and (minute.symbol, minute.start_at) == (prediction.symbol, start),
              "EI_entry_compare_causal_minute_identity_differs")
        if minute.available_at is not None:
            _clock(minute.available_at)
            _need(minute.available_at >= at, "EI_entry_compare_causal_availability_before_declared_lag")
    if minute is None or minute.available_at != at:
        reason = "causal_close_missing" if minute is None else "causal_close_availability_differs"
        return EntryDecisionPair(opportunity, None, None, reason, reason)
    causal_sha = context_entry_feature_sha256(features)
    common = dict(visible_minute=minute, risk_budget=risk, cost_bps=cost,
                  causal_source_sha256=causal_sha, execution_policy_sha256=policy)
    baseline = prepare_context_entry(features, book, mode="baseline", rule_enabled=True, **common)
    arm = prediction.predictions[MODEL_KEYS.index("context_sequence")]
    if arm.status == "unsupported":
        _need(arm.probabilities is None and type(arm.reason) is str and bool(arm.reason),
              "EI_entry_compare_unsupported_forecast_payload_differs")
        return EntryDecisionPair(opportunity, baseline, None, selected_unavailable=arm.reason)
    _need(arm.status == "predicted" and arm.reason is None, "EI_entry_compare_forecast_status_differs")
    boundary = forecast_probability_boundary(arm.probabilities)
    question = features.question
    query = EntryValueQuery(question.question_id, at, question.start_close, question.anchor_id[1].pre_peak_range,
                            Fraction(cost), boundary.probabilities, arm.key, forecast_sha, causal_sha, policy, boundary)
    value = assess_entry_value(model, query)
    selected = prepare_context_entry(features, book, mode="selected", forecast_source_sha256=forecast_sha,
                                     entry_value=value, **common)
    return EntryDecisionPair(opportunity, baseline, selected)


def prepare_entry_comparison(
    roster: EntryRoster, stored: StoredForecast, fold: ChronologicalFold, model: EntryValueModel, *,
    book: cash.CashBook, risk_budget: Fraction, cost_bps: int, execution_policy_sha256: str,
    read_visible: ReadVisible,
) -> PreparedEntryComparison:
    """Prepare both policies before a caller opens any evaluation outcome."""
    _validate_fold(fold)
    _need(type(roster) is EntryRoster and type(stored) is StoredForecast and type(model) is EntryValueModel
          and type(book) is cash.CashBook and callable(read_visible), "EI_entry_compare_typed_inputs_required")
    forecast = stored.forecast
    for digest in (roster.source_sha256, stored.seal.sha256, model.model_sha256, execution_policy_sha256):
        _digest(digest)
    _need(roster.source_sha256 == stored.feature_seal.sha256 and roster.scenario == forecast.scenario
          and fold.index == forecast.index and fold.first_test_opening == forecast.first_test_opening == model.cutoff_at
          and execution_policy_sha256 == model.execution_policy_sha256,
          "EI_entry_compare_forecast_roster_training_or_policy_differs")
    # Check the policy envelope even if the fold contains no usable opportunities.
    start_episode(book, "EI-validation", fold.first_test_opening, fold.first_test_opening + MINUTE,
                  risk_budget=risk_budget, cost_bps=cost_bps)
    test_days = set(fold.test_sessions)
    originals = tuple(row for row in roster.coordinates if row.session in test_days)
    keys = tuple((row.session, row.symbol) for row in originals)
    _need(tuple(sorted(set(keys))) == keys and {day for day, _ in keys} == test_days
          and keys == tuple((row.session, row.symbol) for row in forecast.original_test_coordinates),
          "EI_entry_compare_original_test_denominator_differs")
    predictions = {(row.session, row.symbol, row.question_id): row for row in forecast.predictions}
    _need(len(predictions) == len(forecast.predictions), "EI_entry_compare_duplicate_forecast")
    inputs = []
    for original, coordinate in zip(originals, forecast.original_test_coordinates, strict=True):
        expected = CoordinateOpportunities(coordinate.session, coordinate.symbol, coordinate.status,
            len(coordinate.frames), tuple(sorted(Counter(frame.status for frame in coordinate.frames).items())),
            _opportunities(coordinate))
        _need(original == expected, "EI_entry_compare_original_reservation_or_frames_differ")
        pairs = []
        for opportunity in original.questions:
            prediction = predictions.pop((original.session, original.symbol, opportunity.question_id), None)
            _need(prediction is not None and prediction.question == opportunity.feature
                  and tuple(arm.key for arm in prediction.predictions) == MODEL_KEYS,
                  "EI_entry_compare_original_prediction_differs")
            pairs.append((opportunity, prediction))
        inputs.append((original, coordinate, pairs))
    _need(not predictions, "EI_entry_compare_extra_forecasts")
    prepared = tuple(PreparedEntryCoordinate(original, coordinate.opening, coordinate.closing,
        tuple(_prepare_pair(opportunity, prediction, read_visible, book=book, risk=risk_budget, cost=cost_bps,
                            policy=execution_policy_sha256, model=model, forecast_sha=stored.seal.sha256)
              for opportunity, prediction in pairs)) for original, coordinate, pairs in inputs)
    return PreparedEntryComparison(roster.scenario, fold, roster.source_sha256, stored.seal.sha256, model.model_sha256,
                                   execution_policy_sha256, cost_bps, roster.original_union_coordinates, prepared)


class _SessionOpens(Mapping):
    def __init__(self, session: SessionInputs):
        self.session = session
        self.receipts: dict[datetime, tuple[Fraction | None, str | None]] = {}

    def __getitem__(self, at):
        if at not in self.receipts:
            _need(self.session.opening <= at < self.session.closing, "EI_entry_compare_open_outside_original_session")
            row, issue = _price_row(self.session, at, at + 3 * MINUTE)
            self.receipts[at] = (None if issue else Fraction(row.ohlc[0]), issue)
        return self.receipts[at][0]

    def __iter__(self):
        return iter(self.receipts)

    def __len__(self):
        return len(self.receipts)


def _static_outcome(opportunity, prepared, unavailable, mode):
    if opportunity.status != "opportunity":
        _need(prepared is None, "EI_entry_compare_preparation_for_suppressed_question")
        return EntryArmOutcome(opportunity.status, ", ".join(opportunity.reasons))
    if prepared is None:
        _need(type(unavailable) is str and bool(unavailable), "EI_entry_compare_missing_preparation_reason")
        return EntryArmOutcome("unsupported", unavailable)
    _need(type(prepared) is PreparedContextEntry and prepared.mode == mode and prepared.features == opportunity.feature,
          "EI_entry_compare_prepared_opportunity_differs")
    if prepared.status == "abstain":
        return EntryArmOutcome("unsupported", prepared.reason)
    if prepared.step.action != "enter":
        return EntryArmOutcome("no_entry", prepared.reason, _NO_TRADE)
    request = prepared.step.episode.pending
    _need(request is not None and request.action == "enter"
          and request.execute_at == opportunity.feature.forecast_at + MINUTE,
          "EI_entry_compare_prepared_next_open_differs")
    return None


def _execute(prepared: PreparedContextEntry, opens: _SessionOpens | None) -> EntryArmOutcome:
    if opens is None:
        return EntryArmOutcome("unresolved", "evaluation_session_missing")
    session = opens.session
    at = prepared.visible_minute.start_at
    minute, issue = _price_row(session, at, prepared.features.forecast_at)
    _need(issue is None and minute == prepared.visible_minute, "EI_entry_compare_original_causal_minute_differs")
    execute_at = prepared.step.episode.pending.execute_at
    initial, fill = fill_context_entry(prepared, execute_at, opens[execute_at])
    if fill.outcome == "indeterminate":
        return EntryArmOutcome("unresolved", fill.reason, initial=initial, fill=fill)
    if not fill.filled:
        return EntryArmOutcome("no_fill", fill.reason, _NO_TRADE, initial, fill)
    holding = _position_replay(initial, session, opens, None)
    book = holding.episode.book
    if holding.status != "closed" or book.indeterminate or book.lots:
        return EntryArmOutcome("unresolved", holding.reason, initial=initial, fill=fill, holding=holding)
    lot, = initial.book.lots
    exits = tuple(leg for leg in book.realized_exits if leg.lot_id == lot.lot_id)
    _need(len(exits) == 1 and exits[0].quantity == lot.original_quantity == fill.quantity
          and exits[0].entry_basis == lot.entry_price * fill.quantity
          and exits[0].allocated_entry_cost == lot.original_entry_cost == fill.assumed_cost,
          "EI_entry_compare_fixed_exit_cash_binding_differs")
    exit_leg = exits[0]
    payoff = EventPayoff(exit_leg.gross_pnl, exit_leg.allocated_entry_cost, exit_leg.entry_basis)
    _need(book.cash - prepared.book_before.cash == payoff.net, "EI_entry_compare_terminal_cash_differs")
    return EntryArmOutcome("closed", holding.reason, payoff, initial, fill, holding)


def replay_entry_comparison(prepared: PreparedEntryComparison, *, read_session: ReadSession) -> EntryComparison:
    """Resolve fixed exits once per requested pair and retain unsupported rows."""
    _need(type(prepared) is PreparedEntryComparison and callable(read_session), "EI_entry_compare_preparation_required")
    coordinates = []
    for coordinate in prepared.coordinates:
        outcomes = []
        opened = False
        opens = None
        for pair in coordinate.decisions:
            baseline = _static_outcome(pair.opportunity, pair.baseline, pair.baseline_unavailable, "baseline")
            selected = _static_outcome(pair.opportunity, pair.selected, pair.selected_unavailable, "selected")
            if baseline is None or selected is None:
                if not opened:
                    opened = True
                    session = read_session(coordinate.roster.session, coordinate.roster.symbol)
                    if session is not None:
                        _session(session)
                        _need((session.symbol, session.opening, session.closing)
                              == (coordinate.roster.symbol, coordinate.opening, coordinate.closing),
                              "EI_entry_compare_original_session_differs")
                        opens = _SessionOpens(session)
                if baseline is None and selected is None:
                    _need(pair.baseline.step.episode == pair.selected.step.episode,
                          "EI_entry_compare_same_entry_request_differs")
                    baseline = selected = _execute(pair.baseline, opens)
                elif baseline is None:
                    baseline = _execute(pair.baseline, opens)
                else:
                    selected = _execute(pair.selected, opens)
            outcomes.append(EntryPairOutcome(pair, baseline, selected))
        receipts = () if opens is None else tuple((at, *row) for at, row in sorted(opens.receipts.items()))
        coordinates.append(EntryCoordinateOutcome(coordinate, tuple(outcomes), receipts))
    return EntryComparison(prepared, tuple(coordinates))
