from contextlib import nullcontext

import pytest

from research import intraday_candidate_laggard_run as run


def test_both_scenarios_are_sealed_before_execution_and_audit_refuses_tampering(tmp_path, monkeypatch):
    project = run._project
    calls = set()

    def check_projection(reader, symbol, day, offsets, invented, audit):
        calls.add((offsets, audit))
        if offsets == run.TRADE_OFFSETS:
            assert all((tmp_path / f"{scenario}-pre-entry.jsonl").is_file() for scenario in ("S1", "S2"))
        return project(reader, symbol, day, offsets, invented, audit)

    monkeypatch.setattr(run, "_project", check_projection)
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    source = lambda *_: nullcontext()
    reports, outputs = run.calculate({}, parents, tmp_path, True, None, source)
    rebuilt, _ = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    assert calls == {(offsets, audit) for offsets in (run.PRE_ENTRY_OFFSETS, run.TRADE_OFFSETS)
                     for audit in (False, True)}
    for report in reports.values():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        panel = comparison["groups"]["all"]["totals"]
        assert panel["baseline"]["closed_trades"] > panel["candidate"]["closed_trades"] > 0
        assert all(panel["baseline"]["status_counts"][state] > 0 for state in ("no_entry", "no_fill", "unresolved"))

    outputs["S1"][0]["questions"][0]["baseline"]["net"] = "123456"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
