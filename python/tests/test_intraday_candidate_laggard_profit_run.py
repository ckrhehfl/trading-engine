from contextlib import nullcontext

import pytest

from research import intraday_candidate_laggard_profit_run as run
from research import intraday_candidate_laggard_run as laggard


def test_profit_replay_preserves_parent_and_audits_capacity(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    source = lambda *_: nullcontext()
    original_slot = tmp_path / "laggard"
    original_slot.mkdir()
    original, rows = laggard.calculate({}, parents, original_slot, True, None, source)
    monkeypatch.setattr(run, "_parent", lambda *_: (original, rows))
    spec = {"laggard_parent": {}}
    reports, outputs = run.calculate(spec, parents, tmp_path, True, None, source, None, None)
    rebuilt, _ = run.calculate(spec, parents, tmp_path, True, None, source, None, None, audit=True, stored=outputs)
    assert rebuilt == reports
    for scenario, report in reports.items():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        panel = comparison["groups"]["all"]["totals"]
        assert panel["baseline"] == original[scenario]["comparison"]["groups"]["all"]["totals"]["baseline"]
        assert panel["selected_triggers"]["profit66"] > 0
        assert report["path_capacity"]["groups"]["all"]["totals"]["resolved_capacity"] == panel["baseline"]["closed_trades"]

    row = next(row for row in outputs["S1"] if row["questions"][0]["exit_capacity"] is not None)
    row["questions"][0]["exit_capacity"]["best_open"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, tmp_path, True, None, source, None, None, audit=True, stored=outputs)
