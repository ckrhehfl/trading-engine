"""Invented-only EG raw geometry, support and post-warmup branch checks."""
from collections import Counter
import gzip
import hashlib
import json

import pytest

from research import intraday_ed_sources as sources
from research import intraday_highvol_events as events
from research import intraday_highvol_invented as invented
from research.intraday_ed_inputs import MINUTE


def test_full_layout_keeps_original_counts_without_writing_or_loading_data():
    geometry, days, universe, coordinates = invented._layout(257)
    assert geometry == {"sessions": 257, "targets_per_scenario": 5140, "union_coordinates": 5158,
                        "cc_chunks": 5158, "cc_rows": 1002211, "cx_chunks": 5158,
                        "cx_rows": 1002211, "cx_daily_rows": 2674}
    assert len(days) == 257 and len(coordinates) == 5158
    assert sum(count for _, _, count in coordinates) == 1002211
    assert all(sum(len(day["targets"]) for day in variant["days"]) == 5140
               for variant in universe["variants"].values())
    assert universe["invented_only"] is True


def test_generated_roster_counts_and_compressed_decoded_pins(tmp_path):
    calls = []
    result = invented.make_invented(tmp_path / "raw", 2, calls.append)
    assert result["invented_only"] is True
    assert calls[0] == "eg_invented_before_create" and calls[-1] == "eg_invented_complete"
    geometry = result["geometry"]
    assert calls.count("eg_invented_raw_chunk") == geometry["cc_chunks"] + geometry["cx_chunks"]
    assert [row["name"] for row in result["local_received_sources"]] == ["CB", "CC", "CX"]
    for item in result["local_received_sources"]:
        raw = (tmp_path / "raw" / item["name"]).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item["sha256"] and len(raw) == item["size"]
        assert len(raw) <= item["maximum"]
        if item["name"] == "CB":
            universe = sources.load_universe(raw, item["sha256"], expected_sessions=2)
            assert len(universe.targets) == 80
            continue
        decoded = gzip.decompress(raw)
        assert hashlib.sha256(decoded).hexdigest() == result["decoded_archives"][item["name"]]
        chunks = [json.loads(line) for line in decoded.splitlines()]
        kind = item["name"].lower()
        assert len(chunks) == geometry[kind + "_chunks"]
        assert sum(len(chunk["rows"]) for chunk in chunks) == geometry[kind + "_rows"]
        assert len({(chunk["session"], chunk["symbol"]) for chunk in chunks}) == len(chunks)
        if kind == "cx":
            assert sum(len(chunk["daily_rows"]) for chunk in chunks) == geometry["cx_daily_rows"]


def test_generated_bytes_are_deterministic_and_existing_directory_is_preserved(tmp_path):
    first = invented.make_invented(tmp_path / "first", 1, lambda _: None)
    second = invented.make_invented(tmp_path / "second", 1, lambda _: None)
    assert first["decoded_archives"] == second["decoded_archives"]
    assert [(row["sha256"], row["size"]) for row in first["local_received_sources"]] == [
        (row["sha256"], row["size"]) for row in second["local_received_sources"]]
    before = {name: (tmp_path / "first" / name).read_bytes() for name in ("CB", "CC", "CX")}
    with pytest.raises(FileExistsError):
        invented.make_invented(tmp_path / "first", 1, lambda _: None)
    assert before == {name: (tmp_path / "first" / name).read_bytes() for name in before}


@pytest.mark.parametrize("sessions,full", [(True, False), (0, False), (258, False), (21, True), (1, 1)])
def test_bad_geometry_rejected_before_path_creation(tmp_path, sessions, full):
    with pytest.raises(ValueError):
        invented.make_invented(tmp_path / "bad", sessions, lambda _: None, full=full)
    assert not (tmp_path / "bad").exists()


def test_checkpoint_failure_leaves_partial_files_and_cannot_overwrite(tmp_path):
    def fail(stage):
        if stage == "eg_invented_raw_chunk":
            raise RuntimeError("invented interruption")
    path = tmp_path / "failed"
    with pytest.raises(RuntimeError, match="invented interruption"):
        invented.make_invented(path, 1, fail)
    assert (path / "CB").exists() and (path / "CC").exists() and not (path / "CX").exists()
    with pytest.raises(FileExistsError):
        invented.make_invented(path, 1, lambda _: None)


def _decisions(case):
    _, days, _, _ = invented._layout(21)
    symbol, metrics = f"KRX:{case + 1:06}", []
    views = []
    for index, day in enumerate(days):
        chunks = [invented._chunk(kind, day, symbol, 194, index, case, False) for kind in ("CC", "CX")]
        view = sources.session_inputs(symbol, day, *chunks).session
        views.append(view)
        metrics.append(tuple(events.measure_window(view, offset, view.opening + (offset + 2) * MINUTE)
                             for offset in events.OFFSETS))
    expected = tuple(view.opening for view in views[:20])
    return tuple(events.classify_window(current, expected,
        {at: metrics[index][position] for index, at in enumerate(expected)})
        for position, current in enumerate(metrics[-1]))


@pytest.fixture(scope="module")
def branches():
    return {case: _decisions(case) for case in range(8)}


def test_day21_exercises_high_low_tie_zero_and_censoring(branches):
    assert branches[0][0].status == "not_high" and branches[0][0].threshold_tie is True
    assert branches[1][0].status == "high"
    assert events.group_episodes(branches[1]).left_censored_clusters == 1
    assert branches[2][0].status == "not_high"
    assert events.group_episodes(branches[2]).confirmed_onsets == 1
    assert branches[4][0].current.zero_path is True and branches[4][0].baseline_zero is True
    assert branches[4][0].status == "not_high"
    assert branches[5][0].status == "high" and branches[5][0].current.signed_move < 0


def test_current_gap_bridges_highs_and_missing_suffix_stays_unknown(branches):
    summary = events.group_episodes(branches[3])
    assert len(summary.episodes) == 1 and summary.left_censored_clusters == 1
    assert summary.episodes[0].unknown_bridge_offsets == (50, 55, 60)
    assert summary.episodes[0].right_censored is True
    assert summary.exact_onset_count is None and summary.confirmed_onsets == 0
    states = Counter(item.status for item in branches[3])
    assert states["high"] > 0 and states["unavailable"] > 0
    assert branches[3][-1].reason == "current_price_unavailable"


def test_price_and_volume_gaps_are_independent_and_real_zero_is_preserved(branches):
    by_offset = {item.current.offset: item for item in branches[6]}
    assert by_offset[80].current.price_status == "available" and by_offset[80].status == "high"
    assert by_offset[80].current.shares is None and by_offset[80].current.volume_issues
    invalid = {item.current.offset: item for item in branches[7]}[100]
    assert invalid.current.price_status == "unavailable" and invalid.current.shares is not None
    _, days, _, _ = invented._layout(21)
    price, volume = [invented._chunk(kind, days[-1], "KRX:000007", 194, 20, 6, False)
                     for kind in ("CC", "CX")]
    view = sources.session_inputs("KRX:000007", days[-1], price, volume).session
    assert view.minutes[view.opening + 120 * MINUTE].shares == 0
    assert view.minutes[view.opening + 75 * MINUTE].shares is None
    assert len(price["rows"]) == len(volume["rows"]) == 194
