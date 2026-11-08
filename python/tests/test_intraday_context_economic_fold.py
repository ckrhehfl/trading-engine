"""Invented one-fold entry sealing and matched holding integration."""

from fractions import Fraction
from hashlib import sha256
import json

import pytest

from research import intraday_chart_cash as cash
from research.intraday_context_economic_fold import (
    DecisionSeal, _plain, entry_decision_records, fit_economic_entry_fold, run_economic_fold, seal_entry_decisions,
)
from research.intraday_context_economic_saved import (
    _decision_coordinate_bytes, _entry as audit_saved_entry, _ledger_holding, _verify_sales_basis,
)
from research.intraday_context_economic_unit import _case
from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_entry_report import summarize_matched_holding
from research.intraday_context_entry_values import fit_entry_values
from research.intraday_context_forecast_fold import forecast_fold
from research.intraday_context_forecast_seal import read_forecasts, seal_forecasts
from research.intraday_context_snapshot import (
    FEATURE_SCHEMA, LABEL_SCHEMA, LabeledCoordinate, QuestionLabel, project_coordinate,
)
from research.intraday_ed_inputs import MINUTE, InputRefusal
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_snapshot import SnapshotSeal
from research.intraday_scenario_labels import label_horizons
from test_intraday_context_entry_compare import change_minute, first_pair, prepare, replay
from test_intraday_context_entry_values import POLICY, SOURCE, historical
from test_intraday_context_forecast_fold import BASE, DAYS, adapters, fixture, inputs, label_reader


HELD_BARS = (*BASE, *((114, 115, 113, 114),) * 7)


@pytest.fixture
def data(tmp_path):
    targets, coordinates, _, fold = fixture(extra=True)
    sessions = {(row.session, row.symbol): inputs(row.session, row.symbol, HELD_BARS) for row in coordinates}
    coordinates = tuple(project_coordinate(row.targets, None if (row.session, row.symbol) == (DAYS[1], "other")
                                           else sessions[row.session, row.symbol]) for row in coordinates)
    quiet_targets = tuple(TargetCoordinate(scenario, "quiet", DAYS[1], 3, 100, "observed", b"{}")
                          for scenario in ("S1", "S2"))
    targets += quiet_targets
    quiet = project_coordinate(quiet_targets, inputs(DAYS[1], "quiet", ((100, 100, 100, 100),) * 20))
    coordinates = (*coordinates[:4], quiet, *coordinates[4:])
    forecast = forecast_fold(coordinates, fold, "S1", label_reader(sessions, coordinates, []),
                             expected_targets=targets, adapters=adapters([]))
    feature = SnapshotSeal(FEATURE_SCHEMA, "a" * 64, 1024, len(coordinates))
    label = SnapshotSeal(LABEL_SCHEMA, "b" * 64, 2048, len(coordinates))
    path = tmp_path / "invented-forecasts.jsonl"
    seal = seal_forecasts(path, forecast, feature, label)
    stored = read_forecasts(path, seal, feature_seal=feature, label_seal=label,
        expected_coordinates=forecast.original_test_coordinates, expected_fold=fold, expected_scenario="S1",
        expected_adapter_mode="injected_diagnostic")
    roster = build_entry_roster(coordinates, expected_targets=targets, scenario="S1", source_sha256=feature.sha256)
    return roster, stored, fold, sessions[DAYS[1], "invented"]


@pytest.fixture
def assembly(data, tmp_path):
    roster, stored, fold, session = data
    training = tuple(historical(outcome=index, stage=stage, episode=index + 1, gross=Fraction(2),
                                weight=Fraction(1, 10)) for stage in ("approach", "breakout") for index in range(10))
    model = fit_entry_values(training, cutoff_at=fold.first_test_opening, execution_policy_sha256=POLICY)
    events = []
    ledger = tmp_path / "invented-entry-decisions.jsonl"

    def visible(day, symbol, at):
        assert (day, symbol) == (DAYS[1], session.symbol) and not ledger.exists()
        events.append("visible")
        return session.minutes.get(at)

    def seal(records):
        receipt = seal_entry_decisions(ledger, records, lambda _: None)
        events.append("durable_readback")
        return receipt

    def prior(day, symbol):
        assert day == DAYS[0]
        return inputs(day, symbol, HELD_BARS)

    def evaluate(day, symbol):
        assert (day, symbol) == (DAYS[1], session.symbol) and ledger.is_file()
        assert "durable_readback" in events
        events.append("evaluation")
        return session

    arguments = {"training_rows": training, "book": cash.new_book("invented-EI"), "risk_budget": Fraction(100),
                 "cost_bps": 33, "execution_policy_sha256": POLICY, "training_source_sha256": SOURCE,
                 "read_visible": visible, "read_training_session": prior, "read_evaluation_session": evaluate,
                 "seal_decisions": seal, "include_reentry": True}

    def run(**changes):
        return run_economic_fold(roster, stored, fold, model, **(arguments | changes))

    return run, events, ledger


def test_complete_seal_before_single_evaluation_read_and_same_fill_fork(assembly):
    run, events, ledger = assembly
    result = run()
    assert events.count("evaluation") == 1 and events[-2:] == ["durable_readback", "evaluation"]
    assert ledger.read_bytes() == b"".join(entry_decision_records(result.entry.prepared))
    assert result.decision_seal.sha256 == sha256(ledger.read_bytes()).hexdigest()
    assert len(result.entry.coordinates) == len(result.entry_audits) == result.decision_seal.coordinates == 3
    assert result.entry_values_audit["verified"] and all(audit["verified"] for audit in result.entry_audits)
    assert result.held_pool.roster.rows
    assert result.management[1:] == result.management_audits[1:] == ((), ())
    pair = first_pair(result.entry)
    case, = (case for case in result.management[0] if case is not None)
    audit, = (audit for audit in result.management_audits[0] if audit is not None)
    assert case.initial == pair.selected.initial and case.initial_fill == pair.selected.fill
    assert case.fixed == pair.selected.holding and audit["net_by_arm"]["fixed"] == pair.selected.payoff.net
    assert audit["discretionary_decisions_checked"] > 0 and audit["reentry_reset_verified"]
    report = summarize_matched_holding((result,), expected_folds=(result.entry.prepared.fold,), include_reentry=True)
    assert report["holding"]["same_modeled_initial_fills"] == 1
    assert report["holding"]["arms"]["fixed"]["net"] == pair.selected.payoff.net
    assert report["holding"]["contrasts"][1]["paired_net_delta"] == (
        audit["net_by_arm"]["chart_context"] - audit["net_by_arm"]["fixed"])
    saved = _plain(_case(pair.decisions.opportunity, pair, case, audit))
    symbol = result.entry.coordinates[0].prepared.roster.symbol
    cost = Fraction(33, 10_000)
    _, filled, payoff = audit_saved_entry(saved["selected"], cost, symbol)
    assert filled and payoff[2] == pair.selected.payoff.net
    assert _ledger_holding(saved["same_fill_management"]["chart_context"], cost, symbol)[2] == (
        audit["net_by_arm"]["chart_context"])
    selected = saved["selected"]
    fill = selected["fill"]
    exits = saved["same_fill_management"]["chart_context"]["realized_exits"]
    price = Fraction(fill["cost"]) / cost / fill["quantity"]
    _verify_sales_basis(exits, fill["quantity"], price, True)
    with pytest.raises(ValueError, match="same-entry"):
        _verify_sales_basis(exits, fill["quantity"] + 1, price, True)
    saved["selected"]["holding"]["realized_exits"][0]["allocated_entry_cost"] = "0"
    with pytest.raises(ValueError, match="sale basis or fee"):
        audit_saved_entry(saved["selected"], cost, symbol)


def test_sealed_fold_question_digest_rejects_dropped_opportunity(assembly):
    run, _, ledger = assembly
    result = run()
    raw = json.loads(ledger.read_bytes().splitlines()[0])
    pinned = raw["header"]["question_roster_sha256"]
    saved = []
    for coordinate, cases, audits in zip(result.entry.coordinates, result.management, result.management_audits, strict=True):
        original = coordinate.prepared.roster
        saved.append({"session": original.session, "symbol": original.symbol, "coordinate_status": original.coordinate_status,
                      "original_frames": original.original_frames,
                      "questions": [_plain(_case(pair.decisions.opportunity, pair, case, audit))
                                    for pair, case, audit in zip(coordinate.outcomes, cases, audits, strict=True)]})
    assert sha256(b"".join(map(_decision_coordinate_bytes, saved))).hexdigest() == pinned
    assert saved[0]["questions"].pop()["question_sha256"]
    assert sha256(b"".join(map(_decision_coordinate_bytes, saved))).hexdigest() != pinned


@pytest.mark.parametrize("complete", [False, True])
def test_incomplete_stream_or_wrong_readback_never_opens_evaluation(assembly, complete):
    run, events, _ = assembly

    def wrong_seal(records):
        raw = b"".join(records) if complete else next(records)
        return DecisionSeal("0" * 64 if complete else sha256(raw).hexdigest(), len(raw), 3)

    with pytest.raises(InputRefusal, match="entry_decision_readback"):
        run(seal_decisions=wrong_seal)
    assert "evaluation" not in events


def test_training_source_mismatch_precedes_even_causal_evaluation_reads(assembly):
    run, events, ledger = assembly
    with pytest.raises(InputRefusal, match="training_row_outside_source_or_fold"):
        run(training_source_sha256="0" * 64)
    assert not events and not ledger.exists()


def test_unresolved_fixed_close_does_not_erase_confirmed_selected_fill(assembly, data):
    run, events, ledger = assembly
    prepared, _ = prepare(data)
    initial, _ = replay(prepared, data[-1])
    fixed_exit_at = first_pair(initial).selected.holding.episode.closed_at
    session = change_minute(data[-1], fixed_exit_at, ohlc=None, price_state="halt")

    def evaluate(day, symbol):
        assert (day, symbol) == (DAYS[1], session.symbol) and ledger.is_file()
        events.append("evaluation")
        return session

    result = run(read_evaluation_session=evaluate)
    pair = first_pair(result.entry)
    case, = (case for case in result.management[0] if case is not None)
    audit, = (audit for audit in result.management_audits[0] if audit is not None)
    assert pair.selected.status == "unresolved" and pair.selected.payoff is None and pair.selected.fill.filled
    assert case.initial_fill == pair.selected.fill and case.fixed == pair.selected.holding
    assert audit["net_by_arm"]["fixed"] is None
    report = summarize_matched_holding((result,), expected_folds=(result.entry.prepared.fold,), include_reentry=True)
    assert report["holding"]["same_modeled_initial_fills"] == 1
    assert report["holding"]["arms"]["fixed"]["unknown"] == 1
    assert report["holding"]["contrasts"][1]["paired_net_delta"] is None


def test_training_fits_full_scenario_scope_and_audits_independent_minutes(data):
    roster, _, fold, _ = data
    targets, original, _, _ = fixture(extra=True)
    training = tuple(row for row in original if row.session in fold.training_sessions
                     and any(target.scenario == "S1" for target in row.targets))
    seen = []

    def read_session(day, symbol):
        assert day in fold.training_sessions
        return inputs(day, symbol, BASE)

    def labels(day, symbol):
        coordinate = next(row for row in training if (row.session, row.symbol) == (day, symbol))
        session = read_session(day, symbol)
        cutoff = session.closing + 2 * MINUTE
        return LabeledCoordinate(day, symbol, "observed", tuple(QuestionLabel(row.question, cutoff, "observed",
            label_horizons(row.question.path, row.question.start_close, session, cutoff_at=cutoff))
            for row in coordinate.questions))

    def reference(day, symbol):
        seen.append((day, symbol))
        return read_session(day, symbol)

    parameters = {"expected_targets": targets, "read_labels": labels, "read_session": read_session,
                  "read_reference_session": reference, "book": cash.new_book("invented-EI"),
                  "risk_budget": Fraction(100), "cost_bps": 33, "training_source_sha256": SOURCE,
                  "execution_policy_sha256": POLICY, "checkpoint": lambda _: None}
    model, rows, receipt = fit_economic_entry_fold(training, roster, fold, **parameters)
    assert receipt["original_training_coordinates"] == len(training)
    assert receipt["training_rows"] == len(rows) and model.cutoff_at == fold.first_test_opening
    assert all(day in fold.training_sessions for day, _ in seen)
    with pytest.raises(ValueError, match=r"zip\(\) argument 2 is shorter"):
        fit_economic_entry_fold(training[:-1], roster, fold, **parameters)
