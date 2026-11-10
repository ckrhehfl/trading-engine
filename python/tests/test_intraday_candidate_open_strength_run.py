from contextlib import nullcontext

import pytest

from research import intraday_candidate_open_strength_run as run


def test_open_strength_seals_both_scenarios_before_projecting_payoffs(tmp_path, monkeypatch):
    project, seal, rosters = run._project, run.laggard._seal, run.laggard._rosters
    sealed = {False: set(), True: set()}
    projected = {False: set(), True: set()}

    def record_seal(slot, scenario, days, audit):
        result = seal(slot, scenario, days, audit)
        sealed[audit].add(scenario)
        return result

    def check_projection(reader, symbol, day, offsets, invented, audit):
        assert invented
        if offsets == run.TRADE_OFFSETS:
            assert offsets == tuple(range(63, 124))
            assert sealed[audit] == {"S1", "S2"}
            assert symbol not in ("100004", "100005")
            projected[audit].add((day, symbol))
        else:
            assert offsets == (0, 29, *range(45, 60))
        return project(reader, symbol, day, offsets, invented, audit)

    def different_rosters(root, parent, invented):
        rows = rosters(root, parent, invented)
        if parent["scenario"] == "S2":
            fold, roster = rows[0]
            rows[0] = fold, {**roster, "symbol": "100020"}
        return rows

    monkeypatch.setattr(run, "_project", check_projection)
    monkeypatch.setattr(run.laggard, "_seal", record_seal)
    monkeypatch.setattr(run.laggard, "_rosters", different_rosters)
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}

    def source(*_):
        return nullcontext()

    reports, outputs = run.calculate({}, parents, tmp_path, True, None, source)
    rebuilt, _ = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    assert reports["S1"]["selection_seal"] != reports["S2"]["selection_seal"]
    assert projected[False] == projected[True]
    assert projected[False]

    for scenario, rows in outputs.items():
        comparison = reports[scenario]["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        questions = [row["questions"][0] for row in rows]
        statuses = set()
        exits = set()
        for question in questions:
            entry = question["rank_decision"]
            baseline, candidate = question["baseline"], question["candidate"]
            statuses.add((baseline["status"], candidate["status"]))
            if baseline["status"] == "closed":
                exits.add(baseline["selected_trigger"])
            if entry["gated_state"] == entry["rank_state"]:
                assert candidate == baseline
            elif entry["gated_state"] == "decline":
                assert candidate["net"] == "0"
            else:
                assert candidate["net"] is None
        assert exits == {"structural", "time"}
        assert {("closed", "closed"), ("closed", "no_entry"), ("closed", "unresolved"),
                ("no_fill", "no_fill"), ("unresolved", "no_entry")} <= statuses

    outputs["S1"][0]["questions"][0]["candidate"]["net"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)


def test_actual_projection_delegates_only_requested_offsets(monkeypatch):
    reader, result = object(), object()

    def project(used, symbol, day, offsets, invented, audit):
        assert used is reader
        assert (symbol, day, offsets, invented, audit) == ("100000", "2026-01-05", run.TRADE_OFFSETS, False, True)
        return result

    monkeypatch.setattr(run.laggard, "_project", project)
    assert run._project(reader, "100000", "2026-01-05", run.TRADE_OFFSETS, False, True) is result
