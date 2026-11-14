"""Invented sealed card streams, with no archive, network, or actual audit access."""

from copy import deepcopy
from datetime import date, timedelta
from hashlib import sha256
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_event_case_report as report
from research.intraday_event_dataset import CAUSAL_FILES, CONTRACT
from research.intraday_event_dataset_run import SHARDS


UNIVERSE = "1" * 64
CALENDAR = "2" * 64


def _onset(day_index, family, minute):
    day = (date(2025, 9, 4) + timedelta(days=day_index)).isoformat()
    symbol = "KRX:000001"
    series = f"EL:{UNIVERSE}:{day}:{symbol}"
    event = f"{series}:{family}:{minute}"
    identity = (family, event, series, minute, minute + 3)
    rank = sha256(json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    return {"family": family, "event_id": event, "series_id": series, "session": day, "symbol": symbol,
            "onset_offset": minute, "initial_available_offset": minute + 3, "selection_hash": rank}


def _write(slot, name, rows):
    raw = b"".join(map(wire.encode, rows))
    (slot / name).write_bytes(raw)
    return {"filename": name, "bytes": len(raw), "rows": len(rows), "sha256": sha256(raw).hexdigest()}


def _digest(value):
    return sha256(wire.encode(value)).hexdigest()


def _fixture(root, *, outcome="unknown", future_in_before=False):
    shards, population = [], []
    for index, bounds in enumerate(SHARDS):
        slot = root / f"invented-{index}"
        slot.mkdir(parents=True)
        onsets = [_onset(bounds[0], "A_DOWNFAIL", minute) for minute in range(30, 36)] if index < 8 else []
        if index == 4:
            onsets.append(_onset(bounds[0], "B_ORL30", 48))
        population.extend(onsets)
        examples = sorted((row for row in onsets if row["family"] == "A_DOWNFAIL"),
                          key=lambda row: row["selection_hash"])[:3]
        examples += [row for row in onsets if row["family"] == "B_ORL30"]
        frames, before, after = [], [], []
        for onset in onsets:
            event, clock = onset["event_id"], onset["initial_available_offset"]
            frame = {"kind": "at_onset", "event_id": event, "family": onset["family"],
                     "overlap_group_id": onset["series_id"], "onset_offset": onset["onset_offset"],
                     "decision_offset": clock}
            state = {"kind": "event_state", "session": onset["session"], "symbol": onset["symbol"], "frame": frame}
            frames.extend((state, {**state, "frame": {**frame, "kind": "terminal", "decision_offset": clock + 1}}))
            if onset not in examples:
                continue
            key = f"{event}@{clock}"
            base = {"selected_onset": onset, "decision_query_key": key}
            before.append({"kind": "before_card", **base, "card": {"schema": "EL-before-decision-card-v1",
                "frame": frame, "as_of_offset": clock, "observations": [{"offset": clock if future_in_before else clock - 1}]}})
            after.append({"kind": "after_card", **base, "selection_changed_after_outcome": False,
                "future_observations": [{"offset": clock + 1}],
                "targets": {"kind": "labels", "query_kind": "entry", "key": key, "session": onset["session"],
                            "symbol": onset["symbol"], "targets": {"path": {"status": outcome}}}})
        pins = {name: _write(slot, name, [{"kind": "placeholder"}]) for name in CAUSAL_FILES}
        pins["event-states.jsonl"] = _write(slot, "event-states.jsonl", frames or [{"kind": "header"}])
        causal = _digest(pins)
        pins[report.ROSTER] = _write(slot, report.ROSTER, [{"schema": "EL-onset-case-roster-v1",
            "per_family": 3, "selection": CONTRACT["case_selection"], "inference_sample": False,
            "candidate_episodes": len(onsets), "examples": examples, "causal_sha256": causal}])
        footer = {"kind": "footer", "cases": len(examples)}
        header = {"kind": "header", "case_roster": pins[report.ROSTER]}
        pins[report.BEFORE] = _write(slot, report.BEFORE, [{**header, "causal_sha256": causal}, *before, footer])
        before_seal = _digest(pins)
        # No label stream exists in the fixture. Its upstream pin is enough to
        # bind the case copy, and the case reader should never open that stream.
        pins[report.LABELS] = {"filename": report.LABELS, "bytes": 100, "rows": 2, "sha256": "3" * 64}
        pins[report.AFTER] = _write(slot, report.AFTER, [{**header, "labels": pins[report.LABELS]}, *after, footer])
        scenario_coords = 20 * (bounds[1] - bounds[0])
        coordinates = scenario_coords + (18 if index == 0 else 0)
        counts = dict.fromkeys((*report.SCALARS, *report.COUNT_MAPS), 0)
        counts.update(coordinates=coordinates, series_clusters=coordinates, original_episodes=len(onsets),
                      cases=len(examples), scenario_coordinates={"S1": scenario_coords, "S2": scenario_coords},
                      events_by_family={family: sum(row["family"] == family for row in onsets) for family in report.FAMILIES},
                      broad_minutes_by_family=dict.fromkeys(report.FAMILIES, 0), frames_by_state={})
        manifest = {"schema": "EL-event-dataset-v1", "universe_sha256": UNIVERSE, "calendar_sha256": CALENDAR,
                    "scientific_contract": CONTRACT, "session_indices": list(bounds), "counts": counts,
                    "causal_sha256": causal, "before_sha256": before_seal, "files": pins,
                    "stage_order": ["causal", "case_roster", "before_cards", "outcomes", "after_cards"]}
        manifest["manifest_file"] = _write(slot, "dataset-manifest.json", [manifest])
        identity = {"shard": index, "attempt": f"invented-{index}", "session_indices": list(bounds),
                    "invented": True, "registration_sha256": "4" * 64}
        shards.append((slot, {"identity": identity, "dataset": manifest}, f"{index + 1:064x}"))
    return shards, population


def test_global_minima_are_frozen_before_reading_outcomes_and_keep_whole_coverage(tmp_path, monkeypatch):
    shards, population = _fixture(tmp_path)
    opens = []
    real_read = report._read_stream

    def observed_read(slot, pin):
        opens.append(pin["filename"])
        yield from real_read(slot, pin)

    monkeypatch.setattr(report, "_read_stream", observed_read)
    result = report.assemble_cases(shards)
    expected = sorted((row for row in population if row["family"] == "A_DOWNFAIL"),
                      key=lambda row: row["selection_hash"])[:3]
    expected += [row for row in population if row["family"] == "B_ORL30"]
    assert result["selection"]["onsets"] == expected
    assert [case["onset"] for case in result["cases"]] == expected
    assert all(len(case["timeline"]) == 2 and case["after"]["targets"]["targets"]["path"]["status"] == "unknown"
               for case in result["cases"])
    assert opens[:9] == [report.ROSTER] * 9
    assert opens.index(report.AFTER) > max(i for i, name in enumerate(opens) if name == report.BEFORE)
    assert report.LABELS not in opens
    assert result["coverage"]["coordinates"] == 5158 and result["coverage"]["sessions"] == 257
    assert result["families"]["A_DOWNFAIL"]["original_episodes"] == 48
    assert result["families"]["C_FIRSTPULLBACK"] == {"original_episodes": 0, "selected_cases": 0,
        "unfilled_example_slots": 3, "shards_without_onsets": 9}


def test_selection_digest_is_invariant_to_future_label_contents(tmp_path):
    a = report.assemble_cases(_fixture(tmp_path / "a")[0])
    b = report.assemble_cases(_fixture(tmp_path / "b", outcome="known")[0])
    assert a["selection_sha256"] == b["selection_sha256"]
    assert a["cases"] != b["cases"]


def test_missing_or_mixed_audit_never_reaches_after_cards(tmp_path, monkeypatch):
    shards, _ = _fixture(tmp_path)
    original = report._read_stream

    def causal_only(slot, pin):
        assert pin["filename"] == report.ROSTER
        yield from original(slot, pin)

    monkeypatch.setattr(report, "_read_stream", causal_only)
    with pytest.raises(ValueError, match="all nine"):
        report.assemble_cases(shards[:-1])
    _, mixed, _ = shards[-1]
    mixed["identity"]["registration_sha256"] = "5" * 64
    with pytest.raises(ValueError, match="one audited release"):
        report.assemble_cases(shards)


def test_mismatched_manifest_pin_and_before_clock_fail(tmp_path):
    shards, _ = _fixture(tmp_path / "pin")
    changed = deepcopy(shards)
    changed[0][1]["dataset"]["counts"]["regular_minutes"] += 1
    with pytest.raises(ValueError, match="manifest pin"):
        report.assemble_cases(changed)
    future, _ = _fixture(tmp_path / "clock", future_in_before=True)
    with pytest.raises(ValueError, match="before card crosses"):
        report.assemble_cases(future)
