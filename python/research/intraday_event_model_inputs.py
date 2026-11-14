"""Manifest-bound streams for EL fits; parsing test targets is prohibited.

These primitives receive caller-admitted shard manifests, not arbitrary input
authority. Complete bytes are authenticated, but only prior-session target rows
are decoded. The causal query stream determines the exact training prefix.
"""

from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path

from research import intraday_campaign_wsl_transport as wire
from research.intraday_event_dataset import MAX_FILE, MAX_ROW, _day, _decode_frame, _decode_label_query, _population
from research import intraday_event_dataset_run as dataset_run
from research.intraday_event_evaluation import Candidate, Episode, SessionMarket
from research.intraday_event_model_workflow import FOLDS, collect, decode_labels, decode_query


def admit_dataset(release, qualification_pin, shard_audit_pins):
    """Resolve all nine actual datasets from immutable upstream audit receipts.

    The outer runner supplies the released source identity and frozen receipt
    pins. This checks the prior journal and file identities via the dataset's
    existing verifier before handing out readers for their row content.
    """
    pins = tuple(shard_audit_pins)
    wire.require(len(pins) == len(dataset_run.SHARDS) and len(set(pins)) == len(pins)
                 and wire.valid_sha(qualification_pin) and all(wire.valid_sha(pin) for pin in pins),
                 "EL all nine independent dataset audit pins are required")
    _, authenticated = dataset_run.audited(dataset_run.identity_for(release, invented=True))
    wire.require(authenticated == qualification_pin, "EL dataset qualification audit pin differs")
    prior = dataset_run.identity_for(release, invented=False, qualification=qualification_pin)
    shards = []
    for shard, pin in enumerate(pins):
        report, authenticated = dataset_run.audited_shard(prior, shard)
        wire.require(authenticated == pin, "EL dataset shard audit pin differs")
        shards.append((dataset_run._slot(report["identity"]["attempt"]), report, pin))
    binding = sha256(wire.encode({"qualification_audit_sha256": qualification_pin, "shard_audit_sha256": pins})).hexdigest()
    return tuple(shards), binding


@contextmanager
def verified_stream(slot, pin):
    path = Path(slot) / pin["filename"]
    wire.require(Path(pin["filename"]).name == pin["filename"] and type(pin["bytes"]) is int
                 and 0 < pin["bytes"] <= MAX_FILE and wire.valid_sha(pin["sha256"]), "EL bounded manifest pin required")
    before = wire.file_identity(wire.regular(path))
    wire.require(wire.file_digest(path, MAX_FILE, lambda _: None) == (pin["sha256"], pin["bytes"]),
                 "EL model input bytes differ")
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        wire.require(before == wire.file_identity(os.fstat(stream.fileno())), "EL input changed before parsing")
        yield stream
        wire.require(before == wire.file_identity(os.fstat(stream.fileno()))
                     == wire.file_identity(wire.regular(path)), "EL input changed during parsing")
    wire.require(wire.file_digest(path, MAX_FILE, lambda _: None) == (pin["sha256"], pin["bytes"]),
                 "EL model input changed after parsing")


def line(stream):
    raw = stream.readline(MAX_ROW + 1)
    wire.require(raw and len(raw) <= MAX_ROW and raw.endswith(b"\n"), "EL model input row exceeds bound or is truncated")
    value = json.loads(raw)
    wire.require(type(value) is dict and wire.encode(value) == raw, "EL model input row is noncanonical")
    return value


def _rows(slot, manifest, filename, kind):
    pin = manifest["files"][filename]
    with verified_stream(slot, pin) as stream:
        header = line(stream)
        wire.require(header["kind"] == "header" and header["calendar_sha256"] == manifest["calendar_sha256"]
                     and header["session_indices"] == manifest["session_indices"], "EL stream header identity differs")
        if kind == "labels":
            wire.require(header["before_sha256"] == manifest["before_sha256"], "EL target seal differs")
        count = 0
        while True:
            row = line(stream)
            if row["kind"] == "footer":
                wire.require(row["data_rows"] == count and count + 2 == pin["rows"] and not stream.read(1),
                             "EL stream count/EOF differs")
                return
            wire.require(row["kind"] == kind, "EL stream contains unexpected row")
            count += 1
            yield row


def query_rows(slot, manifest):
    yield from _rows(slot, manifest, "queries.jsonl", "query")


def _training_prefix(query_records, cutoff, prefix):
    boundary_seen = False
    for query in query_records:
        before = query["session"] < cutoff
        wire.require(not before or not boundary_seen, "EL shard query dates are not ordered")
        if before:
            prefix.append((query["key"], query["session"], query["symbol"]))
        else:
            boundary_seen = True
        yield query


def _labels_for_prefix(slot, manifest, prefix):
    # Do not decode the first evaluation label to discover the cutoff.
    pin = manifest["files"]["labels.jsonl"]
    query_pin = manifest["files"]["queries.jsonl"]
    wire.require(pin["rows"] == query_pin["rows"] and len(prefix) <= pin["rows"] - 2,
                 "EL query and label stream geometry differs")
    with verified_stream(slot, pin) as stream:
        header = line(stream)
        wire.require(header["kind"] == "header" and header["before_sha256"] == manifest["before_sha256"]
                     and header["calendar_sha256"] == manifest["calendar_sha256"], "EL training label header differs")
        for expected in prefix:
            row = line(stream)
            wire.require(row["kind"] == "labels" and (row["key"], row["session"], row["symbol"]) == expected,
                         "EL causal query/training target prefix differs")
            yield row
        if len(prefix) + 2 == pin["rows"]:
            footer = line(stream)
            wire.require(footer["kind"] == "footer" and footer["data_rows"] == len(prefix)
                         and len(prefix) + 2 == pin["rows"] and not stream.read(1), "EL full training target EOF differs")


def training_labels(slot, manifest, cutoff):
    prefix = []
    for _ in _training_prefix(query_rows(slot, manifest), cutoff, prefix):
        pass
    yield from _labels_for_prefix(slot, manifest, prefix)


def calendar_from_coordinates(shards):
    """All original days, including unsupported names; never selected episodes."""
    days, expected_first, calendar_pin = [], 0, None
    for slot, manifest in shards:
        first, end = manifest["session_indices"]
        wire.require(first == expected_first and end > first, "EL complete ordered shards required")
        expected_first = end
        calendar_pin = calendar_pin or manifest["calendar_sha256"]
        wire.require(calendar_pin == manifest["calendar_sha256"], "EL shard calendar changed")
        pin = manifest["files"]["coordinate-support.jsonl"]
        selected, count = set(), 0
        with verified_stream(slot, pin) as stream:
            header = line(stream)
            wire.require(header["kind"] == "header" and header["calendar_sha256"] == calendar_pin,
                         "EL coordinate calendar header differs")
            while True:
                row = line(stream)
                if row["kind"] == "footer":
                    wire.require(row["data_rows"] == count and pin["rows"] == count + 2 and not stream.read(1),
                                 "EL coordinate stream count/EOF differs")
                    break
                wire.require(row["kind"] == "coordinate", "EL original coordinate row required")
                selected.add(row["session"])
                count += 1
        wire.require(len(selected) == end - first, "EL original calendar dates were lost")
        days.extend(sorted(selected))
    wire.require(expected_first == 257 and len(days) == len(set(days)) == 257 and days == sorted(days)
                 and sha256(wire.encode(tuple(days))).hexdigest() == calendar_pin, "EL all-nine calendar pin differs")
    return tuple(days)


def _test_shards(shards, fold):
    _, start, end = FOLDS[fold]
    for slot, manifest in shards:
        first, last = manifest["session_indices"]
        if first < end and last > start:
            yield slot, manifest


def load_fold(shards, fold, scope):
    """Assemble a fold from the complete, already audited shard set."""
    shards = tuple(shards)
    calendar = calendar_from_coordinates(shards)
    _, start, end = FOLDS[fold]
    prefixes = []

    def queries():
        for slot, manifest in shards:
            first, _ = manifest["session_indices"]
            if first >= end:
                break
            rows = query_rows(slot, manifest)
            if first < start:
                prefix = []
                yield from _training_prefix(rows, calendar[start], prefix)
                prefixes.append((slot, manifest, prefix))
            else:
                yield from rows

    def targets():
        # collect exhausts authenticated causal queries before consuming targets.
        for slot, manifest, prefix in prefixes:
            yield from _labels_for_prefix(slot, manifest, prefix)

    return collect(calendar, fold, scope, queries(), targets())


@dataclass(frozen=True)
class DecisionRoster:
    episodes: tuple[Episode, ...]
    candidates: tuple[Candidate, ...]


def decision_roster(shards, data):
    """Recover the original onsets, including those erased by coalescing."""
    _, start, end = FOLDS[data.fold]
    test_days = set(data.calendar[start:end])
    decoded = {query.key: query for query in data.evaluation}
    episodes, candidates = {}, []
    for slot, manifest in _test_shards(shards, data.fold):
        for row in _rows(slot, manifest, "event-states.jsonl", "event_state"):
            frame = row["frame"]
            if row["session"] not in test_days or frame["kind"] != "at_onset":
                continue
            episode = Episode(row["session"], row["symbol"], frame["event_id"],
                              frame["overlap_group_id"], frame["family"], frame["decision_offset"])
            wire.require(episode.event_id not in episodes, "EL duplicate original onset")
            episodes[episode.event_id] = episode
        for row in query_rows(slot, manifest):
            if row["session"] not in test_days or row["query_kind"] != "entry":
                continue
            frame = _decode_frame(row["frame"])
            episode = episodes.get(frame.event_id)
            wire.require(episode is not None and (episode.session, episode.symbol) == (row["session"], row["symbol"]),
                         "EL entry without matching original onset")
            query = decoded.get(row["key"])
            if query is None and row["query"] is not None:
                query = decode_query(row["query"])
            candidate = Candidate(episode, frame, _decode_label_query(row["label_query"]), query)
            wire.require(candidate.key == row["key"], "EL entry query key differs from its wrapper")
            candidates.append(candidate)
    return DecisionRoster(tuple(episodes.values()), tuple(candidates))


def evaluation_targets(shards, data):
    """Reporting phase only. Unknown labels are rows with unsupported heads."""
    wanted = {query.key: (query.session, query.symbol) for query in data.evaluation}
    seen = set()
    for slot, manifest in _test_shards(shards, data.fold):
        for row in _rows(slot, manifest, "labels.jsonl", "labels"):
            key = row["key"]
            if key not in wanted:
                continue
            wire.require(key not in seen and wanted[key] == (row["session"], row["symbol"])
                         and row["training_labels"] is not None, "EL evaluation target roster differs")
            label = decode_labels(row["training_labels"])
            wire.require(label.key == key, "EL evaluation label identity differs")
            seen.add(key)
            yield label
    wire.require(seen == wanted.keys(), "EL evaluation targets are incomplete")


class SessionMarkets:
    """Day-local replay using the same original coordinates as dataset production.

    The caller passes the authenticated original reader and chooses its audit
    decoder. Construction reads no bars. The current date is the only cache.
    """

    def __init__(self, universe, reader, dates, *, audit=False):
        self._groups, _, _ = _population(universe, None)
        self._reader = reader
        self._dates = frozenset(dates)
        wire.require(self._dates <= set(universe.sessions), "EL market replay dates escaped the original calendar")
        self._audit = audit
        self._current_day = None
        self._markets = {}

    def __getitem__(self, key):
        day, symbol = key
        wire.require(day in self._dates and key in self._groups, "EL replay escaped its outer-fold population")
        if day != self._current_day:
            self._markets.clear()
            observations = _day(day, self._groups, self._reader, self._audit)
            peers = {name: continuous.bars for name, (_, continuous, _) in observations.items()
                     if {target.scenario for target in self._groups[day, name]} == {"S1", "S2"}}
            self._markets = {name: SessionMarket(continuous.session_length, continuous.bars, continuous.volumes, peers)
                             for name, (_, continuous, _) in observations.items()}
            self._current_day = day
        return self._markets[symbol]
