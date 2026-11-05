"""Exercise the five-fold EF artifact boundary with invented sealed inputs."""

from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from fractions import Fraction
import json
import os

import pytest

from research import intraday_chart_fit_run as fit
from research import intraday_chart_snapshot as snapshot
from research.intraday_campaign_wsl_bridge import EF_FIT_UNITS
from research.intraday_chart_handoffs import AuditedArtifact
from research.intraday_chart_model_inputs import ADMISSION_PCT, LGB_ARMS, TCN_ARMS
from research.intraday_chart_report_run import REPORT_FILE, run_report
from research.intraday_chart_snapshot_unit import SCIENCE
from research.intraday_chart_tensors import ChartTensor, PeerTensor
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_pattern_calibration import session_close, session_open


pytestmark = pytest.mark.skipif(os.name != "posix", reason="native private sealed files required")
DAYS = tuple((date(2025, 8, 1) + timedelta(days=index)).isoformat() for index in range(257))
SYMBOL = "KRX:000001"
MINUTE_ROW = (-1.0, 0.5, -1.5, 0.0, 1.0, 2.0, 0.75, 0.0, 0.0, 0.0)
CHART = ChartTensor((MINUTE_ROW,) * 60, ((0.0, 0.5, -0.5, 0.0, 1.0, 0.0),) * 24,
                    (0.25, 0.0, 0.0, 0.0, 0.0))
PEER = PeerTensor(((0.125, 0.5, 0.625),) * 60, 0.625)
BINDING = {"source_sha256": "a" * 64, "runtime_sha256": "b" * 64, "registration_sha256": "c" * 64, "scenario": "S1"}
AUDIT_LABELS = snapshot.LabelSnapshot.audit_rows


def checkpoint(_phase):
    pass


def _day(day):
    opening, closing = session_open(day), session_close(day)
    train = day in DAYS[:30]
    for offset in snapshot.QUOTE_OFFSETS:
        chart_offsets = (60, 75, 90, 105) if train else (60, 70, 75, 90, 300, 305, 310, 315)
        peer_offsets = (60, 75, 90, 105) if train else (60, 90, 300, 305, 310, 315)
        bar_end = opening + offset * MINUTE
        feature_clock = offset in snapshot.FEATURE_OFFSETS and bar_end <= closing
        chart = CHART if feature_clock and offset in chart_offsets else None
        peers = (snapshot.PeerRow("S1", PEER if offset in peer_offsets else None, b"invented"),
                 snapshot.PeerRow("S2", None, b"invented")) if feature_clock else ()
        quote = Fraction(10_000) if bar_end + 3 * MINUTE < closing - 15 * MINUTE else None
        execution_state = "available" if quote is not None else "outside_clock"
        row = snapshot.SnapshotRow(day, SYMBOL, offset, chart, peers, quote, execution_state, b"invented")
        label = None
        if offset in snapshot.ENTRY_OFFSETS:
            heads = (None,) * 6
            if (train or day >= DAYS[100]) and chart is not None and bar_end + 33 * MINUTE < closing - 15 * MINUTE:
                gross30 = Fraction(1, 250) if train else Fraction(0)
                heads = (snapshot.LabelHead(gross30, bar_end + 36 * MINUTE), *heads[1:])
            label = snapshot.LabelRow(day, SYMBOL, offset, heads, b"invented")
        yield row, label


@pytest.fixture(scope="module")
def sealed(tmp_path_factory):
    parent = tmp_path_factory.mktemp("ef-sealed")
    parent.chmod(0o700)
    paths = parent / "features.sqlite", parent / "labels.sqlite"
    targets = tuple(TargetCoordinate(scenario, SYMBOL, day, 1, 391, "stored_rows_completeness_unverified", b"invented")
                    for day in DAYS for scenario in ("S1", "S2"))
    metadata = snapshot.SnapshotMetadata(DAYS, targets, b"invented")
    sessions = (snapshot.SnapshotDay(day, session_open(day), session_close(day), ((SYMBOL, b"invented"),), _day(day))
                for day in DAYS)
    return *paths, snapshot.write_snapshot(*paths, metadata, sessions, checkpoint)


@pytest.fixture
def adapters(monkeypatch):
    calls = []

    def train(_view, split, binding):
        assert split.supported and split.fold.first_test_opening.date().isoformat() == split.fold.test_sessions[0]
        assert all(row.key[0] < split.fold.test_sessions[0] for row in split.training)
        calls.append((binding.fold, binding.arm))
        return {"fold": binding.fold, "arm": binding.arm}

    def predict(_view, keys, binding, model):
        assert model == {"fold": binding.fold, "arm": binding.arm}
        if binding.arm in LGB_ARMS:
            return tuple(0.5 for _ in keys)
        return tuple((ADMISSION_PCT if offset == 90 else 0.5, -0.1, 0.0, 0.0, 0.0, 0.0)
                     for _, _, offset in keys)

    def replay(view, split, keys, binding, model, scores):
        assert split.fold.index == binding.fold
        if scores != predict(view, keys, binding, model):
            raise ValueError("invented independent replay differs")
        return {"verified": True, "rows": len(keys)}

    monkeypatch.setattr(fit, "fit_sequence", train)
    monkeypatch.setattr(fit, "fit_lightgbm", train)
    monkeypatch.setattr(fit, "predict_sequence", predict)
    monkeypatch.setattr(fit, "predict_lightgbm", predict)
    monkeypatch.setattr(fit, "verify_sequence", replay)
    monkeypatch.setattr(fit, "verify_lightgbm", replay)
    monkeypatch.setattr(snapshot.LabelSnapshot, "audit_rows", lambda *_: pytest.fail("read evaluation labels"))
    return calls


def _forecast(rows, arm, day, offset):
    return next(row for row in rows if row.get("arm") == arm and row.get("key") == [day, SYMBOL, offset])


@pytest.mark.parametrize("family,arms,learned", [
    ("TCN", TCN_ARMS, TCN_ARMS), ("LGB", (*LGB_ARMS, *fit.CONTROLS), LGB_ARMS),
])
def test_five_folds_keep_every_coordinate_and_only_forecast_causal_support(tmp_path, sealed, adapters,
                                                                       family, arms, learned):
    tmp_path.chmod(0o700)
    kwargs = {**BINDING, "family": family, "checkpoint": checkpoint}
    receipt = fit.run_fit(tmp_path, *sealed, **kwargs)
    artifact = tmp_path / fit.FIT_FILE
    records = list(fit.iter_fit_records(artifact, checkpoint))
    assert receipt["fits"] == 5 * len(learned)
    assert receipt["forecast_rows"] == 157 * 17 * len(arms)
    assert receipt == json.loads((tmp_path / fit.FIT_VERIFICATION_FILE).read_bytes())
    assert receipt == fit.verify_fit(artifact, *sealed, **kwargs)
    assert adapters == [(fold, arm) for fold in range(1, 6) for arm in learned]
    assert [row["evaluation_sessions"][0] for row in records if row["type"] == "fold"] == [
        DAYS[index] for index in (100, 132, 164, 195, 226)]
    for arm in arms:
        assert _forecast(records, arm, DAYS[100], 105)["predictions_pct"] is None
        assert _forecast(records, arm, "2026-01-02", 300)["status"] == "outside_clock"
    assert _forecast(records, arms[0], DAYS[100], 75)["support"] == "chart_only"
    assert _forecast(records, arms[0], DAYS[100], 75)["status"] == "scored"
    assert _forecast(records, arms[1], DAYS[100], 75)["status"] == "peer_unavailable"
    if family == "TCN":
        chart, peer, ablation = (_forecast(records, arm, DAYS[100], 60) for arm in arms)
        assert [row["after_minutes"] for row in chart["updates"]] == [5, 10, 15]
        assert [row["status"] for row in chart["updates"]] == ["chart_unavailable", "scored", "scored"]
        assert [row["status"] for row in peer["updates"]] == ["chart_unavailable", "peer_unavailable", "peer_unavailable"]
        assert chart["updates"][1]["predictions_pct"][1] == -0.1
        assert not ablation["updates"]
        assert not _forecast(records, "TCN_CHART", DAYS[100], 90)["updates"]
        assert _forecast(records, "TCN_CHART_PEER", DAYS[100], 300)["updates"][-1]["status"] == "scored"
    else:
        assert _forecast(records, fit.CONTROLS[0], DAYS[100], 60)["predictions_pct"] == [0.4]
        assert _forecast(records, fit.CONTROLS[1], DAYS[100], 60)["predictions_pct"] == [1 / 0.99]
    with pytest.raises(FileExistsError):
        fit.run_fit(tmp_path, *sealed, **kwargs)


def test_readback_refuses_omitted_unknown_changed_update_and_coefficient_divergence(tmp_path, sealed, adapters):
    tmp_path.chmod(0o700)
    kwargs = {**BINDING, "family": "TCN", "checkpoint": checkpoint}
    fit.run_fit(tmp_path, *sealed, **kwargs)
    records = list(fit.iter_fit_records(tmp_path / fit.FIT_FILE, checkpoint))
    for mutation, error in (("omit_unknown", "forecast row differs"),
                            ("update", "unavailable EF forecast has a value"),
                            ("score", "independent replay differs"), ("append", "unexpected trailing")):
        changed = deepcopy(records)
        chart = _forecast(changed, "TCN_CHART", DAYS[100], 60)
        if mutation == "omit_unknown":
            changed.remove(_forecast(changed, "TCN_CHART", DAYS[100], 105))
        elif mutation == "update":
            chart["updates"][0]["predictions_pct"] = chart["predictions_pct"]
        elif mutation == "score":
            chart["predictions_pct"][-1] += 1
        else:
            changed.append(chart)
        path = tmp_path / mutation
        fit._write_artifact(path, changed, checkpoint)
        with pytest.raises(ValueError, match=error):
            fit.verify_fit(path, *sealed, **kwargs)
    assert len(adapters) == 15


def test_unsupported_fold_preserves_entries_without_fitting(tmp_path, sealed, adapters, monkeypatch):
    tmp_path.chmod(0o700)
    select = fit.snapshot_fold

    def missing_training(*args):
        return replace(select(*args), supported=False)

    monkeypatch.setattr(fit, "snapshot_fold", missing_training)
    kwargs = {**BINDING, "family": "LGB", "checkpoint": checkpoint}
    receipt = fit.run_fit(tmp_path, *sealed, **kwargs)
    assert receipt["fits"] == 0 and not adapters
    records = list(fit.iter_fit_records(tmp_path / fit.FIT_FILE, checkpoint))
    assert all(record["model"] is None for record in records if record["type"] == "model")
    for arm in (*LGB_ARMS, fit.CONTROLS[0]):
        row = _forecast(records, arm, DAYS[100], 60)
        assert row["support"] == "common" and row["status"] == "fold_unsupported" and row["predictions_pct"] is None
    assert _forecast(records, fit.CONTROLS[1], DAYS[100], 60)["predictions_pct"] == [1 / 0.99]


def test_report_reads_four_saved_fits_and_preserves_unsupported_scenario(tmp_path, sealed, adapters, monkeypatch):
    tmp_path.chmod(0o700)
    artifacts = []
    for name, (family, scenario) in EF_FIT_UNITS.items():
        cache = tmp_path / name
        cache.mkdir(mode=0o700)
        verification = fit.run_fit(cache, *sealed, **(BINDING | {"family": family, "scenario": scenario,
                                                                    "checkpoint": checkpoint}))
        receipt = {"seal": {"artifact_sha256": verification["artifact_sha256"]},
                   "artifact": {"name": fit.FIT_FILE, "size": verification["artifact_size"]},
                   "binding": {"source_sha256": BINDING["source_sha256"], "native_runtime_sha256": BINDING["runtime_sha256"],
                               "registrations": {SCIENCE: BINDING["registration_sha256"]}},
                   "independent_verification": verification}
        artifacts.append(AuditedArtifact(name, cache, "d" * 64, "e" * 64, "f" * 64, receipt))
    monkeypatch.setattr(snapshot.LabelSnapshot, "audit_rows", AUDIT_LABELS)
    verified = run_report(tmp_path, *sealed, tuple(artifacts), checkpoint)
    report = json.loads((tmp_path / REPORT_FILE).read_bytes())
    assert verified["verified"] is True and verified["scenarios"] == 2
    assert report["fits"] == 25 and report["forecast_rows"] == 2 * 157 * 17 * 7
    assert len(report["accounts"]["S1"]["TCN_CHART"]) == 4
    assert all(path["continuous_return"] is None for path in report["accounts"]["S2"]["TCN_CHART_PEER"])


def test_failed_replay_keeps_the_original_artifact_without_a_verification_receipt(tmp_path, sealed, adapters, monkeypatch):
    tmp_path.chmod(0o700)

    def refuse(*_args):
        raise ValueError("invented replay refusal")

    monkeypatch.setattr(fit, "verify_sequence", refuse)
    with pytest.raises(ValueError, match="invented replay refusal"):
        fit.run_fit(tmp_path, *sealed, **BINDING, family="TCN", checkpoint=checkpoint)
    assert (tmp_path / fit.FIT_FILE).is_file()
    assert not (tmp_path / fit.FIT_VERIFICATION_FILE).exists()
