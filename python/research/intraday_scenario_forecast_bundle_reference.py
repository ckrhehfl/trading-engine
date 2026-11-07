"""Read a sealed EH forecast bundle without invoking the producer or model."""

from datetime import UTC, date, datetime, timedelta, timezone
import json
from math import fsum, isclose
import os
from pathlib import Path
import stat
from typing import BinaryIO, Callable

from research import intraday_campaign_wsl_transport as wire
from research.intraday_campaign_ml_labels import FOLD_SLICES


_SCHEMA = "eh-forecast-fold-bundle-v1"
_PREDICTION_SCHEMA = "eh-opening-fold-predictions-v1"
_SNAPSHOT_SCHEMAS = {"features": "eh-opening-features-v1", "labels": "eh-opening-labels-v1"}
_MAX_BUNDLE = 64 * wire.MIB
_MAX_HEADER = wire.MIB
_MAX_ROW = 8 * wire.MIB
_MAX_FOLD_RECORDS = 40 * 2 * 13 * 40 + 1
_KST = timezone(timedelta(hours=9))
_GROUPS = tuple((stage, clock, contrast) for stage in ("approach", "breakout")
                for clock in ("initial", "update") for contrast in ("chart_vs_empirical", "market_vs_matched_chart"))
_IDENTITY = ("stage", "clock", "contrast")
_COUNTS = ("candidates", "first_supported", "second_supported", "paired_forecasts",
           "paired_outcomes", "scored_events", "scored_dates")
_HORIZONS = (5, 15, 30)
_HORIZON_COUNTS = ("horizon_pairs", "horizon_outcomes", "horizon_events", "horizon_dates")
_SCORE_TOLERANCE = 1e-12


def _fields(value: object, names: tuple[str, ...], message: str) -> None:
    wire.require(type(value) is dict and set(value) == set(names), message)


def _integer(value: object, low: int, high: int) -> bool:
    return type(value) is int and low <= value <= high


def _seal(value: dict, schema: str, size_limit: int, record_range: tuple[int, int]) -> None:
    _fields(value, ("schema", "sha256", "size", "records"), "EH bundle seal fields differ")
    wire.require(value["schema"] == schema and wire.valid_sha(value["sha256"])
                 and _integer(value["size"], 1, size_limit) and _integer(value["records"], *record_range),
                 "EH bundle seal bounds differ")


def _document(raw: bytes) -> dict:
    value = json.loads(raw)
    wire.require(type(value) is dict and wire.encode(value) == raw, "EH bundle record is not canonical")
    return value


def _day(value: str) -> str:
    wire.require(type(value) is str and len(value) == 10 and date.fromisoformat(value).isoformat() == value,
                 "EH bundle evaluation date is not canonical")
    return value


def _clock(value: str) -> datetime:
    wire.require(type(value) is str and len(value) == 25, "EH bundle decision clock differs")
    timestamp = datetime.fromisoformat(value)
    wire.require(timestamp.tzinfo == UTC and timestamp.isoformat() == value
                 and timestamp.second == timestamp.microsecond == 0, "EH bundle decision clock differs")
    return timestamp


def _vector(value: object, low: float, high: float) -> bool:
    return type(value) is list and len(value) == 3 and all(
        item is None or (type(item) in (int, float) and low <= item <= high) for item in value)


def _same_vector(value: list, expected: tuple) -> bool:
    return _vector(value, -2 - _SCORE_TOLERANCE, 2 + _SCORE_TOLERANCE) and all(
        left is right if left is None or right is None else isclose(left, right, rel_tol=0, abs_tol=_SCORE_TOLERANCE)
        for left, right in zip(value, expected, strict=True))


def _report_counts(report: dict, scenario: str, index: int, days: list[str]) -> list[dict]:
    _fields(report, ("scenario", "fold", "original_coordinates", "decisions_without_question", "groups"),
            "EH bundle fold report fields differ")
    wire.require(report["scenario"] == scenario and _integer(report["fold"], index, index)
                 and _integer(report["original_coordinates"], 20 * len(days), 20 * len(days))
                 and _integer(report["decisions_without_question"], 0, _MAX_FOLD_RECORDS - 1)
                 and type(report["groups"]) is list and len(report["groups"]) == len(_GROUPS),
                 "EH bundle fold report identity or original population differs")
    for group, expected in zip(report["groups"], _GROUPS, strict=True):
        _fields(group, (*_IDENTITY, "counts"), "EH bundle report group fields differ")
        wire.require(tuple(group[key] for key in _IDENTITY) == expected, "EH bundle report group order differs")
        counts = group["counts"]
        _fields(counts, (*_COUNTS, *_HORIZON_COUNTS, "first_brier", "second_brier", "daily_difference"),
                "EH bundle group scores differ")
        wire.require(all(_integer(counts[key], 0, _MAX_FOLD_RECORDS - 1) for key in _COUNTS),
                     "EH bundle report counts are invalid")
        candidates, first, second, paired, outcomes, events, dates = (counts[key] for key in _COUNTS)
        wire.require(dates <= events <= outcomes <= paired <= min(first, second) <= max(first, second) <= candidates
                     and paired >= first + second - candidates and (dates == 0) == (outcomes == 0),
                     "EH bundle report counts contradict one another")
        pairs, by_outcome, by_event, by_date = (counts[key] for key in _HORIZON_COUNTS)
        wire.require(all(type(value) is list and len(value) == 3 and all(_integer(item, 0, paired) for item in value)
                         for value in (pairs, by_outcome, by_event, by_date))
                     and all(d <= e <= o <= p and (d == 0) == (o == 0)
                             for p, o, e, d in zip(pairs, by_outcome, by_event, by_date, strict=True))
                     and all(max(vector) <= total <= sum(vector) for vector, total in
                             ((by_outcome, outcomes), (by_event, events), (by_date, dates))),
                     "EH bundle per-horizon support counts contradict one another")
        daily = counts["daily_difference"]
        wire.require(type(daily) is list and len(daily) == dates <= len(days)
                     and all(type(row) is list and len(row) == 2 and row[0] in days
                             and _vector(row[1], -2 - _SCORE_TOLERANCE, 2 + _SCORE_TOLERANCE)
                             and any(value is not None for value in row[1]) for row in daily),
                     "EH bundle scored dates differ")
        scored_days = [row[0] for row in daily]
        wire.require(scored_days == sorted(set(scored_days)), "EH bundle scored dates are not strictly increasing")
        left, right = counts["first_brier"], counts["second_brier"]
        if dates:
            wire.require(_vector(left, 0, 2 + _SCORE_TOLERANCE) and _vector(right, 0, 2 + _SCORE_TOLERANCE),
                         "EH bundle fold Brier differs")
            columns = tuple(tuple(value for value in column if value is not None)
                            for column in zip(*(row[1] for row in daily), strict=True))
            wire.require(tuple(map(len, columns)) == tuple(by_date)
                         and all((a is None) == (b is None) == (d == 0)
                                 for a, b, d in zip(left, right, by_date, strict=True)), "EH bundle horizon scores differ")
            daily_mean = tuple(fsum(column) / len(column) if column else None for column in columns)
            wire.require(_same_vector([None if a is None else b - a for a, b in zip(left, right, strict=True)], daily_mean),
                         "EH bundle fold Brier differs from date-weighted differences")
        else:
            wire.require(left is None and right is None, "EH bundle empty Brier differs")
    return report["groups"]


def _aggregate(groups: list[list[dict]], combined: list[dict]) -> None:
    wire.require(type(combined) is list and len(combined) == len(_GROUPS), "EH bundle combined group count differs")
    for identity, merged, members in zip(_GROUPS, combined, zip(*groups, strict=True), strict=True):
        _fields(merged, (*_IDENTITY, *_COUNTS, *_HORIZON_COUNTS, "first_brier", "second_brier", "second_minus_first"),
                "EH bundle combined scores fields differ")
        wire.require(tuple(merged[key] for key in _IDENTITY) == identity, "EH bundle combined score identity differs")
        scores = [member["counts"] for member in members]
        for key in _COUNTS:
            total = sum(score[key] for score in scores)
            wire.require(_integer(merged[key], total, total), "EH bundle combined counts differ")
        for key in _HORIZON_COUNTS:
            total = [sum(score[key][horizon] for score in scores) for horizon in range(3)]
            wire.require(type(merged[key]) is list and len(merged[key]) == 3 and all(
                _integer(value, count, count) for value, count in zip(merged[key], total, strict=True)),
                "EH bundle combined horizon counts differ")
        dates = merged["scored_dates"]
        horizon_dates = merged["horizon_dates"]
        for key in ("first_brier", "second_brier"):
            if dates:
                mean = tuple(fsum(score[key][horizon] * (score["horizon_dates"][horizon] / count)
                                  for score in scores if score["horizon_dates"][horizon]) if count else None
                             for horizon, count in enumerate(horizon_dates))
                wire.require(_same_vector(merged[key], mean), "EH bundle combined Brier differs")
            else:
                wire.require(merged[key] is None, "EH bundle combined empty Brier differs")
        if dates:
            daily = (row[1] for score in scores for row in score["daily_difference"])
            mean_difference = tuple(fsum(value for value in values if value is not None) / count if count else None
                for values, count in zip(zip(*daily, strict=True), horizon_dates, strict=True))
            wire.require(_same_vector(merged["second_minus_first"], mean_difference), "EH bundle combined difference differs")
        else:
            wire.require(merged["second_minus_first"] is None, "EH bundle combined empty difference differs")


def _forecast_row(row: dict, days: set[str]) -> tuple[tuple, tuple[str, str] | None, list, tuple[bool, ...]]:
    _fields(row, ("day", "symbol", "stage", "decision_at", "question", "chart_status", "peers_supported", "probabilities"),
            "EH bundle prediction row fields differ")
    day, symbol, stage = row["day"], row["symbol"], row["stage"]
    at = _clock(row["decision_at"])
    wire.require(type(day) is str and day in days and day == at.date().isoformat() == at.astimezone(_KST).date().isoformat()
                 and type(symbol) is str and bool(symbol) and stage in ("approach", "breakout"),
                 "EH bundle prediction row is outside its fold")
    arms = row["probabilities"]
    wire.require(type(arms) is list and len(arms) == 4 and all(
        arm is None or (type(arm) is list and len(arm) == 10
                        and all(type(weight) in (int, float) and 0 <= weight <= 1 for weight in arm)
                        and isclose(fsum(arm), 1, rel_tol=0, abs_tol=1e-9)) for arm in arms),
        "EH bundle prediction probability differs")
    status, peers = row["chart_status"], row["peers_supported"]
    wire.require(status in ("ready", "resolved", "unknown", "unsupported") and type(peers) is bool
                 and (status == "ready" or (not peers and all(arm is None for arm in arms)))
                 and (peers or all(arm is None for arm in arms[2:])), "EH bundle prediction support differs")
    identity = day, at, symbol, stage
    question = row["question"]
    if question is None:
        wire.require(status == "unsupported", "EH bundle absent question has chart support")
        return identity, None, arms, (False,) * len(_HORIZONS)
    wire.require(type(question) is list and len(question) == 6 and all(type(part) is str for part in question[1:5]),
                 "EH bundle prediction question fields differ")
    start, end = _clock(question[0]), _clock(question[-1])
    wire.require(start.date().isoformat() == day and start.astimezone(_KST).date().isoformat() == day
                 and start <= at and end - start == timedelta(minutes=30), "EH bundle prediction question time differs")
    pending = tuple(at < start + timedelta(minutes=horizon) for horizon in _HORIZONS)
    return identity, (stage, "initial" if start == at else "update"), arms, pending


def _member(stream: BinaryIO, fold: dict, index: int, scenario: str, snapshot: dict,
            outer, checkpoint: Callable[[str], object], expected_roster: dict | None) -> None:
    seal, report, days = fold["seal"], fold["score"], set(fold["test_sessions"])
    remaining = seal["size"]
    member = wire._sha256()

    def line() -> dict:
        nonlocal remaining
        raw = stream.readline(min(remaining, _MAX_ROW + 1))
        wire.require(0 < len(raw) <= _MAX_ROW and raw.endswith(b"\n"), "EH bundle member is truncated or unbounded")
        member.update(raw)
        outer.update(raw)
        remaining -= len(raw)
        return _document(raw)

    expected_header = {"schema": _PREDICTION_SCHEMA, "scenario": scenario, "fold": index,
                       "feature_sha256": snapshot["features"]["sha256"], "label_sha256": snapshot["labels"]["sha256"]}
    header = line()
    wire.require(header == expected_header and type(header["fold"]) is int, "EH bundle member header differs")
    metadata = line()
    _fields(metadata, ("original_coordinates", "training_events", "training_dates", "matched_training_events",
                       "matched_training_dates", "artifacts"), "EH bundle member metadata differs")
    original = report["original_coordinates"]
    wire.require(_integer(metadata["original_coordinates"], original, original)
                 and type(metadata["artifacts"]) is list and len(metadata["artifacts"]) == 3
                 and all(item is None or type(item) is dict for item in metadata["artifacts"]),
                 "EH bundle member original population or artifact count differs")
    training_start, training_end, _, _ = FOLD_SLICES[index - 1]
    training_span = training_end - training_start
    events, sessions, matched, matched_sessions = (metadata[key] for key in (
        "training_events", "training_dates", "matched_training_events", "matched_training_dates"))
    wire.require(_integer(sessions, 0, training_span) and _integer(events, sessions, sessions * 20)
                 and _integer(matched_sessions, 0, sessions) and _integer(matched, matched_sessions, min(events, matched_sessions * 20)),
                 "EH bundle member training population differs")
    observed = {group: [0, 0, 0, 0, set(), [0] * len(_HORIZONS), [set() for _ in _HORIZONS]] for group in _GROUPS}
    previous, missing = None, 0
    symbols = set()
    expected = None if expected_roster is None else iter(
        (day, *decision) for day in fold["test_sessions"] for decision in expected_roster[day])
    for count in range(seal["records"] - 1):
        if count % 128 == 0:
            checkpoint("eh_forecast_verify_fold_rows")
        row = line()
        identity, group, arms, pending = _forecast_row(row, days)
        if expected is not None:
            roster_key = tuple(row[key] for key in ("day", "symbol", "stage", "decision_at", "question",
                                                   "chart_status", "peers_supported"))
            wire.require(next(expected, None) == roster_key, "EH bundle prediction roster differs from sealed features")
        wire.require(previous is None or previous < identity, "EH bundle prediction rows are not strictly ordered")
        if previous is not None and previous[0] != identity[0]:
            symbols.clear()
        symbols.add(identity[2])
        wire.require(len(symbols) <= 20, "EH bundle prediction population exceeds twenty symbols in one session")
        previous = identity
        if group is None:
            missing += 1
            continue
        for contrast, left, right in (("chart_vs_empirical", arms[0], arms[1]), ("market_vs_matched_chart", arms[2], arms[3])):
            support = observed[(*group, contrast)]
            support[0] += 1
            support[1] += left is not None
            support[2] += right is not None
            if left is not None and right is not None:
                support[3] += 1
                event = identity[0], identity[2]
                if any(pending):
                    support[4].add(event)
                for horizon, eligible in enumerate(pending):
                    if eligible:
                        support[5][horizon] += 1
                        support[6][horizon].add(event)
    footer = line()
    wire.require(expected is None or next(expected, None) is None, "EH bundle prediction roster is incomplete")
    wire.require(footer == {"records": seal["records"]} and type(footer["records"]) is int
                 and remaining == 0 and member.hexdigest() == seal["sha256"], "EH bundle member seal or EOF differs")
    wire.require(missing == report["decisions_without_question"], "EH bundle missing questions count differs")
    for group in report["groups"]:
        candidates, first, second, paired, events, live, live_events = observed[tuple(group[key] for key in _IDENTITY)]
        scores = group["counts"]
        wire.require((candidates, first, second, paired) == tuple(scores[key] for key in _COUNTS[:4])
                     and scores["scored_events"] <= len(events)
                     and {row[0] for row in scores["daily_difference"]} <= {day for day, _ in events}
                     and scores["horizon_pairs"] == live
                     and all(scores["horizon_events"][horizon] <= len(eligible)
                             and {day for day, values in scores["daily_difference"] if values[horizon] is not None}
                             <= {day for day, _ in eligible} for horizon, eligible in enumerate(live_events)),
                     "EH bundle report support differs from sealed predictions")


def verify_forecast_bundle(
    path: Path, *, scenario: str, expected_sha256: str, expected_size: int, expected_members: dict,
    checkpoint: Callable[[str], object], expected_evaluation_sessions: tuple[str, ...] | None = None,
    expected_forecast_roster: dict | None = None,
) -> dict:
    """Check saved bytes, support counts and date weighting without opening labels."""
    wire.require(scenario in ("S1", "S2") and wire.valid_sha(expected_sha256)
                 and _integer(expected_size, 1, _MAX_BUNDLE) and callable(checkpoint), "EH bundle expected identity differs")
    _fields(expected_members, ("features", "labels"), "EH bundle expected snapshot members differ")
    sessions = FOLD_SLICES[-1][-1]
    _seal(expected_members["features"], _SNAPSHOT_SCHEMAS["features"], 128 * wire.MIB, (sessions, sessions))
    _seal(expected_members["labels"], _SNAPSHOT_SCHEMAS["labels"], 128 * wire.MIB, (0, 366 * 40 * 2))
    before = wire.regular(path)
    identity = wire.file_identity(before)
    wire.require(stat.S_IMODE(before.st_mode) == 0o400 and before.st_size == expected_size,
                 "EH bundle file size or mode differs")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == identity, "EH bundle file identity changed before open")
        raw = stream.readline(_MAX_HEADER + 1)
        wire.require(len(raw) <= _MAX_HEADER and raw.endswith(b"\n"), "EH bundle outer header is truncated or unbounded")
        header = _document(raw)
        _fields(header, ("schema", "scenario", "snapshot", "folds", "date_weighted_comparisons"), "EH bundle outer fields differ")
        wire.require(header["schema"] == _SCHEMA and header["scenario"] == scenario
                     and wire.encode(header["snapshot"]) == wire.encode(expected_members), "EH bundle outer identity differs")
        folds = header["folds"]
        wire.require(type(folds) is list and len(folds) == len(FOLD_SLICES), "EH bundle fold roster differs")
        dates, groups = [], []
        for index, ((_, _, start, end), fold) in enumerate(zip(FOLD_SLICES, folds, strict=True), 1):
            _fields(fold, ("fold", "test_sessions", "seal", "score"), "EH bundle fold fields differ")
            days = fold["test_sessions"]
            wire.require(_integer(fold["fold"], index, index) and type(days) is list and len(days) == end - start,
                         "EH bundle fold evaluation span differs")
            dates.extend(_day(day) for day in days)
            _seal(fold["seal"], _PREDICTION_SCHEMA, _MAX_BUNDLE, (1, _MAX_FOLD_RECORDS))
            groups.append(_report_counts(fold["score"], scenario, index, days))
        wire.require(dates == sorted(set(dates)) and (expected_evaluation_sessions is None or tuple(dates) == expected_evaluation_sessions),
                     "EH bundle evaluation dates differ")
        wire.require(expected_forecast_roster is None or (type(expected_forecast_roster) is dict
                     and set(expected_forecast_roster) == set(dates)), "EH bundle expected roster dates differ")
        wire.require(len(raw) + sum(fold["seal"]["size"] for fold in folds) == expected_size, "EH bundle total member bytes differ")
        _aggregate(groups, header["date_weighted_comparisons"])
        outer = wire._sha256(raw)
        for index, fold in enumerate(folds, 1):
            checkpoint("eh_forecast_verify_fold")
            _member(stream, fold, index, scenario, expected_members, outer, checkpoint, expected_forecast_roster)
        wire.require(not stream.read(1) and outer.hexdigest() == expected_sha256
                     and wire.file_identity(os.fstat(stream.fileno())) == identity == wire.file_identity(wire.regular(path)),
                     "EH bundle outer hash, EOF or identity differs")
    return {"schema": _SCHEMA, "scenario": scenario, "input_members": expected_members,
            "folds": len(folds), "evaluation_sessions": len(dates), "original_evaluation_coordinates": 20 * len(dates),
            "fold_seals": [fold["seal"] for fold in folds], "bundle_integrity_verified": True}
