from contextlib import nullcontext

import pytest

from research import intraday_candidate_open_late_run as run


@pytest.mark.parametrize(("day", "decision"), [
    ("2026-01-26", 344),
    ("2025-11-13", 344),
    ("2026-01-02", 284),
])
def test_projection_uses_open_and_close_relative_clocks(day, decision):
    assert run._decision_offsets(day) == (0, 29, *range(decision - 17, decision - 2))
    assert run._execution_offsets(day) == tuple(range(decision + 1, decision + 32))


def test_open_late_seals_both_rosters_before_projecting_payoffs(tmp_path, monkeypatch):
    project = run._project
    projected_outcomes = []

    def check(reader, symbol, day, offsets, invented, audit):
        if offsets == run._execution_offsets(day):
            assert all((tmp_path / f"{scenario}-pre-entry.jsonl").is_file() for scenario in ("S1", "S2"))
            projected_outcomes.append(audit)
        else:
            assert offsets == run._decision_offsets(day)
        return project(reader, symbol, day, offsets, invented, audit)

    rosters = run.laggard._rosters

    def different_rosters(root, parent, invented):
        rows = rosters(root, parent, invented)
        if parent["scenario"] == "S2":
            fold, roster = rows[19]
            rows[19] = fold, {**roster, "symbol": "100020"}
        return rows

    monkeypatch.setattr(run, "_project", check)
    monkeypatch.setattr(run.laggard, "_rosters", different_rosters)
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    source = lambda *_: nullcontext()

    reports, outputs = run.calculate({}, parents, tmp_path, True, None, source)
    rebuilt, _ = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    assert reports["S1"]["selection_seal"] != reports["S2"]["selection_seal"]
    assert set(projected_outcomes) == {False, True}
    for report in reports.values():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        stats = report["execution"]["totals"]["arms"]
        assert stats["baseline"]["closed"] > stats["candidate"]["closed"] > 0
        assert stats["baseline"]["no_fill"] > 0
        assert stats["baseline"]["unresolved_after_entry"] > 0
        assert stats["candidate"]["unresolved_before_entry"] > stats["baseline"]["unresolved_before_entry"]

    outputs["S1"][0]["questions"][0]["candidate"]["net"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
