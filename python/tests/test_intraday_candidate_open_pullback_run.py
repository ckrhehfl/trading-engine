from contextlib import nullcontext

import pytest

from research import intraday_candidate_open_pullback_run as run
from research.intraday_candidate_open_strength_run import TRADE_OFFSETS


def test_open_pullback_seals_both_rosters_before_independent_payoffs(tmp_path, monkeypatch):
    project, seal, rosters = run._project, run.laggard._seal, run.laggard._rosters
    sealed = {False: set(), True: set()}
    projected = {False: set(), True: set()}

    def record_seal(slot, scenario, days, audit):
        result = seal(slot, scenario, days, audit)
        sealed[audit].add(scenario)
        return result

    def check_projection(reader, symbol, day, offsets, invented, audit):
        assert invented
        if offsets == TRADE_OFFSETS:
            assert offsets == tuple(range(63, 124))
            assert sealed[audit] == {"S1", "S2"}
            assert symbol in {"100000", "100001", "100002", "100003", "100004"}
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
    rebuilt, rebuilt_rows = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    assert rebuilt_rows == outputs
    assert reports["S1"]["selection_seal"] != reports["S2"]["selection_seal"]
    assert projected[False] == projected[True]
    assert len(projected[False]) == 157 * 5

    for scenario, rows in outputs.items():
        comparison = reports[scenario]["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        assert {row["fold"] for row in rows} == set(range(1, 6))

    first_day = outputs["S1"][0]["session"]
    first_questions = [row["questions"][0] for row in outputs["S1"] if row["session"] == first_day]
    assert len(first_questions) == 20
    assert [(q["baseline"]["status"], q["candidate"]["status"]) for q in first_questions[:12]] == [
        ("closed", "closed"), ("no_fill", "no_fill"), ("closed", "closed"), ("unresolved", "unresolved"),
        ("closed", "no_entry"), ("unresolved", "unresolved"), ("unresolved", "no_entry"),
        ("no_entry", "no_entry"), ("no_entry", "no_entry"), ("no_entry", "no_entry"),
        ("no_entry", "no_entry"), ("unresolved", "unresolved"),
    ]
    first = first_questions[0]["rank_decision"]
    assert (first["first_open"], first["mid_close"], first["last_close"]) == ("10000", "10066", "10033")
    assert first["rebound_reference_close"] == "10028"
    assert first_questions[0]["baseline"]["selected_trigger"] == "time"
    assert first_questions[2]["baseline"]["selected_trigger"] == "structural"
    assert first_questions[5]["candidate"]["net"] is None
    assert first_questions[6]["candidate"]["net"] == "0"
    assert [q["rank_decision"]["reason"] for q in first_questions[7:12]] == [
        "limit_at_stop", "pullback_below_half_gain", "first_half_below_threshold", "last_close_not_below_mid",
        "own_prices_unavailable",
    ]

    first_questions[0]["candidate"] = {**first_questions[0]["candidate"], "net": "1"}
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)


def test_actual_projection_passes_only_requested_offsets_to_existing_decoder(monkeypatch):
    reader, result = object(), object()

    def project(used, symbol, day, offsets, invented, audit):
        assert used is reader
        assert (symbol, day, offsets, invented, audit) == ("100000", "2026-01-05", TRADE_OFFSETS, False, True)
        return result

    monkeypatch.setattr(run.laggard, "_project", project)
    assert run._project(reader, "100000", "2026-01-05", TRADE_OFFSETS, False, True) is result
