"""Invented stored-format join, with the decision seal preceding ledger access."""

from dataclasses import asdict
from fractions import Fraction
from hashlib import sha256
import json
from stat import S_IMODE

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_chart_cash as cash
from research import intraday_context_saved_fixed_exit_unit as unit
from research.intraday_context_economic_binding import POLICY_SHA256
from research.intraday_context_economic_fold import DecisionSeal, _plain, entry_decision_records, seal_entry_decisions
from research.intraday_context_economic_unit import _case
from research.intraday_context_entry_compare import prepare_entry_comparison, replay_entry_comparison
from research.intraday_context_entry_roster import build_entry_roster
from research.intraday_context_entry_values import fit_entry_values
from research.intraday_context_forecast_fold import forecast_fold
from research.intraday_context_forecast_seal import read_forecasts, seal_forecasts
from research.intraday_context_snapshot import FEATURE_SCHEMA, LABEL_SCHEMA
from research.intraday_scenario_snapshot import SnapshotSeal
from research.intraday_scenario_snapshot_unit import BundleSeal, _sealed_output
from test_intraday_context_entry_values import historical
from test_intraday_context_forecast_fold import adapters, fixture, label_reader


def _checkpoint(_):
    pass


def _save(path, rows):
    raw = b"".join(wire.encode(row) for row in rows)
    with _sealed_output(path) as stream:
        wire.write_all(stream, raw)
    return BundleSeal(sha256(raw).hexdigest(), len(raw))


@pytest.fixture
def stored_inputs(tmp_path):
    targets, coordinates, sessions, fold = fixture(extra=True)
    scenario = "S2"
    forecast = forecast_fold(coordinates, fold, scenario, label_reader(sessions, coordinates, []),
                             expected_targets=targets, adapters=adapters([]))
    feature = SnapshotSeal(FEATURE_SCHEMA, "a" * 64, 1024, len(coordinates))
    label = SnapshotSeal(LABEL_SCHEMA, "b" * 64, 2048, len(coordinates))
    forecast_path = tmp_path / "invented-forecast.jsonl"
    forecast_pin = seal_forecasts(forecast_path, forecast, feature, label)
    stored = read_forecasts(forecast_path, forecast_pin, feature_seal=feature, label_seal=label,
        expected_coordinates=forecast.original_test_coordinates, expected_fold=fold, expected_scenario=scenario,
        expected_adapter_mode="injected_diagnostic")
    roster = build_entry_roster(coordinates, expected_targets=targets, scenario=scenario, source_sha256=feature.sha256)
    model = fit_entry_values(tuple(historical(outcome=index, stage=stage, episode=index + 1,
        gross=Fraction(2), weight=Fraction(1, 10)) for stage in ("approach", "breakout") for index in range(10)),
        cutoff_at=fold.first_test_opening, execution_policy_sha256=POLICY_SHA256)
    prepared = prepare_entry_comparison(roster, stored, fold, model, book=cash.new_book("EI-S2"),
        risk_budget=Fraction(100_000), cost_bps=33, execution_policy_sha256=POLICY_SHA256,
        read_visible=lambda day, symbol, at: sessions[day, symbol].minutes.get(at))
    path = tmp_path / "original-decisions.jsonl"
    seal = seal_entry_decisions(path, entry_decision_records(prepared), _checkpoint)
    result = replay_entry_comparison(prepared, read_session=lambda day, symbol: sessions[day, symbol])
    rows = []
    for coordinate in result.coordinates:
        original = coordinate.prepared.roster
        rows.append(_plain({"fold": fold.index, "session": original.session, "symbol": original.symbol,
            "coordinate_status": original.coordinate_status, "original_frames": original.original_frames,
            "entry_independently_verified": True, "modeled_opens": coordinate.modeled_opens,
            "questions": [_case(pair.decisions.opportunity, pair, None, None) for pair in coordinate.outcomes]}))
    outcome_header = {"schema": unit.ECONOMIC_SCHEMA, "scenario": scenario, "cost_bps": 33,
                      "entry_decision_seals": [asdict(seal)]}
    outcome_path = tmp_path / "original-economics.jsonl"
    outcome_pin = _save(outcome_path, (outcome_header, *rows, {"records": len(rows)}))
    expected = tuple((row["fold"], row["session"], row["symbol"]) for row in rows)
    binding = {"schema": unit.DECISION_SCHEMA, "scenario": scenario, "original_ledger": asdict(outcome_pin)}
    return path, seal, stored, fold, prepared, outcome_path, outcome_pin, outcome_header, expected, binding


def _run(directory, stored_inputs):
    path, seal, stored, fold, prepared, outcome_path, outcome_pin, header, expected, binding = stored_inputs
    rows = unit._fold_decisions(path, seal, stored, fold, tuple(row.roster for row in prepared.coordinates),
                               prepared.original_union_coordinates, _checkpoint)
    return unit._seal_and_join(directory, rows, expected, binding, outcome_path, outcome_pin, header, _checkpoint)


def test_joins_exact_sealed_original_data_only_after_the_new_decision_is_durable(tmp_path, stored_inputs, monkeypatch):
    original_open = unit._sealed_input
    seen = []

    def observed(path):
        if path == stored_inputs[5]:
            seal = tmp_path / unit.DECISIONS
            assert S_IMODE(seal.stat().st_mode) == 0o400
            seen.append(sha256(seal.read_bytes()).hexdigest())
        return original_open(path)

    monkeypatch.setattr(unit, "_sealed_input", observed)
    result = _run(tmp_path, stored_inputs)
    assert seen == [result.decisions.sha256]
    assert result.decisions.coordinates == len(stored_inputs[8])
    assert sha256((tmp_path / unit.OUTPUT).read_bytes()).hexdigest() == result.output.sha256
    overall = result.report["overall"]
    assert overall["arms"]["baseline"] == overall["arms"]["sequence"] == overall["arms"]["lightgbm"]


def test_bad_causal_hash_fails_before_opening_the_outcome_ledger(tmp_path, stored_inputs, monkeypatch):
    path, seal, *rest = stored_inputs
    original_open = unit._sealed_input

    def observed(path):
        assert path != stored_inputs[5], "opened outcomes for an unverified causal stream"
        return original_open(path)

    monkeypatch.setattr(unit, "_sealed_input", observed)
    with pytest.raises(ValueError, match="input hash or EOF"):
        _run(tmp_path, (path, DecisionSeal("0" * 64, seal.size, seal.coordinates), *rest))
    assert not (tmp_path / unit.OUTPUT).exists()


def test_resealed_original_ledger_with_trailing_record_cannot_publish(tmp_path, stored_inputs):
    path, seal, stored, fold, prepared, outcome_path, _, header, expected, binding = stored_inputs
    altered = tmp_path / "original-trailing-record.jsonl"
    rows = tuple(map(json.loads, outcome_path.read_bytes().splitlines()))
    pin = _save(altered, (*rows, {"extra": True}))
    binding = {**binding, "original_ledger": asdict(pin)}
    with pytest.raises(ValueError, match="input hash or EOF"):
        _run(tmp_path, (path, seal, stored, fold, prepared, altered, pin, header, expected, binding))
    assert S_IMODE((tmp_path / unit.DECISIONS).stat().st_mode) == 0o400
    assert S_IMODE((tmp_path / unit.OUTPUT).stat().st_mode) != 0o400
