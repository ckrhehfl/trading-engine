"""Invented full-stream and resealed scientific mutation checks for EG."""

from copy import deepcopy
from datetime import date, timedelta

import pytest

from research import intraday_ed_sources as sources
from research import intraday_highvol_snapshot_io as storage
from research import intraday_highvol_snapshot_unit as unit
from research.intraday_highvol_coverage import write_coverage
from research.intraday_highvol_invented import _chunk
from research.intraday_highvol_population import iter_coverage_records
from research.intraday_highvol_snapshot_reference import ScenarioVerificationRefusal, verify_snapshot


def checkpoint(_):
    pass


class InventedReader:
    def __init__(self, days, symbols):
        self.originals = {
            (kind, symbol, day): _chunk(kind, day, symbol, 120, day_index, symbol_index,
                                       False, scenario_fixture=True)
            for day_index, day in enumerate(days)
            for symbol_index, symbol in enumerate(symbols)
            for kind in ("CC", "CX")
        }

    def load_raw_chunk(self, kind, symbol, day):
        return self.originals.get((kind, symbol, day))

    def assert_unchanged(self):
        pass


@pytest.fixture(scope="module")
def invented(tmp_path_factory):
    start = date(2026, 7, 6)
    days = tuple((start + timedelta(days=index)).isoformat() for index in range(21))
    symbols = tuple(f"KRX:{index:06}" for index in range(1, 7))
    targets = tuple(sources.TargetCoordinate(timing, symbol, day, rank, 120,
                    "stored_rows_completeness_unverified", b"invented")
                    for day in days for timing in ("S1", "S2")
                    for rank, symbol in enumerate(symbols if timing == "S1" else symbols[:4], 1))
    universe = sources.UniverseCalendar(days, targets, "b" * 64)
    reader = InventedReader(days, symbols)
    coverage_binding = {"invented_coverage": True}
    bound = {"invented_snapshot": True}
    scope = {"sessions": list(days), "raw_sha256": universe.raw_sha256, "target_count": len(targets)}
    path = tmp_path_factory.mktemp("eg-scenario-reference") / "coverage.jsonl.gz"
    seal, _ = write_coverage(path, universe, coverage_binding,
                             iter_coverage_records(universe, reader, checkpoint), checkpoint)
    frames = tuple(unit._produced(path, seal, coverage_binding, universe, reader, checkpoint))
    confirmed = {feature["attempt"]["scenario"] for feature, _ in frames
                 if feature["attempt"]["state"] == "confirmed"}
    assert confirmed == {"A", "B"}
    return frames, bound, scope, path, seal, coverage_binding, universe, reader


def test_independent_snapshot_reconstructs_both_branches_and_full_reports(invented, tmp_path, monkeypatch):
    frames, bound, scope, *originals = invented
    seals, report = storage.write_snapshot(tmp_path, frames, bound, scope, checkpoint)

    def producer_forbidden(*_args, **_kwargs):
        raise AssertionError("verifier used producer science or decoder")

    monkeypatch.setattr(unit, "iter_scenario_day", producer_forbidden)
    monkeypatch.setattr(unit.archive, "session_inputs", producer_forbidden)
    monkeypatch.setattr(storage.SnapshotSummary, "add", producer_forbidden)

    result = verify_snapshot(tmp_path, seals, bound, scope, *originals, checkpoint)
    assert result == report
    assert result["timings"]["S1"]["counts"]["A:common_support"] >= 1
    assert result["timings"]["S1"]["counts"]["B:common_support"] >= 1
    assert result["timings"]["S1"]["weekly"][0] == {"week": "2026-W28"}


@pytest.mark.parametrize("change", ["chart", "peer", "first_touch", "hold_open", "omitted"])
def test_resealed_scientific_mutation_is_refused(invented, tmp_path, change):
    original_frames, bound, scope, *originals = invented
    frames = list(deepcopy(original_frames))
    index = next(i for i, (feature, label) in enumerate(frames)
                 if feature["entry"] is not None and label["primary"]["status"] == "admitted")
    feature, label = frames[index]
    if change == "chart":
        feature["entry"]["chart"]["sequence"][-1][0] += 1.0
    elif change == "peer":
        feature["entry"]["peers"]["S1"]["sequence"][-1][1] += 1.0
    elif change == "first_touch":
        label["primary"]["first_touch"]["minute"] = label["primary"]["entry_at"]
        label["primary"]["first_touch"]["status"] = "same_minute_unknown"
    elif change == "hold_open":
        label["holding"][0]["next_open"] = {"fraction": [7, 1]}
    else:
        del frames[index]
    seals, _ = storage.write_snapshot(tmp_path, frames, bound, scope, checkpoint)
    reason = "reference_scenario_feature_differs" if change in {"chart", "peer", "omitted"} else (
        "reference_scenario_label_differs"
    )
    with pytest.raises(ScenarioVerificationRefusal, match=reason):
        verify_snapshot(tmp_path, seals, bound, scope, *originals, checkpoint)


def test_correctly_resealed_forged_report_is_refused(invented, tmp_path, monkeypatch):
    frames, bound, scope, *originals = invented
    original_finish = storage.SnapshotSummary.finish

    def forged_report(self):
        report = original_finish(self)
        report["timings"]["S1"]["counts"]["observed_clusters"] += 1
        return report

    monkeypatch.setattr(storage.SnapshotSummary, "finish", forged_report)
    seals, _ = storage.write_snapshot(tmp_path, frames, bound, scope, checkpoint)
    with pytest.raises(ScenarioVerificationRefusal, match="reference_scenario_snapshot_footer"):
        verify_snapshot(tmp_path, seals, bound, scope, *originals, checkpoint)
