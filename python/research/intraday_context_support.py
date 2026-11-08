"""EI support census and bounded outcome-blind chart cases, no fitted model."""

from collections import Counter
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from fractions import Fraction
import hashlib
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_context_situations as situations
from research import intraday_ed_sources as sources
from research.intraday_trader_inputs import _groups


SCHEMA = "EI-SUPPORT-1-context-support-v1"
FILENAME = "ei-context-support.jsonl"
MAX_BYTES = 32 * wire.MIB
MAX_LINE = 256 * 1024
MAX_RECORDS = 6000
CLASSES = ("A", "B", "other", "unclassified", "unknown")
CASE_LIMIT_PER_CLASS = 2
POPULATIONS = ("union", "S1", "S2", "overlap")


@dataclass(frozen=True)
class CensusSeal:
    sha256: str
    size: int
    records: int
    cases: int


def exact(value):
    """Wire schema only; no classification or statistical calculation."""
    if isinstance(value, Fraction):
        return [value.numerator, value.denominator]
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return {field.name: exact(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, (tuple, list)):
        return [exact(item) for item in value]
    if isinstance(value, dict):
        return {name: exact(item) for name, item in value.items()}
    return value


def frame_payload(frame):
    """Bounded context identity without repeatedly copying its chart prefix."""
    return {field.name: exact(getattr(frame, field.name))
            for field in fields(frame) if field.name != "prefix"}


def target_payload(target):
    return {"scenario": target.scenario, "rank": target.rank,
            "stored_row_count": target.stored_row_count, "state": target.state,
            "metadata_sha256": wire.digest(target.metadata)}


def day_record(targets, frames, *, price_present, volume_present):
    """Retain every original coordinate, with repeated clocks named as clocks."""
    targets = tuple(targets)
    first = targets[0]
    frame_hash = hashlib.sha256()
    classes, phases, reasons, first_hour = (Counter() for _ in range(4))
    support = {str(size): {"price_complete": 0, "volume_complete": 0,
                           "price_counts": Counter(), "volume_counts": Counter(),
                           "observed_zero_volume_slots": 0} for size in (30, 60, 120)}
    first_frames, count, prefix = {}, 0, 0
    for frame in frames:
        wire.require(frame.symbol == first.symbol and frame.classification in CLASSES,
                     "EI context identity differs")
        payload = frame_payload(frame)
        frame_hash.update(wire.encode(payload))
        count += 1
        classes[frame.classification] += 1
        phases[frame.phase] += 1
        reasons[frame.reason] += 1
        prefix += int(frame.prefix_observed_on_grid)
        if (frame.observation_end - frame.opening).total_seconds() <= 3600:
            first_hour[frame.classification] += 1
        first_frames.setdefault(frame.classification, frame.available_at.isoformat())
        for cell in frame.windows:
            row = support[str(cell.minutes)]
            row["price_complete"] += int(cell.price_complete)
            row["volume_complete"] += int(cell.volume_complete)
            row["price_counts"].update(dict(cell.price_counts))
            row["volume_counts"].update(dict(cell.volume_counts))
            row["observed_zero_volume_slots"] += cell.zero_volume_minutes
    return {"kind": "record", "session": first.session, "symbol": first.symbol,
            "targets": [target_payload(item) for item in targets],
            "price_chunk_present": price_present, "volume_chunk_present": volume_present,
            "frames": count, "frame_sha256": frame_hash.hexdigest(),
            "classes": dict(classes), "phases": dict(phases), "reasons": dict(reasons),
            "first_hour_classes": dict(first_hour), "prefix_observed_frames": prefix,
            "first_frames": first_frames, "support": exact(support)}


def summarize(records):
    """Descriptive support denominators, never trade frequency or power."""
    output = {}
    for name in POPULATIONS:
        chosen = []
        for row in records:
            clocks = {target["scenario"] for target in row["targets"]}
            if name == "union" or name in clocks or (name == "overlap" and clocks == {"S1", "S2"}):
                chosen.append(row)
        classes, first_hour = Counter(), Counter()
        for row in chosen:
            classes.update(row["classes"])
            first_hour.update(row["first_hour_classes"])
        output[name] = {
            "coordinates": len(chosen), "frames": sum(row["frames"] for row in chosen),
            "class_frames": dict(classes), "first_hour_class_frames": dict(first_hour),
            "class_coordinates": {kind: sum(row["classes"].get(kind, 0) > 0 for row in chosen) for kind in CLASSES},
            "class_dates": {kind: len({row["session"] for row in chosen if row["classes"].get(kind, 0)}) for kind in CLASSES},
            "price_chunks_absent": sum(not row["price_chunk_present"] for row in chosen),
            "volume_chunks_absent": sum(not row["volume_chunk_present"] for row in chosen),
            "prefix_observed_frames": sum(row["prefix_observed_frames"] for row in chosen),
            "window_complete_frames": {
                size: {name: sum(row["support"][size][name] for row in chosen)
                       for name in ("price_complete", "volume_complete")} for size in ("30", "60", "120")},
        }
    return {"populations": output, "unit_of_count": "overlapping decision frames and distinct coordinates/dates",
            "historical_availability_certified": False, "stored_volume_units_certified": False,
            "contains_outcomes_or_returns": False}


def write_support(path, universe, raw_reader, geometry, input_manifest, checkpoint):
    """Write an exclusive sealed census; original-source access is caller-gated."""
    groups = _groups(universe)
    wire.require(0 < len(groups) <= MAX_RECORDS, "EI census bound")
    header = {"kind": "header", "schema": SCHEMA, "geometry": geometry,
              "input_manifest": input_manifest, "universe_sha256": universe.raw_sha256,
              "calendar_sha256": wire.digest(wire.encode(universe.sessions)),
              "case_selection": "first two distinct sorted coordinates per class, earliest class clock; outcome blind"}
    digest, size = hashlib.sha256(), 0
    records, cases = [], []
    case_counts = Counter()
    wire.safe_path(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        def write(value):
            nonlocal size
            line = wire.encode(value)
            size += len(line)
            wire.require(len(line) <= MAX_LINE and size <= MAX_BYTES, "EI census output bound")
            wire.write_all(stream, line)
            digest.update(line)

        write(header)
        for (day, symbol), targets in sorted(groups.items()):
            checkpoint("ei_context_coordinate")
            raw_reader.assert_unchanged()
            price = raw_reader.load_raw_chunk("CC", symbol, day)
            volume = raw_reader.load_raw_chunk("CX", symbol, day)
            session = sources.session_inputs(symbol, day, price, volume).session
            pending = {}

            def frames():
                for frame in situations.scan_context_frames(session, through=session.closing):
                    kind = frame.classification
                    if kind not in pending and case_counts[kind] < CASE_LIMIT_PER_CLASS:
                        pending[kind] = {"kind": "chart_case", "session": day, "symbol": symbol,
                                         "classification": kind, "frame": frame_payload(frame),
                                         "chart_prefix": exact(frame.prefix)}
                    yield frame

            record = day_record(targets, frames(), price_present=price is not None, volume_present=volume is not None)
            records.append(record)
            write(record)
            for kind in CLASSES:
                if kind in pending:
                    cases.append(pending[kind])
                    case_counts[kind] += 1
            raw_reader.assert_unchanged()
        for case in cases:
            write(case)
        summary = summarize(records)
        write({"kind": "footer", "records": len(records), "cases": len(cases), "summary": summary})
        stream.flush()
        os.fsync(stream.fileno())
    wire.fsync_directory(Path(path).parent)
    return CensusSeal(digest.hexdigest(), size, len(records), len(cases)), summary
