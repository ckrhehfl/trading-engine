"""Invented sealed snapshots exercise EK timing, support, persistence and audit."""

from copy import deepcopy
from datetime import date, timedelta
from decimal import Decimal
from hashlib import sha256
import importlib.util
from pathlib import Path

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_recovery_comparison as comparison
from research import intraday_recovery_features as features
from research import intraday_recovery_models as models
from research import intraday_recovery_snapshot as snapshot
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_pattern_calibration import session_open, session_close


PINS = {name: str(index) * 64 for index, name in enumerate((
    "source_sha256", "runtime_sha256", "registration_sha256", "features_sha256", "labels_sha256"), 1)}
NATIVE = pytest.mark.skipif(importlib.util.find_spec("lightgbm") is None,
                           reason="approved native invented model environment absent")


@pytest.fixture(autouse=True)
def one_native_thread(monkeypatch):
    for name in models.THREAD_VARIABLES:
        monkeypatch.setenv(name, "1")


def _session(day):
    opening = session_open(day)
    minutes = {}
    for offset in range(124):
        at = opening + offset * MINUTE
        price = Decimal("100") if offset < 100 else Decimal("101")
        op = Decimal("101") if offset == 0 else price
        prices = op, max(op, price) + Decimal("0.1"), Decimal("99"), price
        minutes[at] = Minute("INVENTED", at, at + 3 * MINUTE, prices, Decimal(10), "observed", "observed")
    return SessionInputs("INVENTED", opening, session_close(day), minutes)


def _peer(available):
    return {"status": "available" if available else "unavailable",
            "minute": [[0.0, 0.0, 0.5] for _ in range(60)] if available else None}


def _empty_own(day):
    return {"symbol": "INVENTED", "own_return": None, "candidate_state": "endpoints_unknown",
            "order_state": "unresolved", "limit": None, "stop": None, "chart_state": "unavailable",
            "minute": None, "static": None, "decision_at": (session_open(day) + 62 * MINUTE).isoformat()}


def _files(tmp_path, *, supported=False):
    source, output = tmp_path / "snapshot", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    days = [(date(2031, 1, 1) + timedelta(days=index)).isoformat() for index in range(257)]
    initial, held, labels = [], [], []
    active = {0, 1, 2, 3, 4, 5, 100, 101, 102} if supported else set()
    for index, day in enumerate(days):
        key = day + "/INVENTED"
        own = features.build_features(_session(day)) if index in active else _empty_own(day)
        rank = {"rank_state": "select" if index in active else "unresolved",
                "order_state": "ready" if index in active else "unresolved"}
        context = {"rank_control": rank, "weak_control": "weak", "peer": _peer(index in active and index != 101)}
        initial.append({"key": key, "session": day, "symbol": "INVENTED", "targets": [],
                        "own": own, "scenarios": {"S1": context}})
        held_rows, label_rows, tape = [], [], []
        outcome = {"baseline": None, "path": None, "gross_pct": None}
        if index in active:
            session = _session(day)
            outcome = comparison.payoff.evaluate(session, comparison.Fraction(own["limit"]), comparison.Fraction(own["stop"]))
            tape = snapshot._holding_prices(session, comparison.payoff._reference_bar)
            for end in (75, 90, 105):
                held_rows.append({"end_offset": end,
                    "features": features.build_features(session, end_offset=end, remaining_minutes=120 - end),
                    "peer": {"S1": _peer(index != 101)}})
                # An invented, frozen supervised target; no evaluation payoff is used to pick a model.
                label_rows.append({"end_offset": end, "remaining_gross_pct": "-1"})
        held.append({"key": key, "session": day, "symbol": "INVENTED", "holding_queries": held_rows})
        labels.append({"key": key, "session": day, "symbol": "INVENTED", "outcome": outcome,
                       "holding_queries": label_rows, "holding_prices": tape})
    saved = {"header": {"sessions": days, "coordinates": 257, "scenario_coordinates": {"S1": 257, "S2": 0}}}
    for name, key, rows in ((snapshot.FEATURES, "features", initial), (snapshot.LABELS, "labels", labels),
                            (comparison.HOLDING_FEATURES, "holding_features", held)):
        saved[key] = snapshot._write_or_check(source / name, rows, audit=False)
    return source, output, saved, initial, held, labels


def test_complete_original_roster_survives_empty_model_support_and_audit_never_fits(tmp_path, monkeypatch):
    source, output, saved, *_ = _files(tmp_path)
    result = comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS)
    assert result["fit_artifacts"] == 13 and result["original_evaluation_coordinates"] == 32
    assert set(result["arms"]) == set(comparison.ARM_NAMES) and len(comparison.ARM_NAMES) == 18
    assert all(arm["totals"]["all_opportunities"] == 32 for arm in result["arms"].values())
    assert all(arm["totals"]["unsupported"] == 32 for arm in result["arms"].values())
    monkeypatch.setattr(models, "fit_model", lambda *_: pytest.fail("audit refitted a model"))
    assert comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS, audit=True) == result


def test_forecasts_and_directory_are_durable_before_any_evaluation_label_decode(tmp_path, monkeypatch):
    source, output, saved, *_ = _files(tmp_path)
    read, sync = comparison._read, wire.fsync_directory
    sealed = False
    observed_evaluation = []

    def synced(path):
        nonlocal sealed
        sync(path)
        if Path(path) == output and (output / comparison.SEAL).is_file():
            sealed = True

    def watched(path):
        for index, row in enumerate(read(path)):
            if path == source / snapshot.LABELS and index >= 100:
                assert sealed
                assert index < 132  # No outcome from a later fold is decoded.
                observed_evaluation.append(index)
            yield row

    monkeypatch.setattr(wire, "fsync_directory", synced)
    monkeypatch.setattr(comparison, "_read", watched)
    comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS)
    assert observed_evaluation == list(range(100, 132))


def test_changed_snapshot_or_saved_forecast_bytes_refuse(tmp_path):
    source, output, saved, *_ = _files(tmp_path)
    comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS)
    with (output / comparison.FORECASTS).open("ab") as stream:
        stream.write(b"{}\n")
    with pytest.raises(ValueError, match="publication changed"):
        comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS, audit=True)
    altered = deepcopy(saved)
    altered["features"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="seal differs"):
        comparison.compare(source, output, altered, scenario="S1", fold=1, provenance=PINS, audit=True)


def test_own_support_is_separate_from_old_rank_and_peer_support():
    own = {"own_return": "-1/100", "order_state": "ready", "chart_state": "unavailable"}
    context = {"rank_control": {"rank_state": "select", "order_state": "ready"},
               "weak_control": "weak", "peer": _peer(False)}
    row = {"own": own, "scenarios": {"S1": context}}
    assert comparison._eligible(row, "S1", "own_unfiltered", None) == "select"
    assert comparison._eligible(row, "S1", "old_weak", None) == "select"
    assert comparison._eligible(row, "S1", "own_common", None) == "unsupported"
    assert comparison._eligible(row, "S1", "sequence_chart", None) == "unsupported"
    assert comparison._eligible(row, "S1", "sequence_chart", {"supported": True, "gross_pct": 0.33}) == "no_entry"
    assert comparison._eligible(row, "S1", "sequence_chart", {"supported": True, "gross_pct": 0.330001}) == "select"


def test_unknown_future_label_does_not_remove_training_candidate_or_query(tmp_path):
    source, _, saved, *_ = _files(tmp_path, supported=True)
    split, first, past, held = comparison._coordinates(source, saved, "S1", 1)
    changed = deepcopy(past)
    changed[first[0]["key"]]["outcome"].update(gross_pct=None, path=None)
    prepared, queries = comparison._prepare(first, changed, held, split, "S1", "chart", False, PINS, holding=False)
    assert len(prepared.rows) == 6 and len(queries.rows) == 3
    assert prepared.rows[0].target_gross_pct is None and prepared.rows[0].path_class is None
    assert queries.rows[0].session >= split.test_sessions[0]
    assert not hasattr(queries.rows[0], "target_gross_pct")
    common, common_queries = comparison._prepare(first, changed, held, split, "S1", "chart", True, PINS, holding=False)
    peer, peer_queries = comparison._prepare(first, changed, held, split, "S1", "chart_peer", True, PINS, holding=False)
    assert common.contract["roster_sha256"] == peer.contract["roster_sha256"]
    assert tuple(row.key for row in common_queries.rows) == tuple(row.key for row in peer_queries.rows)
    assert len(peer_queries.rows) == 2


@NATIVE
def test_nonempty_native_forecasts_and_matched_holding_roundtrip(tmp_path, monkeypatch):
    source, output, saved, *_ = _files(tmp_path, supported=True)
    result = comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS)
    artifacts = list(comparison._read(output / comparison.MODELS))
    assert all(row["artifact"]["training"]["gross_rows"] > 0 for row in artifacts)
    assert result["arms"]["lightgbm_chart"]["totals"]["filled_closed"] == 3
    assert result["arms"]["lightgbm_chart_peer"]["totals"]["filled_closed"] == 2
    assert result["arms"]["lightgbm_chart_managed"]["totals"]["filled_closed"] == 3
    assert result["pairs"]["lightgbm_chart_holding_increment"]["selection_changed"] == 0
    rows = list(comparison._read(output / comparison.EVALUATION))
    selected = [row for row in rows if row["management"]["lightgbm_chart"] is not None]
    assert len(selected) == 3 and any(len(row["management"]["lightgbm_chart"]["sales"]) == 2 for row in selected)
    predictions = list(comparison._read(output / comparison.FORECASTS))
    assert any(type(row["opening"]["sequence_chart"]["probabilities"]) is list for row in predictions)
    monkeypatch.setattr(models, "fit_model", lambda *_: pytest.fail("audit refitted a numerical model"))
    assert comparison.compare(source, output, saved, scenario="S1", fold=1, provenance=PINS, audit=True) == result
    assert result["files"][comparison.EVALUATION]["sha256"] == sha256((output / comparison.EVALUATION).read_bytes()).hexdigest()
