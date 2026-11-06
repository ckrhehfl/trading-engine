"""Bounded lossless EG coverage artifact and descriptive frequency report.

Storage framing and summary arithmetic do not authenticate market inputs or
independently verify scientific results. The caller must invoke the separate
raw reconstruction verifier before publishing a successful execution receipt.
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime, date
from fractions import Fraction
from functools import lru_cache
import gzip
import hashlib
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as sources
from research.intraday_highvol_events import OFFSETS
from research.intraday_highvol_population import CoverageRecord
from research.intraday_trader_inputs import _groups


SCHEMA = "EG-highvol-coverage-v1"
REPORT_SCHEMA = "EG-highvol-coverage-report-v1"
FILENAME = "eg-highvol-coverage.jsonl.gz"
MAX_COMPRESSED = 512 * 1024 * 1024
MAX_DECODED = 4 * 1024 * 1024 * 1024
MAX_LINE = 8 * 1024 * 1024
CLAIM = "predecision coverage and observed high clusters, not trades or profit"


@lru_cache(maxsize=32)
def _names(kind: type) -> tuple[str, ...]:
    return tuple(field.name for field in fields(kind))


def project(value: object) -> object:
    """Lossless inert projection; JSON cannot choose an executable class."""
    if value is None or type(value) in (str, int, bool):
        return value
    if type(value) is bytes:
        return {"bytes_hex": value.hex()}
    if type(value) is Fraction:
        return {"fraction": [value.numerator, value.denominator]}
    if type(value) is datetime:
        wire.require(value.utcoffset() is not None, "EG aware clock required")
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {name: project(getattr(value, name)) for name in _names(type(value))}
    if type(value) in (tuple, list):
        return [project(item) for item in value]
    if type(value) is dict and all(type(key) is str for key in value):
        return {key: project(item) for key, item in value.items()}
    raise ValueError("unsupported EG coverage scalar")


def _week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year:04d}-W{week:02d}"


def _blank_windows() -> dict:
    return {"expected_windows": 0, "price_statuses": Counter(), "decision_statuses": Counter(),
            "decision_reasons": Counter(), "threshold_ties": 0, "zero_baselines": 0,
            "current_zero_paths": 0, "known_volume_windows": 0, "zero_volume_windows": 0,
            "history_issue_counts": Counter()}


def _blank_minutes() -> dict:
    return {"expected_minutes": 0, "price_states": Counter(), "volume_states": Counter(),
            "zero_volume_minutes": 0}


def _blank_episodes(*, first_hour: bool = False) -> dict:
    result = dict.fromkeys(("observed_clusters", "confirmed_onsets", "left_censored_clusters",
                           "gap_linked_clusters"), 0)
    if not first_hour:
        result.update(dict.fromkeys(("right_censored_clusters", "unavailable_windows",
                                    "days_exact_onset_unknown"), 0))
    return result


class CoverageSummary:
    """Aggregate original-coordinate counts; unknowns remain in denominators."""

    def __init__(self, universe: sources.UniverseCalendar):
        self.universe = universe
        self.groups = _groups(universe)
        self.order = iter(sorted(self.groups))
        self.count = 0
        weeks = Counter(_week(day) for day in universe.sessions)
        self.scenarios = {}
        for scenario in ("S1", "S2"):
            self.scenarios[scenario] = {
                "target_days": 0, "source_absent_price_days": 0, "source_absent_volume_days": 0,
                "minute_segments": {key: _blank_minutes() for key in ("first_hour", "remaining")},
                "window_segments": {key: _blank_windows() for key in ("first_hour", "later")},
                "episodes": _blank_episodes(), "first_hour_episodes": _blank_episodes(first_hour=True),
                "weekly": {week: {"iso_week": week, "calendar_sessions": count,
                    **dict.fromkeys(("original_target_days", "observed_clusters", "confirmed_onsets",
                                    "first_hour_observed_clusters", "first_hour_confirmed_onsets",
                                    "unavailable_windows"), 0)} for week, count in sorted(weeks.items())},
            }

    def add(self, record: CoverageRecord) -> None:
        wire.require(type(record) is CoverageRecord and next(self.order, None) == (record.session, record.symbol),
                     "EG original record order differs")
        expected = tuple(sorted(self.groups[(record.session, record.symbol)],
                                key=lambda item: (item.scenario, item.rank)))
        wire.require(record.targets == expected and tuple(item.current.offset for item in record.decisions) == OFFSETS
                     and len(record.minute_coverage) == 2, "EG original scope or window grid differs")
        self.count += 1
        early = tuple(episode for episode in record.episodes.episodes if episode.high_offsets[0] <= 60)
        for target in record.targets:
            summary = self.scenarios[target.scenario]
            summary["target_days"] += 1
            summary["source_absent_price_days"] += not record.price_chunk_present
            summary["source_absent_volume_days"] += not record.volume_chunk_present
            week = summary["weekly"][_week(record.session)]
            week["original_target_days"] += 1
            for name, segment in zip(("first_hour", "remaining"), record.minute_coverage, strict=True):
                cell = summary["minute_segments"][name]
                cell["expected_minutes"] += segment.end_offset_exclusive - segment.start_offset
                cell["price_states"].update(dict(segment.price_states))
                cell["volume_states"].update(dict(segment.volume_states))
                cell["zero_volume_minutes"] += segment.zero_volume_minutes
            for decision in record.decisions:
                metric = decision.current
                cell = summary["window_segments"]["first_hour" if metric.offset <= 60 else "later"]
                cell["expected_windows"] += 1
                cell["price_statuses"][metric.price_status] += 1
                cell["decision_statuses"][decision.status] += 1
                cell["decision_reasons"][decision.reason] += 1
                cell["threshold_ties"] += decision.threshold_tie is True
                cell["zero_baselines"] += decision.baseline_zero is True
                cell["current_zero_paths"] += metric.zero_path is True
                cell["known_volume_windows"] += metric.shares is not None
                cell["zero_volume_windows"] += metric.shares == 0
                cell["history_issue_counts"].update(issue.reason for issue in decision.history_issues)
            for key, episodes in (("episodes", record.episodes.episodes), ("first_hour_episodes", early)):
                cell = summary[key]
                cell["observed_clusters"] += len(episodes)
                cell["confirmed_onsets"] += sum(item.confirmed_onset for item in episodes)
                cell["left_censored_clusters"] += sum(item.left_censored for item in episodes)
                cell["gap_linked_clusters"] += sum(bool(item.unknown_bridge_offsets) for item in episodes)
            cell = summary["episodes"]
            cell["right_censored_clusters"] += sum(item.right_censored for item in record.episodes.episodes)
            cell["unavailable_windows"] += len(record.episodes.unavailable_offsets)
            cell["days_exact_onset_unknown"] += record.episodes.exact_onset_count is None
            week["observed_clusters"] += len(record.episodes.episodes)
            week["confirmed_onsets"] += record.episodes.confirmed_onsets
            week["first_hour_observed_clusters"] += len(early)
            week["first_hour_confirmed_onsets"] += sum(item.confirmed_onset for item in early)
            week["unavailable_windows"] += len(record.episodes.unavailable_offsets)

    def finish(self) -> dict:
        wire.require(next(self.order, None) is None and self.count == len(self.groups),
                     "EG coverage population is incomplete")
        result = {}
        for scenario, summary in self.scenarios.items():
            weekly = list(summary["weekly"].values())
            # Canonical roundtrip makes a detached report without Counter objects.
            result[scenario] = json.loads(wire.encode({**summary, "weekly": weekly,
                "observed_clusters_weeks_ge15": sum(row["observed_clusters"] >= 15 for row in weekly),
                "confirmed_onsets_weeks_ge15": sum(row["confirmed_onsets"] >= 15 for row in weekly)}))
        return {"schema": REPORT_SCHEMA, "calendar_sessions": len(self.universe.sessions),
                "union_records": self.count, "claim": CLAIM, "scenarios": result}


@dataclass(frozen=True)
class CoverageSeal:
    sha256: str
    compressed_bytes: int
    decoded_sha256: str
    decoded_bytes: int
    records: int


def write_coverage(
    path: Path, universe: sources.UniverseCalendar, binding: dict,
    records: Iterable[CoverageRecord], checkpoint: Callable[[str], object],
) -> tuple[CoverageSeal, dict]:
    """Create one exclusive deterministic artifact; failures preserve partials."""
    wire.require(type(binding) is dict and callable(checkpoint), "EG binding and checkpoint required")
    summary = CoverageSummary(universe)
    decoded = hashlib.sha256()
    size = 0
    with path.open("xb") as raw:
        with gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=3) as zipped:
            def write(value: dict) -> None:
                nonlocal size
                line = wire.encode(value)
                size += len(line)
                wire.require(len(line) <= MAX_LINE and size <= MAX_DECODED, "EG decoded artifact exceeds bound")
                decoded.update(line)
                zipped.write(line)
                wire.require(raw.tell() <= MAX_COMPRESSED, "EG compressed artifact exceeds bound")

            write({"kind": "header", "schema": SCHEMA,
                   "universe": {"sessions": list(universe.sessions), "raw_sha256": universe.raw_sha256,
                                "target_count": len(universe.targets)}, "binding": binding})
            for record in records:
                checkpoint("eg_coverage_write_record")
                summary.add(record)
                write({"kind": "record", "value": project(record)})
            report = summary.finish()
            write({"kind": "footer", "records": summary.count, "report": report})
        raw.flush()
        os.fsync(raw.fileno())
    digest, compressed_size = wire.file_digest(path, MAX_COMPRESSED, checkpoint)
    return CoverageSeal(digest, compressed_size, decoded.hexdigest(), size, summary.count), report


def _pairs(items):
    result = {}
    for key, value in items:
        wire.require(key not in result, "duplicate EG artifact key")
        result[key] = value
    return result


class CoverageReader:
    """Bounded encoded records; full consumption validates footer and gzip EOF."""

    def __init__(self, zipped, seal: CoverageSeal, checkpoint, *, schema: str = SCHEMA):
        self.zipped, self.seal, self.checkpoint = zipped, seal, checkpoint
        self.decoded = hashlib.sha256()
        self.size = self.count = 0
        self.completed = False
        self.footer = None
        self.header = self._line()
        wire.require(type(self.header) is dict and set(self.header) == {"kind", "schema", "universe", "binding"}
                     and self.header["kind"] == "header" and self.header["schema"] == schema,
                     "EG artifact header differs")

    def _line(self):
        self.checkpoint("eg_coverage_read_record")
        raw = self.zipped.readline(MAX_LINE + 1)
        wire.require(0 < len(raw) <= MAX_LINE and raw.endswith(b"\n"), "EG truncated or oversized line")
        self.size += len(raw)
        wire.require(self.size <= MAX_DECODED, "EG decoded artifact exceeds bound")
        self.decoded.update(raw)
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite EG value")))
        wire.require(wire.encode(value) == raw, "EG artifact JSON is not canonical")
        return value

    def records(self) -> Iterator[dict]:
        wire.require(not self.completed and self.count == 0, "EG record stream already consumed")
        while True:
            value = self._line()
            wire.require(type(value) is dict, "EG typed line required")
            if value.get("kind") == "footer":
                wire.require(set(value) == {"kind", "records", "report"}
                             and type(value["records"]) is int and value["records"] == self.count == self.seal.records,
                             "EG artifact footer count differs")
                wire.require(self.zipped.read(1) == b"" and self.size == self.seal.decoded_bytes
                             and self.decoded.hexdigest() == self.seal.decoded_sha256,
                             "EG artifact EOF or decoded seal differs")
                self.footer, self.completed = value, True
                return
            wire.require(set(value) == {"kind", "value"} and value["kind"] == "record"
                         and type(value["value"]) is dict, "EG artifact record differs")
            self.count += 1
            wire.require(self.count <= self.seal.records, "extra EG artifact record")
            yield value["value"]


@contextmanager
def open_coverage(path: Path, seal: CoverageSeal, checkpoint, *, schema: str = SCHEMA,
                  allow_empty: bool = False):
    """Open sealed bytes, refusing early consumption and subsequent mutation."""
    wire.require(type(seal) is CoverageSeal and wire.valid_sha(seal.sha256)
                 and wire.valid_sha(seal.decoded_sha256) and type(seal.compressed_bytes) is int
                 and 0 < seal.compressed_bytes <= MAX_COMPRESSED and type(seal.decoded_bytes) is int
                 and 0 < seal.decoded_bytes <= MAX_DECODED and type(seal.records) is int
                 and seal.records >= (0 if allow_empty else 1),
                 "invalid EG artifact seal")
    wire.require(wire.file_digest(path, MAX_COMPRESSED, checkpoint) == (seal.sha256, seal.compressed_bytes),
                 "EG compressed artifact seal differs")
    with path.open("rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as zipped:
        reader = CoverageReader(zipped, seal, checkpoint, schema=schema)
        yield reader
        wire.require(reader.completed, "EG artifact not consumed through EOF")
    wire.require(wire.file_digest(path, MAX_COMPRESSED, checkpoint) == (seal.sha256, seal.compressed_bytes),
                 "EG artifact changed during read")
