"""One fixed EK fold: seal target-free predictions, then compare exact ledgers.

The caller authenticates the snapshot/publication and numerical runtime and owns
resource limits. Audit reconstructs contracts and predictions without fitting.
"""

from dataclasses import asdict
from fractions import Fraction
from hashlib import sha256
from itertools import islice
import json
import os

from research import intraday_campaign_wsl_transport as wire
from research import intraday_recovery_models as models
from research import intraday_recovery_models_reference as model_reference
from research import intraday_recovery_payoff as payoff
from research import intraday_recovery_report as report
from research import intraday_recovery_snapshot as snapshot
from research.intraday_campaign_ml_labels import fixed_calendar
from research.intraday_recovery_features import STATIC_NAMES


SCHEMA = "EK-chart-recovery-comparison-v1"
MODELS = "ek-models.jsonl"
FORECASTS = "ek-forecasts.jsonl"
SEAL = "ek-forecast-seal.json"
EVALUATION = "ek-evaluation.jsonl"
REPORT = "ek-comparison.json"
HOLDING_FEATURES = "ek-holding-features.jsonl"
OPENING_ARMS = (
    ("empirical_chart", "empirical", "chart", False),
    ("lightgbm_chart", "lightgbm", "chart", False),
    ("sequence_chart", "sequence", "chart", False),
    ("lightgbm_chart_common", "lightgbm", "chart", True),
    ("sequence_chart_common", "sequence", "chart", True),
    ("lightgbm_chart_peer", "lightgbm", "chart_peer", True),
    ("sequence_chart_peer", "sequence", "chart_peer", True),
    ("sequence_order_removed", "sequence", "order_removed", False),
)
HOLDING_ARMS = tuple((name, kind, mode, common) for name, kind, mode, common in OPENING_ARMS
                    if name in ("lightgbm_chart", "sequence_chart", "lightgbm_chart_peer", "sequence_chart_peer"))
CONTROLS = ("own_unfiltered", "own_common", "old_rank", "old_weak")
ARM_NAMES = (*CONTROLS, *(name for name, *_ in OPENING_ARMS), *(name + "_managed" for name, *_ in HOLDING_ARMS))
_MAX_LINE = models.MAX_ARTIFACT_BYTES + wire.MIB
_MAX_FILE = 512 * wire.MIB


def _need(condition, reason):
    wire.require(condition, "EK comparison: " + reason)


def _read(path):
    wire.regular(path)
    with path.open("rb") as stream:
        while raw := stream.readline(_MAX_LINE + 1):
            _need(len(raw) <= _MAX_LINE and raw.endswith(b"\n"), "bounded JSON row required")
            row = json.loads(raw)
            _need(wire.encode(row) == raw, "canonical JSON row required")
            yield row


def _save(path, rows, *, audit):
    digest, size, count = sha256(), 0, 0
    original = iter(_read(path)) if audit else None
    stream = None if audit else path.open("xb")
    try:
        if stream is not None:
            os.fchmod(stream.fileno(), 0o600)
        for row in rows:
            raw = wire.encode(row)
            _need(len(raw) <= _MAX_LINE and size + len(raw) <= _MAX_FILE, "output bound exceeded")
            if audit:
                _need(next(original, None) == row, "saved output differs: " + path.name)
            else:
                wire.write_all(stream, raw)
            digest.update(raw)
            size += len(raw)
            count += 1
        if audit:
            _need(next(original, None) is None, "extra saved output row")
        else:
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if stream is not None:
            stream.close()
    return {"sha256": digest.hexdigest(), "size": size, "rows": count}


def _authenticate(slot, saved):
    for name, key in ((snapshot.FEATURES, "features"), (snapshot.LABELS, "labels"),
                      (HOLDING_FEATURES, "holding_features")):
        pin = saved[key]
        _need(wire.file_digest(slot / name, _MAX_FILE, lambda _: None) == (pin["sha256"], pin["size"]),
              "snapshot file seal differs: " + name)


def _coordinates(slot, saved, scenario, fold):
    _need(scenario in ("S1", "S2") and type(fold) is int and 1 <= fold <= 5, "fixed scenario/fold required")
    calendar = fixed_calendar(saved["header"]["sessions"])
    split = calendar.folds[fold - 1]
    features = tuple(_read(slot / snapshot.FEATURES))
    _need(len(features) == saved["header"]["coordinates"] == saved["features"]["rows"], "original feature count differs")
    keys = [(row["session"], row["symbol"]) for row in features]
    _need(keys == sorted(set(keys)) and {day for day, _ in keys} == set(calendar.source_reference_sessions)
          and all(row["key"] == row["session"] + "/" + row["symbol"] for row in features), "original feature identity differs")
    _need(sum(scenario in row["scenarios"] for row in features)
          == saved["header"]["scenario_coordinates"][scenario], "original scenario count differs")
    # The exact past-prefix count comes from features, never the next label row.
    prefix = tuple(row for row in features if row["session"] < split.test_sessions[0])
    past = tuple(islice(_read(slot / snapshot.LABELS), len(prefix)))
    _need(len(past) == len(prefix) and all((left["key"], left["session"], left["symbol"])
          == (right["key"], right["session"], right["symbol"]) for left, right in zip(prefix, past, strict=True)),
          "training label prefix differs")
    held = tuple(_read(slot / HOLDING_FEATURES))
    _need(len(held) == len(features) == saved["holding_features"]["rows"]
          and all((left["key"], left["session"], left["symbol"])
                  == (right["key"], right["session"], right["symbol"])
                  for left, right in zip(features, held, strict=True)), "target-free held roster differs")
    for row in held:
        _need(set(row) == {"key", "session", "symbol", "holding_queries"}
              and type(row["holding_queries"]) is list
              and all(set(item) == {"end_offset", "features", "peer"} for item in row["holding_queries"]),
              "held feature file contains targets or unknown fields")
    return split, features, {row["key"]: row for row in past}, {row["key"]: row for row in held}


def _supported(feature, peer, common):
    return feature["chart_state"] == "available" and (not common or peer["status"] == "available")


def _numeric(feature, peer, mode):
    minute = tuple(tuple(map(float, point)) for point in feature["minute"])
    static = tuple(map(float, feature["static"]))
    peers = tuple(tuple(map(float, point)) for point in peer["minute"]) if mode == "chart_peer" else None
    return minute, static, peers


def _targets(label):
    outcome = label["outcome"]
    gross = None if outcome["gross_pct"] is None else float(Fraction(outcome["gross_pct"]))
    path = None if outcome["path"] is None else outcome["path"]["class"]
    return gross, path


def _prepare(features, labels, held, split, scenario, mode, common, provenance, *, holding):
    training, queries, stocks = [], [], {}
    past, future = set(split.training_sessions), set(split.test_sessions)
    for row in features:
        if scenario not in row["scenarios"] or row["session"] not in past | future:
            continue
        if holding:
            entries = held[row["key"]]["holding_queries"]
        else:
            entries = [{"end_offset": 60, "features": row["own"], "peer": {
                scenario: row["scenarios"][scenario]["peer"]}}]
        offsets = [item["end_offset"] for item in entries]
        _need(offsets == sorted(set(offsets)) and all(value in ((75, 90, 105) if holding else (60,)) for value in offsets),
              "query offsets differ")
        label_updates = ({item["end_offset"]: item for item in labels[row["key"]]["holding_queries"]}
                         if holding and row["session"] in past else {})
        for item in entries:
            feature, peer = item["features"], item["peer"][scenario]
            if not _supported(feature, peer, common):
                continue
            minute, static, peers = _numeric(feature, peer, mode)
            key = row["key"] + ("@" + str(item["end_offset"]) if holding else "")
            if row["session"] in past:
                if holding:
                    _need(item["end_offset"] in label_updates, "held training target coordinate missing")
                    value = label_updates[item["end_offset"]]["remaining_gross_pct"]
                    gross, path = None if value is None else float(Fraction(value)), None
                else:
                    gross, path = _targets(labels[row["key"]])
                training.append(models.ModelRow(key, row["session"], minute, static, gross, path, row["session"], peers))
                stocks[key] = row["symbol"]
            else:
                queries.append(models.ModelQuery(key, row["session"], minute, static, peers))
    prepared = models.prepare_training(training, stock_by_key=stocks, fold=split.index,
        cutoff_session=split.test_sessions[0], mode=mode, static_names=STATIC_NAMES, provenance=provenance,
        target_kind="holding_remaining" if holding else "opening")
    query = models.prepare_queries(queries, models.training_contract(prepared))
    return prepared, query


def _saved_predictions(rows, name, *, holding):
    result = {}
    for row in rows:
        if holding:
            for item in row["holding"][name]:
                if item["supported"]:
                    result[row["key"] + "@" + str(item["end_offset"])] = models.ModelPrediction(
                        row["key"] + "@" + str(item["end_offset"]), item["gross_pct"], None)
        else:
            item = row["opening"][name]
            if item["supported"]:
                result[row["key"]] = models.ModelPrediction(row["key"], item["gross_pct"],
                    None if item["probabilities"] is None else tuple(item["probabilities"]))
    return result


def _fit_predictions(features, labels, held, split, scenario, provenance, *, saved_models=None, saved_forecasts=None):
    artifacts, forecasts, contracts = [], {}, {}
    for holding, arms in ((False, OPENING_ARMS), (True, HOLDING_ARMS)):
        for name, kind, mode, common in arms:
            identity = ("holding_" if holding else "opening_") + name
            prepared, query = _prepare(features, labels, held, split, scenario, mode, common, provenance, holding=holding)
            contract = models.training_contract(prepared)
            contracts[identity] = contract
            if saved_models is None:
                artifact = models.fit_model(kind, prepared)
                predicted = models.predict_model(artifact, query)
            else:
                saved = saved_models[identity]
                artifact = models.load_model(models.encode(saved["artifact"]), expected_sha256=saved["bytes_sha256"])
                _need(artifact["training"] == contract and artifact["kind"] == kind, "saved training identity differs")
                values = _saved_predictions(saved_forecasts, name, holding=holding)
                _need(set(values) == {row.key for row in query.rows}, "saved prediction support differs")
                predicted = tuple(values[row.key] for row in query.rows)
                model_reference.verify_predictions(artifact, query, predicted)
            artifacts.append({"key": identity, "artifact": artifact,
                              "bytes_sha256": sha256(models.dump_model(artifact)).hexdigest()})
            forecasts[identity] = {row.key: asdict(row) for row in predicted}
    # These contrasts must use exactly the same past targets, sample weights and query coordinates.
    for kind in ("lightgbm", "sequence"):
        first, second = contracts["opening_" + kind + "_chart_common"], contracts["opening_" + kind + "_chart_peer"]
        _need(all(first[key] == second[key] for key in ("roster_sha256", "targets_sha256", "weights_sha256")),
              "peer increment training roster differs")
        _need(forecasts["opening_" + kind + "_chart_common"].keys()
              == forecasts["opening_" + kind + "_chart_peer"].keys(), "peer increment query roster differs")
    return artifacts, forecasts


def _forecast_rows(features, held, split, scenario, values):
    for row in features:
        if row["session"] not in split.test_sessions or scenario not in row["scenarios"]:
            continue
        opening, holding = {}, {}
        for name, *_ in OPENING_ARMS:
            forecast = values["opening_" + name].get(row["key"])
            opening[name] = {"supported": forecast is not None, "gross_pct": None if forecast is None else forecast["gross_pct"],
                            "probabilities": None if forecast is None or forecast["probabilities"] is None
                            else list(forecast["probabilities"])}
        for name, *_ in HOLDING_ARMS:
            holding[name] = []
            for item in held[row["key"]]["holding_queries"]:
                forecast = values["holding_" + name].get(row["key"] + "@" + str(item["end_offset"]))
                holding[name].append({"end_offset": item["end_offset"], "decision_at": item["features"]["decision_at"],
                    "supported": forecast is not None, "gross_pct": None if forecast is None else forecast["gross_pct"]})
        yield {"key": row["key"], "session": row["session"], "symbol": row["symbol"],
               "opening": opening, "holding": holding}


def _eligible(row, scenario, arm, forecast):
    own, context = row["own"], row["scenarios"][scenario]
    if arm in ("old_rank", "old_weak"):
        rank = context["rank_control"]
        if rank["rank_state"] == "unresolved" or rank["order_state"] == "unresolved":
            return "unsupported"
        if rank["rank_state"] != "select" or rank["order_state"] != "ready":
            return "no_entry"
        if arm == "old_weak":
            if context["weak_control"] == "unresolved":
                return "unsupported"
            if context["weak_control"] != "weak":
                return "no_entry"
        return "select"
    if own["own_return"] is None:
        return "unsupported"
    if Fraction(own["own_return"]) >= 0 or own["order_state"] == "decline":
        return "no_entry"
    if own["order_state"] != "ready":
        return "unsupported"
    if arm == "own_unfiltered":
        return "select"
    if arm == "own_common":
        return "select" if context["peer"]["status"] == "available" and own["chart_state"] == "available" else "unsupported"
    if forecast is None or not forecast["supported"] or forecast["gross_pct"] is None:
        return "unsupported"
    return "select" if forecast["gross_pct"] > 0.33 else "no_entry"


def _record(row, label, split, scenario, arm, forecast=None):
    decision = _eligible(row, scenario, arm, forecast)
    result = {"key": row["key"], "session": row["session"], "symbol": row["symbol"], "fold": split.index,
        "status": decision, "selected": decision == "select", "gross": None, "cost": None,
        "net": None, "entry_notional": None, "path_truth": None, "probabilities": None, "predicted_gross_pct": None}
    if row["own"]["own_return"] is not None and Fraction(row["own"]["own_return"]) < 0:
        result["path_truth"] = _targets(label)[1]
        if forecast is not None:
            result["probabilities"] = forecast["probabilities"]
            result["predicted_gross_pct"] = forecast["gross_pct"]
    if decision == "no_entry":
        result.update({name: "0" for name in report.AMOUNTS})
    elif decision == "select":
        baseline = label["outcome"]["baseline"]
        _need(baseline is not None and baseline["status"] in ("closed", "no_fill", "unresolved"),
              "selected opportunity has no original execution outcome")
        result["status"] = baseline["status"]
        result.update({name: baseline[name] for name in report.AMOUNTS})
    return result


def _management(row, label, initial, forecasts, *, saved=None, audit=False):
    baseline = label["outcome"]["baseline"]
    if not initial["selected"] or baseline is None or baseline["entry_at"] is None:
        _need(not audit or saved is None, "nonentry has saved holding ledger")
        return dict(initial), None
    session = snapshot.holding_session(label)
    updates = [{"decision_at": item["decision_at"], "available_at": item["decision_at"],
                "expected_remaining_gross_pct": None if item["gross_pct"] is None else str(item["gross_pct"])}
               for item in forecasts]
    ledger = saved if audit else payoff.manage(session, baseline, updates)
    payoff.verify_management(session, baseline, updates, ledger)
    result = {**initial, "status": ledger["status"], "gross": ledger["gross"], "cost": ledger["cost"],
              "net": ledger["net"], "entry_notional": ledger["entry_basis"] if ledger["status"] == "closed" else None}
    _need(result["status"] in ("closed", "unresolved"), "filled management did not finish")
    if result["status"] == "unresolved":
        result.update({name: None for name in report.AMOUNTS})
    return result, ledger


def _evaluation(features, labels, forecasts, split, scenario, *, saved=None, audit=False):
    evaluated = {row["key"]: row for row in features
                 if row["session"] in split.test_sessions and scenario in row["scenarios"]}
    seen = set()
    for label in labels:
        key = label["key"]
        if key not in evaluated:
            continue
        _need(key not in seen, "duplicate evaluation label")
        seen.add(key)
        row, prediction = evaluated[key], forecasts[key]
        _need((label["session"], label["symbol"]) == (row["session"], row["symbol"]), "evaluation identity differs")
        records = {name: _record(row, label, split, scenario, name) for name in CONTROLS}
        for name, *_ in OPENING_ARMS:
            records[name] = _record(row, label, split, scenario, name, prediction["opening"][name])
        ledgers = {}
        for name, *_ in HOLDING_ARMS:
            prior = None if saved is None else saved[key]["management"][name]
            records[name + "_managed"], ledgers[name] = _management(row, label, records[name], prediction["holding"][name],
                                                                  saved=prior, audit=audit)
        # Selection-neutral forecast score: every own-negative supported query uses the same fixed baseline.
        scoring = {}
        for name, *_ in OPENING_ARMS:
            forecast = prediction["opening"][name]
            score = _record(row, label, split, scenario, "own_unfiltered", forecast)
            if not forecast["supported"]:
                score.update(status="unsupported", selected=False, **{amount: None for amount in report.AMOUNTS})
            scoring[name] = score
        yield {"key": key, "arms": records, "management": ledgers, "forecast_scores": scoring}
    _need(seen == set(evaluated), "evaluation labels omitted original coordinates")


def compare(snapshot_slot, output_slot, snapshot_report, *, scenario, fold, provenance, audit=False):
    """Run/audit one declared fold; no evaluation outcomes are decoded before the forecast seal."""
    _need(type(audit) is bool, "explicit audit mode required")
    _authenticate(snapshot_slot, snapshot_report)
    split, features, training_labels, held = _coordinates(snapshot_slot, snapshot_report, scenario, fold)
    saved_models = saved_forecasts = saved_evaluation = None
    if audit:
        sealed, = tuple(_read(output_slot / SEAL))
        for name, pin in ((MODELS, sealed["models"]), (FORECASTS, sealed["forecasts"])):
            _need(wire.file_digest(output_slot / name, _MAX_FILE, lambda _: None) == (pin["sha256"], pin["size"]),
                  "saved forecast publication changed")
        original_models = tuple(_read(output_slot / MODELS))
        saved_models = {row["key"]: row for row in original_models}
        expected = tuple("opening_" + name for name, *_ in OPENING_ARMS) + tuple("holding_" + name for name, *_ in HOLDING_ARMS)
        _need(tuple(row["key"] for row in original_models) == expected, "saved model roster differs")
        saved_forecasts = tuple(_read(output_slot / FORECASTS))
    artifacts, values = _fit_predictions(features, training_labels, held, split, scenario, provenance,
                                         saved_models=saved_models, saved_forecasts=saved_forecasts)
    model_pin = _save(output_slot / MODELS, artifacts, audit=audit)
    forecast_rows = list(_forecast_rows(features, held, split, scenario, values))
    forecast_pin = _save(output_slot / FORECASTS, forecast_rows, audit=audit)
    seal = {"schema": SCHEMA, "scenario": scenario, "fold": fold, "provenance": provenance,
            "training_sessions": list(split.training_sessions), "evaluation_sessions": list(split.test_sessions),
            "snapshot": {key: snapshot_report[key] for key in ("features", "holding_features", "labels")},
            "models": model_pin, "forecasts": forecast_pin}
    seal_pin = _save(output_slot / SEAL, [seal], audit=audit)
    wire.fsync_directory(output_slot)
    # Only now may evaluation labels, modeled fills and future prices enter this path.
    if audit:
        saved_rows = tuple(_read(output_slot / EVALUATION))
        saved_evaluation = {row["key"]: row for row in saved_rows}
        _need(len(saved_evaluation) == len(saved_rows), "duplicate saved evaluation row")
    first = sum(row["session"] < split.test_sessions[0] for row in features)
    after = sum(row["session"] <= split.test_sessions[-1] for row in features)
    evaluation_labels = islice(_read(snapshot_slot / snapshot.LABELS), first, after)
    evaluated = list(_evaluation(features, evaluation_labels,
        {row["key"]: row for row in forecast_rows}, split, scenario, saved=saved_evaluation, audit=audit))
    evaluation_pin = _save(output_slot / EVALUATION, evaluated, audit=audit)
    arm_rows = {name: [row["arms"][name] for row in evaluated] for name in ARM_NAMES}
    calendar = list(split.test_sessions)
    summaries = {name: report.summarize(rows, calendar) for name, rows in arm_rows.items()}
    for name, rows in arm_rows.items():
        report.verify_summary(rows, calendar, summaries[name])
    contrasts = {name + "_vs_unfiltered": (name, "own_unfiltered") for name, *_ in OPENING_ARMS}
    contrasts.update({name + "_vs_unfiltered": (name, "own_unfiltered") for name in ("old_rank", "old_weak", "own_common")})
    for kind in ("lightgbm", "sequence"):
        contrasts[kind + "_peer_increment"] = (kind + "_chart_peer", kind + "_chart_common")
    contrasts["sequence_vs_tree"] = ("sequence_chart", "lightgbm_chart")
    contrasts["temporal_order_increment"] = ("sequence_chart", "sequence_order_removed")
    contrasts.update({name + "_holding_increment": (name + "_managed", name) for name, *_ in HOLDING_ARMS})
    result = {"schema": SCHEMA, "scenario": scenario, "fold": fold, "fit_artifacts": len(artifacts),
        "original_evaluation_coordinates": len(evaluated), "evaluation_sessions": calendar,
        "files": {MODELS: model_pin, FORECASTS: forecast_pin, SEAL: seal_pin, EVALUATION: evaluation_pin},
        "arms": summaries, "pairs": {name: report.pair(arm_rows[new], arm_rows[old]) for name, (new, old) in contrasts.items()},
        "forecast_scores": {name: report.summarize([row["forecast_scores"][name] for row in evaluated], calendar)["forecast"]
                            for name, *_ in OPENING_ARMS},
        "forecast_score_scope": "original own-negative opportunities; admission-independent fixed execution outcomes",
        "claim": "Discovery only; S1/S2 overlap; nominal event cash, not concurrent account NAV; no promotion."}
    _save(output_slot / REPORT, [result], audit=audit)
    _authenticate(snapshot_slot, snapshot_report)
    return result
