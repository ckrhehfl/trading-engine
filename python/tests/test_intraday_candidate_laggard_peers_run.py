from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace

import pytest

from research import intraday_candidate_laggard_hold_run as hold
from research import intraday_candidate_laggard_path_run as path
from research import intraday_candidate_laggard_peers_run as run
from research import intraday_candidate_laggard_run as laggard
from research.intraday_ed_inputs import MINUTE


def test_peer_paths_seal_both_rosters_before_opening_weak_payoffs(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    release = {"qualification": {"attempt": "qualification1"}}
    spec = {"laggard_parent": release, "hold_parent": release, "path_parent": release}

    def source(*_):
        return nullcontext()

    def parent_slot(unit):
        slot = tmp_path / "output" / f"{unit}-qualification1"
        slot.mkdir(parents=True)
        return slot

    rank_slot = parent_slot(path.RANK_UNIT)
    ranked, rank_rows = laggard.calculate({}, parents, rank_slot, True, tmp_path, source)
    monkeypatch.setattr(hold, "_parent", lambda *_: (ranked, rank_rows))
    held, held_rows = hold.calculate(spec, parents, tmp_path, True, tmp_path, source, None, None)

    def path_parent(_, unit, *args, load_outputs=True):
        if unit == path.RANK_UNIT:
            assert not load_outputs
            return ranked, {}
        assert unit == path.HOLD_UNIT and load_outputs
        return held, held_rows

    monkeypatch.setattr(path, "_parent", path_parent)
    path_slot = parent_slot(run.PATH_UNIT)
    pathed, path_rows = path.calculate(spec, parents, path_slot, True, tmp_path, source, None, None)
    slot = tmp_path / "peers"
    slot.mkdir()
    reads = []

    def peers_parent(parent_release, unit, root, invented, publication, audit_receipt, *, load_outputs=True):
        assert parent_release is release and unit == run.PATH_UNIT and root == tmp_path and invented
        reads.append(load_outputs)
        if not load_outputs:
            return pathed, {}
        for scenario in ("S1", "S2"):
            assert (slot / f"{scenario}-pre-entry.jsonl").stat().st_size > 0
        return pathed, path_rows

    weak_by_day = {}
    for rows in path_rows.values():
        for row in rows:
            if row["questions"][0]["path"]["state"] == "weak":
                weak_by_day.setdefault(row["session"], set()).add(row["symbol"])
    missing_peers_day = min(weak_by_day)
    assert len(weak_by_day[missing_peers_day]) <= 8
    projected = []
    original_session = run._session

    def session(reader, symbol, day, invented, audit):
        assert invented and day in weak_by_day
        projected.append((audit, day, symbol))
        result = original_session(reader, symbol, day, invented, audit)
        if day != missing_peers_day or symbol in weak_by_day[day]:
            return result
        minutes = dict(result.minutes)
        minutes.pop(result.opening + 29 * MINUTE)
        return replace(result, minutes=minutes)

    monkeypatch.setattr(run, "_parent", peers_parent)
    monkeypatch.setattr(run, "_session", session)
    reports, outputs = run.calculate(spec, parents, slot, True, tmp_path, source, None, None)
    rebuilt, _ = run.calculate(spec, parents, slot, True, tmp_path, source, None, None, audit=True, stored=outputs)
    assert rebuilt == reports
    assert reads == [False, True, False, True]
    coordinates = {(row["session"], row["symbol"]) for panel in path_rows.values() for row in panel
                   if row["session"] in weak_by_day}
    assert len(projected) == len(set(projected)) == 2 * len(coordinates)

    for scenario, rows in outputs.items():
        states = set()
        for old, new in zip(path_rows[scenario], rows, strict=True):
            question, = new["questions"]
            prior, = old["questions"]
            baseline = question["baseline"]
            assert baseline == prior["weak"]
            assert question["path"] == prior["path"]
            state = question["peer"]["state"]
            states.add(state)
            weaker, other = question["candidate"], question["other"]
            if state == "retain":
                assert weaker == other == baseline
            elif state == "unresolved":
                assert (weaker["status"], other["status"]) == ("unresolved", "unresolved")
                assert weaker["net"] is other["net"] is None
            else:
                selected, skipped = (weaker, other) if state == "weaker" else (other, weaker)
                assert selected == baseline
                assert (skipped["status"], skipped["net"]) == ("no_entry", "0")
        assert states == {"retain", "weaker", "other", "unresolved"}

    tampered = deepcopy(outputs)
    tampered["S1"][0]["questions"][0]["peer"]["state"] = "corrupt"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, slot, True, tmp_path, source, None, None, audit=True, stored=tampered)


def test_local_projection_requests_only_two_causal_bars(monkeypatch):
    reader = object()
    result = object()

    def project(used, symbol, day, offsets, invented, audit):
        assert used is reader
        assert (symbol, day, offsets, invented, audit) == ("100000", "2026-01-05", (29, 59), False, True)
        return result

    monkeypatch.setattr(laggard, "_project", project)
    assert run._session(reader, "100000", "2026-01-05", False, True) is result
