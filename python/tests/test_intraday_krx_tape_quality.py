"""Invented market-data journals for independent first-hour quality checks."""

import hashlib
import json
from datetime import datetime

import pytest

from research import intraday_krx_tape_quality as quality


DATE = "2026-11-20"
START = int(datetime.fromisoformat(f"{DATE}T09:00:00+09:00").timestamp()) * quality.NS
END = START + 60 * quality.MINUTE_NS
SYMBOLS = ["005930", "000660"]
TRADE, QUOTE = "H0STCNT0", "H0STASP0"


def transition(event, instant, connection=1, **details):
    return {"event": event, "received_ns": instant, "connection": connection, **details}


def bootstrap():
    rows = [{"event": "start", "session": DATE, "symbols": SYMBOLS,
             "start_ns": START, "end_ns": END, "received_ns": START - 2 * quality.NS}]
    rows.append(transition("connected", START - quality.NS))
    rows.extend(transition("ack", START - quality.NS, tr=tr, symbol=symbol)
                for symbol in SYMBOLS for tr in (TRADE, QUOTE))
    return rows


def stream(tr, symbol="005930", *, minute=0, second=1, received_offset_ns=0, received_ns=None,
           width=None, **changes):
    fields = ["0"] * (width or (47 if tr == TRADE else 63))
    fields[:2] = symbol, f"09{minute:02}{second:02}"
    if tr == TRADE:
        for index, value in {2: "100000", 12: "100", 13: "100", 21: "5", 33: "20261120", 35: "N", 43: "0"}.items():
            fields[index] = value
    else:
        for index, value in {2: "0", 3: "100100", 13: "100000", 23: "200", 33: "300"}.items():
            fields[index] = value
    modify = {"side": 21, "cumulative": 13, "stamp": 33, "ask": 3, "hour": 1, "session_type": 43 if tr == TRADE else 2}
    for field, value in changes.items():
        fields[modify[field]] = value
    instant = START + minute * quality.MINUTE_NS + second * quality.NS + received_offset_ns
    return transition("market", instant if received_ns is None else received_ns, tr=tr, count=1, payload="^".join(fields))


def write(path, rows, *, end=True, close=True):
    if end:
        rows = list(rows)
        if close:
            connection = next((row["connection"] for row in reversed(rows) if row["event"] == "connected"), 1)
            rows.append(transition("disconnected", END, connection, reason="window_end"))
        rows.append({"event": "end", "status": "complete", "received_ns": END})
    raw = b"".join(json.dumps(row).encode("ascii") + b"\n" for row in rows)
    path.write_bytes(raw)
    return raw


def test_full_original_hour_has_independent_counts_and_hashes(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = bootstrap()
    for minute in range(60):
        rows.extend(stream(QUOTE, symbol, minute=minute) for symbol in SYMBOLS)
        rows.extend(stream(TRADE, symbol, minute=minute, second=2, cumulative=str((minute + 1) * 100),
                           side="1" if minute % 2 else "5") for symbol in SYMBOLS)
    raw = write(path, rows)
    actual = quality.inspect_journal(path)
    assert actual["status"] == "quality_pass"
    assert actual["journal_sha256"] == hashlib.sha256(raw).hexdigest()
    assert actual["journal_sha256"] != actual["canonical_jsonl_sha256"]
    assert actual["market_width_counts"] == {"H0STASP0:63": 120, "H0STCNT0:47": 120}
    assert actual["subscriptions_ack_before_open"] == 4
    for stats in actual["symbols"].values():
        assert stats["joint_minutes"] == stats["paired_classified_trades"] == 60
        assert stats["rows"]["buy_shares"] == stats["rows"]["sell_shares"] == 3000
        assert stats["rows"]["cumulative_checks"] == 59
        assert stats["minute_counts"]["paired"] == [1] * 60
    write(path, rows, close=False)
    incomplete = quality.inspect_journal(path)
    assert incomplete["status"] == "insufficient_evidence" and not incomplete["checks"]["complete_end"]


def test_equal_receive_clock_respects_journal_order_and_uses_strict_exchange_second(tmp_path):
    path = tmp_path / "capture.jsonl"
    receipt = START + 2 * quality.NS + 5
    first_trade = stream(TRADE, second=2, received_ns=receipt)
    quote_after = stream(QUOTE, second=1, received_ns=receipt)
    same_exchange_quote = stream(QUOTE, second=2, received_ns=receipt)
    second_trade = stream(TRADE, second=2, received_ns=receipt, cumulative="200")
    write(path, [*bootstrap(), first_trade, quote_after, same_exchange_quote, second_trade])
    actual = quality.inspect_journal(path)["symbols"]["005930"]
    assert actual["paired_classified_trades"] == 1
    assert actual["issues"] == {"no_strictly_prior_quote": 1, "same_exchange_second": 1}


def test_locked_last_quote_clears_old_touch_and_volume_gap_downgrades(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = [*bootstrap(), stream(QUOTE), stream(TRADE, second=2), stream(QUOTE, second=3, ask="100000"),
            stream(TRADE, second=4, cumulative="300")]
    write(path, rows)
    actual = quality.inspect_journal(path)
    stats = actual["symbols"]["005930"]
    assert actual["status"] == "insufficient_evidence"
    assert stats["paired_classified_trades"] == 1
    assert stats["issues"]["locked_or_crossed_quote"] == 1
    assert not stats["checks"]["identifiable_regular_data"]
    assert stats["issues"]["excess_cumulative_delta"] == 1
    assert stats["rows"]["unreconciled_shares"] == 100


def test_stale_l1_outside_regular_reports_and_unknown_directions_stay_distinct(tmp_path):
    path = tmp_path / "capture.jsonl"
    write(path, [*bootstrap(), stream(QUOTE, width=59), stream(TRADE, second=5, width=46),
                 stream(TRADE, second=6, cumulative="200", side="3"),
                 stream(TRADE, second=7, cumulative="300", session_type="3")])
    actual = quality.inspect_journal(path)
    stats = actual["symbols"]["005930"]
    assert stats["side_known_trades"] == stats["rows"]["unknown_trades"] == 1
    assert stats["issues"]["unverified_regular_session_code"] == stats["issues"]["stale_prior_quote"] == 1
    assert not stats["checks"]["identifiable_regular_data"]
    assert actual["market_width_counts"]["H0STASP0:59"] == actual["market_width_counts"]["H0STCNT0:46"] == 1
    assert stats["paired_classified_trades"] == 0


def test_queued_old_quote_is_stale_even_when_messages_arrive_closely(tmp_path):
    path = tmp_path / "capture.jsonl"
    delayed = 10 * quality.NS
    write(path, [*bootstrap(), stream(QUOTE, received_offset_ns=delayed),
                 stream(TRADE, second=2, received_offset_ns=delayed)])
    stats = quality.inspect_journal(path)["symbols"]["005930"]
    assert stats["paired_classified_trades"] == 0
    assert stats["issues"]["stale_prior_quote"] == 1
    assert stats["max_exchange_to_receive_ns"] == {QUOTE: delayed, TRADE: delayed}


def test_reconnect_does_not_bridge_quotes_or_cumulative_shares(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = [*bootstrap(), stream(QUOTE), stream(TRADE, second=2),
            transition("disconnected", START + 3 * quality.NS, reason="network_failure"),
            transition("connected", START + 4 * quality.NS, connection=2),
            transition("ack", START + 4 * quality.NS, connection=2, tr=TRADE, symbol="005930"),
            {**stream(TRADE, second=5, cumulative="1200"), "connection": 2}]
    write(path, rows)
    actual = quality.inspect_journal(path)
    assert actual["events"]["connected"] == 2
    assert actual["issues"]["network_failure"] == 1
    assert actual["symbols"]["005930"]["paired_classified_trades"] == 1
    assert "cumulative_checks" not in actual["symbols"]["005930"]["rows"]
    assert actual["status"] == "insufficient_evidence"


def test_failed_initial_upgrade_does_not_hide_second_connection_data(tmp_path):
    path = tmp_path / "capture.jsonl"
    rows = [bootstrap()[0], transition("disconnected", START - quality.NS, reason="network_failure"),
            transition("connected", START - quality.NS, connection=2),
            transition("ack", START - quality.NS, connection=2, tr=TRADE, symbol="005930"),
            {**stream(TRADE, second=2), "connection": 2}]
    write(path, rows)
    actual = quality.inspect_journal(path)
    assert actual["issues"] == {"network_failure": 1}
    assert actual["symbols"]["005930"]["side_known_trades"] == 1
    assert actual["status"] == "insufficient_evidence"


def test_unacknowledged_and_malformed_data_never_become_quotes(tmp_path):
    path = tmp_path / "capture.jsonl"
    pre = [row for row in bootstrap() if not (row["event"] == "ack" and row["tr"] == QUOTE)]
    quote_bad = {**stream(QUOTE), "payload": "005930^1"}
    bad_event = transition("invalid", START + 2 * quality.NS, reason="not-safe-to-repeat")
    write(path, [*pre, quote_bad, stream(QUOTE), bad_event, stream(TRADE, second=2)], end=False)
    actual = quality.inspect_journal(path)
    assert actual["end_status"] is None
    assert actual["issues"] == {"invalid_market_width": 1, "market_without_prior_ack": 1}
    assert "not-safe-to-repeat" not in json.dumps(actual)
    assert actual["symbols"]["005930"]["paired_classified_trades"] == 0


def test_refuses_scope_change_and_incomplete_final_line(tmp_path):
    path = tmp_path / "capture.jsonl"
    altered, *rest = bootstrap()
    write(path, [{**altered, "symbols": ["000660"]}, *rest])
    with pytest.raises(ValueError, match="invalid session scope"):
        quality.inspect_journal(path)
    write(path, [{**altered, "session": "2026-11-23"}, *rest])
    with pytest.raises(ValueError, match="invalid session scope"):
        quality.inspect_journal(path)
    raw = write(path, bootstrap())
    path.write_bytes(raw[:-1])
    actual = quality.inspect_journal(path)
    assert actual["canonical_jsonl_sha256"] is None
    assert actual["issues"]["truncated_line"] == 1
    assert actual["status"] == "insufficient_evidence"
