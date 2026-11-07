"""Seal and verify the EH opening census. The artifact contains future labels."""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import BinaryIO

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research import intraday_scenario_observations as producer
from research.intraday_ed_inputs import Issue, MINUTE, SessionInputs
from research.intraday_scenario_labels import HORIZONS_MINUTES
from research.intraday_scenario_state import OpeningRangePriceEvidence
from research.intraday_scenario_support_storage import SupportSeal
from research.intraday_trader_inputs import AuditedChunkReader, _groups


FILENAME = "eh-opening-support.jsonl"
SCHEMA = "EH-OPEN-1-opening-support-v1"
MAX_BYTES = 32 * wire.MIB
MAX_LINE = 8192
MAX_RECORDS = 6000
MAX_SUMMARY_BYTES = 16 * 1024
_POPULATIONS = ("union", "S1", "S2", "overlap")
_STAGES = ("approach", "breakout")
_SCAN_STATUSES = ("not_yet_eligible", "known_no_event", "support_unknown", "event")
_EVENT_KINDS = ("prepared", "breakout", "left_censored_gap")
_LABEL_STATUSES = ("resolved", "pending", "unknown", "unsupported")
_PATHS = ("P1", "P2", "P3", "P4")

Checkpoint = Callable[[str], object]
Census = Callable[[SessionInputs, OpeningRangePriceEvidence | None], producer.OpeningSupportObservation]
Groups = dict[tuple[str, str], list[sources.TargetCoordinate]]


@dataclass(frozen=True)
class VerifiedOpeningSupport:
    support_totals: dict
    by_session: dict
    by_symbol: dict


def _header(universe: sources.UniverseCalendar) -> dict:
    return {"kind": "header", "schema": SCHEMA, "universe_sha256": universe.raw_sha256,
            "calendar_sha256": wire.digest(wire.encode(universe.sessions)),
            "calendar_sessions": len(universe.sessions), "original_targets": len(universe.targets)}


def _footer(records: int) -> dict:
    return {"kind": "footer", "records": records}


def _issue(issue: Issue) -> dict:
    return {"symbol": issue.symbol, "minute": issue.minute.isoformat(), "reason": issue.reason}


def _record(day: str, session: SessionInputs, targets: list[sources.TargetCoordinate],
            evidence: OpeningRangePriceEvidence | None, observation: producer.OpeningSupportObservation) -> dict:
    event = observation.scan.event
    event_record = None
    if event is not None:
        event_record = {"kind": event.kind, "origin_at": event.origin.available_at.isoformat(),
                        "breakout_at": event.breakout.available_at.isoformat() if event.breakout is not None else None,
                        "expires_at": event.expires_at.isoformat(), "level_id": event.level.level_id,
                        "level_available_at": event.level.available_at.isoformat(),
                        "level": str(event.level.price), "band": str(event.band), "censor_reason": event.censor_reason}
    initial_paths = []
    for path in observation.paths:
        initial = path.initial
        decision_at = event.origin.available_at if initial.stage == "approach" else event.breakout.available_at
        question = initial.question
        if question is None:
            labels = [{"horizon_minutes": horizon, "status": "unsupported", "path": None,
                       "reason": "question_unsupported", "observed_through": None, "mature_at": None,
                       "departed": False, "retested": False} for horizon in HORIZONS_MINUTES]
        else:
            labels = [
                {"horizon_minutes": horizon, "status": result.status, "path": result.path,
                 "reason": result.reason, "observed_through": result.observed_through.isoformat(),
                 "mature_at": result.mature_at.isoformat() if result.mature_at is not None else None,
                 "departed": result.departed, "retested": result.retested}
                for horizon, result in zip(HORIZONS_MINUTES, path.results, strict=True)
            ]
        initial_paths.append({
            "stage": initial.stage, "start_at": decision_at.isoformat(), "start_close": str(initial.start_close),
            "question": None if question is None else {
                "start_at": question.start_at.isoformat(), "end_at": question.end_at.isoformat(),
                "level": str(question.level), "band": str(question.band), "target": str(question.target),
                "stop": str(question.stop),
            },
            "target_basis": initial.target_basis, "issues": [_issue(item) for item in initial.issues],
            "labels": labels,
        })
    return {
        "kind": "record", "session": day, "symbol": session.symbol,
        "opening": session.opening.isoformat(), "closing": session.closing.isoformat(),
        "targets": [{"scenario": target.scenario, "symbol": target.symbol, "session": target.session,
                     "rank": target.rank, "stored_row_count": target.stored_row_count, "state": target.state,
                     "metadata_sha256": wire.digest(target.metadata)}
                    for target in sorted(targets, key=lambda item: item.scenario)],
        "price_chunk_present": evidence is not None,
        "price_evidence": None if evidence is None else {"receipt_id": evidence.receipt_id, "price_unit": evidence.price_unit},
        "scan_status": observation.scan.status, "scan_issues": [_issue(item) for item in observation.scan.issues],
        "event": event_record, "initial_paths": initial_paths,
    }


def _validated_groups(universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
                      checkpoint: Checkpoint) -> Groups:
    groups = _groups(universe)
    sources._need(0 < len(groups) <= MAX_RECORDS and callable(checkpoint)
                  and callable(getattr(raw_reader, "load_raw_chunk", None))
                  and callable(getattr(raw_reader, "assert_unchanged", None)), "invalid_opening_census_contract")
    return groups


def _records(groups: Groups, raw_reader: AuditedChunkReader,
             checkpoint: Checkpoint, census: Census) -> Iterator[dict]:
    for day, symbol in sorted(groups):
        checkpoint("eh_open_before_coordinate")
        raw_reader.assert_unchanged()
        try:
            raw = raw_reader.load_raw_chunk("CC", symbol, day)
            session = sources.full_reference_session_inputs(symbol, day, raw, None)
            # Receipt key in the audited CC index. The numeric unit is local to
            # this coordinate and carries no physical-unit or basis assertion.
            evidence = (OpeningRangePriceEvidence(f"CC:{day}:{symbol}", "stored_cc_same_session_numeric_unit")
                        if raw is not None else None)
            record = _record(day, session, groups[(day, symbol)], evidence, census(session, evidence))
            del raw, session
        finally:
            raw_reader.assert_unchanged()
            checkpoint("eh_open_after_coordinate")
        yield record


def write_opening_support(path: Path, universe: sources.UniverseCalendar, raw_reader: AuditedChunkReader,
                          checkpoint: Checkpoint) -> SupportSeal:
    """Write deterministic original-coordinate rows to an exclusive artifact."""
    groups = _validated_groups(universe, raw_reader, checkpoint)
    wire.safe_path(path)
    digest, size = sha256(), 0
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        def write(value: dict) -> None:
            nonlocal size
            checkpoint("eh_open_write_record")
            line = wire.encode(value)
            size += len(line)
            sources._need(len(line) <= MAX_LINE and size <= MAX_BYTES, "opening_artifact_bound")
            wire.write_all(stream, line)
            digest.update(line)

        write(_header(universe))
        for record in _records(groups, raw_reader, checkpoint, producer.opening_support_observation):
            write(record)
        raw_reader.assert_unchanged()
        write(_footer(len(groups)))
        stream.flush()
        os.fsync(stream.fileno())
        written = os.fstat(stream.fileno())
    wire.fsync_directory(path.parent)
    sources._need(wire.file_identity(wire.regular(path)) == wire.file_identity(written)
                  and written.st_size == size, "opening_artifact_changed_during_write")
    return SupportSeal(digest.hexdigest(), size, len(groups))


def _population() -> dict:
    return {"coordinates": 0, "price_chunk_absent_records": 0,
            "scan_status_counts": dict.fromkeys(_SCAN_STATUSES, 0),
            "event_kind_counts": dict.fromkeys(_EVENT_KINDS, 0),
            "stages": {stage: {"initial": 0, "first_hour": 0, "question_supported": 0,
                               "horizons": {str(horizon): {"status_counts": dict.fromkeys(_LABEL_STATUSES, 0),
                                                           "path_counts": dict.fromkeys(_PATHS, 0)}
                                            for horizon in HORIZONS_MINUTES}} for stage in _STAGES}}


def _count_record(totals: dict, day: Counter, symbol: Counter, record: dict) -> None:
    totals["coordinates"] += 1
    totals["price_chunk_absent_records"] += int(not record["price_chunk_present"])
    totals["scan_status_counts"][record["scan_status"]] += 1
    if record["event"] is not None:
        totals["event_kind_counts"][record["event"]["kind"]] += 1
    opening = sources._clock(record["opening"])
    detail = ["coordinates"]
    for initial in record["initial_paths"]:
        stage = initial["stage"]
        stage_totals = totals["stages"][stage]
        stage_totals["initial"] += 1
        stage_totals["question_supported"] += int(initial["question"] is not None)
        detail.append(f"{stage}_initial")
        if sources._clock(initial["start_at"]) - opening < 60 * MINUTE:
            stage_totals["first_hour"] += 1
            detail.append(f"{stage}_first_hour")
        for label in initial["labels"]:
            horizon = label["horizon_minutes"]
            counts = stage_totals["horizons"][str(horizon)]
            counts["status_counts"][label["status"]] += 1
            if label["path"] is not None:
                counts["path_counts"][label["path"]] += 1
            if label["status"] == "resolved":
                detail.append(f"{stage}_resolved_{horizon}m")
    day.update(detail)
    symbol.update(detail)


def _verify_records(stream: BinaryIO, seal: SupportSeal, universe: sources.UniverseCalendar, groups: Groups,
                    raw_reader: AuditedChunkReader, checkpoint: Checkpoint, census: Census) -> VerifiedOpeningSupport:
    digest, size = sha256(), 0

    def match_line(expected: dict, reason: str) -> None:
        nonlocal size
        checkpoint("eh_open_read_record")
        line = stream.readline(MAX_LINE + 1)
        size += len(line)
        sources._need(line.endswith(b"\n") and len(line) <= MAX_LINE and size <= seal.size,
                      "opening_artifact_framing_or_bound")
        sources._need(line == wire.encode(expected), reason)
        digest.update(line)

    totals = {name: _population() for name in _POPULATIONS}
    dates = {name: {day: Counter(coordinates=0) for day in universe.sessions} for name in _POPULATIONS}
    symbols = {name: defaultdict(Counter) for name in _POPULATIONS}
    match_line(_header(universe), "opening_artifact_header_differs")
    for record in _records(groups, raw_reader, checkpoint, census):
        match_line(record, "stored_opening_record_differs")
        clocks = tuple(target["scenario"] for target in record["targets"])
        populations = ("union", *clocks, "overlap") if len(clocks) == 2 else ("union", *clocks)
        for name in populations:
            _count_record(totals[name], dates[name][record["session"]], symbols[name][record["symbol"]], record)
    match_line(_footer(seal.records), "opening_artifact_footer_differs")
    sources._need(stream.read(1) == b"", "opening_artifact_trailing_bytes")
    sources._need(size == seal.size and digest.hexdigest() == seal.sha256, "opening_artifact_seal_differs")
    sources._need(len(wire.encode(totals)) <= MAX_SUMMARY_BYTES, "opening_summary_bound")
    raw_reader.assert_unchanged()
    return VerifiedOpeningSupport(totals,
                                  {name: {day: dict(counts) for day, counts in rows.items()} for name, rows in dates.items()},
                                  {name: {symbol: dict(counts) for symbol, counts in rows.items()}
                                   for name, rows in symbols.items()})


def verify_opening_support(path: Path, seal: SupportSeal, universe: sources.UniverseCalendar,
                           raw_reader: AuditedChunkReader, checkpoint: Checkpoint) -> VerifiedOpeningSupport:
    """Verify every stored byte against a separate calculation from the audited CC reader."""
    from research.intraday_scenario_open_reference import reference_opening_support

    groups = _validated_groups(universe, raw_reader, checkpoint)
    sources._need(type(seal) is SupportSeal and wire.valid_sha(seal.sha256)
                  and type(seal.size) is int and 0 < seal.size <= MAX_BYTES
                  and type(seal.records) is int and seal.records == len(groups), "invalid_opening_artifact_seal")
    before = wire.regular(path)
    identity = wire.file_identity(before)
    sources._need(before.st_size == seal.size, "opening_artifact_size_differs")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        sources._need(wire.file_identity(os.fstat(stream.fileno())) == identity,
                      "opening_artifact_changed_before_read")
        verified = _verify_records(stream, seal, universe, groups, raw_reader, checkpoint, reference_opening_support)
        sources._need(wire.file_identity(os.fstat(stream.fileno())) == identity,
                      "opening_artifact_changed_during_read")
    sources._need(wire.file_identity(wire.regular(path)) == identity, "opening_artifact_replaced")
    return verified
