"""Compare recovery after a price shock with and without a volume gate."""

from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from itertools import groupby
from os.path import lexists

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_exit as cash_summary, intraday_candidate_exit_reference as cash_reference
from research import intraday_candidate_laggard_execution as execution
from research import intraday_candidate_laggard_report as payoff_support
from research import intraday_candidate_laggard_run as laggard
from research import intraday_candidate_shock_report as support
from research import intraday_candidate_shock_selection as selection
from research import intraday_context_cost_census_inputs as saved
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_snapshot_verify import reconstruct_reference_session
from research.intraday_pattern_calibration import session_close, session_open


PRICE_OFFSETS = tuple(offset for decision in selection.DECISION_OFFSETS for offset in range(decision - 12, decision - 2))


def _snapshot_clear(root, unit, actual):
    markers = [root / "canonical" / "ei-context-snapshot-unresolved.json",
               root / "launches" / unit / "actual1" / "original-receipt-audit.json"]
    for leaf in (unit + "-invented-qualification-v1", actual):
        output = root / "output" / leaf
        markers.extend((output / "unresolved.json", output / "bridge-failure.json",
                        root / "cache" / leaf / "worker-failure.json",
                        root / "diagnostics" / leaf / "process-failure.json"))
    wire.require(not any(lexists(path) for path in markers), "EJ shock snapshot predecessor is unresolved")


@contextmanager
def _sessions(spec, root, invented):
    if invented:
        yield None
        return
    pins = spec["snapshot_receipts"]
    unit = "EI-SNAPSHOT-1"
    leaf = unit + "-actual1"
    cache = root / "cache" / leaf
    _snapshot_clear(root, unit, leaf)
    publication = saved._metadata(root / "output" / leaf / "publication.json", pins["publication_sha256"])
    audit = saved._metadata(root / "launches" / unit / "actual1" / "supplemental-saved-receipt-audit-v2.json",
                            pins["audit_sha256"])
    proof = publication["independent_verification"]
    raw_audit = proof["raw_byte_audit"]
    wire.require(publication["unit_id"] == audit["unit_id"] == unit
                 and publication["invented_only"] is audit["invented_only"] is False
                 and publication["status"] == "completed" and publication["execution_host"] == "local_wsl_only"
                 and publication["verification_complete"] is publication["full_bridge"] is True
                 and audit["schema"] == "ei-snapshot-supplemental-saved-receipt-audit-v2" and audit["status"] == "PASS"
                 and audit["publication_sha256"] == pins["publication_sha256"]
                 and proof["verified"] is proof["full_eof_verified"] is True
                 and raw_audit["verified"] is raw_audit["full_eof_verified"] is True,
                 "EJ shock snapshot publication or audit differs")
    manifest = proof["input_manifest"]
    wire.require(wire.digest(wire.encode(manifest)) == proof["input_manifest_sha256"]
                 and [row["name"] for row in manifest] == ["CB", "CC", "CX"]
                 and set(raw_audit["archives"]) == {"CC", "CX"}, "EJ shock snapshot manifest differs")
    archives = {}
    for item in manifest[1:]:
        kind = item["name"]
        archive = sources.RawArchivePin(**raw_audit["archives"][kind])
        path = cache / "received" / kind
        wire.require(archive.kind == kind and archive.compressed_sha256 == item["sha256"]
                     and wire.regular(path).st_size == item["size"], "EJ shock snapshot archive pin differs")
        archives[kind] = path, archive
    old = spec["price_archive"]
    path, archive = archives["CC"]
    wire.require(path == root / old["relative_path"]
                 and (archive.compressed_sha256, archive.decoded_sha256, archive.expected_chunks, archive.expected_rows)
                 == tuple(old[key] for key in ("compressed_sha256", "decoded_sha256", "chunks", "rows"))
                 and raw_audit["raw_keys"] == sum(pin.expected_chunks for _, pin in archives.values()),
                 "EJ shock original price archive differs")
    with sources.audit_index(cache / "raw-byte-chunks.sqlite", archives, lambda _: None,
                             expected_cache_sha256=raw_audit["cache_sha256"]) as reader:
        _snapshot_clear(root, unit, leaf)
        yield reader
        reader.assert_unchanged()
        _snapshot_clear(root, unit, leaf)


def _invented_session(symbol, day, offsets, kind):
    opening, closing = session_open(day), session_close(day)
    index = int(symbol.removeprefix("KRX:")) - 100000
    event = selection.DECISION_OFFSETS[index % 3]
    minutes = {}
    for offset in offsets:
        at = opening + offset * MINUTE
        if kind == "CX":
            if index % 7 == 3 and offset == event - 13:
                continue
            volume = Decimal(200 if index % 2 == 0 and event - 12 <= offset <= event - 8 else 100)
            minutes[at] = Minute(symbol, at, at + 3 * MINUTE, None, volume, "missing", "observed")
            continue
        if index % 6 == 1 and offset == 62 - 12:
            continue
        if index % 6 == 5 and offset == event + 2:
            continue
        op = close = Decimal(10000)
        if index % 5 != 4 and event - 12 <= offset <= event - 3:
            op = Decimal(10000 if offset == event - 12 else 9900)
            close = Decimal(9900 if offset <= event - 8 else 9940)
        elif offset == event + 1:
            op = close = Decimal(9941 if index % 5 == 2 else 9935)
        elif offset == event + 31:
            op = close = Decimal(10000)
        elif event + 1 < offset < event + 31:
            op = close = Decimal(9880 if index % 5 == 3 and offset == event + 5 else 9950)
        low, high = min(op, close) - Decimal(10), max(op, close)
        minutes[at] = Minute(symbol, at, at + 3 * MINUTE, (op, high, low, close), None, "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _project(reader, symbol, day, offsets, kind, invented, audit):
    if invented:
        return _invented_session(symbol, day, offsets, kind)
    opening = session_open(day)
    starts = tuple(opening + offset * MINUTE for offset in offsets)
    chunk = reader.load_raw_chunk(kind, symbol, day)
    decode = reconstruct_reference_session if audit else sources.reference_session_inputs
    return decode(symbol, day, chunk if kind == "CC" else None, chunk if kind == "CX" else None,
                  requested_starts=starts)


def _plan(reader, symbol, day, invented, audit):
    price = _project(reader, symbol, day, PRICE_OFFSETS, "CC", invented, audit)
    price_select = selection.reference_price if audit else selection.select_price
    volume_select = selection.reference_volume if audit else selection.apply_volume
    plan = price_select(price)
    volume, offset = None, None
    if plan["rank_state"] == "select":
        decision = datetime.fromisoformat(plan["decision_at"])
        offset = (decision - price.opening) // MINUTE
        wire.require(offset in selection.DECISION_OFFSETS, "EJ shock selected clock differs")
        volume = _project(reader, symbol, day, tuple(range(offset - 37, offset - 7)), "CX", invented, audit)
    return {**volume_select(plan, volume), "decision_offset": offset}


def _row(day, fold, roster, plan, trade):
    baseline = laggard._arm(plan, plan["rank_state"], trade)
    if plan["gated_state"] == plan["rank_state"]:
        candidate = baseline
    elif plan["gated_state"] == "unresolved":
        candidate = laggard._inactive_arm("unresolved", plan["volume_reason"])
    else:
        wire.require(plan["rank_state"] == "select" and plan["gated_state"] == "decline",
                     "EJ shock volume selection differs")
        candidate = laggard._inactive_arm("no_entry", plan["volume_reason"])
    question = {"status": "opportunity", "episode_origin_context": "other", "decision_at": plan["decision_at"],
                "rank_decision": plan, "baseline": baseline, "candidate": candidate}
    return {"session": day, "symbol": roster["symbol"], "fold": fold, "roster": roster, "questions": [question]}


def calculate(spec, parents, slot, invented, root, *, audit=False, stored=None):
    """Seal first events and the volume decisions before opening execution prices."""
    execute = execution.reference_shock_trade if audit else execution.execute_shock_trade
    rosters = {parent["scenario"]: laggard._rosters(root, parent, invented) for parent in parents["inputs"]}
    plans, seals, reports, outputs = {}, {}, {}, {}
    with _sessions(spec, root, invented) as reader:
        pre = {}
        for scenario, rows in rosters.items():
            days = []
            for day, group in groupby(rows, key=lambda item: item[1]["session"]):
                symbols, folds = {}, set()
                for fold, roster in group:
                    symbol = roster["symbol"]
                    folds.add(fold)
                    key = day, symbol
                    if key not in pre:
                        pre[key] = _plan(reader, symbol, day, invented, audit)
                    symbols[symbol] = pre[key]
                wire.require(len(symbols) == 20 and len(folds) == 1, "EJ shock original date roster differs")
                days.append((day, folds.pop(), symbols))
            plans[scenario] = {day: symbols for day, _, symbols in days}
            seals[scenario] = laggard._seal(slot, scenario, days, audit)

        trades = {}
        for scenario, rows in rosters.items():
            result = []
            persisted = stored[scenario] if audit else [None] * len(rows)
            for (fold, roster), previous in zip(rows, persisted, strict=True):
                day, symbol = roster["session"], roster["symbol"]
                plan = plans[scenario][day][symbol]
                trade = None
                if plan["rank_state"] == "select" and plan["order_state"] == "ready":
                    key = day, symbol
                    if key not in trades:
                        offset = plan["decision_offset"]
                        session = _project(reader, symbol, day, tuple(range(offset + 1, offset + 32)), "CC", invented, audit)
                        decision = datetime.fromisoformat(plan["decision_at"])
                        limit, stop = (Fraction(plan[k]) for k in ("limit", "stop"))
                        trades[key] = execute(symbol, decision, limit, stop, session)
                    trade = trades[key]
                rebuilt = _row(day, fold, roster, plan, trade)
                if audit:
                    wire.require(wire.encode(rebuilt) == wire.encode(previous), "EJ shock audited trade differs")
                result.append(rebuilt)
            comparison = cash_summary.summarize(result)
            cash_reference.verify_summary(result, comparison)
            wire.require(tuple(comparison[k] for k in ("coordinates", "sessions", "weeks")) == (3140, 157, 34),
                         "EJ shock original evaluation geometry differs")
            reports[scenario] = {"selection_seal": seals[scenario],
                                 "selection": support.selection_summary(result, audit=audit),
                                 "execution": payoff_support.execution_summary(result, audit=audit),
                                 "comparison": comparison}
            outputs[scenario] = result
    return reports, outputs
