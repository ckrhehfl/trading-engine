"""Independently inspect the local, one-hour KRX market-data receipt journal.

The KIS examples define H0STCNT0 and H0STASP0. The current trade/quote
widths are 47/63. The separately documented 46/59-field layouts have the
same audited prefix. No connection or trading dependency is imported here.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat


KST = timezone(timedelta(hours=9))
NS = 1_000_000_000
MINUTE_NS = 60 * NS
SYMBOLS = ("005930", "000660")
TRADE, QUOTE = "H0STCNT0", "H0STASP0"
WIDTHS = {TRADE: (46, 47), QUOTE: (59, 63)}
MAX_BYTES = 512 * 1024 * 1024
MAX_LINE = 2 * 1024 * 1024
MAX_AGE_NS = 3 * NS
MIN_JOINT_MINUTES = 54
MIN_SIDE_PERCENT = 95
MIN_PAIR_PERCENT = 90
SUBSCRIPTIONS = {(tr, symbol) for tr in WIDTHS for symbol in SYMBOLS}
DISCONNECT_REASONS = {"network_failure", "network_timeout", "ack_timeout", "window_end"}
END_STATUSES = {"complete", "incomplete", "failed"}


@dataclass
class SymbolStats:
    rows: Counter = field(default_factory=Counter)
    issues: Counter = field(default_factory=Counter)
    minutes: dict = field(default_factory=lambda: {TRADE: [0] * 60, QUOTE: [0] * 60, "paired": [0] * 60})
    last_exchange: dict = field(default_factory=dict)
    last_received: dict = field(default_factory=dict)
    max_receive_gap: dict = field(default_factory=dict)
    max_exchange_to_receive: dict = field(default_factory=dict)
    recent_quotes: list = field(default_factory=list)
    last_trade: tuple | None = None


def _require(condition, reason):
    if not condition:
        raise ValueError(reason)


def _strict_object(pairs):
    doc = dict(pairs)
    _require(len(doc) == len(pairs), "duplicate JSON keys")
    return doc


def _reject_constant(_):
    raise ValueError("non-finite JSON constant")


def _second(hour):
    _require(len(hour) == 6 and hour.isascii() and hour.isdigit(), "invalid exchange time")
    hours, minutes, seconds = int(hour[:2]), int(hour[2:4]), int(hour[4:])
    _require(hours < 24 and minutes < 60 and seconds < 60, "invalid exchange time")
    return hours * 3600 + minutes * 60 + seconds


def _number(value):
    _require(0 < len(value) <= 20 and value.isascii() and value.isdigit(), "invalid integer quantity or price")
    return int(value)


def _window(start):
    keys = {"event", "session", "symbols", "start_ns", "end_ns", "received_ns"}
    _require(type(start) is dict and set(start) == keys and start["event"] == "start", "missing journal start")
    session = start["session"]
    _require(type(session) is str, "invalid session")
    try:
        day = date.fromisoformat(session)
    except ValueError:
        raise ValueError("invalid session date") from None
    _require(day.isoformat() == session and day.weekday() < 5 and start["symbols"] == list(SYMBOLS), "invalid session scope")
    midnight = int(datetime.combine(day, time.min, KST).timestamp()) * NS
    first = midnight + 9 * 3600 * NS
    last = first + 60 * MINUTE_NS
    _require(all(type(start[key]) is int for key in ("start_ns", "end_ns", "received_ns"))
             and (start["start_ns"], start["end_ns"]) == (first, last), "invalid session window")
    _require(first - 2 * MINUTE_NS <= start["received_ns"] < last, "invalid start receipt")
    return session, midnight, first, last


def _observe_clock(stats, tr, receipt_ns, exchange_ns, connection):
    prior = stats.last_exchange.get(tr)
    if prior is not None and exchange_ns < prior:
        stats.issues["exchange_time_regression"] += 1
        return False
    stats.last_exchange[tr] = exchange_ns
    delay = receipt_ns - exchange_ns
    stats.max_exchange_to_receive[tr] = max(stats.max_exchange_to_receive.get(tr, 0), delay)
    if delay < 0:
        stats.issues["exchange_after_receipt"] += 1
        return False
    previous = stats.last_received.get(tr)
    if previous is not None and previous[0] == connection:
        gap = receipt_ns - previous[1]
        stats.max_receive_gap[tr] = max(stats.max_receive_gap.get(tr, 0), gap)
    stats.last_received[tr] = (connection, receipt_ns)
    return True


def _remember_quote(stats, fields, exchange_ns, receipt_ns, connection):
    try:
        ask, bid, ask_qty, bid_qty = (_number(fields[index]) for index in (3, 13, 23, 33))
    except ValueError:
        stats.issues["invalid_quote_price_or_size"] += 1
        stats.recent_quotes.clear()
        return False
    if min(ask, bid, ask_qty, bid_qty) == 0:
        stats.issues["empty_quote_at_touch"] += 1
        stats.recent_quotes.clear()
        return False
    if bid >= ask:
        stats.issues["locked_or_crossed_quote"] += 1
        stats.recent_quotes.clear()
        return False
    if stats.recent_quotes and stats.recent_quotes[-1][0] == exchange_ns:
        stats.recent_quotes.pop()
    stats.recent_quotes.append((exchange_ns, receipt_ns, connection))
    if len(stats.recent_quotes) > 2:
        del stats.recent_quotes[0]
    return True


def _observe_trade(stats, fields, stamp, exchange_ns, receipt_ns, connection, minute):
    if fields[33] != stamp:
        stats.issues["wrong_trade_session"] += 1
        return
    if fields[35] not in {"N", "Y"}:
        stats.issues["unknown_halt_status"] += 1
        return
    if fields[35] == "Y":
        stats.issues["halted_trade"] += 1
        return
    try:
        price, size, cumulative = (_number(fields[index]) for index in (2, 12, 13))
        _require(min(price, size) > 0 and cumulative >= size, "invalid trade price or size")
    except ValueError:
        stats.issues["invalid_trade_price_or_size"] += 1
        return
    last = stats.last_trade
    if last is not None and last[0] == connection:
        difference = cumulative - last[1]
        if difference == 0 and fields == last[2]:
            stats.issues["duplicate_trade"] += 1
            return
        stats.rows["cumulative_checks"] += 1
        if difference < size:
            stats.issues["short_or_regressed_cumulative_delta"] += 1
        elif difference > size:
            stats.issues["excess_cumulative_delta"] += 1
            stats.rows["unreconciled_shares"] += difference - size
    stats.last_trade = (connection, cumulative, fields)
    stats.minutes[TRADE][minute] += 1
    stats.rows["trade_shares"] += size
    side = {"1": "buy", "5": "sell"}.get(fields[21], "unknown")
    stats.rows[f"{side}_trades"] += 1
    stats.rows[f"{side}_shares"] += size
    if side == "unknown":
        return
    quotes = [q for q in stats.recent_quotes if q[2] == connection]
    if quotes and quotes[-1][0] > exchange_ns:
        stats.issues["quote_exchange_ahead_of_trade"] += 1
        return
    if quotes and quotes[-1][0] == exchange_ns:
        stats.issues["same_exchange_second"] += 1
    earlier = next((q for q in reversed(quotes) if q[0] < exchange_ns and q[1] <= receipt_ns), None)
    if earlier is None:
        stats.issues["no_strictly_prior_quote"] += 1
        return
    if receipt_ns - earlier[0] > MAX_AGE_NS:
        stats.issues["stale_prior_quote"] += 1
        return
    stats.rows["paired_classified_trades"] += 1
    stats.minutes["paired"][minute] += 1


def _market(doc, session, midnight, first, last, stats_by_symbol, acked, issues, widths):
    tr, count, payload = (doc[name] for name in ("tr", "count", "payload"))
    if type(tr) is not str or tr not in WIDTHS or type(count) is not int or count <= 0 or type(payload) is not str:
        issues["invalid_market_envelope"] += 1
        return
    fields = payload.split("^")
    width, remainder = divmod(len(fields), count)
    if remainder or width not in WIDTHS[tr]:
        issues["invalid_market_width"] += 1
        return
    widths[f"{tr}:{width}"] += count
    receipt_ns = doc["received_ns"]
    if not first <= receipt_ns < last:
        issues["market_received_outside_window"] += count
        return
    connection = doc["connection"]
    for offset in range(0, len(fields), width):
        row = fields[offset:offset + width]
        symbol = row[0]
        if symbol not in stats_by_symbol:
            issues["unexpected_symbol"] += 1
            continue
        stats = stats_by_symbol[symbol]
        stats.rows[tr] += 1
        if (tr, symbol) not in acked:
            issues["market_without_prior_ack"] += 1
            continue
        try:
            exchange_ns = midnight + _second(row[1]) * NS
        except ValueError:
            stats.issues["invalid_exchange_time"] += 1
            if tr == QUOTE:
                stats.recent_quotes.clear()
            continue
        if not first <= exchange_ns < last or row[43 if tr == TRADE else 2] != "0":
            stats.issues["outside_regular_exchange_session"] += 1
            if tr == QUOTE:
                stats.recent_quotes.clear()
            continue
        if not _observe_clock(stats, tr, receipt_ns, exchange_ns, connection):
            if tr == QUOTE:
                stats.recent_quotes.clear()
            continue
        minute = (exchange_ns - first) // MINUTE_NS
        if tr == TRADE:
            _observe_trade(stats, row, session.replace("-", ""), exchange_ns, receipt_ns, connection, minute)
        elif _remember_quote(stats, row, exchange_ns, receipt_ns, connection):
            stats.minutes[QUOTE][minute] += 1


def _symbol_summary(stats):
    joint = sum(bool(a and b) for a, b in zip(stats.minutes[TRADE], stats.minutes[QUOTE]))
    known = stats.rows["buy_trades"] + stats.rows["sell_trades"]
    all_trades = known + stats.rows["unknown_trades"]
    paired = stats.rows["paired_classified_trades"]
    checks = {
        "joint_minutes": joint >= MIN_JOINT_MINUTES,
        "trade_side_coverage": all_trades > 0 and 100 * known >= MIN_SIDE_PERCENT * all_trades,
        "causal_quote_coverage": known > 0 and 100 * paired >= MIN_PAIR_PERCENT * known,
        "exchange_order": not (stats.issues["exchange_time_regression"] or stats.issues["exchange_after_receipt"]),
        "trade_volume_reconciliation": stats.rows["cumulative_checks"] > 0 and not any(stats.issues[key] for key in (
            "duplicate_trade", "short_or_regressed_cumulative_delta", "excess_cumulative_delta")),
        "identifiable_regular_data": not any(stats.issues[key] for key in (
            "invalid_exchange_time", "wrong_trade_session", "invalid_trade_price_or_size", "invalid_quote_price_or_size")),
    }
    return {"rows": dict(stats.rows), "issues": dict(stats.issues), "minute_counts": stats.minutes, "joint_minutes": joint,
            "side_known_trades": known, "side_eligible_trades": all_trades, "paired_classified_trades": paired,
            "max_inter_receipt_gap_ns": stats.max_receive_gap, "max_exchange_to_receive_ns": stats.max_exchange_to_receive,
            "checks": checks}


def inspect_journal(path: Path | str) -> dict:
    """Return a bounded, JSON-compatible quality report without exposing rows."""
    raw, canonical = hashlib.sha256(), hashlib.sha256()
    stats = {symbol: SymbolStats() for symbol in SYMBOLS}
    issues, widths, events = Counter(), Counter(), Counter()
    session = end_status = None
    acked = set()
    connection = previous_receipt = 0
    ack_before_first = 0
    active = False
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as src:
        initial = os.fstat(src.fileno())
        _require(stat.S_ISREG(initial.st_mode) and 0 < initial.st_size <= MAX_BYTES, "invalid journal file or size")
        bytes_read = 0
        while line := src.readline(MAX_LINE + 1):
            bytes_read += len(line)
            _require(len(line) <= MAX_LINE and bytes_read <= MAX_BYTES, "journal bound exceeded")
            raw.update(line)
            if not line.endswith(b"\n"):
                issues["truncated_line"] += 1
                break
            try:
                row = json.loads(line, object_pairs_hook=_strict_object, parse_constant=_reject_constant)
                stable = json.dumps(row, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
            except (ValueError, UnicodeError, RecursionError):
                issues["invalid_json"] += 1
                continue
            canonical.update(stable.encode("ascii") + b"\n")
            if session is None:
                session, midnight, first, last = _window(row)
                previous_receipt = row["received_ns"]
                if previous_receipt > first:
                    issues["late_capture_start"] += 1
                events["start"] += 1
                continue
            _consume = type(row) is dict and type(row.get("event")) is str and type(row.get("received_ns")) is int
            if not _consume or end_status is not None:
                issues["unexpected_journal_row"] += 1
                continue
            receipt_ns = row["received_ns"]
            event = row["event"]
            if receipt_ns < previous_receipt:
                issues["receive_time_regression"] += 1
                for s in stats.values():
                    s.recent_quotes.clear()
                continue
            previous_receipt = receipt_ns
            common = {"event", "received_ns"}
            content = set(row) - common
            if event == "end" and content == {"status"} and type(row["status"]) is str and row["status"] in END_STATUSES:
                end_status = row["status"]
                events[event] += 1
                if receipt_ns < last:
                    issues["early_capture_end"] += 1
                continue
            this_connection = row.get("connection")
            if type(this_connection) is not int or not 1 <= this_connection <= 2:
                issues["unexpected_connection"] += 1
                continue
            if event == "connected" and content == {"connection"} and not active and this_connection == connection + 1:
                active = True
                connection = this_connection
                acked.clear()
                events[event] += 1
                continue
            if event == "invalid" and content == {"connection", "reason"} and type(row["reason"]) is str:
                events[event] += 1
                continue
            if (event == "disconnected" and content == {"connection", "reason"} and not active
                    and this_connection == connection + 1 and row["reason"] == "network_failure"):
                # An upgrade can fail before the connection acquires a receipt.
                connection = this_connection
                events[event] += 1
                issues["network_failure"] += 1
                continue
            if not active or this_connection != connection:
                issues["event_outside_active_connection"] += 1
                continue
            if event == "disconnected" and content == {"connection", "reason"} and type(row["reason"]) is str:
                reason = row["reason"] if row["reason"] in DISCONNECT_REASONS else "unrecognized_disconnect"
                events[event] += 1
                if reason != "window_end" or receipt_ns < last:
                    issues[reason] += 1
                active = False
                for s in stats.values():
                    s.recent_quotes.clear()
                continue
            if event == "ack" and content == {"connection", "tr", "symbol"}:
                pair = row["tr"], row["symbol"]
                if all(type(v) is str for v in pair) and pair in SUBSCRIPTIONS and pair not in acked:
                    acked.add(pair)
                    ack_before_first += connection == 1 and receipt_ns < first
                    events[event] += 1
                else:
                    issues["invalid_or_duplicate_ack"] += 1
                continue
            if event == "market" and content == {"connection", "tr", "count", "payload"}:
                events[event] += 1
                _market(row, session, midnight, first, last, stats, acked, issues, widths)
                continue
            issues["unknown_journal_event"] += 1
        final = os.fstat(src.fileno())
        _require(bytes_read == initial.st_size == final.st_size and initial.st_mtime_ns == final.st_mtime_ns,
                 "journal changed while being inspected")
    _require(session is not None, "missing journal start")
    summaries = {symbol: _symbol_summary(s) for symbol, s in stats.items()}
    checks = {"complete_end": end_status == "complete", "single_connection": events["connected"] == 1,
              "all_subscriptions_ack_before_open": ack_before_first == len(SUBSCRIPTIONS),
              "journal_protocol": not issues and events["invalid"] == 0,
              "per_symbol": all(all(s["checks"].values()) for s in summaries.values())}
    return {"schema": "krx-tape-quality-v1", "session": session, "start_ns": first, "end_ns": last, "journal_bytes": bytes_read,
            "journal_sha256": raw.hexdigest(), "canonical_jsonl_sha256": None if issues["invalid_json"] or issues["truncated_line"] else canonical.hexdigest(),
            "status": "quality_pass" if all(checks.values()) else "insufficient_evidence", "checks": checks,
            "end_status": end_status, "events": dict(events), "issues": {key: value for key, value in issues.items() if value},
            "subscriptions_ack_before_open": ack_before_first, "market_width_counts": dict(widths), "symbols": summaries,
            "bounds": {"min_joint_minutes": MIN_JOINT_MINUTES, "min_side_percent": MIN_SIDE_PERCENT,
                       "min_paired_percent": MIN_PAIR_PERCENT, "max_quote_age_ns": MAX_AGE_NS,
                       "quote_join": "prior_receipt_and_strictly_earlier_exchange_second"}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("journal", type=Path)
    args = parser.parse_args()
    print(json.dumps(inspect_journal(args.journal), sort_keys=True))


if __name__ == "__main__":
    main()
