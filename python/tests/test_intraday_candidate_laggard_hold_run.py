from contextlib import nullcontext

import pytest

from research import intraday_candidate_laggard_hold_run as run
from research import intraday_candidate_laggard_run as laggard


def test_extended_holds_only_project_original_time_exits_and_audit_all_rows(tmp_path, monkeypatch):
    parents = {"inputs": [{"scenario": "S1"}, {"scenario": "S2"}]}

    def source(*_):
        return nullcontext()

    original_slot = tmp_path / "laggard"
    original_slot.mkdir()
    original, rows = laggard.calculate({}, parents, original_slot, True, None, source)
    spec = {"laggard_parent": {"qualification": {"attempt": "qualification1"}}}

    def parent(release, unit, root, invented, publication, audit_receipt):
        assert release is spec["laggard_parent"]
        assert unit == "EJ-ENTRY-OPENING-LAGGARDS"
        assert root == tmp_path and invented
        return original, rows

    eligible = {
        (row["session"], row["symbol"])
        for panel in rows.values() for row in panel
        if row["questions"][0]["baseline"]["status"] == "closed"
        and row["questions"][0]["baseline"]["selected_trigger"] == "time"
    }
    projected = []
    original_project = laggard._project

    def project(reader, symbol, day, offsets, invented, audit):
        assert offsets == tuple(range(89, 154))
        assert invented and (day, symbol) in eligible
        projected.append((audit, day, symbol))
        return original_project(reader, symbol, day, offsets, invented, audit)

    monkeypatch.setattr(run, "_parent", parent)
    monkeypatch.setattr(laggard, "_project", project)
    reports, outputs = run.calculate(spec, parents, tmp_path, True, tmp_path, source, None, None)
    rebuilt, _ = run.calculate(spec, parents, tmp_path, True, tmp_path, source, None, None,
                               audit=True, stored=outputs)
    assert rebuilt == reports
    assert len(projected) == len(set(projected)) == 2 * len(eligible)

    for scenario, panel in outputs.items():
        paths = set()
        for old, new in zip(rows[scenario], panel, strict=True):
            question, = new["questions"]
            baseline = question["baseline"]
            assert baseline == old["questions"][0]["baseline"]
            if (new["session"], new["symbol"]) not in eligible:
                assert baseline == question["candidate"] == question["hold90"]
            else:
                paths.add((question["candidate"]["selected_trigger"], question["hold90"]["selected_trigger"]))
        assert {("structural", "structural"), ("time", "structural"), ("unknown", "unknown"), ("time", "time")} <= paths
        for contrast in reports[scenario].values():
            comparison = contrast["comparison"]
            assert (comparison["coordinates"], comparison["sessions"], comparison["weeks"]) == (3140, 157, 34)
            assert (comparison["groups"]["all"]["totals"]["baseline"]
                    == original[scenario]["comparison"]["groups"]["all"]["totals"]["baseline"])

    row = next(row for row in outputs["S1"] if (row["session"], row["symbol"]) in eligible)
    row["questions"][0]["hold90"]["exit_price"] = "1"
    with pytest.raises(ValueError, match="audited trade differs"):
        run.calculate(spec, parents, tmp_path, True, tmp_path, source, None, None, audit=True, stored=outputs)
