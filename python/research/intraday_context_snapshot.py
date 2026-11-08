"""Sealed, coordinate-streamed EI features and separately opened path labels.

Callers authenticate the original dated roster and sessions. This pure layer
performs no acquisition, fitting, payoff calculation or actual-run admission.
Its schemas are EI-specific; only immutable seal/EOF mechanics are reused.
"""

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta, timezone
from fractions import Fraction
from itertools import tee, zip_longest
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_chart_inputs import _clock, _need, _session
from research.intraday_context_features import (
    CONTEXT_NAMES, HISTORY_MINUTES, SCHEMA as FEATURE_PROJECTION, ContextFeatures,
    project_context_features,
)
from research.intraday_context_questions import ContextQuestion, SCHEME_ID, scan_context_questions
from research.intraday_context_situations import ResistanceAnchor, scan_context_frames
from research.intraday_ed_inputs import MINUTE, STATES, SessionInputs
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_labels import HORIZONS_MINUTES, PathQuestion, PathResult, label_horizons
from research.intraday_scenario_snapshot import MAX_BYTES, MAX_LINE, SnapshotSeal, _read, _write


FEATURE_SCHEMA = "ei-context-feature-snapshot-draft-v1"
LABEL_SCHEMA = "ei-context-path-labels-draft-v1"
MAX_DAYS = 366
MAX_COORDINATES = MAX_DAYS * 40
MAX_TARGETS = MAX_DAYS * 40
MAX_METADATA_BYTES = 4096
MAX_FRAMES_PER_COORDINATE = 72
MAX_QUESTIONS_PER_COORDINATE = 2 * MAX_FRAMES_PER_COORDINATE
_KST = timezone(timedelta(hours=9))
_CLASSES = {"A", "B", "other", "unclassified", "unknown"}
_FRAME_STATES = {"no_anchor", "unknown", "invalidated", "no_event", "questions"}
OutcomeSession = Callable[[str, str], SessionInputs | None]


@dataclass(frozen=True)
class FrameSummary:
    available_at: datetime
    classification: str
    phase: str
    status: str
    reason: str
    question_ids: tuple[str, ...]


@dataclass(frozen=True)
class ProjectedCoordinate:
    session: str
    symbol: str
    targets: tuple[TargetCoordinate, ...]
    status: str
    opening: datetime | None
    closing: datetime | None
    frames: tuple[FrameSummary, ...]
    questions: tuple[ContextFeatures, ...]

    @property
    def feature_support(self) -> dict:
        """Question/feature counts only; none of these are outcome or trade counts."""
        return {stage: {"questions": sum(row.question.stage == stage for row in self.questions),
                        "ready_features": sum(row.question.stage == stage and row.status == "ready"
                                              for row in self.questions)}
                for stage in ("approach", "breakout")}


@dataclass(frozen=True)
class QuestionLabel:
    question: ContextQuestion
    cutoff_at: datetime
    status: str
    results: tuple[PathResult, ...]

    @property
    def question_id(self) -> str:
        return question_digest(self.question)


@dataclass(frozen=True)
class LabeledCoordinate:
    session: str
    symbol: str
    status: str
    labels: tuple[QuestionLabel, ...]

    @property
    def horizon_support(self) -> dict:
        output = {}
        for stage in ("approach", "breakout"):
            output[stage] = {}
            for index, horizon in enumerate(HORIZONS_MINUTES):
                output[stage][str(horizon)] = dict(Counter(
                    row.results[index].status if row.results else row.status
                    for row in self.labels if row.question.stage == stage))
        return output


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    _clock(value)
    return value.astimezone(UTC).isoformat()


def _read_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    _need(type(value) is str, "EI_snapshot_timestamp_type")
    result = datetime.fromisoformat(value)
    _need(_time(result) == value, "EI_snapshot_noncanonical_timestamp")
    return result


def _fraction(value: str) -> Fraction:
    _need(type(value) is str, "EI_snapshot_fraction_type")
    result = Fraction(value)
    _need(str(result) == value, "EI_snapshot_noncanonical_fraction")
    return result


def _keys(value: dict, names: str) -> None:
    _need(type(value) is dict and set(value) == set(names.split()), "EI_snapshot_schema_fields_differ")


def _list(value, size: int | None = None) -> None:
    _need(type(value) is list and (size is None or len(value) == size), "EI_snapshot_list_shape_differs")


def _anchor(value: ResistanceAnchor) -> list:
    return [_time(value.start_at), str(value.start_price), _time(value.peak_end), _time(value.confirmed_at),
            str(value.price), str(value.pre_peak_range), str(value.advance), str(value.band)]


def _read_anchor(value: list) -> ResistanceAnchor:
    _list(value, 8)
    start, price, peak, confirmed, level, width, advance, band = value
    return ResistanceAnchor(_read_time(start), _fraction(price), _read_time(peak), _read_time(confirmed),
                            _fraction(level), _fraction(width), _fraction(advance), _fraction(band))


def _path(value: PathQuestion) -> list:
    return [_time(value.start_at), _time(value.end_at), str(value.level), str(value.band),
            str(value.target), str(value.stop)]


def _read_path(value: list) -> PathQuestion:
    _list(value, 6)
    start, end, level, band, target, stop = value
    return PathQuestion(_read_time(start), _read_time(end), _fraction(level), _fraction(band),
                        _fraction(target), _fraction(stop))


def _identity(value: tuple) -> list:
    ((symbol, anchor), number), stage, path = value
    start, level, band, target, stop, end = path
    return [symbol, _anchor(anchor), number, stage, _path(PathQuestion(start, end, level, band, target, stop))]


def _read_identity(value: list) -> tuple:
    _list(value, 5)
    symbol, anchor, number, stage, path = value
    _need(type(symbol) is str and 0 < len(symbol) <= 64 and type(number) is int and 0 <= number <= 72
          and stage in ("approach", "breakout"), "EI_snapshot_question_identity_differ")
    return ((symbol, _read_anchor(anchor)), number), stage, _read_path(path).question_id


def question_digest(value: ContextQuestion) -> str:
    """Hash the full anchor/episode/stage/absolute path identity, not a short name."""
    return wire.digest(wire.encode(_identity(value.question_id)))


def _question(value: ContextQuestion) -> dict:
    return {"identity": _identity(value.question_id), "start_close": str(value.start_close),
            "origin_context": value.episode_origin_context, "origin_at": _time(value.episode_origin_at),
            "current_context": value.question_time_context, "phase": value.phase,
            "unsupported": list(value.unsupported_reasons), "priority": value.economic_priority,
            "overlap": [_identity(item) for item in value.overlapping_question_ids]}


def _read_question(value: dict) -> ContextQuestion:
    _keys(value, "identity start_close origin_context origin_at current_context phase unsupported priority overlap")
    episode, stage, path = _read_identity(value["identity"])
    start, level, band, target, stop, end = path
    _list(value["unsupported"])
    _list(value["overlap"])
    result = ContextQuestion(episode, stage, PathQuestion(start, end, level, band, target, stop),
        _fraction(value["start_close"]), value["origin_context"], _read_time(value["origin_at"]),
        value["current_context"], value["phase"], tuple(value["unsupported"]), value["priority"],
        tuple(_read_identity(row) for row in value["overlap"]))
    anchor = episode[0][1]
    _need(result.episode_origin_context in {"A", "B", "other"}
          and result.question_time_context in {"A", "B", "other"} and type(result.phase) is str
          and type(result.economic_priority) is bool and anchor.start_price > 0 and anchor.pre_peak_range > 0
          and anchor.advance == anchor.price - anchor.start_price > 0 and anchor.band == anchor.pre_peak_range / 4
          and anchor.start_at < anchor.peak_end < anchor.confirmed_at <= result.episode_origin_at <= start
          and level == anchor.price and band == anchor.band and target == level + 2 * anchor.pre_peak_range
          and stop == level - anchor.pre_peak_range and end - start == 30 * MINUTE
          and len(set(result.unsupported_reasons)) == len(result.unsupported_reasons)
          and set(result.unsupported_reasons) <= {"unsupported_price_geometry", "outside_session"}
          and len(result.overlapping_question_ids) <= MAX_QUESTIONS_PER_COORDINATE
          and len(set(result.overlapping_question_ids)) == len(result.overlapping_question_ids)
          and result.question_id not in result.overlapping_question_ids,
          "EI_snapshot_question_metadata_differs")
    return result


def _feature(value: ContextFeatures) -> dict:
    return {"id": question_digest(value.question), "question": _question(value.question),
            "forecast_at": _time(value.forecast_at), "observation_end": _time(value.observation_end),
            "window_start": _time(value.window_start), "status": value.status, "reasons": list(value.reasons),
            "available": list(map(_time, value.minute_available_at)), "price_states": list(value.price_states),
            "volume_states": list(value.volume_states),
            "prices": None if value.prices_in_R is None else [list(map(str, row)) for row in value.prices_in_R],
            "volumes": [None if item is None else str(item) for item in value.stored_volumes],
            "volume_scale": None if value.volume_scale is None else str(value.volume_scale),
            "common": None if value.common is None else list(map(str, value.common)),
            "context": None if value.context is None else list(map(str, value.context))}


def _read_feature(value: dict) -> ContextFeatures:
    """Decode a member; read_features validates it with its complete coordinate."""
    _keys(value, "id question forecast_at observation_end window_start status reasons available price_states volume_states prices volumes volume_scale common context")
    question = _read_question(value["question"])
    for name in ("reasons", "available", "price_states", "volume_states", "volumes"):
        _list(value[name])
    for name in ("prices", "common", "context"):
        if value[name] is not None:
            _list(value[name])
    if value["prices"] is not None:
        for row in value["prices"]:
            _list(row, 4)
    result = ContextFeatures(question, _read_time(value["forecast_at"]), _read_time(value["observation_end"]),
        _read_time(value["window_start"]), value["status"], tuple(value["reasons"]),
        tuple(map(_read_time, value["available"])), tuple(value["price_states"]), tuple(value["volume_states"]),
        None if value["prices"] is None else tuple(tuple(map(_fraction, row)) for row in value["prices"]),
        tuple(None if item is None else _fraction(item) for item in value["volumes"]),
        None if value["volume_scale"] is None else _fraction(value["volume_scale"]),
        None if value["common"] is None else tuple(map(_fraction, value["common"])),
        None if value["context"] is None else tuple(map(_fraction, value["context"])))
    _need(value["id"] == question_digest(question), "EI_snapshot_question_hash_differs")
    return result


def _validate_feature(value: ContextFeatures) -> None:
    question, anchor = value.question, value.question.anchor_id[1]
    _need(value.forecast_at == question.path.start_at == value.observation_end + 2 * MINUTE
          and value.window_start == value.observation_end - HISTORY_MINUTES * MINUTE,
          "EI_snapshot_feature_clocks_differ")
    count = len(value.minute_available_at)
    _need(count == min(HISTORY_MINUTES, (value.observation_end - anchor.start_at) // MINUTE)
          and count == len(value.price_states) == len(value.volume_states) == len(value.stored_volumes),
          "EI_snapshot_feature_window_shape_differs")
    first = max(value.window_start, anchor.start_at)
    for index, (stamp, price_state, volume_state, volume) in enumerate(zip(value.minute_available_at,
            value.price_states, value.volume_states, value.stored_volumes, strict=True)):
        _need(price_state in STATES | {"late"} and volume_state in STATES | {"late"}
              and (stamp is None or first + (index + 3) * MINUTE <= stamp <= value.forecast_at)
              and (volume_state == "observed") == (volume is not None)
              and (volume is None or type(volume) is Fraction and volume >= 0)
              and (stamp is not None or price_state != "observed" and volume_state != "observed")
              and ("late" not in (price_state, volume_state) or stamp is None),
              "EI_snapshot_feature_observation_metadata_differs")
    observed = tuple(value for value in value.stored_volumes if value is not None)
    _need(value.volume_scale == (sum(observed, Fraction()) / len(observed) if observed else None),
          "EI_snapshot_volume_scale_differs")
    _need(value.status in ("ready", "unsupported") and all(type(item) is str and item for item in value.reasons)
          and len(set(value.reasons)) == len(value.reasons), "EI_snapshot_feature_status_differs")
    if value.status == "unsupported":
        _need(value.reasons and value.prices_in_R is value.common is value.context is None,
              "EI_snapshot_unsupported_features_have_values")
        return
    _need(not value.reasons and count == HISTORY_MINUTES and set(value.price_states) == {"observed"}
          and value.prices_in_R is not None and len(value.prices_in_R) == count
          and all(len(row) == 4 and row[2] <= min(row[0], row[3]) <= max(row[0], row[3]) <= row[1]
                  and all(anchor.price + item * anchor.pre_peak_range > 0 for item in row)
                  for row in value.prices_in_R), "EI_snapshot_ready_prices_differ")
    width, close = anchor.pre_peak_range, question.start_close
    common = (Fraction(question.stage == "breakout"), Fraction((value.forecast_at - anchor.start_at) // MINUTE),
              anchor.band / width, (question.path.target - close) / width, (close - question.path.stop) / width,
              Fraction(30), (close - anchor.price) / width)
    _need(value.common == common and value.prices_in_R[-1][3] == common[-1]
          and value.context is not None and len(value.context) == len(CONTEXT_NAMES)
          and value.context[:6] == tuple(Fraction(context == name)
              for context in (question.episode_origin_context, question.question_time_context)
              for name in ("A", "B", "other"))
          and value.context[6] == anchor.advance / width and all(item >= 0 for item in value.context[7:])
          and value.context[-2] == (value.observation_end - anchor.peak_end) // (5 * MINUTE)
          and all(item.denominator == 1 for item in value.context[-2:]), "EI_snapshot_static_features_differ")
    value.numeric_view(with_context=True)


def _target(value: TargetCoordinate) -> list:
    return [value.scenario, value.symbol, value.session, value.rank, value.stored_row_count, value.state,
            value.metadata.hex()]


def _read_target(value: list) -> TargetCoordinate:
    _list(value, 7)
    scenario, symbol, day, rank, count, state, metadata = value
    _need(type(metadata) is str, "EI_snapshot_target_metadata_type")
    return TargetCoordinate(scenario, symbol, day, rank, count, state, bytes.fromhex(metadata))


def _roster(targets: tuple[TargetCoordinate, ...]) -> dict[tuple[str, str], tuple[TargetCoordinate, ...]]:
    _need(type(targets) is tuple and 0 < len(targets) <= MAX_TARGETS, "EI_snapshot_original_roster_required")
    groups, ranks, membership = {}, set(), set()
    for value in targets:
        _need(type(value) is TargetCoordinate and value.scenario in ("S1", "S2")
              and type(value.symbol) is str and 0 < len(value.symbol) <= 64 and type(value.session) is str
              and date.fromisoformat(value.session).isoformat() == value.session
              and type(value.rank) is int and 1 <= value.rank <= 20
              and (value.stored_row_count is None or type(value.stored_row_count) is int and value.stored_row_count >= 0)
              and type(value.state) is str and bool(value.state)
              and type(value.metadata) is bytes and len(value.metadata) <= MAX_METADATA_BYTES,
              "EI_snapshot_invalid_original_target")
        rank, member = (value.session, value.scenario, value.rank), (value.session, value.scenario, value.symbol)
        _need(rank not in ranks and member not in membership, "EI_snapshot_duplicate_original_target")
        ranks.add(rank)
        membership.add(member)
        groups.setdefault((value.session, value.symbol), []).append(value)
    _need(len({day for day, _ in groups}) <= MAX_DAYS and len(groups) <= MAX_COORDINATES,
          "EI_snapshot_roster_bound_exceeded")
    return {key: tuple(sorted(rows, key=lambda row: row.scenario)) for key, rows in sorted(groups.items())}


def _header(groups: dict) -> dict:
    return {"schema": FEATURE_SCHEMA, "projection": FEATURE_PROJECTION, "questions": SCHEME_ID,
            "roster_sha256": wire.digest(wire.encode([_target(row) for rows in groups.values() for row in rows])),
            "coordinates": len(groups), "memberships": sum(map(len, groups.values()))}


def project_coordinate(targets: tuple[TargetCoordinate, ...], session: SessionInputs | None) -> ProjectedCoordinate:
    groups = _roster(targets)
    _need(len(groups) == 1, "EI_snapshot_one_original_coordinate_required")
    (day, symbol), members = next(iter(groups.items()))
    if session is None:
        return ProjectedCoordinate(day, symbol, members, "session_missing", None, None, (), ())
    _session(session)
    _need(session.symbol == symbol and session.opening.astimezone(_KST).date().isoformat() == day,
          "EI_snapshot_session_identity_differs")
    left, right = tee(scan_context_frames(session, through=session.closing))
    frames, questions = [], []
    for frame, found in zip(left, scan_context_questions(right, closing=session.closing), strict=True):
        frames.append(FrameSummary(frame.available_at, frame.classification, frame.phase, found.status, found.reason,
                                   tuple(question_digest(item) for item in found.questions)))
        questions.extend(project_context_features(frame, item) for item in found.questions)
    result = ProjectedCoordinate(day, symbol, members, "observed_session", session.opening, session.closing,
                                 tuple(frames), tuple(questions))
    _validate_coordinate(result, members)
    return result


def _coordinate(value: ProjectedCoordinate) -> dict:
    return {"session": value.session, "symbol": value.symbol, "targets": [_target(row) for row in value.targets],
            "status": value.status, "opening": _time(value.opening), "closing": _time(value.closing),
            "frames": [[_time(row.available_at), row.classification, row.phase, row.status, row.reason,
                        list(row.question_ids)] for row in value.frames],
            "questions": [_feature(row) for row in value.questions]}


def _read_coordinate(value: dict) -> ProjectedCoordinate:
    _keys(value, "session symbol targets status opening closing frames questions")
    for name in ("targets", "frames", "questions"):
        _list(value[name])
    frames = []
    for row in value["frames"]:
        _list(row, 6)
        stamp, classification, phase, status, reason, ids = row
        _list(ids)
        frames.append(FrameSummary(_read_time(stamp), classification, phase, status, reason, tuple(ids)))
    return ProjectedCoordinate(value["session"], value["symbol"], tuple(map(_read_target, value["targets"])),
        value["status"], _read_time(value["opening"]), _read_time(value["closing"]),
        tuple(frames), tuple(map(_read_feature, value["questions"])))


def _validate_coordinate(value: ProjectedCoordinate, targets: tuple[TargetCoordinate, ...]) -> None:
    _need(type(value) is ProjectedCoordinate and value.targets == targets and bool(targets)
          and (value.session, value.symbol) == (targets[0].session, targets[0].symbol),
          "EI_snapshot_original_coordinate_differs")
    if value.status == "session_missing":
        _need(value.opening is value.closing is None and not (value.frames or value.questions),
              "EI_snapshot_absent_session_has_values")
        return
    _need(value.status == "observed_session" and value.opening is not None and value.closing is not None,
          "EI_snapshot_coordinate_status_differs")
    _clock(value.opening)
    _clock(value.closing)
    _need(value.opening < value.closing and value.opening.astimezone(_KST).date().isoformat() == value.session,
          "EI_snapshot_coordinate_calendar_differs")
    expected = tuple(value.opening + (end + 2) * MINUTE
                     for end in range(30, int((value.closing - value.opening) // MINUTE), 5)
                     if value.opening + (end + 2) * MINUTE < value.closing)
    _need(len(expected) <= MAX_FRAMES_PER_COORDINATE and type(value.frames) is tuple
          and tuple(row.available_at for row in value.frames) == expected
          and type(value.questions) is tuple and len(value.questions) <= MAX_QUESTIONS_PER_COORDINATE,
          "EI_snapshot_complete_frame_grid_required")
    keys, selected, order = set(), [], []
    for row in value.questions:
        _need(type(row) is ContextFeatures, "EI_snapshot_typed_features_required")
        _validate_feature(row)
        question = row.question
        _need(_read_question(_question(question)) == question and question.anchor_id[0] == value.symbol
              and question.anchor_id[1].start_at == value.opening and row.forecast_at in expected,
              "EI_snapshot_question_coordinate_differs")
        reasons = () if question.path.stop > 0 and question.path.stop < question.start_close < question.path.target else (
            "unsupported_price_geometry",)
        if question.path.end_at > value.closing:
            reasons += ("outside_session",)
        _need(question.unsupported_reasons == reasons, "EI_snapshot_question_support_metadata_differs")
        key = question_digest(question)
        _need(key not in keys, "EI_snapshot_duplicate_question")
        keys.add(key)
        selected.append(key)
        order.append((row.forecast_at, question.stage))
    _need(order == sorted(set(order)), "EI_snapshot_question_order_differs")
    anchors = {row.question.anchor_id for row in value.questions}
    _need(len(anchors) <= 1, "EI_snapshot_fixed_anchor_changed")
    origins = {}
    for row in value.questions:
        question = row.question
        origin = (question.episode_origin_context, question.episode_origin_at)
        _need(origins.setdefault(question.episode_id, origin) == origin, "EI_snapshot_episode_origin_changed")
        expected_overlap = tuple(other.question.question_id for other in value.questions
            if other.question.question_id != question.question_id and other.forecast_at <= row.forecast_at
            < other.question.path.end_at)
        _need(question.overlapping_question_ids == expected_overlap, "EI_snapshot_question_overlap_differs")
    by_clock = {at: tuple(question_digest(row.question) for row in value.questions if row.forecast_at == at)
                for at in expected}
    for row in value.frames:
        _need(type(row) is FrameSummary and row.classification in _CLASSES and row.status in _FRAME_STATES
              and type(row.phase) is str and type(row.reason) is str and row.question_ids == by_clock[row.available_at]
              and (row.status == "questions") == bool(row.question_ids)
              and all((item.question.question_time_context, item.question.phase) == (row.classification, row.phase)
                      for item in value.questions if item.forecast_at == row.available_at),
              "EI_snapshot_frame_summary_differs")
    _need(tuple(selected) == tuple(key for row in value.frames for key in row.question_ids),
          "EI_snapshot_frame_question_bijection_differs")


def write_features(path: Path, coordinates: Iterable[ProjectedCoordinate], *,
                   expected_targets: tuple[TargetCoordinate, ...]) -> SnapshotSeal:
    groups = _roster(expected_targets)

    def rows():
        for pair in zip_longest(groups.items(), coordinates):
            expected, value = pair
            _need(expected is not None and value is not None, "EI_snapshot_original_coordinate_omitted_or_extra")
            key, targets = expected
            _validate_coordinate(value, targets)
            _need((value.session, value.symbol) == key, "EI_snapshot_coordinate_order_differs")
            yield _coordinate(value)

    return _write(path, _header(groups), rows(), maximum=MAX_COORDINATES)


def read_features(path: Path, seal: SnapshotSeal, *,
                  expected_targets: tuple[TargetCoordinate, ...]) -> Iterator[ProjectedCoordinate]:
    """Exhaust this stream to validate the exact original roster and full EOF."""
    groups = _roster(expected_targets)
    _need(seal.records == len(groups), "EI_snapshot_coordinate_count_differs")
    for expected, received in zip_longest(groups.items(), _read(path, seal, _header(groups), maximum=MAX_COORDINATES)):
        _need(expected is not None and received is not None, "EI_snapshot_original_coordinate_omitted_or_extra")
        key, targets = expected
        value, raw = received
        coordinate = _read_coordinate(value)
        _validate_coordinate(coordinate, targets)
        _need((coordinate.session, coordinate.symbol) == key and wire.encode(_coordinate(coordinate)) == raw,
              "EI_snapshot_coordinate_readback_differs")
        yield coordinate


def _label_header(feature_seal: SnapshotSeal) -> dict:
    _need(type(feature_seal) is SnapshotSeal and feature_seal.schema == FEATURE_SCHEMA
          and wire.valid_sha(feature_seal.sha256), "EI_snapshot_feature_seal_required")
    return {"schema": LABEL_SCHEMA, "feature_sha256": feature_seal.sha256,
            "feature_size": feature_seal.size, "feature_records": feature_seal.records,
            "horizons_minutes": list(HORIZONS_MINUTES)}


def _label_coordinate(value: LabeledCoordinate) -> dict:
    return {"session": value.session, "symbol": value.symbol, "status": value.status, "labels": [
        {"id": row.question_id, "question": _question(row.question), "cutoff_at": _time(row.cutoff_at),
         "status": row.status, "results": [[horizon, result.status, _time(result.observed_through),
             result.path, _time(result.mature_at), result.reason, result.departed, result.retested]
             for horizon, result in zip(HORIZONS_MINUTES, row.results, strict=bool(row.results))]}
        for row in value.labels]}


def _read_label_coordinate(value: dict) -> LabeledCoordinate:
    _keys(value, "session symbol status labels")
    _list(value["labels"])
    labels = []
    for row in value["labels"]:
        _keys(row, "id question cutoff_at status results")
        question = _read_question(row["question"])
        _need(row["id"] == question_digest(question), "EI_snapshot_label_question_hash_differs")
        _list(row["results"])
        results = []
        for position, result in enumerate(row["results"]):
            _list(result, 8)
            horizon, status, through, path, mature, reason, departed, retested = result
            _need(position < len(HORIZONS_MINUTES) and type(horizon) is int
                  and horizon == HORIZONS_MINUTES[position], "EI_snapshot_label_horizon_order_differs")
            results.append(PathResult(replace(question.path, end_at=question.path.start_at + horizon * MINUTE),
                status, _read_time(through), path, _read_time(mature), reason, departed, retested))
        labels.append(QuestionLabel(question, _read_time(row["cutoff_at"]), row["status"], tuple(results)))
    return LabeledCoordinate(value["session"], value["symbol"], value["status"], tuple(labels))


def _validate_labels(value: LabeledCoordinate, coordinate: ProjectedCoordinate) -> None:
    _need(type(value) is LabeledCoordinate and (value.session, value.symbol) == (coordinate.session, coordinate.symbol)
          and tuple(row.question for row in value.labels) == tuple(row.question for row in coordinate.questions),
          "EI_snapshot_label_feature_bijection_differs")
    if coordinate.status == "session_missing":
        _need(value.status == "feature_session_missing" and not value.labels, "EI_snapshot_label_missing_feature_session")
        return
    if not coordinate.questions:
        _need(value.status == "no_questions" and not value.labels, "EI_snapshot_no_question_labels_differ")
        return
    _need(value.status in ("observed", "session_missing"), "EI_snapshot_label_coordinate_status_differs")
    for label in value.labels:
        _need(label.cutoff_at == coordinate.closing + 2 * MINUTE and label.status == value.status,
              "EI_snapshot_label_cutoff_or_status_differs")
        if label.status == "session_missing":
            _need(not label.results, "EI_snapshot_missing_label_has_outcomes")
            continue
        _need(len(label.results) == len(HORIZONS_MINUTES), "EI_snapshot_each_horizon_required")
        for horizon, result in zip(HORIZONS_MINUTES, label.results, strict=True):
            expected = replace(label.question.path, end_at=label.question.path.start_at + horizon * MINUTE)
            _need(result.question == expected and result.status in ("resolved", "pending", "unknown", "unsupported")
                  and expected.start_at <= result.observed_through <= min(expected.end_at, label.cutoff_at)
                  and type(result.departed) is bool and type(result.retested) is bool
                  and (not result.retested or result.departed), "EI_snapshot_label_geometry_differs")
            if result.status == "resolved":
                _need(result.path in ("P1", "P2", "P3", "P4") and result.mature_at is not None
                      and result.observed_through + 2 * MINUTE <= result.mature_at <= label.cutoff_at,
                      "EI_snapshot_resolved_label_maturity_differs")
            else:
                ambiguous = result.status == "unknown" and result.reason in {
                    "same_minute_target_stop_order_unknown", "same_minute_retest_target_order_unknown"}
                maturity_valid = (result.mature_at is not None and expected.start_at < result.observed_through
                                  and result.observed_through + 2 * MINUTE <= result.mature_at <= label.cutoff_at
                                  if ambiguous else result.mature_at is None)
                _need(result.path is None and maturity_valid and type(result.reason) is str
                      and bool(result.reason), "EI_snapshot_unresolved_label_has_outcome")


def write_labels(path: Path, feature_path: Path, feature_seal: SnapshotSeal, outcome_session: OutcomeSession, *,
                 expected_targets: tuple[TargetCoordinate, ...]) -> SnapshotSeal:
    """Verify the entire sealed feature stream before the first outcome callback."""
    _need(path != feature_path and callable(outcome_session), "EI_snapshot_separate_label_boundary_required")
    for _ in read_features(feature_path, feature_seal, expected_targets=expected_targets):
        pass

    def rows():
        for coordinate in read_features(feature_path, feature_seal, expected_targets=expected_targets):
            if coordinate.status == "session_missing":
                value = LabeledCoordinate(coordinate.session, coordinate.symbol, "feature_session_missing", ())
            elif not coordinate.questions:
                value = LabeledCoordinate(coordinate.session, coordinate.symbol, "no_questions", ())
            else:
                session = outcome_session(coordinate.session, coordinate.symbol)
                if session is not None:
                    _session(session)
                    _need((session.symbol, session.opening, session.closing)
                          == (coordinate.symbol, coordinate.opening, coordinate.closing),
                          "EI_snapshot_outcome_session_substitution")
                cutoff = coordinate.closing + 2 * MINUTE
                state = "session_missing" if session is None else "observed"
                labels = tuple(QuestionLabel(row.question, cutoff, state, () if session is None else
                    label_horizons(row.question.path, row.question.start_close, session, cutoff_at=cutoff))
                    for row in coordinate.questions)
                value = LabeledCoordinate(coordinate.session, coordinate.symbol, state, labels)
            _validate_labels(value, coordinate)
            yield _label_coordinate(value)

    return _write(path, _label_header(feature_seal), rows(), maximum=MAX_COORDINATES)


def read_labels(path: Path, seal: SnapshotSeal, feature_path: Path, feature_seal: SnapshotSeal, *,
                expected_targets: tuple[TargetCoordinate, ...]) -> Iterator[LabeledCoordinate]:
    """Check full question metadata and a bijection against the exact feature seal."""
    _need(seal.records == feature_seal.records, "EI_snapshot_label_coordinate_count_differs")
    features = read_features(feature_path, feature_seal, expected_targets=expected_targets)
    labels = _read(path, seal, _label_header(feature_seal), maximum=MAX_COORDINATES)
    for coordinate, received in zip_longest(features, labels):
        _need(coordinate is not None and received is not None, "EI_snapshot_label_coordinate_omitted_or_extra")
        value, raw = received
        label = _read_label_coordinate(value)
        _validate_labels(label, coordinate)
        _need(wire.encode(_label_coordinate(label)) == raw, "EI_snapshot_label_readback_differs")
        yield label
