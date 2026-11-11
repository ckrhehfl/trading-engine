"""Independent EL support arithmetic over independently reconstructed raw rows.

No production support helper, selector, event detector or model is imported.
The raw framing, immutable input types, calendar and canonical codec are trusted
input/serialization boundaries, not an oracle for expected support values.
"""

from hashlib import sha256
import itertools

from research import intraday_campaign_wsl_transport as wire
from research.intraday_ed_inputs import MINUTE
from research.intraday_ed_snapshot_verify import reconstruct_reference_session
from research.intraday_pattern_calibration import session_open, session_close


def source_metadata(chunk):
    if chunk is None:
        return None
    bins = {}
    missing, retrievals = 0, set()
    for row in chunk["rows"]:
        minute = row["open_time_ms"] // 60000
        bins[minute] = bins.get(minute, 0) + 1
        if row["fetched_at"] is None:
            missing += 1
        else:
            retrievals.add(row["fetched_at"])
    return {"metadata": dict((key, value) for key, value in chunk.items() if key not in ("rows", "daily_rows")),
            "raw_chunk_sha256": sha256(wire.encode(chunk)).hexdigest(), "minute_rows": len(chunk["rows"]),
            "minute_buckets": len(bins), "collision_buckets": len([n for n in bins.values() if n > 1]),
            "daily_rows": len(chunk.get("daily_rows", [])), "fetched_at_missing_rows": missing,
            "fetched_at_distinct_nonnull": len(retrievals)}


def session_support(session, price, volume):
    expected = int((session.closing - session.opening).total_seconds() // 60)
    wire.require(1 <= expected <= 600 and session.closing == session.opening + expected * MINUTE,
                 "EL reference session duration differs")
    price_counts = dict.fromkeys(("observed", "missing", "invalid", "collision"), 0)
    volume_counts = dict.fromkeys(("positive", "known_zero", "missing", "invalid", "collision"), 0)
    bins = []
    for chunk in (price, volume):
        counts = {}
        for row in chunk["rows"] if chunk else ():
            stamp = row["open_time_ms"] // 60000
            counts[stamp] = counts.get(stamp, 0) + 1
        bins.append(counts)
    available_price, available_volume = set(), set()
    price_late = volume_late = 0
    for index in range(expected):
        at = session.opening + index * MINUTE
        value = session.minutes.get(at)
        states = []
        for channel in range(2):
            n = bins[channel].get(int(at.timestamp()) // 60, 0)
            state = "collision" if n > 1 else "missing" if n == 0 else "invalid"
            if n == 1 and value is not None:
                observed = value.price_state == "observed" if channel == 0 else value.volume_state == "observed"
                if observed:
                    state = "observed" if channel == 0 else "positive" if value.shares != 0 else "known_zero"
            states.append(state)
        price_counts[states[0]] += 1
        volume_counts[states[1]] += 1
        on_time = False
        if value is not None:
            wire.require(value.start_at == at and value.symbol == session.symbol
                         and value.available_at.utcoffset() is not None and value.available_at >= at + MINUTE,
                         "EL reference impossible minute clock")
            on_time = value.available_at <= at + 3 * MINUTE
        if states[0] == "observed":
            if on_time:
                available_price.add(index)
            else:
                price_late += 1
        if states[1] in ("known_zero", "positive"):
            if on_time:
                available_volume.add(index)
            else:
                volume_late += 1

    def complete(supported, start, end):
        return start >= 0 and end <= expected and all(i in supported for i in range(start, end))

    prefix = 0
    while prefix in available_price:
        prefix += 1
    runs = [len(list(values)) for present, values in itertools.groupby(range(expected), key=available_price.__contains__) if present]
    bars = {}
    for width in (5, 15):
        p = v = 0
        for end in range(width, expected + 1, width):
            p += complete(available_price, end - width, end)
            v += complete(available_volume, end - width, end)
        bars[str(width)] = {"expected": expected // width, "price_complete": p, "volume_complete": v}
    masks = []
    for end in range(5, expected + 1, 5):
        decision = session.opening + MINUTE * (end + 2)
        if decision < session.closing:
            masks.append({"end_offset": end, "decision_at": decision.isoformat(),
                "price5": complete(available_price, end - 5, end),
                "price15": complete(available_price, end - 15, end),
                "volume5": complete(available_volume, end - 5, end),
                "volume15": complete(available_volume, end - 15, end),
                "price_prefix": complete(available_price, 0, end),
                "volume_prefix": complete(available_volume, 0, end),
                "first60_price": end >= 60 and complete(available_price, 0, 60)})
    return {"expected_minutes": expected, "price": price_counts, "volume": volume_counts,
            "price_late": price_late, "volume_late": volume_late,
            "price_supported": len(available_price), "volume_supported": len(available_volume),
            "first60_price_minutes": len(available_price.intersection(range(60))),
            "first60_volume_minutes": len(available_volume.intersection(range(60))),
            "first60_price_complete": complete(available_price, 0, 60),
            "first60_volume_complete": complete(available_volume, 0, 60),
            "complete_price_session": len(available_price) == expected,
            "complete_volume_session": len(available_volume) == expected,
            "price_prefix_minutes": prefix, "longest_continuous_price_minutes": max(runs, default=0),
            "bars": bars, "decision_masks": masks}


def records(universe, reader):
    coordinates = {}
    for target in universe.targets:
        coordinates.setdefault((target.session, target.symbol), []).append(target)
    prior = {}
    last_day = None
    for day in universe.sessions:
        this_day = {}
        for session_day, symbol in sorted(coordinates):
            if session_day != day:
                continue
            price = reader.load_raw_chunk("CC", symbol, day)
            volume = reader.load_raw_chunk("CX", symbol, day)
            opening, closing = session_open(day), session_close(day)
            requested = tuple(opening + i * MINUTE for i in range((closing - opening) // MINUTE))
            rebuilt = reconstruct_reference_session(symbol, day, price, volume, requested_starts=requested)
            support = session_support(rebuilt, price, volume)
            targets = [{"scenario": t.scenario, "rank": t.rank, "state": t.state,
                        "stored_row_count": t.stored_row_count, "metadata_hex": t.metadata.hex()}
                       for t in sorted(coordinates[day, symbol], key=lambda item: item.scenario)]
            this_day[symbol] = support["complete_price_session"]
            reader.assert_unchanged()
            yield {"kind": "coordinate", "session": day, "symbol": symbol, "targets": targets,
                   "sources": {"CC": source_metadata(price), "CX": source_metadata(volume)},
                   "support": support, "previous_session": {"session": last_day,
                       "in_original_union": symbol in prior if last_day is not None else None,
                       "complete_price_session": prior.get(symbol), "economic_price_basis": "unknown"}}
        prior, last_day = this_day, day


def compact_support(value):
    totals = {}
    for field in ("expected_minutes", "price_supported", "volume_supported", "price_late",
                  "volume_late", "first60_price_minutes", "first60_volume_minutes"):
        totals[field] = value[field]
    for channel in ("price", "volume"):
        for state in value[channel]:
            totals[f"{channel}_{state}_minutes"] = value[channel][state]
    for width in (5, 15):
        for field in ("expected", "price_complete", "volume_complete"):
            totals[f"bar{width}_{field}"] = value["bars"][str(width)][field]
    totals["decision_frames"] = 0
    for field in ("price5", "price15", "volume5", "volume15", "price_prefix", "volume_prefix", "first60_price"):
        totals["decision_" + field] = 0
    for mask in value["decision_masks"]:
        totals["decision_frames"] += 1
        for field in mask:
            if field in ("end_offset", "decision_at"):
                continue
            totals["decision_" + field] += int(mask[field])
    result = {field: value[field] for field in ("complete_price_session", "complete_volume_session",
        "price_supported", "first60_price_complete", "first60_volume_complete")}
    result["totals"] = totals
    return result


def summary(rows, universe):
    counts = {name: {} for name in ("union", "S1", "S2")}
    cases = {}
    for row in rows:
        value = row["support"]
        if value["complete_price_session"]:
            state = "complete_price_and_volume" if value["complete_volume_session"] else "complete_price"
        else:
            state = "partial_price" if value["price_supported"] > 0 else "no_supported_price"
        for name in ["union"] + [item["scenario"] for item in row["targets"]]:
            count = counts[name]
            for key, increment in (("coordinates", 1), (state, 1),
                ("first60_price_complete", int(value["first60_price_complete"])),
                ("first60_volume_complete", int(value["first60_volume_complete"])),
                ("previous_complete_price", int(row["previous_session"]["complete_price_session"] is True))):
                count[key] = count.get(key, 0) + increment
            for key, increment in value["totals"].items():
                count[key] = count.get(key, 0) + increment
        chosen = cases.setdefault(state, [])
        if len(chosen) < 2:
            chosen.append({"session": row["session"], "symbol": row["symbol"]})
    return {"schema": "EL-original-input-support-v1", "sessions": len(universe.sessions), "counts": counts,
            "cases": cases, "case_selection": "lexicographic first two per support status; no price or outcome selection",
            "hypotheses": {"concentrated_selloff_failure": "price and stored bar-volume support only; aggressor sales unavailable",
                "known_low_breakdown_reclaim": "intraday completed-price support only; prior-session price basis remains unknown",
                "first_pullback_resumption": "completed-price support only; event definition not evaluated"},
            "unsupported": ["quotes", "spread", "depth", "individual_trades", "aggressor_side", "news", "daily_price_source", "cross_session_economic_price_basis"],
            "unverified": ["historical_availability", "complete_exchange_calendar", "physical_volume_units", "price_basis", "corporate_actions", "source_finality_and_revisions"],
            "availability": "assumed minute end+2; late repair excluded; retrieved_at is not historical availability",
            "decision_grid": "opening-aligned completed5m end+2 strictly before session close",
            "claim": "offline input support only; no events, signals, forecasts, returns or execution certification"}
