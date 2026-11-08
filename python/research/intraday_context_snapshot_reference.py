"""Streamed independent EI snapshot audit over caller-authenticated originals.

Each original coordinate is loaded once, including missing/no-question cases.
Features and labels are read once each through the shared sealed-file mechanics;
scientific expectations use independent context, question, feature and path
implementations. No producer projection or label algorithm is called.
"""

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from hashlib import sha256
from itertools import tee, zip_longest
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_features_reference import reference_features
from research.intraday_context_questions import ContextQuestion
from research.intraday_context_questions_reference import reference_question_frames
from research.intraday_context_snapshot import (
    FEATURE_SCHEMA, LABEL_SCHEMA, FrameSummary, LabeledCoordinate, ProjectedCoordinate, QuestionLabel,
    _read_label_coordinate, read_features,
)
from research.intraday_context_support_reference import reference_frames
from research.intraday_ed_inputs import InputRefusal, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_open_reference import _follow
from research.intraday_scenario_snapshot import SnapshotSeal, _read


SCHEMA = "ei-context-snapshot-independent-v1"
_MINUTE = timedelta(minutes=1)
_KST = timezone(timedelta(hours=9))
_HORIZONS = (5, 15, 30)
_MAX_COORDINATES = 366 * 40
ReferenceSession = Callable[[str, str], SessionInputs | None]


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_snapshot_reference_" + reason)


def _same(left, right) -> bool:
    if type(left) is not type(right):
        return False
    if is_dataclass(left):
        return all(_same(getattr(left, field.name), getattr(right, field.name)) for field in fields(left))
    if isinstance(left, tuple):
        return len(left) == len(right) and all(_same(a, b) for a, b in zip(left, right, strict=True))
    return left == right


def _stamp(at: datetime) -> str:
    _require(isinstance(at, datetime) and at.utcoffset() is not None
             and at.second == at.microsecond == 0, "minute_timestamp")
    return at.astimezone(UTC).isoformat()


def _question_key(question: ContextQuestion) -> str:
    """Independently encode the complete identity using shared canonical JSON."""
    (symbol, anchor), episode = question.episode_id
    path = question.path
    identity = [symbol, [_stamp(anchor.start_at), str(anchor.start_price), _stamp(anchor.peak_end),
                        _stamp(anchor.confirmed_at), str(anchor.price), str(anchor.pre_peak_range),
                        str(anchor.advance), str(anchor.band)], episode, question.stage,
                [_stamp(path.start_at), _stamp(path.end_at), str(path.level), str(path.band),
                 str(path.target), str(path.stop)]]
    return sha256(wire.encode(identity)).hexdigest()


def _original_roster(targets: tuple[TargetCoordinate, ...]) -> dict:
    _require(type(targets) is tuple and 0 < len(targets) <= _MAX_COORDINATES, "bounded_original_roster")
    grouped, ranks, members = {}, set(), set()
    for target in targets:
        _require(type(target) is TargetCoordinate and target.scenario in ("S1", "S2")
                 and type(target.session) is str and date.fromisoformat(target.session).isoformat() == target.session
                 and type(target.symbol) is str and 0 < len(target.symbol) <= 64
                 and type(target.rank) is int and 1 <= target.rank <= 20
                 and (target.stored_row_count is None or type(target.stored_row_count) is int and target.stored_row_count >= 0)
                 and type(target.state) is str and bool(target.state)
                 and type(target.metadata) is bytes and len(target.metadata) <= 4096, "original_target_fields")
        rank = target.session, target.scenario, target.rank
        member = target.session, target.scenario, target.symbol
        _require(rank not in ranks and member not in members, "duplicate_original_rank_or_member")
        ranks.add(rank)
        members.add(member)
        grouped.setdefault((target.session, target.symbol), []).append(target)
    _require(len({day for day, _ in grouped}) <= 366, "original_date_bound")
    return {key: tuple(sorted(grouped[key], key=lambda row: row.scenario)) for key in sorted(grouped)}


def _coordinate(day: str, symbol: str, targets: tuple[TargetCoordinate, ...], session: SessionInputs | None,
                checkpoint: Callable[[str], object]) -> ProjectedCoordinate:
    if session is None:
        return ProjectedCoordinate(day, symbol, targets, "session_missing", None, None, (), ())
    _require(type(session) is SessionInputs and session.symbol == symbol and isinstance(session.minutes, Mapping),
             "original_session_identity")
    _stamp(session.opening)
    _stamp(session.closing)
    _require(session.opening.astimezone(_KST).date().isoformat() == day
             and session.closing.astimezone(_KST).date().isoformat() == day
             and session.opening < session.closing, "original_session_calendar")
    left, right = tee(reference_frames(session, session.closing))
    questions = reference_question_frames(right, closing=session.closing)
    summaries, projected = [], []
    for frame, question_frame in zip(left, questions, strict=True):
        checkpoint("ei_snapshot_reference_frame")
        summaries.append(FrameSummary(frame.available_at, frame.classification, frame.phase,
            question_frame.status, question_frame.reason, tuple(_question_key(item) for item in question_frame.questions)))
        projected.extend(reference_features(frame, item) for item in question_frame.questions)
    length = (session.closing - session.opening) // _MINUTE
    expected_grid = tuple(session.opening + minute * _MINUTE for minute in range(32, length, 5))
    _require(len(expected_grid) <= 72 and tuple(row.available_at for row in summaries) == expected_grid,
             "independent_complete_frame_grid")
    _require(len(projected) <= 144, "question_count_bound")
    ids = tuple(_question_key(row.question) for row in projected)
    _require(len(set(ids)) == len(ids) and ids == tuple(key for row in summaries for key in row.question_ids),
             "independent_question_bijection")
    return ProjectedCoordinate(day, symbol, targets, "observed_session", session.opening, session.closing,
                               tuple(summaries), tuple(projected))


def _labels(coordinate: ProjectedCoordinate, session: SessionInputs | None,
            checkpoint: Callable[[str], object]) -> LabeledCoordinate:
    if coordinate.status == "session_missing":
        return LabeledCoordinate(coordinate.session, coordinate.symbol, "feature_session_missing", ())
    if not coordinate.questions:
        return LabeledCoordinate(coordinate.session, coordinate.symbol, "no_questions", ())
    _require(session is not None, "reference_session_disappeared")
    labels = []
    cutoff = session.closing + 2 * _MINUTE
    for feature in coordinate.questions:
        checkpoint("ei_snapshot_reference_question_label")
        question = feature.question
        results = tuple(_follow(session, replace(question.path, end_at=question.path.start_at + horizon * _MINUTE),
                                question.start_close) for horizon in _HORIZONS)
        _require(all(result.mature_at is None or result.mature_at <= cutoff for result in results),
                 "label_maturity_after_cutoff")
        labels.append(QuestionLabel(question, cutoff, "observed", results))
    return LabeledCoordinate(coordinate.session, coordinate.symbol, "observed", tuple(labels))


class _Counts:
    def __init__(self) -> None:
        self.coordinates = 0
        self.coordinate_states: Counter[str] = Counter()
        self.frames = 0
        self.frame_states: Counter[str] = Counter()
        self.contexts: Counter[str] = Counter()
        self.questions = 0
        self.feature_states = {stage: Counter() for stage in ("approach", "breakout")}
        self.horizon_states = {stage: {str(horizon): Counter() for horizon in _HORIZONS}
                               for stage in ("approach", "breakout")}
        self.horizon_paths = {stage: {str(horizon): Counter() for horizon in _HORIZONS}
                              for stage in ("approach", "breakout")}

    def add(self, coordinate: ProjectedCoordinate, labels: LabeledCoordinate) -> None:
        self.coordinates += 1
        self.coordinate_states[coordinate.status] += 1
        self.frames += len(coordinate.frames)
        self.frame_states.update(row.status for row in coordinate.frames)
        self.contexts.update(row.classification for row in coordinate.frames)
        self.questions += len(coordinate.questions)
        for feature in coordinate.questions:
            self.feature_states[feature.question.stage][feature.status] += 1
        for label in labels.labels:
            stage = label.question.stage
            for horizon, result in zip(_HORIZONS, label.results, strict=True):
                self.horizon_states[stage][str(horizon)][result.status] += 1
                if result.path is not None:
                    self.horizon_paths[stage][str(horizon)][result.path] += 1

    def payload(self) -> dict:
        result = {"coordinates": self.coordinates, "coordinate_states": dict(sorted(self.coordinate_states.items())),
                  "frames": self.frames, "frame_states": dict(sorted(self.frame_states.items())),
                  "contexts": dict(sorted(self.contexts.items())), "questions": self.questions,
                  "feature_states": {stage: dict(sorted(counts.items())) for stage, counts in self.feature_states.items()},
                  "horizon_states": {stage: {horizon: dict(sorted(counts.items())) for horizon, counts in horizons.items()}
                                     for stage, horizons in self.horizon_states.items()},
                  "horizon_paths": {stage: {horizon: dict(sorted(counts.items())) for horizon, counts in horizons.items()}
                                    for stage, horizons in self.horizon_paths.items()}}
        _require(sum(self.coordinate_states.values()) == self.coordinates
                 and sum(self.frame_states.values()) == sum(self.contexts.values()) == self.frames
                 and sum(sum(counts.values()) for counts in self.feature_states.values()) == self.questions,
                 "independent_count_balance")
        for stage, feature_states in self.feature_states.items():
            for horizon in map(str, _HORIZONS):
                _require(sum(self.horizon_states[stage][horizon].values()) == sum(feature_states.values())
                         and sum(self.horizon_paths[stage][horizon].values())
                         == self.horizon_states[stage][horizon].get("resolved", 0), "independent_horizon_balance")
        return result


def verify_snapshot(feature_path: Path, feature_seal: SnapshotSeal, label_path: Path, label_seal: SnapshotSeal,
                    expected_targets: tuple[TargetCoordinate, ...], reference_session: ReferenceSession,
                    checkpoint: Callable[[str], object]) -> dict:
    """Audit all original coordinates and both full streams in bounded memory.

    The callback supplies the same immutable original session for features and
    outcomes. Missing individual future bars remain label unknowns; a produced
    label-only missing session cannot replace a present original session.
    """
    _require(callable(reference_session) and callable(checkpoint) and feature_path != label_path,
             "separate_streams_and_reference_callback")
    groups = _original_roster(expected_targets)
    _require(type(feature_seal) is SnapshotSeal and type(label_seal) is SnapshotSeal
             and feature_seal.schema == FEATURE_SCHEMA and label_seal.schema == LABEL_SCHEMA
             and type(feature_seal.records) is int and type(label_seal.records) is int
             and feature_seal.records == label_seal.records == len(groups), "exact_snapshot_seals")
    features = read_features(feature_path, feature_seal, expected_targets=expected_targets)
    label_header = {"schema": LABEL_SCHEMA, "feature_sha256": feature_seal.sha256,
                    "feature_size": feature_seal.size, "feature_records": feature_seal.records,
                    "horizons_minutes": list(_HORIZONS)}
    labels = _read(label_path, label_seal, label_header, maximum=_MAX_COORDINATES)
    total, scenarios = _Counts(), {scenario: _Counts() for scenario in ("S1", "S2")}
    calendars = {}
    sentinel = object()
    for expected, saved, raw_label in zip_longest(groups.items(), features, labels, fillvalue=sentinel):
        _require(expected is not sentinel and saved is not sentinel and raw_label is not sentinel, "coordinate_stream_length")
        (day, symbol), targets = expected
        checkpoint("ei_snapshot_reference_coordinate")
        session = reference_session(day, symbol)
        coordinate = _coordinate(day, symbol, targets, session, checkpoint)
        if session is not None:
            clocks = session.opening, session.closing
            _require(calendars.setdefault(day, clocks) == clocks, "same_date_calendar_changed")
        _require(_same(coordinate, saved), "original_feature_or_frame_differs")
        expected_labels = _labels(coordinate, session, checkpoint)
        label_value, _ = raw_label
        saved_labels = _read_label_coordinate(label_value)
        _require(_same(expected_labels, saved_labels), "original_labels_differ")
        total.add(coordinate, expected_labels)
        for target in targets:
            scenarios[target.scenario].add(coordinate, expected_labels)
    summary = {"union": total.payload(), "scenarios": {name: value.payload() for name, value in scenarios.items()}}
    _require(total.coordinates == len(groups)
             and sum(value.coordinates for value in scenarios.values()) == len(expected_targets), "original_roster_count")
    return {"schema": SCHEMA, "verified": True, "full_eof_verified": True,
            "all_original_coordinates_verified": True, "original_dates": len({day for day, _ in groups}),
            "original_coordinates": len(groups), "original_memberships": len(expected_targets),
            "reference_session_calls": len(groups), "feature_sha256": feature_seal.sha256,
            "label_sha256": label_seal.sha256, "counts": summary,
            "counts_sha256": sha256(wire.encode(summary)).hexdigest()}
