from copy import deepcopy
from hashlib import sha256
from io import BytesIO

import pytest

from research.intraday_context_cost_census import calculate
from research.intraday_context_cost_census_inputs import _Rows, invented_rows, saved_rows, write_invented_inputs
from research.intraday_context_cost_census_reference import verify


def test_full_invented_census_counts_all_original_weeks_and_exact_cost_boundary():
    result = calculate(invented_rows())
    verify(invented_rows(), result)
    assert (result["sessions"], result["coordinates"], result["weeks"]) == (157, 3140, 34)
    assert result["frame_states"]["unknown"] == 9420
    assert result["frames"] == 62800
    all_groups = result["groups"]["all"]
    first, last = all_groups["weekly"]["2026-W05"], all_groups["weekly"]["2026-W38"]
    assert sum(last.values()) == 0
    assert first["above"] == first["equal"] == first["below"] == 9
    assert first["unsupported"] == first["unresolved"] == 6
    assert all_groups["totals"]["above"] == (
        result["groups"]["A_or_B"]["totals"]["above"] + result["groups"]["other"]["totals"]["above"])


def test_independent_verification_rejects_unrelated_price_at_recorded_execution_clock():
    row, roster = next((row, roster) for row, roster in invented_rows()
                       if row["questions"] and row["questions"][0]["baseline"]["status"] == "closed")
    result = calculate([(row, roster)])
    tampered = deepcopy(row)
    tampered["modeled_opens"][1][1] = "10500"
    with pytest.raises(ValueError, match="open or sale cash"):
        verify([(tampered, roster)], result)


def test_sealed_stream_refuses_trailing_rows_and_wrong_digest():
    raw = b'{"records":0}\n'
    pin = {"sha256": sha256(raw).hexdigest()}
    clean = _Rows(BytesIO(raw))
    assert clean.next() == {"records": 0}
    clean.finish(pin)
    for payload in (raw + raw, b'{"records":1}\n'):
        changed = _Rows(BytesIO(payload))
        changed.next()
        with pytest.raises(ValueError, match="digest or EOF"):
            changed.finish(pin)


def test_full_synthetic_stream_matches_direct_fixture_and_rejects_fold_substitution(tmp_path):
    pin = write_invented_inputs(tmp_path, "S1")
    assert calculate(saved_rows(tmp_path, pin)) == calculate(invented_rows())
    changed = deepcopy(pin)
    changed["decisions"][0]["sha256"] = changed["decisions"][1]["sha256"]
    with pytest.raises(ValueError, match="ledger header differs"):
        calculate(saved_rows(tmp_path, changed))
