"""Assemble one EI economic fold from caller-authenticated evidence.

The caller admits the source, reconstructs training rows, and supplies an
exclusive durable seal/readback callback. This module binds its exact decision
stream to that receipt before requesting any full evaluation session. Results
are nominal event comparisons. It owns no files, registration, or transport.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from pathlib import Path

from research import intraday_chart_cash as cash
from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_ml_labels import ChronologicalFold
from research.intraday_campaign_wsl_transport import encode, valid_sha
from research.intraday_chart_inputs import _need
from research.intraday_context_empirical import EmpiricalTrainingRow
from research.intraday_context_entry_compare import (
    EntryComparison, PreparedEntryComparison, ReadSession, ReadVisible, _SessionOpens,
    prepare_entry_comparison, replay_entry_comparison,
)
from research.intraday_context_entry_compare_reference import verify_entry_comparison_coordinate
from research.intraday_context_entry_roster import EntryRoster
from research.intraday_context_entry_training import LabelReader, build_entry_training_coordinate
from research.intraday_context_entry_training_reference import verify_entry_training_coordinate
from research.intraday_context_entry_values import EntryValueModel, HistoricalEntryPayoff, fit_entry_values
from research.intraday_context_entry_values_reference import verify_entry_values
from research.intraday_context_forecast_fold import _validate_fold
from research.intraday_context_forecast_seal import StoredForecast
from research.intraday_context_held_training import HeldTrainingPool, build_held_training_pool
from research.intraday_context_management import ContextManagementCase, replay_context_management
from research.intraday_context_management_execution_reference import verify_management_case
from research.intraday_context_snapshot import ProjectedCoordinate, _roster, _time
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_snapshot_unit import _sealed_output


@dataclass(frozen=True)
class DecisionSeal:
    """Exact bytes and coordinate count observed in the caller's durable readback."""

    sha256: str
    size: int
    coordinates: int


@dataclass(frozen=True)
class EconomicFoldResult:
    decision_seal: DecisionSeal
    entry_values_audit: dict
    entry: EntryComparison
    entry_audits: tuple[dict, ...]
    held_pool: HeldTrainingPool
    management: tuple[tuple[ContextManagementCase | None, ...], ...]
    management_audits: tuple[tuple[dict | None, ...], ...]


def fit_economic_entry_fold(
    coordinates: Iterable[ProjectedCoordinate], roster: EntryRoster, fold: ChronologicalFold, *,
    expected_targets: tuple[TargetCoordinate, ...], read_labels: LabelReader, read_session: ReadSession,
    read_reference_session: ReadSession, book: cash.CashBook, risk_budget: Fraction, cost_bps: int,
    training_source_sha256: str, execution_policy_sha256: str, checkpoint: Callable[[str], None],
) -> tuple[EntryValueModel, tuple[HistoricalEntryPayoff, ...], dict]:
    """Fit on the complete original scenario-training stream and audit each payoff.

    The caller filters its authenticated projected snapshot by scenario/date.
    Exact key, order, coverage and independent minute reproduction are checked
    here before the training rows reach the entry value fitter.
    """
    _validate_fold(fold)
    _need(type(roster) is EntryRoster and callable(read_labels) and callable(read_session)
          and callable(read_reference_session) and callable(checkpoint), "EI_economic_training_scope_differs")
    source = _roster(expected_targets)
    dates = frozenset(fold.training_sessions)
    keys = tuple(key for key, targets in source.items() if key[0] in dates
                 and any(target.scenario == roster.scenario for target in targets))
    _need(keys == tuple((item.session, item.symbol) for item in roster.coordinates if item.session in dates),
          "EI_economic_training_roster_differs")
    parameters = {"cutoff_at": fold.first_test_opening, "book": book, "risk_budget": risk_budget,
                  "cost_bps": cost_bps, "source_sha256": training_source_sha256,
                  "execution_policy_sha256": execution_policy_sha256}
    rows, counts = [], Counter()
    for key, coordinate in zip(keys, coordinates, strict=True):
        checkpoint("ei_economic_train_entry_payoff")
        _need(type(coordinate) is ProjectedCoordinate and (coordinate.session, coordinate.symbol) == key,
              "EI_economic_training_coordinate_differs")
        label = None

        def labels(day, symbol):
            nonlocal label
            _need((day, symbol) == key, "EI_economic_training_label_scope_differs")
            label = read_labels(day, symbol)
            return label

        def session(day, symbol):
            _need((day, symbol) == key, "EI_economic_training_price_scope_differs")
            return read_session(day, symbol)

        result = build_entry_training_coordinate(coordinate, labels, session,
            expected_targets=source[key], **parameters)
        reference = read_reference_session(*key) if "prices" in result.opened_inputs else None
        receipt = verify_entry_training_coordinate(result, coordinate, label, reference,
            expected_targets=source[key], **parameters)
        _need(receipt["verified"] is True and receipt["maximum_absolute_difference"] == 0,
              "EI_economic_training_reference_differs")
        rows.extend(result.rows)
        for name in ("original_questions", "training_rows", "label_supported_rows", "complete_payoff_rows",
                     "unknown_payoff_rows", "known_no_fill_rows"):
            counts[name] += receipt[name]
    model = fit_entry_values(tuple(rows), cutoff_at=fold.first_test_opening,
                             execution_policy_sha256=execution_policy_sha256)
    return model, tuple(rows), {"original_training_coordinates": len(keys), **dict(counts)}


def _plain(value):
    if type(value) is datetime:
        return _time(value)
    if type(value) is Decimal:
        _need(value.is_finite(), "EI_economic_nonfinite_decision_decimal")
        return str(value)
    if type(value) is Fraction:
        return str(value)
    if type(value) in (tuple, list):
        return [_plain(item) for item in value]
    if type(value) is dict:
        return {key: _plain(item) for key, item in value.items()}
    _need(value is None or type(value) in (bool, int, float, str), "EI_economic_decision_encoding_differs")
    return value


def entry_decision_records(prepared: PreparedEntryComparison) -> Iterator[bytes]:
    """Encode the complete original decisions one coordinate at a time."""
    _need(type(prepared) is PreparedEntryComparison, "EI_economic_prepared_decisions_required")
    header = asdict(replace(prepared, coordinates=()))
    header["coordinates"] = len(prepared.coordinates)
    roster_hash = sha256()
    for coordinate in prepared.coordinates:
        original = coordinate.roster
        questions = [[row.question_id, row.status, row.feature.question.stage, _time(row.feature.forecast_at),
                      row.feature.question.episode_origin_context] for row in original.questions]
        roster_hash.update(encode([original.session, original.symbol, original.coordinate_status,
                                   original.original_frames, questions]))
    header["question_roster_sha256"] = roster_hash.hexdigest()
    yield encode({"schema": "ei-context-entry-decisions-draft-v1", "header": _plain(header)})
    for coordinate in prepared.coordinates:
        yield encode({"coordinate": _plain(asdict(coordinate))})


def seal_entry_decisions(path: Path, records: Iterator[bytes], checkpoint: Callable[[str], None]) -> DecisionSeal:
    """Exclusively write and hash the complete bounded decision artifact."""
    maximum = 128 * wire.MIB
    digest = sha256()
    count = size = 0
    with _sealed_output(path) as stream:
        for raw in records:
            checkpoint("ei_economic_write_entry_decisions")
            _need(type(raw) is bytes and raw.endswith(b"\n") and raw.count(b"\n") == 1
                  and 0 < len(raw) <= wire.MIB and size + len(raw) <= maximum,
                  "EI_economic_entry_decision_record_bound")
            wire.write_all(stream, raw)
            digest.update(raw)
            size += len(raw)
            count += 1
    _need(count > 0 and wire.file_digest(path, maximum, checkpoint) == (digest.hexdigest(), size),
          "EI_economic_entry_decision_artifact_readback_differs")
    return DecisionSeal(digest.hexdigest(), size, count - 1)


def _seal(prepared, seal_decisions):
    digest = sha256()
    size = 0
    exhausted = False

    def records():
        nonlocal size, exhausted
        for raw in entry_decision_records(prepared):
            digest.update(raw)
            size += len(raw)
            yield raw
        exhausted = True

    receipt = seal_decisions(records())
    _need(exhausted and type(receipt) is DecisionSeal and type(receipt.size) is int
          and type(receipt.coordinates) is int
          and receipt == DecisionSeal(digest.hexdigest(), size, len(prepared.coordinates)),
          "EI_economic_entry_decision_readback_differs")
    return receipt


def run_economic_fold(
    roster: EntryRoster, stored: StoredForecast, fold: ChronologicalFold, model: EntryValueModel, *,
    training_rows: tuple[HistoricalEntryPayoff, ...], book: cash.CashBook, risk_budget: Fraction,
    cost_bps: int, execution_policy_sha256: str, training_source_sha256: str, read_visible: ReadVisible,
    read_training_session: ReadSession, read_evaluation_session: ReadSession,
    seal_decisions: Callable[[Iterator[bytes]], DecisionSeal], include_reentry: bool,
    checkpoint: Callable[[str], None] | None = None,
) -> EconomicFoldResult:
    """Check one fold's entry values, seal decisions, then replay matched arms.

    Source authentication, the complete independently reconstructed training
    rows (including unavailable payoffs), and durable readback remain with the caller.
    The causal point reader, training reader, and full evaluation reader have
    distinct scopes. A confirmed selected fill retains a holding comparison
    even when its original fixed exit is unresolved.
    """
    _validate_fold(fold)
    _need(type(roster) is EntryRoster and type(training_rows) is tuple and type(model) is EntryValueModel
          and type(cost_bps) is int and cost_bps in (33, 43)
          and valid_sha(training_source_sha256) and type(include_reentry) is bool
          and all(callable(reader) for reader in (read_visible, read_training_session, read_evaluation_session,
                                                   seal_decisions))
          and (checkpoint is None or callable(checkpoint)), "EI_economic_fold_inputs_differ")
    training_days = set(fold.training_sessions)
    training_keys = {(row.session, row.symbol) for row in roster.coordinates if row.session in training_days}
    _need(all(type(row) is HistoricalEntryPayoff and type(row.question) is EmpiricalTrainingRow
              and row.source_sha256 == training_source_sha256
              and (row.question.session, row.question.symbol) in training_keys for row in training_rows),
          "EI_economic_training_row_outside_source_or_fold")
    prepared = prepare_entry_comparison(roster, stored, fold, model, book=book, risk_budget=risk_budget,
        cost_bps=cost_bps, execution_policy_sha256=execution_policy_sha256, read_visible=read_visible)
    values = tuple(pair.selected.entry_value for coordinate in prepared.coordinates for pair in coordinate.decisions
                   if pair.selected is not None and pair.selected.entry_value is not None)
    values_audit = verify_entry_values(model, training_rows, cutoff_at=fold.first_test_opening,
        execution_policy_sha256=execution_policy_sha256, queries=tuple(value.query for value in values), results=values)
    receipt = _seal(prepared, seal_decisions)

    def read_train(day, symbol):
        _need((day, symbol) in training_keys, "EI_economic_training_reader_outside_original_fold")
        return read_training_session(day, symbol)

    def read_prior(day, symbol, pin):
        _need(pin == training_source_sha256, "EI_economic_held_prior_pin_differs")
        return read_train(day, symbol)

    held_pool = build_held_training_pool(roster, fold=fold, read_session=read_train, book=book,
        risk_budget=risk_budget, cost_bps=cost_bps, source_sha256=training_source_sha256,
        execution_policy_sha256=execution_policy_sha256, checkpoint=checkpoint)
    outcomes, entry_audits, management, management_audits = [], [], [], []
    for coordinate in prepared.coordinates:
        if checkpoint is not None:
            checkpoint("ei_economic_evaluation_coordinate")
        key = coordinate.roster.session, coordinate.roster.symbol
        session = None
        opened = False

        def read_eval(day, symbol):
            nonlocal session, opened
            _need((day, symbol) == key, "EI_economic_evaluation_reader_outside_coordinate")
            if not opened:
                session = read_evaluation_session(day, symbol)
                opened = True
            return session

        result, = replay_entry_comparison(replace(prepared, coordinates=(coordinate,)), read_session=read_eval).coordinates
        # The entry comparison opens a coordinate only for a pending request.
        entry_audits.append(verify_entry_comparison_coordinate(result, coordinate, session))
        cases, audits = [], []
        for pair in result.outcomes:
            selected = pair.selected
            case = audit = None
            if selected.fill is not None and selected.fill.filled:
                _need(session is not None, "EI_economic_selected_session_missing")
                opens = _SessionOpens(session)
                opens.receipts.update({at: (price, reason) for at, price, reason in result.modeled_opens})
                case = replay_context_management(pair.decisions.opportunity, pair.decisions.selected,
                    session, opens, held_pool.roster, read_prior_session=read_prior, include_reentry=include_reentry)
                _need(case.initial == selected.initial and case.initial_fill == selected.fill
                      and case.fixed == selected.holding, "EI_economic_selected_initial_or_fixed_arm_differs")
                audit = verify_management_case(case, original_initial=selected.initial,
                    original_entry=pair.decisions.opportunity.feature, original_session=session,
                    original_history=held_pool.roster.rows, fold=fold, read_prior_session=read_prior,
                    include_reentry=include_reentry)
            cases.append(case)
            audits.append(audit)
        outcomes.append(result)
        management.append(tuple(cases))
        management_audits.append(tuple(audits))
    return EconomicFoldResult(receipt, values_audit, EntryComparison(prepared, tuple(outcomes)), tuple(entry_audits),
                              held_pool, tuple(management), tuple(management_audits))
