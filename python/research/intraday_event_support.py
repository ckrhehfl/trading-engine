"""EL input support census; no prices, event selection, labels or trading.

The caller authenticates the original CB/CC/CX bytes and gates every read with
the registered lifecycle. This module preserves both original CB populations.
Minute support is frozen at minute end + two minutes: late repair is excluded.
Full-day counts are offline diagnostics, never decision-time model inputs.
"""

from collections import Counter, defaultdict
from hashlib import sha256
import os
from pathlib import Path
import re

from research import intraday_campaign_wsl_transport as wire
from research import intraday_ed_sources as source
from research.intraday_ed_inputs import MINUTE, SessionInputs


OUTPUT = "el-event-support.jsonl"
SUMMARY = "el-event-support-summary.json"
SCHEMA = "EL-original-input-support-v1"
MAX_LINE = 256 * 1024
MAX_BYTES = 512 * wire.MIB
CASE_LIMIT = 2


def _population(universe):
    days = universe.sessions
    wire.require(days and tuple(sorted(set(days))) == days and len(days) <= 257,
                 "EL original calendar differs")
    groups, ranks, identities = defaultdict(list), defaultdict(list), set()
    for target in universe.targets:
        key = target.scenario, target.session, target.symbol
        wire.require(target.scenario in {"S1", "S2"} and target.session in days
                     and re.fullmatch(r"KRX:[0-9A-Z]{6}", target.symbol) is not None
                     and type(target.rank) is int and type(target.metadata) is bytes
                     and key not in identities, "EL original target identity differs")
        wire.require((target.stored_row_count is None and target.state == "no_stored_rows_unknown_reason")
                     or (type(target.stored_row_count) is int and target.stored_row_count > 0
                         and target.state == "stored_rows_completeness_unverified"),
                     "EL original stored count state differs")
        identities.add(key)
        groups[target.session, target.symbol].append(target)
        ranks[target.scenario, target.session].append(target.rank)
    wire.require(set(ranks) == {(clock, day) for clock in ("S1", "S2") for day in days}
                 and all(sorted(values) == list(range(1, 21)) for values in ranks.values())
                 and len(groups) <= 5158, "EL original population lost targets")
    return groups


def source_metadata(chunk):
    """Only source declarations/counts and a raw-byte pin; no numeric payload."""
    if chunk is None:
        return None
    buckets = Counter(row["open_time_ms"] // 60000 for row in chunk["rows"])
    fetched = [row["fetched_at"] for row in chunk["rows"]]
    return {"metadata": {key: value for key, value in chunk.items() if key not in {"rows", "daily_rows"}},
            "raw_chunk_sha256": sha256(wire.encode(chunk)).hexdigest(),
            "minute_rows": len(chunk["rows"]), "minute_buckets": len(buckets),
            "collision_buckets": sum(count > 1 for count in buckets.values()),
            "daily_rows": len(chunk.get("daily_rows", [])),
            "fetched_at_missing_rows": fetched.count(None),
            "fetched_at_distinct_nonnull": len(set(fetched) - {None})}


def session_support(session: SessionInputs, price, volume):
    """Counts use exact expected minutes, including absent rows and short days.

    Observed/invalid/collision are data states; timely support is a separate
    count. Complete bars require every minute on its original deadline. The
    first60 obligation stays 60 even for an invented session shorter than 60.
    Decision masks use completed opening-aligned 5m ends + 2m before close.
    """
    length = (session.closing - session.opening) // MINUTE
    wire.require(0 < length <= 600 and session.closing == session.opening + length * MINUTE,
                 "EL invalid session duration")
    row_counts = [{at: count for at, count in Counter(
        row["open_time_ms"] // 60000 for row in chunk["rows"]).items()}
        if chunk is not None else {} for chunk in (price, volume)]
    pc, vc = Counter(dict.fromkeys(("observed", "missing", "invalid", "collision"), 0)), Counter(
        dict.fromkeys(("positive", "known_zero", "missing", "invalid", "collision"), 0))
    prices, volumes, late_price, late_volume = [], [], 0, 0
    for offset in range(length):
        at = session.opening + offset * MINUTE
        minute = session.minutes.get(at)
        counts = [rows.get(int(at.timestamp()) // 60, 0) for rows in row_counts]
        states = []
        for channel, count in enumerate(counts):
            if count > 1:
                state = "collision"
            elif not count:
                state = "missing"
            elif minute is None or (minute.price_state if channel == 0 else minute.volume_state) != "observed":
                state = "invalid"
            elif channel == 0:
                state = "observed"
            else:
                state = "known_zero" if minute.shares == 0 else "positive"
            states.append(state)
        pc[states[0]] += 1
        vc[states[1]] += 1
        timely = False
        if minute is not None:
            wire.require(minute.symbol == session.symbol and minute.start_at == at
                         and minute.available_at.utcoffset() is not None
                         and minute.available_at >= at + MINUTE, "EL impossible minute clock")
            timely = minute.available_at <= at + 3 * MINUTE
        p, v = states[0] == "observed", states[1] in {"positive", "known_zero"}
        late_price += int(p and not timely)
        late_volume += int(v and not timely)
        prices.append(p and timely)
        volumes.append(v and timely)
    longest = run = 0
    for supported in prices:
        run = run + 1 if supported else 0
        longest = max(longest, run)
    prefix = next((i for i, supported in enumerate(prices) if not supported), length)
    masks = []
    for end in range(5, length + 1, 5):
        decision = session.opening + (end + 2) * MINUTE
        if decision >= session.closing:
            continue
        masks.append({"end_offset": end, "decision_at": decision.isoformat(),
                      "price5": all(prices[end - 5:end]),
                      "price15": end >= 15 and all(prices[end - 15:end]),
                      "volume5": all(volumes[end - 5:end]),
                      "volume15": end >= 15 and all(volumes[end - 15:end]),
                      "price_prefix": all(prices[:end]), "volume_prefix": all(volumes[:end]),
                      "first60_price": end >= 60 and all(prices[:60])})
    return {"expected_minutes": length, "price": dict(pc), "volume": dict(vc),
            "price_late": late_price, "volume_late": late_volume,
            "price_supported": sum(prices), "volume_supported": sum(volumes),
            "first60_price_minutes": sum(prices[:60]), "first60_volume_minutes": sum(volumes[:60]),
            "first60_price_complete": length >= 60 and all(prices[:60]),
            "first60_volume_complete": length >= 60 and all(volumes[:60]),
            "complete_price_session": all(prices), "complete_volume_session": all(volumes),
            "price_prefix_minutes": prefix, "longest_continuous_price_minutes": longest,
            "bars": {str(width): {"expected": length // width,
                "price_complete": sum(all(prices[i:i + width]) for i in range(0, length - width + 1, width)),
                "volume_complete": sum(all(volumes[i:i + width]) for i in range(0, length - width + 1, width))}
                for width in (5, 15)}, "decision_masks": masks}


def records(universe, reader):
    groups = _population(universe)
    previous = {}
    for day_index, day in enumerate(universe.sessions):
        current = {}
        for _, symbol in sorted(key for key in groups if key[0] == day):
            price, volume = (reader.load_raw_chunk(kind, symbol, day) for kind in ("CC", "CX"))
            session = source.session_inputs(symbol, day, price, volume).session
            support = session_support(session, price, volume)
            prior = {"session": universe.sessions[day_index - 1] if day_index else None,
                     "in_original_union": symbol in previous if day_index else None,
                     "complete_price_session": previous.get(symbol), "economic_price_basis": "unknown"}
            targets = [{"scenario": t.scenario, "rank": t.rank, "state": t.state,
                        "stored_row_count": t.stored_row_count, "metadata_hex": t.metadata.hex()}
                       for t in sorted(groups[day, symbol], key=lambda t: t.scenario)]
            current[symbol] = support["complete_price_session"]
            reader.assert_unchanged()
            yield {"kind": "coordinate", "session": day, "symbol": symbol, "targets": targets,
                   "sources": {"CC": source_metadata(price), "CX": source_metadata(volume)},
                   "support": support, "previous_session": prior}
        previous = current


def _summary(rows, universe):
    counts = {clock: Counter() for clock in ("union", "S1", "S2")}
    cases = defaultdict(list)
    for row in rows:
        support = row["support"]
        status = ("complete_price_and_volume" if support["complete_price_session"] and support["complete_volume_session"]
                  else "complete_price" if support["complete_price_session"]
                  else "no_supported_price" if not support["price_supported"] else "partial_price")
        for clock in ("union", *(target["scenario"] for target in row["targets"])):
            counts[clock]["coordinates"] += 1
            counts[clock][status] += 1
            counts[clock]["first60_price_complete"] += int(support["first60_price_complete"])
            counts[clock]["first60_volume_complete"] += int(support["first60_volume_complete"])
            counts[clock]["previous_complete_price"] += int(row["previous_session"]["complete_price_session"] is True)
            counts[clock].update(support["totals"])
        if len(cases[status]) < CASE_LIMIT:
            cases[status].append({"session": row["session"], "symbol": row["symbol"]})
    return {"schema": SCHEMA, "sessions": len(universe.sessions), "counts": {k: dict(v) for k, v in counts.items()},
            "cases": dict(cases), "case_selection": "lexicographic first two per support status; no price or outcome selection",
            "hypotheses": {"concentrated_selloff_failure": "price and stored bar-volume support only; aggressor sales unavailable",
                "known_low_breakdown_reclaim": "intraday completed-price support only; prior-session price basis remains unknown",
                "first_pullback_resumption": "completed-price support only; event definition not evaluated"},
            "unsupported": ["quotes", "spread", "depth", "individual_trades", "aggressor_side", "news", "daily_price_source", "cross_session_economic_price_basis"],
            "unverified": ["historical_availability", "complete_exchange_calendar", "physical_volume_units", "price_basis", "corporate_actions", "source_finality_and_revisions"],
            "availability": "assumed minute end+2; late repair excluded; retrieved_at is not historical availability",
            "decision_grid": "opening-aligned completed5m end+2 strictly before session close",
            "claim": "offline input support only; no events, signals, forecasts, returns or execution certification"}


def compact_support(support):
    totals = {key: support[key] for key in ("expected_minutes", "price_supported", "volume_supported",
        "price_late", "volume_late", "first60_price_minutes", "first60_volume_minutes")}
    totals.update({channel + "_" + state + "_minutes": count
                   for channel in ("price", "volume") for state, count in support[channel].items()})
    totals.update({"bar" + width + "_" + key: count
                   for width, values in support["bars"].items() for key, count in values.items()})
    totals["decision_frames"] = len(support["decision_masks"])
    totals.update({"decision_" + key: sum(mask[key] for mask in support["decision_masks"])
        for key in ("price5", "price15", "volume5", "volume15", "price_prefix", "volume_prefix", "first60_price")})
    return {**{key: support[key] for key in ("complete_price_session", "complete_volume_session",
        "price_supported", "first60_price_complete", "first60_volume_complete")}, "totals": totals}


def _write_or_check(path, rows, *, audit):
    wire.safe_path(path)
    before = wire.file_identity(wire.regular(path)) if audit else None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW if audit else
                         os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    digest, size, count = sha256(), 0, 0
    with os.fdopen(descriptor, "rb" if audit else "wb") as stream:
        for row in rows:
            raw = wire.encode(row)
            size += len(raw)
            count += 1
            wire.require(len(raw) <= MAX_LINE and size <= MAX_BYTES and count <= 6002, "EL output bound exceeded")
            if audit:
                wire.require(stream.readline(MAX_LINE + 1) == raw, "EL independent saved support differs")
            else:
                wire.write_all(stream, raw)
            digest.update(raw)
        if audit:
            wire.require(stream.read(1) == b"" and wire.file_identity(os.fstat(stream.fileno())) == before,
                         "EL support EOF or file identity differs")
        else:
            stream.flush()
            os.fsync(stream.fileno())
    if audit:
        wire.require(wire.file_identity(wire.regular(path)) == before, "EL saved support replaced")
    else:
        wire.fsync_directory(path.parent)
    return {"filename": path.name, "sha256": digest.hexdigest(), "bytes": size, "rows": count}


def build_support(slot: Path, universe: source.UniverseCalendar, reader, *, audit=False):
    """Write or independently compare every coordinate and the saved summary.

    Whole input authentication/full actual geometry and STARTED are caller
    obligations. Tiny invented calendars retain twenty names per timing arm.
    """
    wire.require(type(audit) is bool, "EL explicit audit mode required")
    _population(universe)
    if audit:
        from research import intraday_event_support_reference as reference
        generate, summarize, compact_support_values = reference.records, reference.summary, reference.compact_support
    else:
        generate, summarize, compact_support_values = records, _summary, compact_support
    # Only compact support summaries survive a coordinate. Raw sessions do not.
    compact = []

    def output_rows():
        yield {"kind": "header", "schema": SCHEMA, "universe_sha256": universe.raw_sha256,
               "calendar_sha256": sha256(wire.encode(universe.sessions)).hexdigest()}
        for row in generate(universe, reader):
            compact.append({"session": row["session"], "symbol": row["symbol"], "targets": row["targets"],
                "support": compact_support_values(row["support"]),
                "previous_session": row["previous_session"]})
            yield row
        yield {"kind": "footer", "coordinates": len(compact)}

    seal = _write_or_check(slot / OUTPUT, output_rows(), audit=audit)
    result = summarize(compact, universe)
    result["records"] = seal
    summary_seal = _write_or_check(slot / SUMMARY, [result], audit=audit)
    return {**result, "summary_file": summary_seal}
