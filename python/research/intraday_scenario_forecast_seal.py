"""Seal chronological EH forecasts before accessing their evaluation labels."""

from collections.abc import Iterator
from pathlib import Path

from research.intraday_campaign_wsl_transport import valid_sha
from research.intraday_ed_inputs import _require
from research.intraday_scenario_forecast_fold import FoldDecision, FoldForecast
from research.intraday_scenario_forecast_targets import horizon_probabilities
from research.intraday_scenario_snapshot import (
    FEATURE_SCHEMA, LABEL_SCHEMA, SnapshotSeal, _question, _read, _read_question, _read_time, _time, _write,
)


SCHEMA = "eh-opening-fold-predictions-v1"
MAX_RECORDS = 40 * 2 * 13 * 40 + 1
_ARMS = ("empirical", "chart", "chart_matched", "market_matched")


def _header(scenario: str, index: int, feature_seal: SnapshotSeal, label_seal: SnapshotSeal) -> dict:
    _require(scenario in ("S1", "S2") and type(index) is int and 1 <= index <= 5,
             "EH_seal_fold_identity_differs")
    _require(all(type(seal) is SnapshotSeal and seal.schema == schema and valid_sha(seal.sha256)
                 for seal, schema in ((feature_seal, FEATURE_SCHEMA), (label_seal, LABEL_SCHEMA))),
             "EH_seal_snapshot_members_required")
    return {"schema": SCHEMA, "scenario": scenario, "fold": index, "feature_sha256": feature_seal.sha256,
            "label_sha256": label_seal.sha256}


def _metadata(value: FoldForecast) -> dict:
    _require(all(type(count) is int and count >= 0 for count in (
        value.original_test_coordinates, value.training_events, value.training_dates,
        value.matched_training_events, value.matched_training_dates))
        and value.matched_training_dates <= min(value.matched_training_events, value.training_dates)
        and value.matched_training_events <= value.training_events and value.training_dates <= value.training_events
        and len(value.artifacts) == 3, "EH_seal_fold_counts_differs")
    return {"original_coordinates": value.original_test_coordinates, "training_events": value.training_events,
            "training_dates": value.training_dates, "matched_training_events": value.matched_training_events,
            "matched_training_dates": value.matched_training_dates, "artifacts": list(value.artifacts)}


def _record(decision: FoldDecision) -> dict:
    key = decision.key
    _require(decision.stage in ("approach", "breakout")
             and decision.chart_status in ("ready", "resolved", "unknown", "unsupported")
             and type(decision.peers_supported) is bool and decision.day == decision.observed_at.date().isoformat()
             and (key is None or (key[:2] == (decision.symbol, decision.stage)
                                  and key[2][0] <= key[3] == decision.observed_at)), "EH_seal_decision_differs")
    arms = [getattr(decision, arm) for arm in _ARMS]
    if decision.chart_status != "ready":
        _require(not decision.peers_supported and all(value is None for value in arms), "EH_seal_unsupported_chart")
    if not decision.peers_supported:
        _require(all(value is None for value in arms[2:]), "EH_seal_unsupported_peer")
    _require(key is not None or decision.chart_status == "unsupported", "EH_seal_question_required")
    for value in arms:
        if value is not None:
            horizon_probabilities(value)
    return {"day": decision.day, "symbol": decision.symbol, "stage": decision.stage,
            "decision_at": _time(decision.observed_at), "question": None if key is None else _question(key[2]),
            "chart_status": decision.chart_status, "peers_supported": decision.peers_supported,
            "probabilities": [None if value is None else list(value) for value in arms]}


def _decode(row: dict) -> FoldDecision:
    at, question = _read_time(row["decision_at"]), row["question"]
    symbol, stage = row["symbol"], row["stage"]
    key = None if question is None else (symbol, stage, _read_question(question).question_id, at)
    result = FoldDecision(row["day"], symbol, stage, at, key, row["chart_status"], row["peers_supported"],
                          *(None if value is None else tuple(value) for value in row["probabilities"]))
    _require(_record(result) == row, "EH_seal_record_readback_differs")
    return result


def _ordered(decisions: Iterator[FoldDecision]) -> Iterator[FoldDecision]:
    previous = None
    for row in decisions:
        coordinate = row.day, row.observed_at, row.symbol, row.stage
        _require(previous is None or coordinate > previous, "EH_seal_decision_order_differs")
        previous = coordinate
        yield row


def seal_forecasts(path: Path, result: FoldForecast, feature_seal: SnapshotSeal, label_seal: SnapshotSeal) -> SnapshotSeal:
    def records():
        yield _metadata(result)
        yield from (_record(row) for row in _ordered(iter(result.decisions)))

    return _write(path, _header(result.scenario, result.index, feature_seal, label_seal), records(), maximum=MAX_RECORDS)


def read_forecasts(path: Path, seal: SnapshotSeal, *, scenario: str, index: int,
                   feature_seal: SnapshotSeal, label_seal: SnapshotSeal) -> FoldForecast:
    stream = _read(path, seal, _header(scenario, index, feature_seal, label_seal), maximum=MAX_RECORDS)
    metadata, _ = next(stream)
    decisions = tuple(_ordered(_decode(row) for row, _ in stream))
    result = FoldForecast(scenario, index, metadata["original_coordinates"], metadata["training_events"],
                          metadata["training_dates"], metadata["matched_training_events"],
                          metadata["matched_training_dates"], tuple(metadata["artifacts"]), decisions)
    _require(_metadata(result) == metadata, "EH_seal_metadata_readback_differs")
    return result
