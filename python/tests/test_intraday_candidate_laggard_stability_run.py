from collections import Counter
from contextlib import nullcontext

import pytest

from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_laggard_stability_run as run


def test_sealed_controls_and_delayed_arms_replay_independently(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    source = lambda *_: nullcontext()
    original_slot = tmp_path / "output" / f"{run.PARENT_UNIT}-qualification1"
    original_slot.mkdir(parents=True)
    original, rows = laggard.calculate({}, parents, original_slot, True, None, source)
    slot = tmp_path / "stability"
    slot.mkdir()

    def parent(*_, load_outputs=True):
        if load_outputs:
            assert all((slot / f"{scenario}-pre-entry.jsonl").is_file() for scenario in original)
        return original, rows if load_outputs else {}

    monkeypatch.setattr(run, "_parent", parent)
    spec = {"laggard_parent": {"qualification": {"attempt": "qualification1"}}}
    reports, outputs = run.calculate(spec, parents, slot, True, tmp_path, source, None, None)
    rebuilt, _ = run.calculate(spec, parents, slot, True, tmp_path, source, None, None, audit=True, stored=outputs)
    assert rebuilt == reports
    for scenario, report in reports.items():
        comparison = report["immediate_vs_wait"]["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        confirmed = Counter(row["questions"][0]["confirmation"]["state"] for row in outputs[scenario]
                            if row["questions"][0]["confirmation"] is not None)
        assert all(confirmed[state] > 0 for state in ("select", "decline", "unresolved"))

    outputs["S1"][0]["questions"][0]["confirmed"]["net"] = "123456"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, slot, True, tmp_path, source, None, None, audit=True, stored=outputs)
