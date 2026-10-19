"""A complete hash prefix must never certify the remaining price-ranked pool."""
from copy import deepcopy
from datetime import date, timedelta
import json
from pathlib import Path

import pytest

from research import price_trend_readiness as m


def frontier():
    formation = date(2021, 4, 1)
    dates = [(formation - timedelta(days=day)).isoformat() for day in range(60, 0, -1)]
    populations = [{"formation": str(formation), "code": code, "isin": isin,
        "normal_input_disposition": state} for code, isin, state in (
            ("000001", "KR7000001000", "eligible"),
            ("000002", "KR7000002000", "unresolved"),
            ("000003", "KR7000003000", "excluded"))]
    return {"schema": "activity-cohort-scope-v1", "complete_target_prefix": True,
        "formations": [{"formation": str(formation), "ACTIVITY": {
            arm: {"target_ceiling": 1, "source_population_count": 3} for arm in ("D1", "D2")}}],
        "population_partition": populations,
        "needed_normal_windows": [{**populations[0], "normal_dates": dates}]}


def test_unknown_tail_is_a_gap_even_when_all_old_hash_controls_are_complete():
    value = m.summarize_frontier(frontier())
    assert value["summary"] == {"formations": 1, "nonzero_budget_formations": 1,
        "source_rows": 3, "unresolved_nonzero_budget_candidate_windows": 1,
        "eligible_baseline_gaps_nonzero_budget": 0, "whole_pool_metadata_ready": False}
    assert not value["prices_read"] and not value["returns_computed"]
    assert value["D1_completed_studies"] == value["D2_completed_studies"] == 0


def test_known_eligible_without_a_saved_baseline_is_not_silently_removed():
    data = frontier()
    data["population_partition"][1]["normal_input_disposition"] = "eligible"
    result = m.summarize_frontier(data)
    assert result["summary"]["eligible_baseline_gaps_nonzero_budget"] == 1
    assert not result["summary"]["whole_pool_metadata_ready"]


def test_zero_budget_needs_no_price_ranking_but_keeps_unknown_population():
    data = frontier()
    for arm in data["formations"][0]["ACTIVITY"].values():
        arm["target_ceiling"] = 0
    result = m.summarize_frontier(data)
    assert result["summary"]["source_rows"] == 3
    assert result["formations"][0]["unresolved"] == 1
    assert result["summary"]["whole_pool_metadata_ready"]


@pytest.mark.parametrize("mutation", ("duplicate_population", "duplicate_window", "duplicate_date",
    "future_date", "identity", "state", "paired_budget", "unproved_frontier", "empty", "population_gap"))
def test_inconsistent_retained_metadata_fails(mutation):
    data = frontier()
    if mutation == "duplicate_population":
        data["population_partition"].append(deepcopy(data["population_partition"][0]))
    elif mutation == "duplicate_window":
        data["needed_normal_windows"].append(deepcopy(data["needed_normal_windows"][0]))
    elif mutation == "duplicate_date":
        data["needed_normal_windows"][0]["normal_dates"][0] = data["needed_normal_windows"][0]["normal_dates"][1]
    elif mutation == "future_date":
        data["needed_normal_windows"][0]["normal_dates"][-1] = "2021-04-01"
    elif mutation == "identity":
        data["needed_normal_windows"][0]["isin"] = "other"
    elif mutation == "state":
        data["population_partition"][1]["normal_input_disposition"] = "unknown"
    elif mutation == "paired_budget":
        data["formations"][0]["ACTIVITY"]["D2"]["target_ceiling"] = 2
    elif mutation == "unproved_frontier":
        data["complete_target_prefix"] = False
    elif mutation == "population_gap":
        data["population_partition"].pop()
    else:
        data["formations"] = []
        data["population_partition"] = []
        data["needed_normal_windows"] = []
    with pytest.raises(ValueError):
        m.summarize_frontier(data)


def test_committed_spec_matches_reference_and_rejects_changed_input():
    root = Path(__file__).resolve().parents[2]
    spec = json.loads((root / "configs/research/discovery/price-trend-readiness-v1.json").read_text())
    reference = json.loads((root / m.saved.REFERENCE).read_text())
    m.validate_spec(spec, reference)
    spec["inputs"]["frontier"]["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        m.validate_spec(spec, reference)


@pytest.mark.parametrize("bad_hash", (False, True))
def test_logged_runner_archives_attempt_and_retains_failed_read(tmp_path, monkeypatch, bad_hash):
    """Fabricated input only; the real logger must precede its first body read."""
    root = Path(__file__).resolve().parents[2]
    monkeypatch.chdir(root)
    output_parent = tmp_path / "private"
    output_parent.mkdir(mode=0o700)
    output = output_parent / "run"
    log = output_parent / "trials.jsonl"
    raw = m.saved.encoded(frontier())
    pin = "0" * 64 if bad_hash else m.saved.digest(raw)
    declaration = {"path": str(tmp_path / "inputs" / "frontier.json"), "sha256": pin}
    monkeypatch.setattr(m, "FRONTIER", declaration)
    spec = {"study_id": m.SCHEMA, "mode": "discovery_saved_metadata_only",
        "window": m.saved.WINDOW, "inputs": {"frontier": declaration}}
    sources = {"files": [], "code_version": "fabricated"}
    monkeypatch.setattr(m.saved, "freeze_sources", lambda *args, **kwargs: (sources, b"{}", spec))
    reads = []
    def read(path):
        records = list(m.experiment_log.read_records(log))
        assert len(records) == 1 and records[0]["status"] == "started"
        assert (output / "input-intent.json").exists()
        reads.append(path)
        return raw
    monkeypatch.setattr(m.saved, "read_private", read)
    if bad_hash:
        with pytest.raises(ValueError, match="hash mismatch"):
            m.run(root, root / "configs/research/discovery/price-trend-readiness-v1.json", output, log)
        assert (output / "failure.json").exists()
        assert not (output / "result.json").exists()
    else:
        result = m.run(root, root / "configs/research/discovery/price-trend-readiness-v1.json", output, log)
        assert result["result"]["summary"]["unresolved_nonzero_budget_candidate_windows"] == 1
    assert reads == [Path(declaration["path"])]
    assert (output / "frontier.raw.json").read_bytes() == raw
    assert (output / "resources.json").exists()
    records = list(m.experiment_log.read_records(log))
    assert [row["status"] for row in records] == ["started", "failed" if bad_hash else "completed"]
    with pytest.raises(ValueError, match="already exists"):
        m.run(root, root / "configs/research/discovery/price-trend-readiness-v1.json", output, log)
    assert len(list(m.experiment_log.read_records(log))) == 2
