"""Independently verify an EH snapshot against the audited CB and CC inputs."""

from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import json
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_ed_inputs import Issue, MINUTE, Minute, SessionInputs, _require
from research.intraday_ed_sources import UniverseCalendar
from research.intraday_pattern_calibration import session_close, session_open
from research.intraday_scenario_forecast_targets import TrainingOutcome
from research.intraday_scenario_open_reference import _prefix_rows, _price_row, reference_opening_support
from research.intraday_scenario_snapshot import FEATURE_SCHEMA, MAX_DAYS, SnapshotSeal, _read, read_labels
from research.intraday_scenario_state import OpeningRangePriceEvidence


_COORDINATE_STATUSES = ("session_missing", "frames_present", "known_no_event", "support_unknown", "not_yet_eligible")
_CHART_STATUSES = ("no_question", "ready", "resolved", "unknown", "unsupported")


def _stamp(at):
    return None if at is None else at.astimezone(UTC).isoformat()


def _issues(rows):
    return [[row.symbol, _stamp(row.minute), row.reason] for row in rows]


def _question(path):
    if path is None:
        return None
    return [_stamp(path.start_at), str(path.level), str(path.band), str(path.target), str(path.stop), _stamp(path.end_at)]


def _original_days(universe):
    _require(type(universe) is UniverseCalendar and universe.sessions == tuple(sorted(set(universe.sessions)))
             and 0 < len(universe.sessions) <= MAX_DAYS, "EH_reference_original_calendar_differs")
    days = {day: defaultdict(list) for day in universe.sessions}
    for row in universe.targets:
        _require(row.session in days and row.scenario in ("S1", "S2"), "EH_reference_original_coordinate_differs")
        days[row.session][row.symbol].append(row)
    return days


def _roster(day, targets, opening):
    by_scenario = {name: [] for name in ("S1", "S2")}
    for rows in targets.values():
        _require(1 <= len(rows) <= 2 and len({row.scenario for row in rows}) == len(rows),
                 "EH_reference_duplicate_original_coordinate")
        for row in rows:
            by_scenario[row.scenario].append(row)
    known_at = None
    for name, rows in by_scenario.items():
        _require(sorted(row.rank for row in rows) == list(range(1, 21))
                 and len({row.metadata for row in rows}) == 1, "EH_reference_original_roster_differs")
        raw = rows[0].metadata
        facts = json.loads(raw)
        _require(wire.encode(facts).rstrip(b"\n") == raw and facts.get("session") == day,
                 "EH_reference_original_roster_metadata_differs")
        available = datetime.fromisoformat(facts["available_at"])
        selected = datetime.fromisoformat(facts["selected_at"])
        _require(available.utcoffset() is not None and selected.utcoffset() is not None
                 and available <= selected < opening, "EH_reference_original_roster_clock_differs")
        if name == "S2":
            known_at = selected
    return tuple(sorted(row.symbol for row in by_scenario["S2"])), known_at


def _ohlc(raw):
    names = ("open", "high", "low", "close")
    if any(type(raw[name]) is not str for name in names):
        return None
    try:
        prices = tuple(Decimal(raw[name]) for name in names)
    except InvalidOperation:
        return None
    first, high, low, close = prices
    if not all(price.is_finite() and price > 0 for price in prices) or not (low <= first <= high and low <= close <= high):
        return None
    return prices


class _AuditedMinutes(Mapping):
    """Keep an independent day index, decoding OHLC only at requested clocks."""

    def __init__(self, symbol, opening, chunk):
        self.symbol, self.opening = symbol, opening
        self.rows, self.decoded = {}, {}
        self.peer_returns = {}
        first = int(opening.timestamp()) // 60
        # The audited reader has already checked each row's key, type and range.
        for raw in chunk["rows"]:
            offset = raw["open_time_ms"] // 60_000 - first
            self.rows[offset] = None if offset in self.rows else raw

    def __getitem__(self, at):
        if at not in self.decoded:
            offset, remainder = divmod(at - self.opening, MINUTE)
            if remainder:
                raise KeyError(at)
            raw = self.rows[offset]
            prices = None if raw is None else _ohlc(raw)
            self.decoded[at] = Minute(self.symbol, at, at + 3 * MINUTE, prices, None,
                                      "conflict" if prices is None else "observed", "missing")
        return self.decoded[at]

    def __len__(self):
        return len(self.rows)

    def __iter__(self):
        return (self.opening + offset * MINUTE for offset in self.rows)

    def peer_prices(self, row):
        """Reuse exact minute-local prices and return across overlapping windows."""
        values = self.peer_returns.get(row.start_at)
        if values is None:
            first, close = Fraction(row.ohlc[0]), Fraction(row.ohlc[3])
            values = self.peer_returns[row.start_at] = (first, close, (close - first) / first)
        return values


def _visible_path(session, question, initial_close, cutoff):
    """Use only due minutes, keeping future and ambiguous first touches distinct."""
    start, end = question.start_at, question.end_at
    lower, upper = question.level - question.band, question.level + question.band
    floor, ceiling = question.stop, question.target
    if not (0 < floor <= lower < upper < ceiling and floor < initial_close < ceiling):
        return "unsupported", start, "unsupported_price_geometry", False, False
    if not session.opening <= start < end <= session.closing:
        return "unsupported", start, "outside_session", False, False
    departed, retested = initial_close > upper, False
    for offset in range((end - start) // MINUTE):
        at = start + offset * MINUTE
        if at + 3 * MINUTE > cutoff:
            return "pending", at, None, departed, retested
        row, reason = _price_row(session, at, cutoff, label=True)
        if reason is not None:
            return "unknown", at, reason, departed, retested
        first, high, low, close = row.ohlc
        if first <= floor or first >= ceiling:
            return "resolved", at + MINUTE, None, departed, retested
        if low <= floor:
            if high >= ceiling:
                return "unknown", at + MINUTE, "same_minute_target_stop_order_unknown", departed, retested
            return "resolved", at + MINUTE, None, departed, retested
        within_band = departed and low <= upper and high >= lower
        if high >= ceiling:
            if within_band and not retested and not lower <= first <= upper:
                return "unknown", at + MINUTE, "same_minute_retest_target_order_unknown", departed, False
            return "resolved", at + MINUTE, None, departed, retested or within_band
        retested |= within_band
        departed |= close > upper
    return "resolved", end, None, departed, retested


def _window(session, clock):
    completed = (clock - session.opening) // MINUTE - 2
    return _prefix_rows(session, range(completed - 20, completed), clock)


def _chart(session, initial, clock, normalized):
    path = initial.question
    if path is None:
        return None
    status, through, reason, departed, retested = _visible_path(session, path, initial.start_close, clock)
    if status != "pending":
        issues = () if reason is None else (Issue(session.symbol, through, reason),)
        return [status, [], [], [], _issues(issues)]
    rows, issues = _window(session, clock)
    if issues:
        return ["unknown", [], [], [], _issues(issues)]

    level, band = path.level, path.band
    upper = level + band

    def relative(price):
        if price not in normalized:
            normalized[price] = str((Fraction(price) - level) / band)
        return normalized[price]

    prices = [row.ohlc for row in rows]
    minutes = [[relative(price) for price in vector] for vector in prices]
    coarse = []
    for start in (0, 5, 10, 15):
        group = prices[start:start + 5]
        coarse.append([relative(group[0][0]), relative(max(row[1] for row in group)),
                       relative(min(row[2] for row in group)), relative(group[-1][3])])
    last = Fraction(prices[-1][3])
    remaining = (path.end_at - through) // MINUTE
    state = ((path.target - last) / band, (last - path.stop) / band, Fraction(remaining, 30),
             int(departed), int(retested), Fraction(sum(row[3] > upper for row in prices), 20))
    return ["ready", minutes, coarse, list(map(str, state)), []]


def _peer_clock(sessions, roster, clock):
    supported, failed = {}, {}
    for symbol in roster:
        session = sessions.get(symbol)
        if session is None:
            failed[symbol] = [Issue(symbol, clock, "expected_peer_session_missing")]
            continue
        rows, issues = _window(session, clock)
        if issues:
            failed[symbol] = issues
            continue
        supported[symbol] = rows

    # A missing fifth member can never supply an exact peer vector at this clock.
    if len(supported) < 5:
        return (), supported, failed
    complete = {}
    totals = [[Fraction(), Fraction(), 0] for _ in range(20)]
    for symbol, rows in supported.items():
        peer_prices = sessions[symbol].minutes.peer_prices
        anchor = peer_prices(rows[0])[0]
        contribution = []
        for total, row in zip(totals, rows, strict=True):
            _, close, move = peer_prices(row)
            cumulative, up = (close - anchor) / anchor, int(close > anchor)
            contribution.append((move, cumulative, up))
            total[0] += move
            total[1] += cumulative
            total[2] += up
        complete[symbol] = contribution
    return totals, complete, failed


def _peers(sessions, roster, symbol, clock, known, cached):
    expected = [other for other in roster if other != symbol]
    if known is None or known > clock:
        issue = Issue(symbol, clock, "peer_roster_unavailable_at_decision")
        return [expected, [], None, _issues((issue,))]
    if clock not in cached:
        cached[clock] = _peer_clock(sessions, roster, clock)
    totals, complete, failed = cached[clock]
    peers = [other for other in complete if other != symbol]
    issues = [issue for other in expected for issue in failed.get(other, ())]
    if len(peers) < 5:
        issues.append(Issue(symbol, clock, "fewer_than_five_complete_original_peers"))
        return [expected, peers, None, _issues(issues)]
    own = complete.get(symbol, ((0, 0, 0),) * 20)
    count = len(peers)
    sequence = [[str((row[0] - single[0]) / count), str((row[1] - single[1]) / count),
                 str(Fraction(row[2] - single[2], count))] for row, single in zip(totals, own, strict=True)]
    return [expected, peers, sequence, _issues(issues)]


def _frames(session, observation):
    event = observation.scan.event
    if event is None:
        return
    for entry in observation.paths:
        initial = entry.initial
        # A later breakout adds its own stage. It cannot move the original
        # event, expiry, or any earlier approach row.
        if initial.stage == "approach":
            first = event.origin.available_at
        else:
            first = event.breakout.available_at
        normalized = {}
        clock = first
        while clock < event.expires_at:
            chart = _chart(session, initial, clock, normalized)
            yield clock, event, entry, chart
            if chart is None or chart[0] in ("resolved", "unsupported"):
                break
            clock += 5 * MINUTE


def _training(results):
    if any(result.status != "resolved" for result in results):
        return None
    outcome = 9
    for group, result in enumerate(results):
        if result.path != "P4":
            outcome = 3 * group + ("P1", "P2", "P3").index(result.path)
            break
    return TrainingOutcome(outcome, max(result.mature_at for result in results))


def _stored_targets(rows):
    return [[row.scenario, row.rank, row.metadata.hex()] for row in sorted(rows, key=lambda row: row.scenario)]


def _same_coordinates(actual, expected):
    return actual == expected and all(type(row[1]) is int for coordinate in actual for row in coordinate[4])


def _day_reference(day, targets, reader, checkpoint):
    opening, closing = session_open(day), session_close(day)
    roster, known = _roster(day, targets, opening)
    sessions, observations, coordinates = {}, {}, []
    for symbol, rows in sorted(targets.items()):
        checkpoint("eh_snapshot_reference_coordinate")
        raw = reader.load_raw_chunk("CC", symbol, day)
        stored = _stored_targets(rows)
        if raw is None:
            coordinates.append([symbol, "session_missing", None, None, stored, None, []])
            continue
        _require((raw["symbol"], raw["session"]) == (symbol, day), "EH_reference_raw_coordinate_differs")
        session = SessionInputs(symbol, opening, closing, _AuditedMinutes(symbol, opening, raw))
        evidence = OpeningRangePriceEvidence(f"CC:{day}:{symbol}", "stored_cc_same_session_numeric_unit")
        observation = reference_opening_support(session, evidence)
        sessions[symbol], observations[symbol] = session, observation
        status = "frames_present" if observation.paths else observation.scan.status
        gap_issues = (_issues(observation.scan.issues)
                      if observation.scan.status == "support_unknown"
                      and any(entry.initial.stage == "approach" for entry in observation.paths) else [])
        coordinates.append([symbol, status, _stamp(opening), _stamp(closing), stored,
                            [evidence.receipt_id, evidence.price_unit], gap_issues])
    frames = sorted(((clock, symbol, entry.initial.stage, event, entry, chart)
                     for symbol, observation in observations.items()
                     for clock, event, entry, chart in _frames(sessions[symbol], observation)), key=lambda row: row[:3])
    questions = {(symbol, stage, entry.initial.question.question_id): entry.results
                 for _, symbol, stage, _, entry, _ in frames if entry.initial.question is not None}

    def decisions():
        peer_cache = {}
        for clock, symbol, stage, event, entry, chart in frames:
            initial = entry.initial
            peer = None if chart is None or chart[0] != "ready" else _peers(sessions, roster, symbol, clock, known, peer_cache)
            yield [symbol, _stamp(clock), _stamp(event.origin.available_at), _stamp(event.expires_at), stage,
                   _question(initial.question), str(initial.start_close), initial.target_basis,
                   _issues(initial.issues), chart, peer]

    return coordinates, known, questions, closing, len(frames), decisions()


def _count_frame(counts, row, memberships):
    chart = "no_question" if row[9] is None else row[9][0]
    peer_ready = int(row[10] is not None and row[10][2] is not None)
    counts["frames"] += 1
    counts["chart_status"][chart] += 1
    counts["peer_ready"] += peer_ready
    for name in memberships[row[0]]:
        totals = counts["scenarios"][name]
        totals["frames"] += 1
        totals["chart_ready"] += int(chart == "ready")
        totals["peer_ready"] += peer_ready


def verify_snapshot(feature_path: Path, feature_seal: SnapshotSeal, label_path: Path, label_seal: SnapshotSeal,
                    universe: UniverseCalendar, reader, checkpoint) -> dict:
    """Check both complete sealed streams against independent original observations."""
    days = _original_days(universe)
    _require(feature_path != label_path and feature_seal.records == len(days), "EH_reference_snapshot_geometry_differs")
    counts = {"sessions": len(days), "union_coordinates": 0,
              "coordinate_status": dict.fromkeys(_COORDINATE_STATUSES, 0),
              "chart_status": dict.fromkeys(_CHART_STATUSES, 0), "label_status": dict.fromkeys(("observed", "session_missing"), 0),
              "frames": 0, "peer_ready": 0, "questions": 0, "mature_training": 0, "breakout_gap_after_approach": 0,
              "scenarios": {name: dict.fromkeys(("original", "frames", "chart_ready", "peer_ready", "questions",
                                                "mature_training", "breakout_gap_after_approach"), 0)
                            for name in ("S1", "S2")}}
    reader.assert_unchanged()
    labels = read_labels(label_path, label_seal, feature_seal)
    features = _read(feature_path, feature_seal, {"schema": FEATURE_SCHEMA}, maximum=MAX_DAYS)
    for (day, targets), (actual, _) in zip(days.items(), features, strict=True):
        checkpoint("eh_snapshot_reference_day")
        _require(set(actual) == {"day", "members_known_at", "coordinates", "rows"} and actual["day"] == day
                 and type(actual["rows"]) is list, "EH_reference_snapshot_day_differs")
        memberships = {symbol: tuple(row.scenario for row in rows) for symbol, rows in targets.items()}
        coordinates, known, questions, closing, frame_count, records = _day_reference(day, targets, reader, checkpoint)
        _require(actual["members_known_at"] == _stamp(known) and _same_coordinates(actual["coordinates"], coordinates),
                 "EH_reference_snapshot_coordinate_differs")
        _require(len(actual["rows"]) == frame_count, "EH_reference_feature_row_count_differs")
        for row, expected in zip(actual["rows"], records, strict=True):
            _require(row == expected, "EH_reference_feature_row_differs")
            _count_frame(counts, expected, memberships)
        for coordinate in coordinates:
            gap = int(bool(coordinate[6]))
            counts["union_coordinates"] += 1
            counts["coordinate_status"][coordinate[1]] += 1
            counts["breakout_gap_after_approach"] += gap
            for name in memberships[coordinate[0]]:
                counts["scenarios"][name]["original"] += 1
                counts["scenarios"][name]["breakout_gap_after_approach"] += gap
        for key, results in sorted(questions.items()):
            checkpoint("eh_snapshot_reference_label")
            label = next(labels, None)
            training = _training(results)
            _require(label is not None and label.key == key and label.cutoff_at == closing + 2 * MINUTE
                     and label.status == "observed" and label.results == results and label.training == training,
                     "EH_reference_label_row_differs")
            counts["questions"] += 1
            counts["label_status"]["observed"] += 1
            counts["mature_training"] += int(training is not None)
            for name in memberships[key[0]]:
                totals = counts["scenarios"][name]
                totals["questions"] += 1
                totals["mature_training"] += int(training is not None)
    _require(next(labels, None) is None, "EH_reference_label_EOF_differs")
    reader.assert_unchanged()
    return counts
