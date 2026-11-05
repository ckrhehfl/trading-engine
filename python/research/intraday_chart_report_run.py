"""Report audited chronological forecasts, paired exits and observed-candidate accounts."""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

from research import intraday_campaign_wsl_bridge as bridge
from research import intraday_campaign_wsl_transport as wire
from research.intraday_chart_account import replay_accounts
from research.intraday_chart_comparison_unit import REPORT_FILE
from research.intraday_chart_evaluation import ARMS, MANAGEMENT_ARMS, FoldForecasts, evaluate_scenario, verify_evaluation
from research.intraday_chart_fit_run import FIT_FILE, FIT_MAX_BYTES, _arms, _binding, _fold_record, _header, iter_fit_records
from research.intraday_chart_folds import ObservedLabel
from research.intraday_chart_model_inputs import snapshot_fold
from research.intraday_chart_snapshot import open_feature_snapshot, open_label_snapshot
from research.intraday_chart_snapshot_unit import SCIENCE


MAX_REPORT_BYTES = 32 * wire.MIB


class _Quotes:
    def __init__(self, features):
        self.features = features

    def row(self, day, symbol, offset):
        price, state = self.features.execution_quote(day, symbol, offset)
        return SimpleNamespace(session=day, symbol=symbol, offset=offset, execution_open=price, execution_state=state)

    def price(self, key):
        price, state = self.features.execution_quote(*key)
        wire.require(state != "outside_clock", "EF account requested a quote outside its clock")
        return price


def _consume_fit(artifact, seal, splits, forecasts, holding, checkpoint):
    family, scenario = bridge.EF_FIT_UNITS[artifact.unit]
    receipt = artifact.receipt
    path = artifact.cache / FIT_FILE
    expected_pin = receipt["seal"]["artifact_sha256"]
    size = receipt["artifact"]["size"]
    wire.require(receipt["artifact"]["name"] == FIT_FILE
                 and wire.file_digest(path, FIT_MAX_BYTES, checkpoint) == (expected_pin, size),
                 "EF sealed model artifact differs")
    bound = receipt["binding"]
    expected = _binding(seal, bound["source_sha256"], bound["native_runtime_sha256"],
                        bound["registrations"][SCIENCE], scenario, family)
    records = iter(iter_fit_records(path, checkpoint))
    wire.require(next(records, None) == _header(expected), "EF report fit input binding differs")
    rows, fits = 0, 0
    for split in splits:
        index = split.fold.index
        wire.require(wire.encode(next(records, None)) == wire.encode(_fold_record(split)),
                     "EF report fit chronology differs")
        for arm in _arms(family):
            model = next(records, None)
            wire.require(model is not None and model["type"] == "model" and (model["arm"], model["fold"]) == (arm, index),
                         "EF report model arm differs")
            fits += model["model"] is not None
            values = forecasts[index][arm]
            for row in split.evaluation:
                forecast = next(records, None)
                wire.require(forecast is not None and forecast["type"] == "forecast" and
                             (forecast["arm"], forecast["fold"], tuple(forecast["key"]), forecast["support"]) ==
                             (arm, index, row.key, row.status), "EF report forecast population differs")
                score = forecast["predictions_pct"]
                values[row.key] = score[0] if score is not None else None
                for update in forecast["updates"]:
                    wire.require(arm in MANAGEMENT_ARMS, "unexpected EF management prediction arm")
                    day, symbol, offset = row.key
                    key = day, symbol, offset + update["after_minutes"]
                    updated = update["predictions_pct"]
                    value = updated[1] if updated is not None else None
                    target = holding[index][arm]
                    wire.require(key not in target or target[key] == value, "EF same-clock update differs")
                    target[key] = value
                rows += 1
    proof = receipt["independent_verification"]
    wire.require(next(records, None) is None and rows == proof["forecast_rows"] and fits == proof["fits"]
                 and wire.file_digest(path, FIT_MAX_BYTES, checkpoint) == (expected_pin, size),
                 "EF report full fit population or sealed file changed")
    return fits, rows


def _folds(features, labels, seal, artifacts, scenario, checkpoint):
    splits = tuple(snapshot_fold(features, labels, scenario, index) for index in range(1, 6))
    forecasts = {index: {arm: {} for arm in ARMS} for index in range(1, 6)}
    holding = {index: {arm: {} for arm in MANAGEMENT_ARMS} for index in range(1, 6)}
    fits, rows = 0, 0
    for item in artifacts:
        added_fits, added_rows = _consume_fit(item, seal, splits, forecasts, holding, checkpoint)
        fits += added_fits
        rows += added_rows
    return tuple(FoldForecasts(split, forecasts[split.fold.index], holding[split.fold.index]) for split in splits), fits, rows


def _account_value(value):
    if isinstance(value, Fraction):
        return [str(value.numerator), str(value.denominator)]
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _account_value(asdict(value))
    if isinstance(value, (tuple, list)):
        return [_account_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _account_value(item) for key, item in value.items()}
    return value


def _accounts(folds, quotes, checkpoint):
    sessions = tuple(day for item in folds for day in item.selection.fold.test_sessions)
    results = {}
    for arm in ARMS:
        checkpoint("ef_report_account_arm")
        selected = ((row.key, item.forecasts[arm][row.key]) for item in folds for row in item.selection.evaluation
                    if row.status == "common")
        updates = {key: value for item in folds for key, value in item.holding.get(arm, {}).items()}
        result = replay_accounts(sessions, selected, quote_at=quotes.price, gross15_at=updates.__getitem__,
                                 include_dynamic=arm in MANAGEMENT_ARMS)
        results[arm] = _account_value(result)
    return results


def run_report(directory: Path, feature_file: Path, label_file: Path, seal, artifacts, checkpoint):
    wire.require(tuple(item.unit for item in artifacts) == tuple(bridge.EF_FIT_UNITS), "EF complete four fit units required")
    with open_feature_snapshot(feature_file, seal, checkpoint) as features:
        with open_label_snapshot(label_file, seal, checkpoint) as labels:
            splits = {scenario: _folds(features, labels, seal, tuple(item for item in artifacts
                      if bridge.EF_FIT_UNITS[item.unit][1] == scenario), scenario, checkpoint) for scenario in ("S1", "S2")}
            eval_dates = {day for folds, _, _ in splits.values() for fold in folds for day in fold.selection.fold.test_sessions}
            # All serialized forecast files are already read and checked before
            # evaluation outcome rows enter this report stage.
            outcome = {(row.session, row.symbol, row.offset): tuple(
                ObservedLabel(head.value, head.mature_at) if head is not None else None for head in row.heads)
                for row in labels.audit_rows() if row.session in eval_dates}
        quotes = _Quotes(features)
        summaries, accounts = {}, {}
        for scenario, (folds, _, _) in splits.items():
            checkpoint("ef_report_scenario")
            summaries[scenario] = evaluate_scenario(folds, labels_at=outcome.__getitem__, quotes=quotes)
            accounts[scenario] = _accounts(folds, quotes, checkpoint)
        report = {"schema": "EF-completed-comparison-report-v1", "snapshot_seal": asdict(seal),
                  "fit_predecessors": [item.pin() for item in artifacts],
                  "fits": sum(fits for _, fits, _ in splits.values()),
                  "forecast_rows": sum(rows for _, _, rows in splits.values()),
                  "scenarios": summaries, "accounts": accounts}
        raw = wire.encode(report)
        wire.require(len(raw) <= MAX_REPORT_BYTES, "bounded EF comparison report exceeded")
        path = directory / REPORT_FILE
        wire.exclusive_bytes(path, raw)
        stored = bridge.canonical(bridge.bounded_read(path, MAX_REPORT_BYTES))
        wire.require(wire.encode(stored) == raw, "EF saved report differs")
        proofs = {}
        for scenario, (folds, _, _) in splits.items():
            checkpoint("ef_report_verify_scenario")
            proofs[scenario] = verify_evaluation(stored["scenarios"][scenario], folds, labels_at=outcome.__getitem__, quotes=quotes)
            wire.require(stored["accounts"][scenario] == _accounts(folds, quotes, checkpoint), "EF saved account path differs")
    return {"verified": True, "scenarios": len(proofs), "artifact_sha256": wire.digest(raw), "artifact_size": len(raw),
            "evaluations": proofs}
