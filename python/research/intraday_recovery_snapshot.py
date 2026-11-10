"""EK source-neutral snapshot: causal first-hour decisions sealed before labels."""

from collections import Counter
from datetime import datetime
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from itertools import groupby
import json
import os

from research import intraday_campaign_wsl_transport as wire
from research import intraday_candidate_laggard_selection as controls
from research import intraday_ed_sources as sources
from research.intraday_ed_inputs import MINUTE, Minute, SessionInputs
from research.intraday_ed_snapshot_verify import reconstruct_reference_session
from research.intraday_pattern_calibration import session_close, session_open
from research.intraday_trader_inputs import _groups


FEATURES = "ek-features.jsonl"
HOLDING_FEATURES = "ek-holding-features.jsonl"
LABELS = "ek-labels.jsonl"
UPDATES = (75, 90, 105)
MAX_LINE = 2 * wire.MIB


def _session(reader, symbol, day, end, audit):
    opening = session_open(day)
    starts = tuple(opening + i * MINUTE for i in range(end))
    price = reader.load_raw_chunk("CC", symbol, day)
    volume = reader.load_raw_chunk("CX", symbol, day)
    if audit:
        return reconstruct_reference_session(symbol, day, price, volume, requested_starts=starts)
    return sources.session_inputs(symbol, day, price, volume, requested_starts=starts).session


def _target(target):
    return {"scenario": target.scenario, "rank": target.rank, "state": target.state,
            "stored_row_count": target.stored_row_count, "metadata_hex": target.metadata.hex()}


def _rows(path):
    """Bounded complete JSON-lines reader; caller authenticates its file seal."""
    wire.regular(path)
    with path.open("rb") as stream:
        while raw := stream.readline(MAX_LINE + 1):
            wire.require(len(raw) <= MAX_LINE and raw.endswith(b"\n"), "EK invalid bounded row")
            row = json.loads(raw)
            wire.require(wire.encode(row) == raw, "EK noncanonical row")
            yield row


def _write_or_check(path, rows, *, audit):
    digest, size, count = sha256(), 0, 0
    previous = iter(_rows(path)) if audit else None
    stream = None if audit else path.open("xb")
    try:
        if stream is not None:
            os.fchmod(stream.fileno(), 0o600)
        for row in rows:
            raw = wire.encode(row)
            wire.require(len(raw) <= MAX_LINE, "EK row exceeds bound")
            if audit:
                wire.require(next(previous, None) == row, "EK independent snapshot differs")
            else:
                wire.write_all(stream, raw)
            digest.update(raw)
            size += len(raw)
            count += 1
        if audit:
            wire.require(next(previous, None) is None, "EK unexpected extra row")
        else:
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        if stream is not None:
            stream.close()
    return {"sha256": digest.hexdigest(), "size": size, "rows": count}


def _feature_rows(universe, reader, audit):
    from research import intraday_recovery_features as feature
    from research import intraday_recovery_features_reference as reference
    from research import intraday_candidate_laggard_path as old_path

    build = reference.build_features if audit else feature.build_features
    peers = reference.build_all_peers if audit else feature.build_all_peers
    select = controls.reference_day if audit else controls.select_day
    classify = old_path.reference_classify if audit else old_path.classify
    groups = _groups(universe)
    for day, keys in groupby(sorted(groups), key=lambda key: key[0]):
        keys = tuple(keys)
        sessions = {symbol: _session(reader, symbol, day, 60, audit) for _, symbol in keys}
        rosters = {scenario: {symbol: sessions[symbol] for _, symbol in keys
                             if any(t.scenario == scenario for t in groups[day, symbol])}
                   for scenario in ("S1", "S2")}
        plans = {scenario: select(members) for scenario, members in rosters.items()}
        peer_rows = {scenario: peers(members) for scenario, members in rosters.items()}
        for _, symbol in keys:
            targets = groups[day, symbol]
            own = build(sessions[symbol])
            per_scenario = {}
            for target in targets:
                plan = plans[target.scenario]
                rank = plan["symbols"][symbol]
                per_scenario[target.scenario] = {
                    "rank_control": rank, "observed": plan["observed"],
                    "weak_control": classify(symbol, datetime.fromisoformat(plan["decision_at"]), rank,
                                             sessions[symbol])["state"],
                    "median_return": plan["median_return"],
                    "peer": peer_rows[target.scenario][symbol]}
            yield {"key": day + "/" + symbol, "session": day, "symbol": symbol,
                   "targets": [_target(t) for t in sorted(targets, key=lambda t: t.scenario)],
                   "own": own, "scenarios": per_scenario}


def _holding_prices(session, quote):
    result = []
    for offset in (*range(45, 60), *range(63, 124)):
        start = session.opening + offset * MINUTE
        if quote(session,start) is not None:
            result.append([offset,[str(value) for value in session.minutes[start].ohlc]])
    return result


def holding_session(row):
    """Restore the exact replay tape from the separately sealed outcomes."""
    day, symbol = row["session"], row["symbol"]
    opening, closing = session_open(day), session_close(day)
    minutes = {}
    for offset, price in row["holding_prices"]:
        start = opening + offset * MINUTE
        wire.require(start not in minutes and 45 <= offset <= 123, "EK invalid holding tape clock")
        values = tuple(map(Decimal,price))
        wire.require(len(values) == 4 and all(value.is_finite() and value > 0 for value in values)
                     and list(map(str,values)) == price,"EK holding tape price differs")
        minutes[start] = Minute(symbol, start, start+3*MINUTE, values, None, "observed", "missing")
    return SessionInputs(symbol, opening, closing, minutes)


def _geometry(own):
    return (own["limit"] is not None and own["stop"] is not None
            and Fraction(own["limit"]) > Fraction(own["stop"]))


def _check_seal(path, pin):
    wire.require(wire.file_digest(path, 512 * wire.MIB, lambda _: None)
                 == (pin["sha256"], pin["size"]), "EK feature seal changed")


def _held_rows(slot, reader, feature_pin, audit):
    from research import intraday_recovery_features as feature
    from research import intraday_recovery_features_reference as reference

    _check_seal(slot / FEATURES, feature_pin)
    build = reference.build_features if audit else feature.build_features
    peers = reference.build_all_peers if audit else feature.build_all_peers
    for day, batch in groupby(_rows(slot / FEATURES), key=lambda row: row["session"]):
        rows = tuple(batch)
        sessions = {row["symbol"]: _session(reader,row["symbol"],day,max(UPDATES),audit) for row in rows}
        rosters = {scenario:{row["symbol"]:sessions[row["symbol"]] for row in rows
                            if scenario in row["scenarios"]} for scenario in ("S1","S2")}
        peer_rows = {end: {scenario: peers(members, end_offset=end) for scenario,members in rosters.items()}
                     for end in UPDATES}
        for row in rows:
            own, session = row["own"], sessions[row["symbol"]]
            updates = [{"end_offset":end,"features":build(session,end_offset=end,remaining_minutes=120-end),
                        "peer":{s:peer_rows[end][s][row["symbol"]] for s in row["scenarios"]}}
                       for end in UPDATES] if _geometry(own) else []
            yield {"key":row["key"],"session":day,"symbol":row["symbol"],"holding_queries":updates}


def _label_rows(slot, reader, feature_pin, audit):
    from research import intraday_recovery_payoff as payoff
    from research.intraday_candidate_exit import _minute

    _check_seal(slot / FEATURES, feature_pin)
    evaluate = payoff.reference_evaluate if audit else payoff.evaluate
    quote = payoff._reference_bar if audit else _minute
    for row in _rows(slot / FEATURES):
        own = row["own"]
        outcome = {"status":"no_valid_initial_geometry", "baseline":None, "path":None, "gross_pct":None}
        updates, tape = [], []
        if _geometry(own):
            session = _session(reader,row["symbol"],row["session"],124,audit)
            outcome = evaluate(session,Fraction(own["limit"]),Fraction(own["stop"]))
            baseline = outcome["baseline"]
            if baseline["entry_at"] is not None:
                tape = _holding_prices(session, quote)
            for end in UPDATES:
                execution_at = session.opening+(end+3)*MINUTE
                future_open = quote(session,execution_at)
                residual = None
                if (baseline["status"] == "closed" and baseline["exit_at"] > execution_at.isoformat()
                        and future_open is not None):
                    residual = str(100*(Fraction(baseline["exit_price"])/Fraction(future_open[0])-1))
                updates.append({"end_offset":end,"remaining_gross_pct":residual})
        yield {"key":row["key"], "session":row["session"], "symbol":row["symbol"],
               "outcome":outcome, "holding_queries":updates, "holding_prices":tape}


def build_snapshot(slot, universe, reader, *, binding, audit=False):
    """All original rows are preserved; the complete initial seal precedes labels."""
    groups = _groups(universe)
    header = {"schema": "EK-chart-recovery-snapshot-v1", "binding": binding,
              "sessions": list(universe.sessions), "universe_sha256": universe.raw_sha256,
              "coordinates": len(groups),
              "scenario_coordinates": dict(Counter(t.scenario for t in universe.targets))}
    feature_pin = _write_or_check(slot / FEATURES, _feature_rows(universe, reader, audit), audit=audit)
    wire.require(feature_pin["rows"] == len(groups), "EK feature population differs")
    # Durable completion of the initial projection is a gate, not an in-memory flag.
    feature_seal = {"header": header, "features": feature_pin}
    seal_path = slot / "feature-seal.json"
    if audit:
        wire.require(json.loads(seal_path.read_bytes()) == feature_seal, "EK original feature seal differs")
    else:
        wire.exclusive_bytes(seal_path, wire.encode(feature_seal))
    holding_pin = _write_or_check(slot / HOLDING_FEATURES, _held_rows(slot, reader, feature_pin, audit), audit=audit)
    wire.require(holding_pin["rows"] == len(groups), "EK held feature population differs")
    label_pin = _write_or_check(slot / LABELS, _label_rows(slot, reader, feature_pin, audit), audit=audit)
    wire.require(label_pin["rows"] == len(groups), "EK label population differs")
    reader.assert_unchanged()
    return {**feature_seal, "holding_features": holding_pin, "labels": label_pin}
