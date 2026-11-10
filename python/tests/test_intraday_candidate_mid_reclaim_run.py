from contextlib import nullcontext
from datetime import datetime
from fractions import Fraction

import pytest

from research import intraday_candidate_mid_reclaim_run as run
from research.intraday_ed_inputs import MINUTE
from research.intraday_pattern_calibration import session_open


def test_mid_reclaim_seals_both_rosters_then_rebuilds_independent_payoffs(tmp_path, monkeypatch):
    project, seal, rosters = run._project, run.laggard._seal, run.laggard._rosters
    sealed = {False: set(), True: set()}
    projected = {False: set(), True: set()}

    def record_seal(slot, scenario, days, audit):
        result = seal(slot, scenario, days, audit)
        sealed[audit].add(scenario)
        return result

    def check_projection(reader, symbol, day, offsets, invented, audit):
        assert invented
        if offsets == tuple(range(123, 154)):
            assert sealed[audit] == {"S1", "S2"}
            assert (day, symbol) not in projected[audit]
            projected[audit].add((day, symbol))
        else:
            assert offsets == (*range(30, 60), *range(114, 120))
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
    rebuilt, audit_outputs = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    assert audit_outputs == outputs
    assert reports["S1"]["selection_seal"] != reports["S2"]["selection_seal"]
    assert projected[False] == projected[True]

    ready = set()
    for scenario, rows in outputs.items():
        comparison = reports[scenario]["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        for row in rows:
            decision = row["questions"][0]["rank_decision"]
            if decision["rank_state"] == "select" and decision["order_state"] == "ready":
                ready.add((row["session"], row["symbol"]))
    assert ready == projected[False]

    first_day = outputs["S1"][0]["session"]
    questions = {int(row["symbol"].removeprefix("KRX:")) - 100000: row["questions"][0]
                 for row in outputs["S1"] if row["session"] == first_day}
    opening = session_open(first_day)

    def clock(text):
        return (datetime.fromisoformat(text) - opening) // MINUTE

    def statuses(member):
        question = questions[member]
        return question["baseline"]["status"], question["candidate"]["status"]

    assert clock(questions[0]["decision_at"]) == 122
    arm = questions[0]["baseline"]
    assert arm == questions[0]["candidate"]
    assert arm["selected_trigger"] == "time"
    assert (clock(arm["entry_request_at"]), clock(arm["entry_at"]), clock(arm["exit_at"])) == (122, 123, 153)
    assert arm["entry_price"] == "10050"
    assert Fraction(arm["cost"]) == Fraction(33, 10000) * Fraction(arm["entry_notional"])
    assert Fraction(arm["net"]) == Fraction(arm["gross"]) - Fraction(arm["cost"])

    arm = questions[2]["baseline"]
    assert arm["selected_trigger"] == "structural"
    assert (clock(arm["trigger_start_at"]), clock(arm["exit_request_at"]), clock(arm["exit_at"])) == (125, 128, 129)
    assert arm["trigger_close"] == "9950"
    assert questions[16]["baseline"]["selected_trigger"] == "time"
    assert clock(questions[16]["baseline"]["exit_at"]) == 153

    for member in (1, 13):
        assert statuses(member) == ("no_fill", "no_fill")
        assert questions[member]["baseline"]["reason"] == "open_outside_frozen_entry_bounds"
    for member, reason in ((3, "first_structural_close_unknown"), (14, "entry_price_unknown"),
                           (15, "exit_price_unknown")):
        assert statuses(member) == ("unresolved", "unresolved")
        assert questions[member]["baseline"]["reason"] == reason
    for member in (4, 17):
        assert statuses(member) == ("closed", "no_entry")
    assert statuses(6) == ("unresolved", "no_entry")
    for member in (5, 18):
        assert statuses(member) == ("unresolved", "unresolved")
        assert questions[member]["candidate"]["reason"] == "reclaim_hold_close_unavailable"
    for member in (7, 8, 11, 12):
        assert statuses(member) == ("unresolved", "unresolved")
    for member in (9, 10, 19):
        assert statuses(member) == ("no_entry", "no_entry")

    outputs["S1"][0]["questions"][0]["candidate"]["net"] = "1"
    projected[True].clear()
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)


@pytest.mark.parametrize("audit", [False, True])
def test_actual_projection_delegates_only_requested_offsets(monkeypatch, audit):
    reader, result = object(), object()

    def project(used, symbol, day, offsets, invented, audited):
        assert used is reader
        assert (symbol, day, offsets, invented, audited) == ("100000", "2026-01-05", run.TRADE_OFFSETS, False, audit)
        return result

    monkeypatch.setattr(run.laggard, "_project", project)
    assert run._project(reader, "100000", "2026-01-05", run.TRADE_OFFSETS, False, audit) is result
