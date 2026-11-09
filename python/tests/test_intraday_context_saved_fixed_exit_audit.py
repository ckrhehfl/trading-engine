"""Independent recount and tamper checks for invented saved fixed exits."""

import json
from dataclasses import asdict

import pytest

from research import intraday_context_saved_fixed_exit_unit as unit
from research.intraday_context_saved_fixed_exit_audit import audit_saved_fixed_exit
from test_intraday_context_saved_fixed_exit_unit import _checkpoint, _run, _save, stored_inputs


@pytest.fixture
def comparison(tmp_path, stored_inputs):
    path, seal, stored, fold, prepared, ledger, _, header, expected, _ = stored_inputs
    original = list(map(json.loads, ledger.read_bytes().splitlines()))
    snapshot = {"sha256": "1" * 64, "size": 1024}
    forecast = {"sha256": "2" * 64, "size": 2048}
    header = {**header, "snapshot": snapshot, "forecast": forecast}
    original_path = tmp_path / "pinned-original-economics.jsonl"
    original_pin = _save(original_path, (header, *original[1:]))
    binding = {"schema": unit.DECISION_SCHEMA, "scenario": "S2", "cost_bps": 33, "replacement": "context_lightgbm",
               "snapshot": snapshot, "forecast": forecast, "original_decisions": [asdict(seal)],
               "original_ledger": asdict(original_pin)}
    result = _run(tmp_path, (path, seal, stored, fold, prepared, original_path, original_pin, header, expected, binding))
    return result, original_path, original_pin, expected, binding


def _audit(directory, comparison, **changes):
    result, original_path, original_pin, expected, binding = comparison
    inputs = {"outcome_pin": original_pin, "decisions": result.decisions, "output": result.output,
              "binding": binding, "expected_coordinates": expected, "checkpoint": _checkpoint}
    return audit_saved_fixed_exit(directory, original_path, **(inputs | changes))


def test_recounts_full_report_from_sealed_inputs(tmp_path, comparison):
    result, _, _, expected, _ = comparison
    proof = _audit(tmp_path, comparison)
    assert proof["verified"] and proof["full_eof_verified"]
    assert proof["coordinates"] == len(expected)
    assert proof["report"] == result.report


@pytest.mark.parametrize(("mutate", "message"), (("row", "projected question row"),
                                                ("footer", "independent report"),
                                                ("tail", "input hash or EOF")))
def test_rejects_resealed_false_rows_totals_or_trailing_data(tmp_path, comparison, mutate, message):
    alternate = tmp_path / "forged"
    alternate.mkdir()
    _save(alternate / unit.DECISIONS, map(json.loads, (tmp_path / unit.DECISIONS).read_bytes().splitlines()))
    rows = list(map(json.loads, (tmp_path / unit.OUTPUT).read_bytes().splitlines()))
    if mutate == "row":
        row = next(question for item in rows[1:-1] for question in item["questions"]
                   if question["lightgbm"]["status"] == "closed")
        row["lightgbm"]["gross"] = "0"
    elif mutate == "footer":
        rows[-1]["report"]["weekly"][0]["modeled_fills"]["lightgbm"] += 1
    else:
        rows.append({"extra": True})
    forged = _save(alternate / unit.OUTPUT, rows)
    with pytest.raises(ValueError, match=message):
        _audit(alternate, comparison, output=forged)
