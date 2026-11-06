"""Invented split streams and fake learner for the EG fit unit lifecycle."""

from collections import Counter
from dataclasses import asdict
import gzip
import json

import pytest

from research import intraday_campaign_wsl_transport as wire
from research import intraday_highvol_fit_run as runner
from research.intraday_highvol_coverage import CoverageReader
from research.intraday_highvol_models import ModelPrediction
from research.intraday_highvol_snapshot_io import SCHEMAS, write_snapshot
from test_intraday_highvol_model_dataset import DAYS, frame, label


UNIT = "EG-TCN-A-S1-1"
CHECKPOINT = lambda _: None
FIT_BINDING = {"unit_id": UNIT, "invented_only": True, "source_sha256": "1" * 64,
               "runtime_sha256": "2" * 64, "registration_sha256": "3" * 64}
SNAPSHOT_BINDING = {"unit_id": "EG-SCENARIO-SNAPSHOT-1", "invented_only": True}


def snapshot(tmp_path):
    root = tmp_path / "snapshot"
    root.mkdir()
    training = [frame(day, onset) for day in DAYS[:10] for onset in (15, 20, 25)]
    events = [*training, frame(DAYS[100]), frame(DAYS[100], 20, peer=False), frame(DAYS[100], 25, chart=False)]
    universe = {"sessions": list(DAYS), "raw_sha256": "4" * 64, "target_count": 257}
    seals, report = write_snapshot(root, ((event, label(event, first_touch="stop_first" if i % 2 else "target_first"))
                                         for i, event in enumerate(events)), SNAPSHOT_BINDING, universe, CHECKPOINT)
    return root, {"schema": "EG-scenario-snapshot-receipt-v1", "binding": SNAPSHOT_BINDING,
                  "seals": {role: asdict(seal) for role, seal in seals.items()}, "report": report,
                  "independent_verification": {"complete": True, "events": len(events),
                                               "report_sha256": wire.digest(wire.encode(report))}}


def test_fixed_roster_has_exactly_sixty_unique_coordinates():
    rosters = [runner.fit_roster(f"EG-TCN-{branch}-{timing}-1") for branch in ("A", "B") for timing in ("S1", "S2")]
    rosters.extend(runner.fit_roster(f"EG-LGB-{branch}-1") for branch in ("A", "B"))
    assert all(len(roster) == 10 for roster in rosters)
    assert len({row.name for roster in rosters for row in roster}) == 60
    for unit in ("EG-COVERAGE-1", "EG-MANAGEMENT-1", "EG-TCN-A-S1-2", "../EG-LGB-A-1"):
        with pytest.raises(ValueError, match="unregistered"):
            runner.fit_roster(unit)


def test_fold_one_decodes_only_positional_prefix_even_when_test_labels_exist(tmp_path, monkeypatch):
    root, receipt = snapshot(tmp_path)
    original = CoverageReader._line
    label_keys = []

    def guard(reader):
        value = original(reader)
        if getattr(reader, "header", {}).get("schema") == SCHEMAS["labels"]:
            assert value["kind"] == "record", "label footer was decoded"
            key = tuple(value["value"]["key"])
            assert key[0] < DAYS[100], "test outcome was decoded"
            label_keys.append(key)
        return value

    monkeypatch.setattr(CoverageReader, "_line", guard)
    seals = {role: runner._seal(seal) for role, seal in receipt["seals"].items()}
    dataset, count = runner._dataset(root, seals, receipt["binding"], runner.fit_roster(UNIT)[0], CHECKPOINT)
    assert dataset.selection.supported and len(dataset.selection.training) == count == len(label_keys) == 30
    assert len(dataset.selection.evaluation) == 3
    assert max(key[0] for key in label_keys) == DAYS[9]


def test_missing_hold_task_is_distinct_from_zero_hold_forecast(tmp_path):
    root, receipt = snapshot(tmp_path)
    seals = {role: runner._seal(seal) for role, seal in receipt["seals"].items()}
    dataset, _ = runner._dataset(root, seals, receipt["binding"], runner.fit_roster(UNIT)[0], CHECKPOINT)
    hold = dataset.inference[1]
    empty = ModelPrediction(None, None, None, None, False)
    zero = ModelPrediction(None, None, 0.0, None, False)
    assert runner._candidate(hold, empty, True, "fit")["status"] == "task_unsupported"
    assert runner._candidate(hold, zero, True, "fit")["status"] == "scored"
    assert runner._candidate(hold, None, True, "fold_unsupported")["status"] == "fold_unsupported"
    with pytest.raises(ValueError, match="forecast fields differ"):
        runner._score(asdict(empty), holding=False)


def test_produce_and_verify_saved_coefficient_fit_with_common_and_wider_rows(tmp_path, monkeypatch):
    root, receipt = snapshot(tmp_path)
    output = tmp_path / "fit"
    output.mkdir()
    calls = Counter()

    def fit(arm, training, *, binding, fold_index):
        calls["fit"] += 1
        assert fold_index == 1
        assert len(training) == 60 and sum(row.target_first is True for row in training) == 15
        assert binding["snapshot"]["receipt_sha256"] == wire.digest(wire.encode(receipt))
        return {"coefficient": 0.33}

    def predict(arm, inputs, model, *, binding):
        calls["predict"] += 1
        return tuple(ModelPrediction(None, None, 0.0, None, False) if row.state[0]
                     else ModelPrediction(model["coefficient"], 0.5, None, 0.0, False) for row in inputs)

    def verify(arm, training, inputs, model, predictions, *, binding, fold_index):
        calls["verify"] += 1
        assert fold_index == 1 and len(training) == 60 and model == {"coefficient": 0.33}
        assert tuple(predictions) == predict(arm, inputs, model, binding=binding)
        entry = [i for i, row in enumerate(inputs) if not row.state[0]]
        holds = [i for i, row in enumerate(inputs) if row.state[0]]
        return {"verified": True, "rows": len(inputs), "artifact_sha256": "5" * 64, "inputs_sha256": "6" * 64,
                "target_first_clipped_rows": 0,
                "threshold_ambiguous_rows": {"gross33": entry, "gross43": [], "hold_zero": holds}}

    monkeypatch.setattr(runner.models, "fit", fit)
    monkeypatch.setattr(runner.models, "predict", predict)
    monkeypatch.setattr(runner.models, "verify", verify)
    saved = runner.produce_fit(output, root, receipt, FIT_BINDING, UNIT, CHECKPOINT)
    assert calls == {"fit": 2, "predict": 2}
    assert saved["fit_count"] == 2 and saved["maximum_fits"] == 10
    assert len(list(output.iterdir())) == 11
    chart, peer = saved["coordinates"][:2]
    assert chart["candidate_counts"] == {"scored": 12, "chart_unavailable": 6}
    assert peer["candidate_counts"] == {"scored": 6, "peer_unavailable": 6, "chart_unavailable": 6}
    assert all(row["fit_status"] == "no_prediction_inputs" for row in saved["coordinates"][2:])
    with gzip.open(output / chart["name"], "rt", encoding="utf-8") as stream:
        header, metadata, *candidates, footer = map(json.loads, stream)
    assert header["schema"] == runner.SCHEMA and footer["records"] == 19
    assert metadata["value"]["target_first_false_true_rows"] == [15, 15]
    assert metadata["value"]["target_first_train_rate"] == [1, 2]
    assert {row["value"]["comparison_scope"] for row in candidates} == {"common", "chart_only", None}

    def refuse(*args, **kwargs):
        pytest.fail("refit attempted")

    monkeypatch.setattr(runner.models, "fit", refuse)
    proof = runner.verify_fit(output, root, receipt, FIT_BINDING, UNIT, CHECKPOINT)
    assert proof["complete"] and proof["refit_count"] == 0 and calls["verify"] == 2
    assert len(proof["coordinates"][0]["threshold_ambiguous_keys"]["gross33"]) == 2
    assert len(proof["coordinates"][1]["threshold_ambiguous_keys"]["gross33"]) == 1
    with pytest.raises(ValueError, match="already exists"):
        runner.produce_fit(output, root, receipt, FIT_BINDING, UNIT, CHECKPOINT)
    with (output / chart["name"]).open("ab") as changed:
        changed.write(b"tampering")
    with pytest.raises(ValueError, match="compressed artifact seal differs"):
        runner.verify_fit(output, root, receipt, FIT_BINDING, UNIT, CHECKPOINT)
