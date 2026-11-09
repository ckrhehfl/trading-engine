"""Read only the original audited EI ledgers and their sealed decision rosters."""

from collections import Counter
from contextlib import contextmanager
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from itertools import groupby
import json
import os

from research import intraday_campaign_wsl_transport as wire


UNITS = {"S1": "EI-ECONOMIC-S1-ADMISSION-RECOVERY", "S2": "EI-ECONOMIC-S2"}
FOLD_COUNTS = (640, 640, 620, 620, 620)
LEDGER = "ei-context-economic-ledger.jsonl"
POLICY_SHA = "8c8ff1214f7510c48971d2a70d4c66a239c3071a0d7c322ebe79f0b18f98214b"
WINDOW_START, WINDOW_END = "2025-09-04", "2026-09-23"


@contextmanager
def _source(path, maximum):
    before = wire.regular(path)
    wire.require(0 < before.st_size <= maximum, "Cost census input size outside its bound")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(before),
                     "Cost census input identity changed")
        yield stream, before.st_size
        wire.require(wire.file_identity(os.fstat(stream.fileno())) == wire.file_identity(wire.regular(path))
                     == wire.file_identity(before), "Cost census input changed during read")


def _metadata(path, pin):
    with _source(path, 65536) as (stream, size):
        raw = stream.read(size + 1)
        wire.require(len(raw) == size and sha256(raw).hexdigest() == pin, "Cost census predecessor receipt differs")
    return json.loads(raw)


def _directories(root, pin):
    unit = UNITS[pin["scenario"]]
    wire.require(pin["unit"] == unit, "Cost census predecessor unit differs")
    leaf = unit + "-actual1"
    return root / "launches" / unit / "actual1", root / "output" / leaf, root / "cache" / leaf


def verify_parents(root, pin):
    """Authenticate pinned receipt metadata before the caller starts the new read."""
    launch, output, cache = _directories(root, pin)
    diagnostics = root / "diagnostics" / output.name
    markers = (root / "canonical/ei-context-economic-unresolved.json", output / "unresolved.json",
               output / "bridge-failure.json", cache / "worker-failure.json", diagnostics / "process-failure.json")
    wire.require(not any(os.path.lexists(path) for path in markers), "Cost census predecessor is unresolved")
    config = _metadata(launch / "launch.json", pin["launch_sha256"])
    pub = _metadata(output / "publication.json", pin["publication_sha256"])
    audit = _metadata(launch / "original-receipt-audit.json", pin["audit_sha256"])
    proof = pub["independent_verification"]
    wire.require(config["attempt"] == audit["attempt"] == "actual1"
                 and config["invented_only"] is pub["invented_only"] is audit["invented_only"] is False
                 and config["unit_id"] == pub["unit_id"] == audit["unit_id"] == pin["unit"]
                 and config["merge_commit"] == pub["local_identity"]["merge_commit"] == audit["merge_commit"] == pin["merge"]
                 and config["tree"] == pub["local_identity"]["tree"] == audit["tree"]
                 and config["execution_host"] == pub["execution_host"] == "local_wsl_only"
                 and audit["schema"] == "eh-support-original-receipt-audit-v1" and audit["status"] == "PASS"
                 and audit["publication_sha256"] == pin["publication_sha256"]
                 and pub["status"] == "completed" and pub["full_bridge"] is pub["verification_complete"] is True
                 and proof["verified"] is proof["full_eof_verified"] is proof["all_original_coordinates_verified"] is True
                 and proof["scenario"] == pin["scenario"] and proof["cost_bps"] == 33
                 and proof["execution_policy_sha256"] == POLICY_SHA
                 and proof["original_evaluation_coordinates"] == sum(FOLD_COUNTS)
                 and proof["entry_decision_seals"] == pin["decisions"]
                 and tuple(row["coordinates"] for row in pin["decisions"]) == FOLD_COUNTS
                 and pub["output_sha256"] == audit["output_sha256"] == pin["ledger"]["sha256"]
                 and pub["output_bytes"] == pin["ledger"]["size"], "Cost census predecessor identity differs")


class _Rows:
    def __init__(self, stream):
        self.stream = stream
        self.digest = sha256()

    def next(self):
        raw = self.stream.readline(4 * wire.MIB + 1)
        wire.require(0 < len(raw) <= 4 * wire.MIB and raw.endswith(b"\n"), "Cost census row bound differs")
        self.digest.update(raw)
        return json.loads(raw)

    def finish(self, pin):
        wire.require(not self.stream.read(1) and self.digest.hexdigest() == pin["sha256"],
                     "Cost census input digest or EOF differs")


def saved_rows(root, pin):
    """Pair every economic row with its earlier, independently audited decision roster."""
    _, output, cache = _directories(root, pin)
    with _source(output / LEDGER, 128 * wire.MIB) as (stream, size):
        ledger = _Rows(stream)
        header = ledger.next()
        wire.require(size == pin["ledger"]["size"] and header["schema"] == "EI-context-economic-comparison-v1"
                     and header["scenario"] == pin["scenario"] and header["cost_bps"] == 33
                     and header["entry_decision_seals"] == pin["decisions"], "Cost census ledger header differs")
        previous = ("", "")
        previous_day = ""
        for fold, seal in enumerate(pin["decisions"], 1):
            path = cache / f"ei-entry-decisions-fold-{fold}.jsonl"
            days = Counter()
            with _source(path, 128 * wire.MIB) as (decision_stream, size):
                decisions = _Rows(decision_stream)
                first = decisions.next()
                wire.require(size == seal["size"] and first["schema"] == "ei-context-entry-decisions-draft-v1"
                             and first["header"]["coordinates"] == seal["coordinates"]
                             and first["header"]["fold"]["index"] == fold, "Cost census decision header differs")
                for _ in range(seal["coordinates"]):
                    row, roster = ledger.next(), decisions.next()["coordinate"]["roster"]
                    day, symbol = row["session"], row["symbol"]
                    wire.require(row["fold"] == fold and row["entry_independently_verified"] is True
                                 and (day, symbol) > previous and day > previous_day
                                 and WINDOW_START <= day <= WINDOW_END
                                 and (day, symbol, row["original_frames"], row["coordinate_status"])
                                 == (roster["session"], roster["symbol"], roster["original_frames"],
                                     roster["coordinate_status"]), "Cost census coordinate or causal roster differs")
                    previous = day, symbol
                    days[day] += 1
                    yield row, roster
                decisions.finish(seal)
            wire.require(all(count == 20 for count in days.values()), "Cost census original date roster differs")
            previous_day = previous[0]
        wire.require(ledger.next() == {"records": sum(FOLD_COUNTS)}, "Cost census ledger footer differs")
        ledger.finish(pin["ledger"])


def invented_rows():
    """Full original geometry with all outcome states and exact cost equality."""
    monday = date(2026, 1, 26)
    days = [monday + timedelta(days=week * 7 + day) for week in range(34)
            for day in range(4 if week < 13 else 5)]
    offset = 0
    for fold, count in enumerate(FOLD_COUNTS, 1):
        for day in days[offset:offset + count // 20]:
            session = day.isoformat()
            decision = datetime.combine(day, time(9, 32), timezone(timedelta(hours=9)))
            entry, request, close = ((decision + timedelta(minutes=n)).isoformat() for n in (1, 5, 6))
            for index in range(20):
                symbol = str(100000 + index)
                context = ("A", "B", "other")[index % 3]
                status = ("closed", "closed", "closed", "no_entry", "no_fill", "unsupported", "unresolved")[index % 7]
                question_status = "question_unsupported" if index == 19 else (
                    "overlap_suppressed" if index == 18 else "opportunity")
                arm = {"status": status, "gross": None, "cost": None, "net": None,
                       "entry_notional": None, "holding": None, "fill": None}
                modeled = []
                if question_status != "opportunity":
                    arm["status"] = question_status
                elif status == "closed":
                    price = (10100, 10033, 10000)[index % 7]
                    modeled = [[entry, "10000", None], [close, str(price), None]]
                    arm.update(gross=str(price - 10000), cost="33", net=str(price - 10033), entry_notional="10000",
                               fill={"outcome": "modeled", "quantity": 1, "cost": "33", "reason": "invented"},
                               holding={"status": "closed", "reason": "invented",
                                        "events": [{"at": request, "action": "exit", "execute_at": close},
                                                   {"at": close, "action": "exit", "open_price": str(price),
                                                    "quantity": 1, "fill_status": "modeled"}],
                                        "realized_exits": [{"entry_basis": "10000", "gross_proceeds": str(price),
                                                            "allocated_entry_cost": "33", "quantity": 1, "symbol": symbol}]})
                elif status in ("no_entry", "no_fill"):
                    arm.update(gross="0", cost="0", net="0", entry_notional="0")
                    if status == "no_fill":
                        arm["fill"] = {"outcome": "declined", "quantity": 0, "cost": "0", "reason": "invented"}
                question = {"question_sha256": sha256(f"{session}:{symbol}".encode()).hexdigest(),
                            "decision_at": decision.isoformat(), "status": question_status,
                            "episode_origin_context": context, "baseline": arm}
                row = {"session": session, "symbol": symbol, "fold": fold, "original_frames": 20,
                       "coordinate_status": "observed_session", "entry_independently_verified": True,
                       "questions": [question] if day != days[0] and day < days[-5] else [], "modeled_opens": modeled}
                yield row, {"session": session, "symbol": symbol, "original_frames": 20,
                            "coordinate_status": "observed_session",
                            "frame_states": [["unknown", 3], ["no_event", 17]]}
        offset += count // 20


def write_invented_inputs(root, scenario):
    """Generate only synthetic input files within the exclusive qualification slot."""
    pin = {"scenario": scenario, "unit": UNITS[scenario]}
    _, output, cache = _directories(root, pin)
    for directory in (output, cache):
        directory.mkdir(mode=0o700, parents=True)

    def write(path, rows):
        digest, size = sha256(), 0
        with path.open("xb") as stream:
            for row in rows:
                raw = wire.encode(row)
                stream.write(raw)
                digest.update(raw)
                size += len(raw)
        return {"sha256": digest.hexdigest(), "size": size}

    def decision_rows(fold, count, rows):
        yield {"schema": "ei-context-entry-decisions-draft-v1",
               "header": {"fold": {"index": fold}, "coordinates": count}}
        for _, roster in rows:
            yield {"coordinate": {"roster": roster}}

    pin["decisions"] = []
    for (fold, rows), count in zip(groupby(invented_rows(), key=lambda pair: pair[0]["fold"]),
                                   FOLD_COUNTS, strict=True):
        path = cache / f"ei-entry-decisions-fold-{fold}.jsonl"
        pin["decisions"].append({**write(path, decision_rows(fold, count, rows)), "coordinates": count})

    def ledger_rows():
        yield {"schema": "EI-context-economic-comparison-v1", "scenario": scenario,
               "cost_bps": 33, "entry_decision_seals": pin["decisions"]}
        yield from (row for row, _ in invented_rows())
        yield {"records": sum(FOLD_COUNTS)}

    pin["ledger"] = write(output / LEDGER, ledger_rows())
    return pin
