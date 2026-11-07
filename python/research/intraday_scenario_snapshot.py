"""Causal EH projection and separately sealed supervised outcomes."""

from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from fractions import Fraction
from hashlib import sha256
import json
import os
from pathlib import Path
import stat

from research import intraday_campaign_wsl_transport as wire
from research.intraday_chart_inputs import _session
from research.intraday_ed_inputs import MINUTE, Issue, SessionInputs, _clock, _require
from research.intraday_ed_sources import TargetCoordinate
from research.intraday_scenario_decision_inputs import ChartDecisionInputs, PeerDecisionInputs, peer_clock_inputs
from research.intraday_scenario_decision_schedule import DecisionFrame, opening_decision_frames
from research.intraday_scenario_forecast_targets import TrainingOutcome, training_outcome
from research.intraday_scenario_labels import HORIZONS_MINUTES, PathQuestion, PathResult, QuestionId, label_horizons
from research.intraday_scenario_questions import InitialPath
from research.intraday_scenario_state import OpeningRangePriceEvidence, scan_opening_range_high


FEATURE_SCHEMA = "eh-opening-features-v1"
LABEL_SCHEMA = "eh-opening-labels-v1"
MAX_BYTES = 128 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024
MAX_DAYS = 366
MAX_QUESTIONS = MAX_DAYS * 40 * 2
QuestionKey = tuple[str, str, QuestionId]
OutcomeSession = Callable[[str, str], SessionInputs | None]


@dataclass(frozen=True)
class ProjectedMembership:
    scenario: str
    symbol: str
    session: str
    rank: int
    metadata: bytes


@dataclass(frozen=True)
class ProjectedCoordinate:
    symbol: str
    targets: tuple[ProjectedMembership, ...]
    status: str
    opening: datetime | None
    closing: datetime | None
    evidence: OpeningRangePriceEvidence | None = None
    breakout_gap: tuple[Issue, ...] = ()

    @property
    def scenarios(self) -> tuple[str, ...]:
        return tuple(target.scenario for target in self.targets)


@dataclass(frozen=True)
class ProjectedDecision:
    frame: DecisionFrame
    scenarios: tuple[str, ...]
    peer: PeerDecisionInputs | None

    @property
    def question_key(self) -> QuestionKey | None:
        initial = self.frame.initial
        if initial.question is None:
            return None
        return self.frame.symbol, initial.stage, initial.question.question_id


@dataclass(frozen=True)
class ProjectedDay:
    day: str
    coordinates: tuple[ProjectedCoordinate, ...]
    rows: tuple[ProjectedDecision, ...]
    members_known_at: datetime | None = None


@dataclass(frozen=True)
class QuestionLabel:
    key: QuestionKey
    cutoff_at: datetime
    status: str
    results: tuple[PathResult, ...]
    training: TrainingOutcome | None


@dataclass(frozen=True)
class SnapshotSeal:
    schema: str
    sha256: str
    size: int
    records: int


def _groups(day: str, targets: tuple[ProjectedMembership, ...]) -> dict[str, tuple[ProjectedMembership, ...]]:
    _require(date.fromisoformat(day).isoformat() == day, "invalid_EH_snapshot_day")
    grouped = defaultdict(list)
    ranks = set()
    _require(type(targets) is tuple and 0 < len(targets) <= 40, "bounded_EH_dated_targets_required")
    for row in targets:
        _require(type(row) is ProjectedMembership and row.session == day and type(row.symbol) is str and row.symbol
                 and row.scenario in ("S1", "S2") and type(row.rank) is int and 1 <= row.rank <= 20
                 and type(row.metadata) is bytes and (row.scenario, row.rank) not in ranks,
                 "invalid_EH_snapshot_target")
        ranks.add((row.scenario, row.rank))
        grouped[row.symbol].append(row)
    groups = {symbol: tuple(sorted(rows, key=lambda item: item.scenario)) for symbol, rows in sorted(grouped.items())}
    _require(all(len({row.scenario for row in rows}) == len(rows) for rows in groups.values()),
             "duplicate_EH_snapshot_coordinate")
    return groups


def project_opening_day(
    day: str, targets: tuple[TargetCoordinate, ...], sessions: Mapping[str, SessionInputs | None],
    evidence: Mapping[str, OpeningRangePriceEvidence | None], *, members_known_at: datetime | None,
) -> ProjectedDay:
    """Project one original union day and same-clock, leave-one-out S2 peers."""
    _require(type(targets) is tuple and all(type(target) is TargetCoordinate for target in targets),
             "typed_EH_snapshot_targets_required")
    groups = _groups(day, tuple(ProjectedMembership(target.scenario, target.symbol, target.session,
                                                    target.rank, target.metadata) for target in targets))
    _require(isinstance(sessions, Mapping) and isinstance(evidence, Mapping), "typed_EH_snapshot_inputs_required")
    if members_known_at is not None:
        _clock(members_known_at)
    roster = tuple(symbol for symbol, rows in groups.items() if any(row.scenario == "S2" for row in rows))
    coordinates, frames, inputs = [], [], {}
    clocks = defaultdict(set)
    session_clocks = None
    for symbol, rows in groups.items():
        session = sessions.get(symbol)
        if session is None:
            coordinates.append(ProjectedCoordinate(symbol, rows, "session_missing", None, None))
            continue
        _session(session)
        _require(session.symbol == symbol and session.opening.date().isoformat() == day, "EH_snapshot_session_identity_differs")
        clock = session.opening, session.closing
        _require(session_clocks is None or clock == session_clocks, "EH_snapshot_calendar_differs")
        session_clocks = clock
        inputs[symbol] = session
        proof = evidence.get(symbol)
        sequence = tuple(opening_decision_frames(session, proof))
        breakout_gap = ()
        if sequence:
            status = "frames_present"
            if all(frame.initial.stage != "breakout" for frame in sequence):
                tail = scan_opening_range_high(session, current_evidence=proof, through=session.closing)
                if tail.status == "support_unknown":
                    breakout_gap = tail.issues
        else:
            status = scan_opening_range_high(session, current_evidence=proof, through=session.closing).status
            _require(status != "event", "EH_snapshot_event_without_frame")
        coordinate = ProjectedCoordinate(symbol, rows, status, *clock, proof, breakout_gap)
        coordinates.append(coordinate)
        for frame in sequence:
            frames.append((frame, coordinate.scenarios))
            if frame.chart is not None and frame.chart.status == "ready":
                clocks[frame.observed_at].add(symbol)

    peers = {}
    for at, symbols in sorted(clocks.items()):
        members = tuple(sorted(symbols))
        projections = peer_clock_inputs(inputs[members[0]], decision_at=at, members_known_at=members_known_at,
                                        roster=roster, target_symbols=members, peers=inputs)
        peers.update(((at, item.symbol), item) for item in projections)
    rows = tuple(ProjectedDecision(frame, scenarios, peers.get((frame.observed_at, frame.symbol))
                                   if frame.chart is not None and frame.chart.status == "ready" else None)
                 for frame, scenarios in sorted(frames, key=lambda item: _row_order(item[0])))
    return ProjectedDay(day, tuple(coordinates), rows, members_known_at)


def _row_order(frame: DecisionFrame) -> tuple:
    return frame.observed_at, frame.symbol, frame.initial.stage


def _time(value: datetime | None) -> str | None:
    if value is None:
        return None
    _clock(value)
    return value.astimezone(UTC).isoformat()


def _read_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    at = datetime.fromisoformat(value)
    _clock(at)
    return at


def _question(key: QuestionId) -> list:
    start, level, band, target, stop, end = key
    return [_time(start), str(level), str(band), str(target), str(stop), _time(end)]


def _read_question(row: list) -> PathQuestion:
    start, level, band, target, stop, end = row
    return PathQuestion(_read_time(start), _read_time(end), Fraction(level), Fraction(band), Fraction(target), Fraction(stop))


def _issues(values: tuple[Issue, ...]) -> list:
    return [[item.symbol, _time(item.minute), item.reason] for item in values]


def _read_issues(rows: list) -> tuple[Issue, ...]:
    return tuple(Issue(symbol, _read_time(at), reason) for symbol, at, reason in rows)


def _vectors(values: tuple) -> list:
    return [[str(value) for value in row] for row in values]


def _read_vectors(values: list) -> tuple:
    return tuple(tuple(Fraction(value) for value in row) for row in values)


def _decision(row: ProjectedDecision) -> list:
    frame, peer = row.frame, row.peer
    initial, chart = frame.initial, frame.chart
    path = None if initial.question is None else _question(initial.question.question_id)
    chart_row = None if chart is None else [chart.status, _vectors(chart.minutes), _vectors(chart.five_minute_bars),
                                            list(map(str, chart.state)), _issues(chart.issues)]
    peer_row = None if peer is None else [peer.expected, peer.complete,
                                         None if peer.sequence is None else _vectors(peer.sequence), _issues(peer.issues)]
    return [frame.symbol, _time(frame.observed_at), _time(frame.event_at), _time(frame.event_expires_at),
            initial.stage, path, str(initial.start_close), initial.target_basis, _issues(initial.issues), chart_row, peer_row]


def _read_decision(row: list, groups: dict[str, ProjectedCoordinate]) -> ProjectedDecision:
    symbol, stamp, started, expires, stage, path, start_close, basis, issues, chart_row, peer_row = row
    at = _read_time(stamp)
    question = None if path is None else _read_question(path)
    initial = InitialPath(stage, question, Fraction(start_close), basis, _read_issues(issues))
    chart = None
    if chart_row is not None:
        status, minutes, coarse, state, problems = chart_row
        chart = ChartDecisionInputs((symbol, *question.forecast_id(at)), status, _read_vectors(minutes),
                                    _read_vectors(coarse), tuple(map(Fraction, state)), _read_issues(problems))
    peer = None
    if peer_row is not None:
        expected, complete, sequence, problems = peer_row
        peer = PeerDecisionInputs(symbol, at, tuple(expected), tuple(complete),
                                  None if sequence is None else _read_vectors(sequence), _read_issues(problems))
    frame = DecisionFrame(symbol, at, _read_time(started), _read_time(expires), initial, chart)
    return ProjectedDecision(frame, groups[symbol].scenarios, peer)


def _day(value: ProjectedDay) -> dict:
    return {"day": value.day, "members_known_at": _time(value.members_known_at), "coordinates": [
        [item.symbol, item.status, _time(item.opening), _time(item.closing),
         [[target.scenario, target.rank, target.metadata.hex()] for target in item.targets],
         None if item.evidence is None else [item.evidence.receipt_id, item.evidence.price_unit], _issues(item.breakout_gap)]
        for item in value.coordinates
    ], "rows": [_decision(item) for item in value.rows]}


def _read_day(raw: dict) -> ProjectedDay:
    day = raw["day"]
    coordinates = tuple(ProjectedCoordinate(symbol, tuple(ProjectedMembership(scenario, symbol, day, rank,
                                                                               bytes.fromhex(metadata))
                                                           for scenario, rank, metadata in targets),
                                            status, _read_time(opening), _read_time(closing),
                                            None if evidence is None else OpeningRangePriceEvidence(*evidence), _read_issues(gap))
                        for symbol, status, opening, closing, targets, evidence, gap in raw["coordinates"])
    groups = {coordinate.symbol: coordinate for coordinate in coordinates}
    result = ProjectedDay(day, coordinates, tuple(_read_decision(row, groups) for row in raw["rows"]),
                          _read_time(raw["members_known_at"]))
    _validate_day(result)
    return result


def _validate_day(day: ProjectedDay) -> None:
    groups = _groups(day.day, tuple(target for item in day.coordinates for target in item.targets))
    _require(tuple(groups) == tuple(item.symbol for item in day.coordinates), "EH_snapshot_union_differs")
    coordinates = {item.symbol: item for item in day.coordinates}
    clocks = {(item.opening, item.closing) for item in day.coordinates if item.opening is not None}
    _require(len(clocks) <= 1, "EH_snapshot_calendar_differs")
    roster = tuple(symbol for symbol, rows in groups.items() if any(row.scenario == "S2" for row in rows))
    events = {}
    breakout_symbols = set()
    for row in day.rows:
        frame = row.frame
        _require(frame.symbol in groups and row.scenarios == tuple(item.scenario for item in groups[frame.symbol])
                 and frame.initial.stage in ("approach", "breakout"), "EH_snapshot_decision_coordinate_differs")
        question, chart = frame.initial.question, frame.chart
        _require((question is None and chart is None) or (question is not None and chart is not None
                 and chart.forecast_id == (frame.symbol, *question.forecast_id(frame.observed_at))),
                 "EH_snapshot_chart_key_differs")
        if chart is not None:
            _require(chart.status in ("ready", "resolved", "unknown", "unsupported"), "EH_snapshot_invalid_chart_status")
            if chart.status == "ready":
                _require(len(chart.minutes) == 20 and all(len(item) == 4 for item in chart.minutes)
                         and len(chart.five_minute_bars) == 4 and all(len(item) == 4 for item in chart.five_minute_bars)
                         and len(chart.state) == 6 and row.peer is not None, "EH_snapshot_ready_chart_geometry_differs")
            else:
                _require(not (chart.minutes or chart.five_minute_bars or chart.state), "EH_snapshot_unsupported_chart_has_values")
        if row.peer is not None:
            peer = row.peer
            _require(chart is not None and chart.status == "ready"
                     and (peer.symbol, peer.decision_at) == (frame.symbol, frame.observed_at)
                     and peer.expected == tuple(symbol for symbol in roster if symbol != frame.symbol)
                     and peer.complete == tuple(sorted(set(peer.complete))) and set(peer.complete) <= set(peer.expected),
                     "EH_snapshot_peer_key_differs")
            if day.members_known_at is None or day.members_known_at > frame.observed_at:
                _require(not peer.complete and peer.sequence is None, "EH_snapshot_future_peer_roster")
            _require((peer.sequence is not None) == (len(peer.complete) >= 5)
                     and (peer.sequence is None or (len(peer.sequence) == 20 and all(len(item) == 3 for item in peer.sequence))),
                     "EH_snapshot_peer_geometry_differs")
        coordinate = coordinates[frame.symbol]
        _require(coordinate.opening is not None and coordinate.closing is not None
                 and coordinate.opening < frame.event_at <= frame.observed_at < frame.event_expires_at < coordinate.closing
                 and (question is None or frame.event_at <= question.start_at <= frame.observed_at),
                 "EH_snapshot_event_clock_differs")
        event = frame.event_at, frame.event_expires_at
        _require(events.setdefault(frame.symbol, event) == event, "EH_snapshot_fixed_event_changed")
        if frame.initial.stage == "breakout":
            breakout_symbols.add(frame.symbol)
    orders = tuple(_row_order(row.frame) for row in day.rows)
    _require(orders == tuple(sorted(set(orders))), "EH_snapshot_decision_order_differs")
    for item in day.coordinates:
        _require(item.targets == groups[item.symbol] and (item.status == "frames_present") == (item.symbol in events),
                 "EH_snapshot_coordinate_status_differs")
        if item.breakout_gap:
            _require(item.symbol in events and item.symbol not in breakout_symbols
                     and all(issue.symbol == item.symbol and events[item.symbol][0] - 2 * MINUTE <= issue.minute
                             < events[item.symbol][1] and issue.reason for issue in item.breakout_gap),
                     "EH_snapshot_breakout_gap_differs")
        if item.status == "session_missing":
            _require(item.opening is None and item.closing is None and item.evidence is None,
                     "EH_snapshot_missing_session_has_clock")
        else:
            _require(item.status in ("frames_present", "known_no_event", "support_unknown", "not_yet_eligible")
                     and item.opening is not None and item.closing is not None and item.opening < item.closing
                     and item.opening.astimezone(UTC).date().isoformat() == day.day,
                     "EH_snapshot_invalid_coordinate_clock")
            if item.evidence is not None:
                _require(type(item.evidence) is OpeningRangePriceEvidence
                         and all(type(value) is str and value.strip() for value in (item.evidence.receipt_id, item.evidence.price_unit)),
                         "EH_snapshot_invalid_price_evidence")


def _label(key: QuestionKey, cutoff: datetime, results: tuple[PathResult, ...]) -> QuestionLabel:
    _clock(cutoff)
    _require(all(row.question.start_at <= row.observed_through <= min(row.question.end_at, cutoff)
                 and (row.mature_at is None or row.observed_through + 2 * MINUTE <= row.mature_at <= cutoff)
                 for row in results), "EH_snapshot_label_beyond_cutoff")
    _require(all(row.status in ("resolved", "pending", "unknown", "unsupported")
                 and ((row.path in ("P1", "P2", "P3", "P4") and row.mature_at is not None)
                      if row.status == "resolved" else row.path is None)
                 and type(row.departed) is bool and type(row.retested) is bool and (not row.retested or row.departed)
                 for row in results), "EH_snapshot_invalid_path_status")
    if not results:
        return QuestionLabel(key, cutoff, "session_missing", (), None)
    return QuestionLabel(key, cutoff, "observed", results, training_outcome(results, before=cutoff + MINUTE))


def _label_row(value: QuestionLabel) -> dict:
    symbol, stage, question = value.key
    return {"key": [symbol, stage, _question(question)], "cutoff": _time(value.cutoff_at), "status": value.status,
            "results": [[item.status, _time(item.observed_through), item.path, _time(item.mature_at), item.reason,
                         item.departed, item.retested] for item in value.results]}


def _read_label(raw: dict) -> QuestionLabel:
    symbol, stage, path = raw["key"]
    question = _read_question(path)
    _require(type(symbol) is str and symbol and stage in ("approach", "breakout"), "EH_snapshot_label_key_differs")
    results = ()
    if raw["results"]:
        results = tuple(PathResult(replace(question, end_at=question.start_at + horizon * MINUTE), status,
                                   _read_time(through), label, _read_time(mature), reason, departed, retested)
                        for horizon, (status, through, label, mature, reason, departed, retested)
                        in zip(HORIZONS_MINUTES, raw["results"], strict=True))
    return _label((symbol, stage, question.question_id), _read_time(raw["cutoff"]), results)


def _write(path: Path, header: dict, rows: Iterable[dict], *, maximum: int) -> SnapshotSeal:
    wire.safe_path(path)
    digest, size, records = sha256(), 0, 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        def write(value):
            nonlocal size
            raw = wire.encode(value)
            size += len(raw)
            _require(len(raw) <= MAX_LINE and size <= MAX_BYTES, "EH_snapshot_size_exceeded")
            wire.write_all(stream, raw)
            digest.update(raw)

        write(header)
        for row in rows:
            records += 1
            _require(records <= maximum, "EH_snapshot_record_limit_exceeded")
            write(row)
        write({"records": records})
        stream.flush()
        os.fchmod(stream.fileno(), 0o400)
        os.fsync(stream.fileno())
        frozen = wire.file_identity(os.fstat(stream.fileno()))
    wire.fsync_directory(path.parent)
    _require(wire.file_identity(wire.regular(path)) == frozen, "EH_snapshot_changed_while_sealing")
    return SnapshotSeal(header["schema"], digest.hexdigest(), size, records)


def _read(path: Path, seal: SnapshotSeal, header: dict, *, maximum: int) -> Iterator[tuple[dict, bytes]]:
    _require(type(seal) is SnapshotSeal and seal.schema == header["schema"] and wire.valid_sha(seal.sha256)
             and type(seal.records) is int and 0 <= seal.records <= maximum
             and type(seal.size) is int and 0 < seal.size <= MAX_BYTES, "invalid_EH_snapshot_seal")
    before = wire.regular(path)
    identity = wire.file_identity(before)
    _require(stat.S_IMODE(before.st_mode) == 0o400 and before.st_size == seal.size, "EH_snapshot_size_or_mode_differs")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        _require(wire.file_identity(os.fstat(stream.fileno())) == identity, "EH_snapshot_changed_before_open")
        digest, hashed_size = sha256(), 0
        while block := stream.read(wire.BLOCK):
            hashed_size += len(block)
            _require(hashed_size <= seal.size, "EH_snapshot_size_exceeded")
            digest.update(block)
        _require(hashed_size == seal.size and digest.hexdigest() == seal.sha256
                 and wire.file_identity(os.fstat(stream.fileno())) == identity,
                 "EH_snapshot_hash_differs")
        stream.seek(0)
        _require(stream.readline(MAX_LINE + 1) == wire.encode(header), "EH_snapshot_header_differs")
        for _ in range(seal.records):
            raw = stream.readline(MAX_LINE + 1)
            _require(raw.endswith(b"\n") and len(raw) <= MAX_LINE, "EH_snapshot_record_bound")
            row = json.loads(raw)
            _require(type(row) is dict and wire.encode(row) == raw, "EH_snapshot_noncanonical_row")
            yield row, raw
        _require(stream.readline(MAX_LINE + 1) == wire.encode({"records": seal.records}) and not stream.read(1),
                 "EH_snapshot_footer_or_EOF_differs")
        _require(wire.file_identity(os.fstat(stream.fileno())) == identity == wire.file_identity(wire.regular(path)),
                 "EH_snapshot_changed_while_reading")


def write_features(path: Path, days: Iterable[ProjectedDay]) -> SnapshotSeal:
    def rows():
        previous = ""
        for day in days:
            _validate_day(day)
            _require(day.day > previous, "EH_snapshot_day_order_differs")
            previous = day.day
            yield _day(day)

    return _write(path, {"schema": FEATURE_SCHEMA}, rows(), maximum=MAX_DAYS)


def read_features(path: Path, seal: SnapshotSeal) -> Iterator[ProjectedDay]:
    """Read typed days from a matching seal and exhaust the iterator to verify EOF."""
    previous = ""
    for row, raw in _read(path, seal, {"schema": FEATURE_SCHEMA}, maximum=MAX_DAYS):
        day = _read_day(row)
        _require(day.day > previous and wire.encode(_day(day)) == raw, "EH_snapshot_day_readback_differs")
        previous = day.day
        yield day


def _label_header(feature_seal: SnapshotSeal) -> dict:
    _require(type(feature_seal) is SnapshotSeal and feature_seal.schema == FEATURE_SCHEMA
             and wire.valid_sha(feature_seal.sha256), "EH_snapshot_feature_seal_required")
    return {"schema": LABEL_SCHEMA, "feature_sha256": feature_seal.sha256, "feature_size": feature_seal.size,
            "feature_records": feature_seal.records}


def write_labels(path: Path, feature_path: Path, feature_seal: SnapshotSeal, outcome_session: OutcomeSession) -> SnapshotSeal:
    """Verify the sealed feature EOF before requesting any outcome session."""
    _require(path != feature_path and callable(outcome_session), "EH_snapshot_label_boundary_required")
    catalog = {}
    for day in read_features(feature_path, feature_seal):
        coordinates = {item.symbol: item for item in day.coordinates}
        for row in day.rows:
            key = row.question_key
            if key is None:
                continue
            item = coordinates[row.frame.symbol]
            entry = day.day, item.opening, item.closing, row.frame.initial
            _require(key not in catalog or catalog[key] == entry, "EH_snapshot_fixed_question_changed")
            catalog[key] = entry
            _require(len(catalog) <= MAX_QUESTIONS, "EH_snapshot_question_limit_exceeded")

    def rows():
        previous, session = None, None
        for key, (day, opening, closing, initial) in sorted(catalog.items(), key=lambda item: (item[1][0], *item[0])):
            symbol = key[0]
            if previous != (day, symbol):
                session = outcome_session(day, symbol)
                previous = day, symbol
                if session is not None:
                    _session(session)
                    _require((session.symbol, session.opening, session.closing) == (symbol, opening, closing),
                             "EH_snapshot_outcome_session_differs")
            cutoff = closing + 2 * MINUTE
            results = () if session is None else label_horizons(initial.question, initial.start_close, session, cutoff_at=cutoff)
            yield _label_row(_label(key, cutoff, results))

    return _write(path, _label_header(feature_seal), rows(), maximum=MAX_QUESTIONS)


def read_labels(path: Path, seal: SnapshotSeal, feature_seal: SnapshotSeal) -> Iterator[QuestionLabel]:
    """Read keyed labels bound to the given feature seal and verify the complete stream."""
    previous = None
    for row, raw in _read(path, seal, _label_header(feature_seal), maximum=MAX_QUESTIONS):
        label = _read_label(row)
        order = label.key[2][0].date(), *label.key
        _require((previous is None or previous < order) and wire.encode(_label_row(label)) == raw,
                 "EH_snapshot_label_readback_differs")
        previous = order
        yield label
