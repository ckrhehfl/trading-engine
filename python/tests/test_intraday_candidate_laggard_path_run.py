from contextlib import nullcontext
from copy import deepcopy

import pytest

from research import intraday_candidate_laggard_hold_run as hold
from research import intraday_candidate_laggard_path_run as run
from research import intraday_candidate_laggard_run as laggard


def test_opening_paths_seal_both_scenarios_before_opening_held_payoffs(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    spec = {
        "laggard_parent": {"qualification": {"attempt": "qualification1"}},
        "hold_parent": {"qualification": {"attempt": "qualification1"}},
    }

    def source(*_):
        return nullcontext()

    rank_slot = tmp_path / "output" / f"{run.RANK_UNIT}-qualification1"
    rank_slot.mkdir(parents=True)
    original, rank_rows = laggard.calculate({}, parents, rank_slot, True, tmp_path, source)
    monkeypatch.setattr(hold, "_parent", lambda *_: (original, rank_rows))
    held, held_rows = hold.calculate(spec, parents, tmp_path, True, tmp_path, source, None, None)

    path_slot = tmp_path / "paths"
    path_slot.mkdir()
    reads = []

    def parent(release, unit, root, invented, publication, audit_receipt, *, load_outputs=True):
        assert root == tmp_path and invented
        reads.append((unit, load_outputs))
        if unit == run.RANK_UNIT:
            assert release is spec["laggard_parent"]
            assert not load_outputs
            return original, {}
        assert unit == run.HOLD_UNIT
        assert release is spec["hold_parent"] and load_outputs
        for scenario in ("S1", "S2"):
            assert (path_slot / f"{scenario}-pre-entry.jsonl").stat().st_size > 0
        return held, held_rows

    eligible = {
        (row["session"], row["symbol"])
        for panel in rank_rows.values()
        for row in panel
        if run.path.ready(row["questions"][0]["rank_decision"])
    }
    projected = []
    original_project = laggard._project

    def project(reader, symbol, day, offsets, invented, audit):
        assert offsets == (0, 29, 59)
        assert invented and (day, symbol) in eligible
        projected.append((audit, day, symbol))
        return original_project(reader, symbol, day, offsets, invented, audit)

    monkeypatch.setattr(run, "_parent", parent)
    monkeypatch.setattr(laggard, "_project", project)
    reports, outputs = run.calculate(spec, parents, path_slot, True, tmp_path, source, None, None)
    rebuilt, _ = run.calculate(spec, parents, path_slot, True, tmp_path, source, None, None,
                               audit=True, stored=outputs)
    assert rebuilt == reports
    assert reads == [(run.RANK_UNIT, False), (run.HOLD_UNIT, True)] * 2
    assert len(projected) == len(set(projected)) == 2 * len(eligible)

    for scenario, rows in outputs.items():
        paths = set()
        for old, new in zip(held_rows[scenario], rows, strict=True):
            question, = new["questions"]
            baseline = question["baseline"]
            assert baseline == old["questions"][0]["candidate"]
            state = question["path"]["state"]
            paths.add(state)
            rebound, weak = question["candidate"], question["weak"]
            if state == "retain":
                assert rebound == weak == baseline
            elif state == "unresolved":
                assert (rebound["status"], weak["status"]) == ("unresolved", "unresolved")
                assert rebound["net"] is weak["net"] is None
            else:
                selected, skipped = (rebound, weak) if state == "rebound" else (weak, rebound)
                assert selected == baseline
                assert (skipped["status"], skipped["net"]) == ("no_entry", "0")
        assert paths == {"retain", "rebound", "weak", "unresolved"}

    tampered = deepcopy(outputs)
    tampered["S1"][0]["questions"][0]["path"]["state"] = "other"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, path_slot, True, tmp_path, source, None, None, audit=True, stored=tampered)
