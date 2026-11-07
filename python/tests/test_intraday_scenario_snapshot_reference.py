"""Independent EH snapshot checks over invented audited CC coordinates."""

from dataclasses import replace
from datetime import UTC
from fractions import Fraction
from hashlib import sha256
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research.intraday_ed_inputs import MINUTE, SessionInputs
from research.intraday_ed_sources import TargetCoordinate, UniverseCalendar, full_reference_session_inputs
from research.intraday_pattern_calibration import session_open
from research.intraday_scenario_snapshot import project_opening_day, write_features, write_labels
from research.intraday_scenario_snapshot_reference import _AuditedMinutes, _peer_clock, _visible_path, verify_snapshot
from research.intraday_scenario_state import OpeningRangePriceEvidence
from test_intraday_scenario_state import BELOW, OPENING, session


DAY = OPENING.date().isoformat()
FIRST = session_open(DAY)
SYMBOLS = tuple(f"KRX:{number:06}" for number in range(1, 22))


class Reader:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def assert_unchanged(self):
        pass

    def load_raw_chunk(self, kind, symbol, day):
        assert kind == "CC" and day == DAY
        self.calls.append((day, symbol))
        return self.chunks.get(symbol)


def _chunk(symbol, template, *, gap=None):
    rows = []
    for at, row in sorted(template.minutes.items()):
        offset = (at - template.opening) // MINUTE
        if gap == offset:
            continue
        raw = {"symbol": symbol, "interval": "1m", "open_time_ms": int((FIRST + offset * MINUTE).timestamp()) * 1000,
               "fetched_at": None}
        raw.update(zip(("open", "high", "low", "close"), map(str, row.ohlc), strict=True))
        rows.append(raw)
    return {"symbol": symbol, "session": DAY, "observation_date": DAY, "available_at": None,
            "retrieved_at": FIRST.isoformat(), "source": "collector klines", "is_final": None,
            "evidence_level": "assumed", "rows": rows}


def _fixture(*, gap=None, quiet_count=5):
    active = session(BELOW, BELOW, (94, 100, 89, 99), (99, 101, 98, 100), *((100, 101, 99, 100),) * 2,
                     (100, 104, 99, 103), *((103, 104, 102, 103),) * 7, closing=70)
    quiet = session(*((94, 100, 89, 94),) * 78, closing=390)
    chunks = {SYMBOLS[0]: _chunk(SYMBOLS[0], active, gap=gap)}
    chunks.update((symbol, _chunk(symbol, quiet)) for symbol in SYMBOLS[1:1 + quiet_count])
    chunks[SYMBOLS[-1]] = {**chunks[SYMBOLS[0]], "symbol": SYMBOLS[-1], "rows": []}
    known = FIRST - 30 * MINUTE
    metadata = wire.encode({"session": DAY, "available_at": known.isoformat(), "selected_at": known.isoformat()}).strip()
    targets = []
    for name, roster in (("S1", (*SYMBOLS[:-2], SYMBOLS[-1])), ("S2", SYMBOLS[:-1])):
        for rank, symbol in enumerate(roster, 1):
            count = len(chunks[symbol]["rows"]) if symbol in chunks else 0
            state = "stored_rows" if count else "no_stored_rows_unknown_reason"
            targets.append(TargetCoordinate(name, symbol, DAY, rank, count or None, state, metadata))
    universe = UniverseCalendar((DAY,), tuple(targets), "a" * 64)
    sessions = {symbol: full_reference_session_inputs(symbol, DAY, raw, None) for symbol, raw in chunks.items()}
    evidence = {symbol: OpeningRangePriceEvidence(f"CC:{DAY}:{symbol}", "stored_cc_same_session_numeric_unit")
                for symbol in sessions}
    projected = project_opening_day(DAY, universe.targets, sessions, evidence, members_known_at=known)
    return universe, Reader(chunks), sessions, projected


def _sealed(tmp_path, day, outcome):
    features, labels = tmp_path / "features.jsonl", tmp_path / "labels.jsonl"
    seal = write_features(features, (day,))
    labels_seal = write_labels(labels, features, seal, outcome)
    return features, seal, labels, labels_seal


def _check(paths, universe, reader):
    return verify_snapshot(*paths, universe, reader, lambda _: None)


def test_reference_keeps_the_original_roster_and_excludes_self(tmp_path):
    universe, reader, sessions, day = _fixture()
    first = day.rows[0]
    assert first.frame.observed_at == FIRST + 22 * MINUTE
    assert first.frame.chart.status == "ready" and len(first.peer.complete) == 5
    assert SYMBOLS[0] not in first.peer.expected and SYMBOLS[-1] not in first.peer.expected
    assert first.peer.sequence[0] == (0, 0, 0)
    paths = _sealed(tmp_path, day, lambda _, symbol: sessions[symbol])
    record = json.loads(paths[0].read_bytes().splitlines()[1])
    first_target = record["coordinates"][0][4][0]
    assert first_target == ["S1", 1, universe.targets[0].metadata.hex()]
    counts = _check(paths, universe, reader)
    assert counts["sessions"] == 1 and counts["union_coordinates"] == 21
    assert all(value["original"] == 20 for value in counts["scenarios"].values())
    assert counts["questions"] == counts["mature_training"] == 2
    assert counts["coordinate_status"]["session_missing"] == 14
    assert counts["coordinate_status"]["support_unknown"] == 1
    assert counts["peer_ready"] == counts["chart_status"]["ready"]
    assert counts["breakout_gap_after_approach"] == 0
    assert reader.calls == [(DAY, symbol) for symbol in SYMBOLS]


def test_reference_counts_an_observed_question_with_unknown_labels(tmp_path):
    universe, reader, sessions, day = _fixture(gap=23, quiet_count=4)
    first = day.rows[0]
    assert first.frame.chart.status == "ready" and first.peer.sequence is None
    assert first.peer.issues[-1].reason == "fewer_than_five_complete_original_peers"
    assert all(row.frame.chart.status == "unknown" for row in day.rows[1:])
    paths = _sealed(tmp_path, day, lambda _, symbol: sessions[symbol])
    record = json.loads(paths[0].read_bytes().splitlines()[1])
    coordinate = record["coordinates"][0]
    missing_at = (FIRST + 23 * MINUTE).astimezone(UTC).isoformat()
    assert coordinate[1] == "frames_present"
    assert coordinate[6] == [[SYMBOLS[0], missing_at, "missing_minute"]]
    counts = _check(paths, universe, reader)
    assert counts["questions"] == counts["label_status"]["observed"] == 1
    assert counts["mature_training"] == counts["peer_ready"] == 0
    assert counts["breakout_gap_after_approach"] == 1
    assert all(value["breakout_gap_after_approach"] == 1 for value in counts["scenarios"].values())


def test_reference_prefix_does_not_use_a_row_until_its_publication():
    _, _, sessions, day = _fixture()
    initial = day.rows[0].frame.initial
    primary = sessions[SYMBOLS[0]]
    at = initial.question.start_at
    late = replace(primary.minutes[at], available_at=at + 4 * MINUTE)
    primary = replace(primary, minutes={**primary.minutes, at: late})
    assert _visible_path(primary, initial.question, initial.start_close, at + 2 * MINUTE)[:3] == ("pending", at, None)
    assert _visible_path(primary, initial.question, initial.start_close, at + 3 * MINUTE)[:3] == (
        "unknown", at, "minute_not_observed_at_cutoff")
    assert _visible_path(primary, initial.question, initial.start_close, at + 4 * MINUTE)[:3] == (
        "pending", at + 2 * MINUTE, None)


def test_reference_overlapping_peer_windows_keep_separate_cumulative_anchors():
    template = session((94, 110, 89, 95), (97, 110, 89, 98), *((100, 110, 89, 101),) * 3)
    roster = SYMBOLS[:5]
    sessions = {symbol: SessionInputs(symbol, FIRST, FIRST + 390 * MINUTE,
                                      _AuditedMinutes(symbol, FIRST, _chunk(symbol, template)))
                for symbol in roster}
    _, first, _ = _peer_clock(sessions, roster, FIRST + 22 * MINUTE)
    _, second, _ = _peer_clock(sessions, roster, FIRST + 27 * MINUTE)
    for symbol in roster:
        assert first[symbol][10] == (Fraction(1, 100), Fraction(7, 94), 1)
        assert second[symbol][5] == (Fraction(1, 100), Fraction(4, 97), 1)


@pytest.mark.parametrize("changed", ("chart", "peer", "metadata", "breakout_gap", "labels"))
def test_reference_rejects_well_formed_resealed_corruption(tmp_path, changed):
    universe, reader, sessions, day = _fixture(gap=23 if changed == "breakout_gap" else None)
    expected = "feature_row_differs"
    if changed == "chart":
        row = day.rows[0]
        chart = row.frame.chart
        chart = replace(chart, state=(chart.state[0] + 1, *chart.state[1:]))
        day = replace(day, rows=(replace(row, frame=replace(row.frame, chart=chart)), *day.rows[1:]))
    elif changed == "peer":
        row = day.rows[0]
        sequence = row.peer.sequence
        wrong = (sequence[0][0] + 1, *sequence[0][1:])
        day = replace(day, rows=(replace(row, peer=replace(row.peer, sequence=(wrong, *sequence[1:]))), *day.rows[1:]))
    elif changed == "metadata":
        coordinate = day.coordinates[0]
        changed_target = replace(coordinate.targets[0], metadata=b"invented replacement")
        day = replace(day, coordinates=(replace(coordinate, targets=(changed_target, *coordinate.targets[1:])),
                                        *day.coordinates[1:]))
        expected = "snapshot_coordinate_differs"
    elif changed == "breakout_gap":
        coordinate = replace(day.coordinates[0], breakout_gap=())
        day = replace(day, coordinates=(coordinate, *day.coordinates[1:]))
        expected = "snapshot_coordinate_differs"
    else:
        expected = "label_row_differs"
    paths = _sealed(tmp_path, day, lambda _, symbol: None if changed == "labels" else sessions[symbol])
    with pytest.raises(ValueError, match=expected):
        _check(paths, universe, reader)


@pytest.mark.parametrize("stream", (0, 2))
def test_reference_requires_both_stream_footers_and_eof(tmp_path, stream):
    universe, reader, sessions, day = _fixture()
    paths = list(_sealed(tmp_path, day, lambda _, symbol: sessions[symbol]))
    path, seal = paths[stream:stream + 2]
    path.chmod(0o600)
    data = path.read_bytes() + b"{}\n"
    path.write_bytes(data)
    path.chmod(0o400)
    paths[stream + 1] = replace(seal, sha256=sha256(data).hexdigest(), size=len(data))
    if stream == 0:
        # Rebind the label header so the verifier reaches the changed footer.
        labels, label_seal = paths[2:]
        _, _, label_rows = labels.read_bytes().partition(b"\n")
        feature_seal = paths[1]
        relinked = wire.encode({"schema": label_seal.schema, "feature_sha256": feature_seal.sha256,
                                "feature_size": feature_seal.size, "feature_records": feature_seal.records}) + label_rows
        labels.chmod(0o600)
        labels.write_bytes(relinked)
        labels.chmod(0o400)
        paths[3] = replace(label_seal, sha256=sha256(relinked).hexdigest(), size=len(relinked))
    with pytest.raises(ValueError, match="footer_or_EOF_differs"):
        _check(paths, universe, reader)
