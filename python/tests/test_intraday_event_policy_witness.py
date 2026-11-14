"""The formal EL replay witness is deterministic and checks behavior."""

import pytest

from research import intraday_event_policy_witness as witness


def test_policy_witness_independently_checks_three_controls():
    result = witness.verify()
    assert result["ledger_rows"] == 54
    assert result["controls"]["model"]["audit"]["outcome_counts"] == {"closed": 12, "no_entry": 6}
    assert result["controls"]["model"]["choices"]["wait_reduce"] == ["wait3", 33]
    assert result["controls"]["confirmation"]["choices"]["abstain"] == ["immediate", 36]
    assert witness.verify() == result


def test_policy_witness_rejects_a_shared_but_incorrect_holding_result(monkeypatch):
    def positive_hold(_artifact, queries):
        return [witness._prediction(query, remaining_gross="1/100") for query in queries]

    monkeypatch.setattr(witness, "_predict_held", positive_hold)
    with pytest.raises(ValueError, match="wait_reduce/forecast_full_exit ledger"):
        witness.verify()
