"""Sealed feature and outcome streams with the same ordered event keys."""

from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import date
import gzip
import hashlib
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_highvol_coverage import CoverageSeal, MAX_COMPRESSED, MAX_DECODED, MAX_LINE, open_coverage


ROLES = ("features", "labels")
FILENAMES = {role: f"eg-scenario-{role}.jsonl.gz" for role in ROLES}
SCHEMAS = {role: f"EG-scenario-{role}-v1" for role in ROLES}


def _week(day: str) -> str:
    year, week, _ = date.fromisoformat(day).isocalendar()
    return f"{year}-W{week:02d}"


class SnapshotSummary:
    """Keep all observed clusters and all weeks in the scenario denominators."""

    def __init__(self, sessions):
        self.weeks = tuple(sorted({_week(day) for day in sessions}))
        self.states = {timing: Counter() for timing in ("S1", "S2")}
        self.weekly = {timing: defaultdict(Counter) for timing in self.states}
        self.events = 0

    def add(self, feature: dict, label: dict) -> None:
        self.events += 1
        day, _, offset = feature["key"]
        attempt = feature["attempt"]
        branch = attempt["scenario"] or "none"
        for timing in feature["timings"]:
            count = self.states[timing]
            weekly = self.weekly[timing][_week(day)]
            count["observed_clusters"] += 1
            count[f"{branch}:{attempt['state']}"] += 1
            weekly["observed_clusters"] += 1
            count["first_hour_clusters"] += offset <= 60
            if attempt["state"] != "confirmed":
                continue
            count[f"{branch}:first_hour_confirmed"] += offset <= 60
            weekly[f"{branch}:confirmed"] += 1
            primary = label["primary"]
            count[f"{branch}:primary:{primary['status']}"] += 1
            if primary["status"] == "admitted":
                weekly[f"{branch}:admitted"] += 1
                count[f"{branch}:fixed_exit_known"] += primary["fixed_gross"] is not None
                count[f"{branch}:first_touch:{primary['first_touch']['status']}"] += 1
            context = feature["entry"]
            if (context is not None and context["chart"]["status"] == "available"
                    and context["peers"][timing]["status"] == "available"
                    and context["primary_clock_supported"]):
                count[f"{branch}:common_support"] += 1
                weekly[f"{branch}:common_support"] += 1

    def finish(self) -> dict:
        return {"schema": "EG-scenario-snapshot-summary-v1", "union_events": self.events,
                "timings": {timing: {"counts": dict(counts), "weekly": [
                    {"week": week, **dict(self.weekly[timing][week])} for week in self.weeks]}
                    for timing, counts in self.states.items()}}


def write_snapshot(directory: Path, records, binding: dict, universe: dict, checkpoint):
    """Write and fsync both complete streams before returning their seals."""
    summary = SnapshotSummary(universe["sessions"])
    paths = {role: directory / FILENAMES[role] for role in ROLES}
    digests = {role: hashlib.sha256() for role in ROLES}
    sizes = dict.fromkeys(ROLES, 0)
    with ExitStack() as files:
        raw = {role: files.enter_context(paths[role].open("xb")) for role in ROLES}
        with ExitStack() as streams:
            zipped = {role: streams.enter_context(gzip.GzipFile(filename="", fileobj=raw[role],
                      mode="wb", mtime=0, compresslevel=3)) for role in ROLES}

            def write(role, frame):
                line = wire.encode(frame)
                sizes[role] += len(line)
                wire.require(len(line) <= MAX_LINE and sizes[role] <= MAX_DECODED,
                             "EG snapshot decoded bound exceeded")
                digests[role].update(line)
                zipped[role].write(line)
                wire.require(raw[role].tell() <= MAX_COMPRESSED, "EG snapshot compressed bound exceeded")

            for role in ROLES:
                write(role, {"kind": "header", "schema": SCHEMAS[role], "universe": universe, "binding": binding})
            previous = None
            for feature, label in records:
                checkpoint("eg_snapshot_write_event")
                day, symbol, offset = feature["key"]
                order = day, offset, symbol
                wire.require(feature["key"] == label["key"] and (previous is None or order > previous),
                             "EG snapshot event identity or order differs")
                previous = order
                summary.add(feature, label)
                write("features", {"kind": "record", "value": feature})
                write("labels", {"kind": "record", "value": label})
            report = summary.finish()
            write("features", {"kind": "footer", "records": summary.events,
                               "report": {"union_events": summary.events}})
            write("labels", {"kind": "footer", "records": summary.events, "report": report})
        for stream in raw.values():
            stream.flush()
            os.fsync(stream.fileno())
    seals = {}
    for role in ROLES:
        digest, size = wire.file_digest(paths[role], MAX_COMPRESSED, checkpoint)
        seals[role] = CoverageSeal(digest, size, digests[role].hexdigest(), sizes[role], summary.events)
    return seals, report


def open_snapshot(directory: Path, role: str, seal: CoverageSeal, checkpoint):
    wire.require(role in ROLES, "EG fixed snapshot role required")
    return open_coverage(directory / FILENAMES[role], seal, checkpoint, schema=SCHEMAS[role], allow_empty=True)
