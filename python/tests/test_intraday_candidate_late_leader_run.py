from contextlib import nullcontext
from hashlib import sha256
from types import SimpleNamespace

import pytest

from research import intraday_candidate_late_leader_run as run


def test_late_leader_seals_both_scenarios_before_execution(tmp_path, monkeypatch):
    project = run._project

    def check(reader, symbol, day, offsets, invented, audit):
        if offsets[0] > run._decision_offset(day):
            assert all((tmp_path / f"{scenario}-pre-entry.jsonl").is_file() for scenario in ("S1", "S2"))
        return project(reader, symbol, day, offsets, invented, audit)

    monkeypatch.setattr(run, "_project", check)
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}
    source = lambda *_: nullcontext()
    reports, outputs = run.calculate({}, parents, tmp_path, True, None, source)
    rebuilt, _ = run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)
    assert rebuilt == reports
    for report in reports.values():
        comparison = report["comparison"]
        assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
        stats = report["execution"]["totals"]["arms"]
        assert stats["baseline"]["closed"] > stats["candidate"]["closed"] > 0
        assert stats["baseline"]["no_fill"] > 0
        assert stats["baseline"]["unresolved_after_entry"] > 0
        assert stats["candidate"]["unresolved_before_entry"] > 0

    outputs["S1"][0]["questions"][0]["candidate"]["net"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate({}, parents, tmp_path, True, None, source, audit=True, stored=outputs)


def test_cb_rank_is_retained_and_different_roster_is_rejected(tmp_path, monkeypatch):
    raw = b"invented frozen CB report"
    (tmp_path / "CB").write_bytes(raw)
    digest = sha256(raw).hexdigest()
    spec = {"universe": {"relative_path": "CB", "size": len(raw), "sha256": digest}}
    day = "2026-01-26"
    symbols = [str(100000 + i) for i in range(20)]
    rosters = {"S1": [(1, {"session": day, "symbol": symbol}) for symbol in symbols]}
    items = [SimpleNamespace(scenario="S1", session=day, symbol=symbol, rank=20-i) for i, symbol in enumerate(symbols)]

    def load(data, pin):
        assert data == raw and pin == digest
        return SimpleNamespace(targets=items)

    monkeypatch.setattr(run.sources, "load_universe", load)
    assert run._ranks(spec, tmp_path, rosters, False)["S1", day][symbols[0]] == 20
    items[-1].symbol = "700000"
    with pytest.raises(ValueError, match="original ranked population differs"):
        run._ranks(spec, tmp_path, rosters, False)
