"""Independent EI reconstruction from caller-authenticated original minutes.

Only record types are shared with the situation producer. Source payloads are
decoded once, when visible, and no unrequested suffix is inspected. This is an
audit of the registered observations, not proof of historical availability,
volume units, representative chart cases or executable trading returns.
"""

from collections.abc import Iterator, Mapping
from datetime import date, datetime, timedelta
from decimal import Decimal
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_context_support import frame_payload
from research.intraday_context_situations import (
    ContextFrame, ContextMetrics, ContextMinute, ResistanceAnchor, WindowSupport,
)
from research.intraday_ed_inputs import InputRefusal, Minute, SessionInputs
from research.intraday_pattern_calibration import session_close, session_open


_MINUTE = timedelta(minutes=1)
_STATES = {"observed", "missing", "no_trade", "halt", "transport_error", "conflict", "unit_unknown"}
_CLASSES = ("A", "B", "other", "unclassified", "unknown")


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise InputRefusal("EI_reference_" + reason)


def _clock(value: object) -> None:
    _require(isinstance(value, datetime) and value.utcoffset() is not None
             and value.second == value.microsecond == 0, "minute_clock_required")


def _identity(session: SessionInputs, through: datetime) -> None:
    _require(isinstance(session, SessionInputs), "session_required")
    _clock(session.opening)
    _clock(session.closing)
    _clock(through)
    _require(type(session.symbol) is str and bool(session.symbol)
             and isinstance(session.minutes, Mapping), "session_identity")
    _require(session.opening < session.closing and session.opening.date() == session.closing.date()
             and (session.closing - session.opening) % _MINUTE == timedelta(0), "session_geometry")
    _require(through >= session.opening, "cutoff_before_opening")


def _decode(row: Minute) -> ContextMinute:
    for state, payload in ((row.price_state, row.ohlc), (row.volume_state, row.shares)):
        _require(type(state) is str and state in _STATES, "observation_state")
        _require(state == "observed" or payload is None, "unknown_payload")
    price = None
    if row.price_state == "observed":
        _require(type(row.ohlc) is tuple and len(row.ohlc) == 4, "ohlc_shape")
        _require(all(isinstance(x, Decimal) and x.is_finite() and x > 0 for x in row.ohlc),
                 "price_values")
        first, maximum, minimum, last = row.ohlc
        _require(minimum <= first <= maximum and minimum <= last <= maximum, "ohlc_order")
        price = tuple(Fraction(x) for x in row.ohlc)
    volume = None
    if row.volume_state == "observed":
        _require(isinstance(row.shares, Decimal) and row.shares.is_finite() and row.shares >= 0,
                 "volume_value")
        volume = Fraction(row.shares)
    return ContextMinute(row.start_at, row.available_at, price, volume,
                         row.price_state, row.volume_state)


def _window(prefix: tuple[ContextMinute, ...], length: int) -> WindowSupport:
    rows = prefix[max(0, len(prefix) - length):]
    price_counts, volume_counts = {}, {}
    for row in rows:
        price_counts[row.price_state] = price_counts.get(row.price_state, 0) + 1
        volume_counts[row.volume_state] = volume_counts.get(row.volume_state, 0) + 1
    if len(rows) < length:
        price_counts["outside_session"] = length - len(rows)
        volume_counts["outside_session"] = length - len(rows)
    total = None
    if volume_counts == {"observed": length}:
        total = sum((row.volume for row in rows), Fraction(0))
    zeroes = sum(row.volume is not None and row.volume == 0 for row in rows)
    return WindowSupport(length, len(rows), tuple(sorted(price_counts.items())),
                         tuple(sorted(volume_counts.items())), zeroes, total)


def _five(rows):
    if any(item.prices is None for item in rows):
        return None
    return (rows[0].prices[0], max(item.prices[1] for item in rows),
            min(item.prices[2] for item in rows), rows[-1].prices[3])


def _classify(prefix: tuple[ContextMinute, ...], opening: datetime, bars=None):
    """Rebuild OHLC then evaluate candidate centers in chronological order."""
    bar_count = len(prefix) // 5
    if bars is None:
        bars = [_five(prefix[number * 5:number * 5 + 5]) for number in range(bar_count)]

    def visible(number: int) -> datetime:
        return opening + _MINUTE * (max(30, 5 * (number + 1)) + 2)

    candidate = None
    zero_seen = False
    for right in range(4, bar_count):
        center = right - 1
        high = bars[center][1]
        if bars[center - 1][1] >= high or bars[right][1] > high:
            continue
        ranges = [bars[x][1] - bars[x][2] for x in range(center - 3, center)]
        # Three-element median without relying on the producer's expression.
        scale = sum(ranges, Fraction(0)) - min(ranges) - max(ranges)
        if scale == 0:
            zero_seen = True
        elif high - bars[0][0] >= scale * 2:
            candidate = (center, high, scale)
            break
    if candidate is None:
        reason = "zero_reference_range" if zero_seen else "no_qualifying_confirmed_peak"
        return None, None, "unclassified", "unclassified", reason

    center, high, scale = candidate
    advance = high - bars[0][0]
    anchor = ResistanceAnchor(opening, bars[0][0], opening + _MINUTE * (center + 1) * 5,
                              visible(center + 1), high, scale, advance, scale / 4)
    lower, upper = high - scale / 4, high + scale / 4
    after = range(center + 1, bar_count)
    pullback_indices = [x for x in after if bars[x][3] < bars[x - 1][3]]
    first_pullback = pullback_indices[0] if pullback_indices else None
    resume_indices = [x for x in after if first_pullback is not None and x > first_pullback
                      and bars[x][3] > bars[x - 1][1]]
    first_resume = resume_indices[0] if resume_indices else None
    invalid_indices = [x for x in after if bars[x][3] <= bars[0][0]]
    invalidated = visible(invalid_indices[0]) if invalid_indices else None
    overlaps = [bars[x][2] <= upper and bars[x][1] >= lower for x in after]
    episodes = 0
    separated = False
    for number, overlap in zip(after, overlaps):
        if overlap and separated and number != center + 1:
            episodes += 1
            separated = False
        if bars[number][1] < lower:
            separated = True
    depth = (high - min([high] + [bars[x][2] for x in after])) / advance
    age = bar_count - center - 1
    pullback_clock = visible(first_pullback) if first_pullback is not None else None
    resume_clock = visible(first_resume) if first_resume is not None else None
    metrics = ContextMetrics(advance / scale, depth, age, pullback_clock,
                             resume_clock, episodes, overlaps[-1], invalidated)
    if invalidated is not None:
        return anchor, metrics, "other", "other", "anchor_invalidated"
    if advance / scale >= 3 and episodes >= 2 and overlaps[-1]:
        return anchor, metrics, "B", "extended_resistance_retest", "extended_repeated_retest"
    if first_pullback is not None and Fraction(0) < depth <= Fraction(1, 2) and age <= 6 and episodes <= 1:
        if resume_clock == visible(bar_count - 1):
            return anchor, metrics, "A", "first_resumption", "first_shallow_attempt"
        if first_resume is None:
            return anchor, metrics, "A", "first_pullback", "first_shallow_attempt"
    reason = "first_attempt_completed" if first_resume is not None else "outside_A_B_definition"
    return anchor, metrics, "other", "other", reason


class _SupportIndex:
    """Exact range subtraction, rebuilt only from an arriving delayed row."""

    def __init__(self):
        self.prices = [{}]
        self.volumes = [{}]
        self.totals = [Fraction(0)]
        self.zeroes = [0]

    def update(self, rows, changed):
        for values in (self.prices, self.volumes, self.totals, self.zeroes):
            del values[changed + 1:]
        for item in rows[changed:]:
            price, volume = self.prices[-1].copy(), self.volumes[-1].copy()
            price[item.price_state] = price.get(item.price_state, 0) + 1
            volume[item.volume_state] = volume.get(item.volume_state, 0) + 1
            self.prices.append(price)
            self.volumes.append(volume)
            self.totals.append(self.totals[-1] + (item.volume if item.volume is not None else 0))
            self.zeroes.append(self.zeroes[-1] + int(item.volume is not None and item.volume == 0))

    def window(self, size):
        end = len(self.prices) - 1
        start = max(0, end - size)
        counts = []
        for cumulative in (self.prices, self.volumes):
            delta = {key: count - cumulative[start].get(key, 0) for key, count in cumulative[end].items()}
            if end < size:
                delta["outside_session"] = size - end
            counts.append(tuple(sorted((key, count) for key, count in delta.items() if count)))
        total = self.totals[end] - self.totals[start] if counts[1] == (("observed", size),) else None
        return WindowSupport(size, end - start, counts[0], counts[1], self.zeroes[end] - self.zeroes[start], total)


def reference_frames(session: SessionInputs, through: datetime) -> Iterator[ContextFrame]:
    """Reconstruct each requested clock with one read/decode per source minute."""
    _identity(session, through)
    decoded: list[ContextMinute] = []
    pending: dict[int, Minute] = {}
    support_index = _SupportIndex()
    bars = []
    end_minutes = 30
    while True:
        decision = session.opening + (end_minutes + 2) * _MINUTE
        if decision > through or decision >= session.closing:
            break
        changed = len(decoded)
        for index in tuple(pending):
            if pending[index].available_at <= decision:
                decoded[index] = _decode(pending.pop(index))
                changed = min(changed, index)
        for index in range(len(decoded), end_minutes):
            stamp = session.opening + index * _MINUTE
            raw = session.minutes.get(stamp)
            if raw is None:
                decoded.append(ContextMinute(stamp, None, None, None, "missing", "missing"))
                continue
            _require(isinstance(raw, Minute) and raw.symbol == session.symbol and raw.start_at == stamp,
                     "minute_identity")
            _clock(raw.available_at)
            _require(raw.available_at >= stamp + 3 * _MINUTE, "availability_floor")
            if raw.available_at > decision:
                pending[index] = raw
                decoded.append(ContextMinute(stamp, None, None, None, "late", "late"))
            else:
                decoded.append(_decode(raw))
        prefix = tuple(decoded)
        support_index.update(decoded, changed)
        del bars[changed // 5:]
        bars.extend(_five(decoded[number:number + 5]) for number in range(changed // 5 * 5, end_minutes, 5))
        support = support_index.window(end_minutes)
        complete = dict(support.price_counts) == {"observed": end_minutes}
        historical = complete and all(row.available_at <= session.opening
                                      + (max(30, 5 * (index // 5 + 1)) + 2) * _MINUTE
                                      for index, row in enumerate(prefix))
        anchor = metrics = None
        if not complete:
            category, phase, reason = "unknown", "unknown", "opening_price_prefix_incomplete"
        elif not historical:
            category, phase, reason = "unknown", "unknown", "prefix_not_observed_on_grid"
        else:
            anchor, metrics, category, phase, reason = _classify(prefix, session.opening, bars)
        yield ContextFrame(session.symbol, session.opening, decision - 2 * _MINUTE, decision,
                           prefix, support, tuple(support_index.window(size) for size in (30, 60, 120)),
                           historical, anchor, metrics, category, phase, reason)
        end_minutes += 5


def _add(destination: dict, values) -> None:
    for name, value in values:
        destination[name] = destination.get(name, 0) + value


def verify_record(saved, targets, session, *, price_present, volume_present) -> None:
    """Compare every saved counter and frame digest with original reconstruction."""
    targets = tuple(targets)
    _require(bool(targets) and type(price_present) is bool and type(volume_present) is bool,
             "record_inputs")
    _require(all(target.symbol == session.symbol and target.session == session.opening.date().isoformat()
                 for target in targets), "target_session_identity")
    reconstructed = {"kind": "record", "session": targets[0].session, "symbol": session.symbol,
                     "targets": [{"scenario": x.scenario, "rank": x.rank,
                                  "stored_row_count": x.stored_row_count, "state": x.state,
                                  "metadata_sha256": hashlib.sha256(x.metadata).hexdigest()} for x in targets],
                     "price_chunk_present": price_present, "volume_chunk_present": volume_present,
                     "frames": 0, "classes": {}, "phases": {}, "reasons": {},
                     "first_hour_classes": {}, "prefix_observed_frames": 0, "first_frames": {},
                     "support": {str(size): {"price_complete": 0, "volume_complete": 0,
                                            "price_counts": {}, "volume_counts": {},
                                            "observed_zero_volume_slots": 0} for size in (30, 60, 120)}}
    digest = hashlib.sha256()
    for frame in reference_frames(session, session.closing):
        digest.update(wire.encode(frame_payload(frame)))
        reconstructed["frames"] += 1
        for key, value in (("classes", frame.classification), ("phases", frame.phase), ("reasons", frame.reason)):
            _add(reconstructed[key], ((value, 1),))
        if frame.observation_end <= session.opening + 60 * _MINUTE:
            _add(reconstructed["first_hour_classes"], ((frame.classification, 1),))
        reconstructed["prefix_observed_frames"] += int(frame.prefix_observed_on_grid)
        if frame.classification not in reconstructed["first_frames"]:
            reconstructed["first_frames"][frame.classification] = frame.available_at.isoformat()
        for cell in frame.windows:
            out = reconstructed["support"][str(cell.minutes)]
            out["price_complete"] += int(dict(cell.price_counts) == {"observed": cell.minutes})
            out["volume_complete"] += int(dict(cell.volume_counts) == {"observed": cell.minutes})
            _add(out["price_counts"], cell.price_counts)
            _add(out["volume_counts"], cell.volume_counts)
            out["observed_zero_volume_slots"] += cell.zero_volume_minutes
    reconstructed["frame_sha256"] = digest.hexdigest()
    _require(wire.encode(saved) == wire.encode(reconstructed), "record_reconstruction_differs")


class _StreamingTotals:
    """Keep numeric totals and distinct dates, never complete saved records."""

    def __init__(self):
        self.cells = {}
        self.dates = {}
        for population in ("union", "S1", "S2", "overlap"):
            self.cells[population] = {
                "coordinates": 0, "frames": 0, "class_frames": {}, "first_hour_class_frames": {},
                "class_coordinates": dict.fromkeys(_CLASSES, 0), "class_dates": {},
                "price_chunks_absent": 0, "volume_chunks_absent": 0, "prefix_observed_frames": 0,
                "window_complete_frames": {str(size): {"price_complete": 0, "volume_complete": 0}
                                           for size in (30, 60, 120)}}
            self.dates[population] = {kind: set() for kind in _CLASSES}

    def add(self, record):
        names = {item["scenario"] for item in record["targets"]}
        included = {"union"} | names
        if names == {"S1", "S2"}:
            included.add("overlap")
        for population in included:
            cell = self.cells[population]
            cell["coordinates"] += 1
            cell["frames"] += record["frames"]
            cell["prefix_observed_frames"] += record["prefix_observed_frames"]
            cell["price_chunks_absent"] += int(not record["price_chunk_present"])
            cell["volume_chunks_absent"] += int(not record["volume_chunk_present"])
            _add(cell["class_frames"], record["classes"].items())
            _add(cell["first_hour_class_frames"], record["first_hour_classes"].items())
            for kind in record["classes"]:
                cell["class_coordinates"][kind] += 1
                self.dates[population][kind].add(record["session"])
            for size, window in cell["window_complete_frames"].items():
                for name in window:
                    window[name] += record["support"][size][name]

    def finish(self):
        for population, cell in self.cells.items():
            cell["class_dates"] = {kind: len(values) for kind, values in self.dates[population].items()}
        return {"populations": self.cells, "unit_of_count": "overlapping decision frames and distinct coordinates/dates",
                "historical_availability_certified": False, "stored_volume_units_certified": False,
                "contains_outcomes_or_returns": False}


def _count(value, maximum=None) -> None:
    _require(type(value) is int and value >= 0 and (maximum is None or value <= maximum), "count")


def _counts(values, names, total=None) -> None:
    _require(type(values) is dict and set(values) <= set(names), "counter_keys")
    for value in values.values():
        _count(value)
        _require(value > 0, "empty_counter_cell")
    _require(total is None or sum(values.values()) == total, "counter_total")


def _stamp(value) -> datetime:
    _require(type(value) is str, "timestamp_type")
    parsed = datetime.fromisoformat(value)
    _clock(parsed)
    _require(parsed.isoformat() == value, "timestamp_canonical")
    return parsed


def _record_shape(record) -> None:
    _require(set(record) == {"kind", "session", "symbol", "targets", "price_chunk_present", "volume_chunk_present",
                            "frames", "frame_sha256", "classes", "phases", "reasons", "first_hour_classes",
                            "prefix_observed_frames", "first_frames", "support"}, "record_fields")
    _require(type(record["session"]) is str and type(record["symbol"]) is str and bool(record["symbol"]),
             "coordinate_identity")
    _require(date.fromisoformat(record["session"]).isoformat() == record["session"], "coordinate_date")
    _require(type(record["price_chunk_present"]) is bool and type(record["volume_chunk_present"]) is bool,
             "chunk_presence_type")
    _require(wire.valid_sha(record["frame_sha256"]), "frame_digest")
    _count(record["frames"], 288)
    opening, closing = session_open(record["session"]), session_close(record["session"])
    decision_offsets = range(32, (closing - opening) // _MINUTE, 5)
    _require(record["frames"] == len(decision_offsets), "original_clock_denominator")
    _require(type(record["targets"]) is list and 1 <= len(record["targets"]) <= 2, "targets_shape")
    scenarios = []
    for target in record["targets"]:
        _require(type(target) is dict and set(target) == {"scenario", "rank", "stored_row_count", "state", "metadata_sha256"},
                 "target_fields")
        scenarios.append(target["scenario"])
        _require(target["scenario"] in ("S1", "S2") and wire.valid_sha(target["metadata_sha256"]), "target_identity")
        _count(target["rank"])
        _require(target["rank"] > 0, "target_rank")
        count = target["stored_row_count"]
        if count is None:
            _require(target["state"] == "no_stored_rows_unknown_reason", "target_missingness")
        else:
            _count(count)
            _require(count > 0 and target["state"] == "stored_rows_completeness_unverified", "target_stored_state")
    _require(len(set(scenarios)) == len(scenarios), "duplicate_target_clock")
    n = record["frames"]
    _counts(record["classes"], _CLASSES, n)
    _counts(record["phases"], ("first_pullback", "first_resumption", "extended_resistance_retest", "other", "unclassified", "unknown"), n)
    _counts(record["reasons"], ("zero_reference_range", "no_qualifying_confirmed_peak", "opening_price_prefix_incomplete",
                               "prefix_not_observed_on_grid", "anchor_invalidated", "first_attempt_completed",
                               "outside_A_B_definition", "first_shallow_attempt", "extended_repeated_retest"), n)
    _counts(record["first_hour_classes"], _CLASSES)
    _require(sum(record["first_hour_classes"].values()) == min(n, 7), "first_hour_denominator")
    _require(all(value <= record["classes"].get(kind, 0) for kind, value in record["first_hour_classes"].items()),
             "first_hour_class_bounds")
    _count(record["prefix_observed_frames"], n)
    _require(record["prefix_observed_frames"] == n - record["classes"].get("unknown", 0), "prefix_denominator")
    _require(type(record["first_frames"]) is dict and set(record["first_frames"]) == set(record["classes"]), "first_frame_keys")
    for stamp in record["first_frames"].values():
        clock = _stamp(stamp)
        _require(clock.date().isoformat() == record["session"]
                 and (clock - opening) // _MINUTE in decision_offsets, "first_frame_grid")
    _require(type(record["support"]) is dict and set(record["support"]) == {"30", "60", "120"}, "support_windows")
    for size, window in record["support"].items():
        _require(type(window) is dict and set(window) == {"price_complete", "volume_complete", "price_counts", "volume_counts",
                                                        "observed_zero_volume_slots"}, "support_fields")
        for name in ("price_complete", "volume_complete"):
            _count(window[name], n)
        states = _STATES | {"late", "outside_session"}
        _counts(window["price_counts"], states, n * int(size))
        _counts(window["volume_counts"], states, n * int(size))
        outside = sum(max(0, int(size) - (offset - 2)) for offset in decision_offsets)
        _require(all(window[name].get("outside_session", 0) == outside for name in ("price_counts", "volume_counts")),
                 "outside_session_denominator")
        _count(window["observed_zero_volume_slots"], window["volume_counts"].get("observed", 0))


def _rational(value, *, zero=False) -> Fraction:
    _require(type(value) is list and len(value) == 2 and all(type(x) is int for x in value)
             and value[1] > 0, "rational_shape")
    result = Fraction(*value)
    _require(result >= 0 if zero else result > 0, "rational_sign")
    _require([result.numerator, result.denominator] == value, "rational_canonical")
    return result


def _case_shape(case, expected) -> None:
    _require(set(case) == {"kind", "session", "symbol", "classification", "frame", "chart_prefix"}, "case_fields")
    _require(type(case["frame"]) is dict, "case_frame_shape")
    key = (case["session"], case["symbol"], case["classification"], case["frame"].get("available_at"))
    _require(key == expected, "deterministic_case_roster")
    payload = case["frame"]
    opening, decision = _stamp(payload["opening"]), _stamp(payload["available_at"])
    end = _stamp(payload["observation_end"])
    length = (end - opening) // _MINUTE
    _require(end == decision - 2 * _MINUTE and length >= 30 and length % 5 == 0
             and opening == session_open(case["session"]) and decision < session_close(case["session"]), "case_clock")
    _require(type(case["chart_prefix"]) is list and len(case["chart_prefix"]) == length, "case_prefix_length")
    rows = []
    for offset, item in enumerate(case["chart_prefix"]):
        _require(type(item) is dict and set(item) == {"start_at", "available_at", "prices", "volume", "price_state", "volume_state"},
                 "case_minute_fields")
        start = _stamp(item["start_at"])
        _require(start == opening + offset * _MINUTE, "case_minute_order")
        available = None if item["available_at"] is None else _stamp(item["available_at"])
        _require(available is None or start + 3 * _MINUTE <= available <= decision, "case_minute_availability")
        for field in ("price_state", "volume_state"):
            _require(type(item[field]) is str and item[field] in _STATES | {"late"}, "case_observation_state")
        _require(available is not None or (item["price_state"] == item["volume_state"] and item["price_state"] in {"late", "missing"}),
                 "case_absent_availability")
        prices = None
        if item["price_state"] == "observed":
            _require(type(item["prices"]) is list and len(item["prices"]) == 4, "case_price_shape")
            prices = tuple(_rational(value) for value in item["prices"])
            _require(prices[2] <= prices[0] <= prices[1] and prices[2] <= prices[3] <= prices[1], "case_price_order")
        else:
            _require(item["prices"] is None, "case_unknown_price")
        volume = _rational(item["volume"], zero=True) if item["volume_state"] == "observed" else None
        _require(item["volume_state"] == "observed" or item["volume"] is None, "case_unknown_volume")
        rows.append(ContextMinute(start, available, prices, volume, item["price_state"], item["volume_state"]))
    prefix = tuple(rows)
    support = _window(prefix, length)
    complete = dict(support.price_counts) == {"observed": length}
    grid = complete and all(item.available_at <= opening + (max(30, (i // 5 + 1) * 5) + 2) * _MINUTE
                            for i, item in enumerate(prefix))
    anchor = metrics = None
    if not complete:
        kind, phase, reason = "unknown", "unknown", "opening_price_prefix_incomplete"
    elif not grid:
        kind, phase, reason = "unknown", "unknown", "prefix_not_observed_on_grid"
    else:
        anchor, metrics, kind, phase, reason = _classify(prefix, opening)
    rebuilt = ContextFrame(case["symbol"], opening, end, decision, prefix, support,
                           tuple(_window(prefix, size) for size in (30, 60, 120)), grid,
                           anchor, metrics, kind, phase, reason)
    _require(wire.encode(frame_payload(rebuilt)) == wire.encode(payload)
             and kind == case["classification"], "case_reconstruction_differs")


def audit_saved(path, *, expected_sha256, expected_bytes, expected_geometry, expected_input_manifest) -> dict:
    """Audit framing, counts, deterministic examples and totals; raw audit is separate."""
    path = Path(path)
    identity = wire.regular(path)
    _require(wire.valid_sha(expected_sha256) and type(expected_bytes) is int
             and 0 < expected_bytes <= 32 * wire.MIB and identity.st_size == expected_bytes, "saved_pin")
    wire.validate_registered_input_manifest(expected_input_manifest, input_contract=wire.EI_SUPPORT_INPUT_CONTRACT)
    universe_pin = expected_input_manifest[0]["sha256"]
    for name in ("sessions", "union_coordinates", "targets_per_scenario"):
        _count(expected_geometry[name])
        _require(expected_geometry[name] > 0, "positive_geometry_required")
    per_day, remainder = divmod(expected_geometry["targets_per_scenario"], expected_geometry["sessions"])
    _require(per_day > 0 and remainder == 0, "fixed_daily_population_required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    expected_cases = []
    totals = _StreamingTotals()
    dates, ranks = set(), set()
    daily_counts = {"S1": {}, "S2": {}}
    previous_coordinate, record_count = None, 0
    case_counts = dict.fromkeys(_CLASSES, 0)
    digest, consumed, case_index = hashlib.sha256(), 0, 0
    summary = None
    phase = "header"
    with os.fdopen(descriptor, "rb") as stream:
        _require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(identity), "saved_open_identity")
        while True:
            raw = stream.readline(256 * 1024 + 1)
            if not raw:
                break
            consumed += len(raw)
            _require(consumed <= expected_bytes and len(raw) <= 256 * 1024 and raw.endswith(b"\n"), "saved_bounds")
            digest.update(raw)
            value = json.loads(raw)
            _require(type(value) is dict and wire.encode(value) == raw, "saved_canonical_line")
            kind = value.get("kind")
            if phase == "header":
                _require(set(value) == {"kind", "schema", "geometry", "input_manifest", "universe_sha256", "calendar_sha256", "case_selection"}
                         and kind == "header" and value["schema"] == "EI-SUPPORT-1-context-support-v1", "saved_header")
                _require(wire.encode(value["geometry"]) == wire.encode(expected_geometry)
                         and wire.encode(value["input_manifest"]) == wire.encode(expected_input_manifest), "saved_contract")
                _require(wire.valid_sha(value["universe_sha256"]) and wire.valid_sha(value["calendar_sha256"])
                         and value["case_selection"] == "first two distinct sorted coordinates per class, earliest class clock; outcome blind",
                         "saved_population_identity")
                _require(value["universe_sha256"] == universe_pin, "saved_universe_pin")
                calendar_pin = value["calendar_sha256"]
                phase = "records"
            elif phase == "records" and kind == "record":
                _record_shape(value)
                coordinate = (value["session"], value["symbol"])
                _require(previous_coordinate is None or coordinate > previous_coordinate, "record_order")
                previous_coordinate = coordinate
                record_count += 1
                _require(record_count <= min(6000, expected_geometry["union_coordinates"]), "record_bound")
                dates.add(value["session"])
                _require(len(dates) <= expected_geometry["sessions"], "calendar_bound")
                for target in value["targets"]:
                    rank = (target["scenario"], value["session"], target["rank"])
                    _require(target["rank"] <= per_day, "original_daily_rank_outside_roster")
                    _require(rank not in ranks, "duplicate_original_rank")
                    ranks.add(rank)
                    _add(daily_counts[target["scenario"]], ((value["session"], 1),))
                totals.add(value)
                for category in _CLASSES:
                    if category in value["first_frames"] and case_counts[category] < 2:
                        expected_cases.append((*coordinate, category, value["first_frames"][category]))
                        case_counts[category] += 1
            elif phase in {"records", "cases"} and kind == "chart_case":
                phase = "cases"
                _require(case_index < len(expected_cases), "extra_case")
                _case_shape(value, expected_cases[case_index])
                case_index += 1
            elif phase in {"records", "cases"} and kind == "footer":
                _require(record_count > 0 and set(value) == {"kind", "records", "cases", "summary"}, "saved_footer")
                summary = totals.finish()
                days = sorted(dates)
                _require(wire.digest(wire.encode(days)) == calendar_pin, "saved_calendar_pin")
                populations = summary["populations"]
                _require(len(days) == expected_geometry["sessions"]
                         and record_count == expected_geometry["union_coordinates"]
                         and all(populations[name]["coordinates"] == expected_geometry["targets_per_scenario"]
                                 for name in ("S1", "S2")), "saved_original_geometry")
                _require(all(set(values) == dates and all(count == per_day for count in values.values())
                             for values in daily_counts.values()), "scenario_daily_population_differs")
                footer = {"kind": "footer", "records": record_count, "cases": len(expected_cases), "summary": summary}
                _require(case_index == len(expected_cases) and wire.encode(value) == wire.encode(footer), "footer_reconstruction_differs")
                phase = "done"
            else:
                _require(False, "unexpected_saved_phase")
        _require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(identity), "saved_changed_during_read")
    _require(phase == "done" and consumed == expected_bytes and digest.hexdigest() == expected_sha256, "saved_seal")
    _require(wire.file_identity(wire.regular(path)) == wire.file_identity(identity), "saved_path_replaced")
    return summary
