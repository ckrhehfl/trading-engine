from contextlib import nullcontext
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from research import intraday_candidate_laggard_hold_run as hold
from research import intraday_candidate_laggard_path_run as path
from research import intraday_candidate_laggard_retry_run as run
from research import intraday_candidate_laggard_run as laggard


def test_retry_seals_both_rosters_before_opening_prior_rows_or_retry_prices(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    release = {"qualification": {"attempt": "qualification1"}}
    spec = {"laggard_parent": release, "hold_parent": release, "path_parent": release}

    def source(*_):
        return nullcontext()

    def parent_slot(unit):
        slot = tmp_path / "output" / f"{unit}-qualification1"
        slot.mkdir(parents=True)
        return slot

    rank_slot = parent_slot(run.RANK_UNIT)
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

    slot = tmp_path / "retry"
    slot.mkdir()
    reads, projected = [], []
    original_session = run._session

    def assert_sealed():
        for scenario in ("S1", "S2"):
            with (slot / f"{scenario}-pre-entry.jsonl").open(encoding="utf-8") as stream:
                plan = json.loads(stream.readline())["plan"]
            initial = datetime.fromisoformat(plan["original_decision_at"])
            assert datetime.fromisoformat(plan["decision_at"]) == initial + timedelta(minutes=5)

    def retry_parent(parent_release, unit, root, invented, publication, audit_receipt, *, load_outputs=True):
        assert parent_release is release and root == tmp_path and invented
        reads.append((unit, load_outputs))
        if unit == run.RANK_UNIT:
            assert not load_outputs
            return ranked, {}
        assert unit == run.PATH_UNIT
        if not load_outputs:
            return pathed, {}
        assert_sealed()
        return pathed, path_rows

    def session(reader, symbol, day, offsets, invented, audit):
        assert invented
        projected.append((audit, day, symbol, offsets))
        if offsets != run.gate.RETRY_GATE_OFFSETS:
            assert offsets == tuple(range(68, 124))
            assert_sealed()
            assert reads[-1] == (run.PATH_UNIT, True)
        return original_session(reader, symbol, day, offsets, invented, audit)

    def retry_source(*_):
        assert reads[-2:] == [(run.RANK_UNIT, False), (run.PATH_UNIT, False)]
        return nullcontext()

    monkeypatch.setattr(run, "_parent", retry_parent)
    monkeypatch.setattr(run, "_session", session)
    reports, outputs = run.calculate(spec, parents, slot, True, tmp_path, retry_source, None, None)
    rebuilt, _ = run.calculate(spec, parents, slot, True, tmp_path, retry_source, None, None, audit=True, stored=outputs)
    assert rebuilt == reports
    assert reads == [(run.RANK_UNIT, False), (run.PATH_UNIT, False), (run.PATH_UNIT, True)] * 2
    assert len(projected) == len(set(projected))

    for scenario, rows in outputs.items():
        states, outcomes = set(), set()
        comparison = reports[scenario]["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        for old, new in zip(path_rows[scenario], rows, strict=True):
            question, = new["questions"]
            prior, = old["questions"]
            baseline, candidate, choice = question["baseline"], question["candidate"], question["retry"]
            assert baseline == prior["weak"]
            assert question["path"] == prior["path"]
            states.add(choice["state"])
            if choice["state"] == "retain":
                assert candidate == baseline
            elif choice["state"] == "unresolved":
                assert candidate["status"] == "unresolved" and candidate["net"] is None
            else:
                assert baseline["status"] == "no_fill"
                assert baseline["entry_at"] is None
                assert candidate["requested_quantity"] == baseline["requested_quantity"] == choice["quantity"]
                outcomes.add((candidate["status"], candidate["selected_trigger"]))
        assert states == {"retain", "unresolved", "retry"}
        assert outcomes == {("closed", "structural"), ("closed", "time"), ("no_fill", "retained"),
                            ("unresolved", "unknown")}

    tampered = deepcopy(outputs)
    tampered["S1"][0]["questions"][0]["retry"]["state"] = "corrupt"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, slot, True, tmp_path, retry_source, None, None, audit=True, stored=tampered)

    invalid = next(row["questions"][0] for row in path_rows["S1"]
                   if row["questions"][0]["weak"]["status"] == "closed")
    monkeypatch.setitem(invalid["weak"], "entry_price", "1")
    with pytest.raises(ValueError, match="original entry receipt differs"):
        run.calculate(spec, parents, slot, True, tmp_path, retry_source, None, None, audit=True, stored=outputs)


@pytest.mark.parametrize("offsets", [(63, 64), tuple(range(68, 124))])
def test_local_projection_reads_only_the_requested_phase(monkeypatch, offsets):
    reader, result = object(), object()

    def project(used, symbol, day, requested, invented, audit):
        assert used is reader
        assert (symbol, day, requested, invented, audit) == ("100000", "2026-01-05", offsets, False, True)
        return result

    monkeypatch.setattr(laggard, "_project", project)
    assert run._session(reader, "100000", "2026-01-05", offsets, False, True) is result
