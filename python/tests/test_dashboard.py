"""Unit tests for `live.dashboard`'s pure parsing/formatting logic.

Deliberately does not exercise the `tmux`-calling functions
(`session_alive`, `capture_pane`) against a real `tmux` process -- those
are thin subprocess wrappers with no branching logic of their own beyond
"did the process fail", and a real integration test would make this suite
depend on a `tmux` binary and live sessions being present in CI. Everything
with actual logic (regex parsing, latest-wins line scanning, equity/return
math, daily-report loading and trend formatting) is tested directly against
sample text/files instead.
"""

import json
from decimal import Decimal
from pathlib import Path

from live import dashboard
from live.dashboard import (
    LoopStatus,
    TickStatus,
    _decimal_default,
    current_equity,
    format_dashboard,
    format_loop_section,
    kill_switch_mentioned,
    latest_signal_decision,
    load_daily_reports,
    parse_latest_tick,
    parse_latest_vst_balance,
    recent_trades,
    return_pct,
    tail_lines,
    to_json_dict,
)


def test_parse_latest_tick_picks_last_successful_line():
    pane = (
        "[paper-trading-loop] INFO engine.runtime.PaperTradingApp - tick complete: "
        "lastTickAt=2026-08-11T09:04:51.847441508Z equity=100000\n"
        "[paper-trading-loop] INFO engine.runtime.PaperTradingApp - tick complete: "
        "lastTickAt=2026-08-11T09:10:16.086912945Z equity=100420.5\n"
    )
    status = parse_latest_tick(pane)
    assert status == TickStatus("2026-08-11T09:10:16.086912945Z", Decimal("100420.5"), True, None)


def test_parse_latest_tick_prefers_later_error_over_earlier_success():
    pane = (
        "tick complete: lastTickAt=2026-08-11T09:04:51Z equity=100000\n"
        "tick completed with an error: lastTickAt=2026-08-11T09:10:16Z equity=100000 error=boom\n"
    )
    status = parse_latest_tick(pane)
    assert status is not None
    assert status.ok is False
    assert status.error == "boom"
    assert status.last_tick_at == "2026-08-11T09:10:16Z"


def test_parse_latest_tick_prefers_later_success_over_earlier_error():
    pane = (
        "tick completed with an error: lastTickAt=2026-08-11T09:04:51Z equity=100000 error=boom\n"
        "tick complete: lastTickAt=2026-08-11T09:10:16Z equity=100420\n"
    )
    status = parse_latest_tick(pane)
    assert status is not None
    assert status.ok is True
    assert status.equity == Decimal("100420")


def test_parse_latest_tick_no_match_returns_none():
    assert parse_latest_tick("nothing relevant here\n") is None


def test_parse_latest_vst_balance_returns_last_match():
    pane = (
        "VstPreflight: real VST balance=96224.4301 equity=96224.4301 availableMargin=96224.4301 "
        "usedMargin=0.0000 unrealizedProfit=0.0000\n"
        "some ticks in between\n"
        "VstPreflight: real VST balance=97000.0000 equity=97000.0000 availableMargin=97000.0000 "
        "usedMargin=0.0000 unrealizedProfit=0.0000\n"
    )
    balance = parse_latest_vst_balance(pane)
    assert balance == {
        "balance": Decimal("97000.0000"),
        "equity": Decimal("97000.0000"),
        "available_margin": Decimal("97000.0000"),
        "used_margin": Decimal("0.0000"),
        "unrealized_profit": Decimal("0.0000"),
    }


def test_parse_latest_vst_balance_no_match_returns_none():
    assert parse_latest_vst_balance("no balance lines here\n") is None


def test_kill_switch_mentioned_true_for_startup_tripped_message():
    pane = "PaperTradingApp starting in bingx-vst mode with the kill switch already TRIPPED\n"
    assert kill_switch_mentioned(pane) is True


def test_kill_switch_mentioned_true_for_reconciliation_trip_message():
    pane = "internal consistency check found 1 mismatch(es); tripping kill switch -- see above\n"
    assert kill_switch_mentioned(pane) is True


def test_kill_switch_mentioned_false_when_absent():
    pane = "tick complete: lastTickAt=2026-08-11T09:04:51Z equity=100000\n"
    assert kill_switch_mentioned(pane) is False


def test_return_pct_basic_math():
    assert return_pct(Decimal("100420"), Decimal("100000")) == Decimal("0.42")
    assert return_pct(Decimal("95000"), Decimal("100000")) == Decimal("-5.00")


def test_return_pct_zero_baseline_returns_none():
    assert return_pct(Decimal("100"), Decimal("0")) is None


def test_load_daily_reports_reads_and_sorts_by_filename(tmp_path):
    (tmp_path / "2026-08-12.json").write_text(json.dumps({"date": "2026-08-12", "ending_equity": "100200"}))
    (tmp_path / "2026-08-11.json").write_text(json.dumps({"date": "2026-08-11", "ending_equity": "100100"}))
    reports = load_daily_reports(tmp_path)
    assert [r["date"] for r in reports] == ["2026-08-11", "2026-08-12"]


def test_load_daily_reports_skips_corrupt_file(tmp_path):
    (tmp_path / "2026-08-11.json").write_text(json.dumps({"date": "2026-08-11", "ending_equity": "100100"}))
    (tmp_path / "2026-08-12.json").write_text("not valid json {{{")
    reports = load_daily_reports(tmp_path)
    assert len(reports) == 1
    assert reports[0]["date"] == "2026-08-11"


def test_load_daily_reports_missing_dir_returns_empty(tmp_path):
    assert load_daily_reports(tmp_path / "does-not-exist") == []


def test_load_daily_reports_skips_array_root(tmp_path):
    (tmp_path / "2026-08-11.json").write_text(json.dumps(["not", "a", "dict"]))
    assert load_daily_reports(tmp_path) == []


def test_load_daily_reports_skips_non_list_errors_field(tmp_path):
    (tmp_path / "2026-08-11.json").write_text(json.dumps({"date": "2026-08-11", "errors": "not-a-list"}))
    assert load_daily_reports(tmp_path) == []


def test_load_daily_reports_skips_non_dict_trade_items(tmp_path):
    (tmp_path / "2026-08-11.json").write_text(json.dumps({"date": "2026-08-11", "trades": ["not-a-dict"]}))
    assert load_daily_reports(tmp_path) == []


def test_load_daily_reports_accepts_well_formed_report_alongside_malformed_ones(tmp_path):
    (tmp_path / "2026-08-10.json").write_text(json.dumps({"date": "2026-08-10", "trades": [], "errors": []}))
    (tmp_path / "2026-08-11.json").write_text(json.dumps(["not", "a", "dict"]))
    reports = load_daily_reports(tmp_path)
    assert [r["date"] for r in reports] == ["2026-08-10"]


def test_load_daily_reports_skips_invalid_utf8_file(tmp_path):
    # Path.read_text() raises UnicodeDecodeError on invalid UTF-8 bytes --
    # not an OSError/json.JSONDecodeError subclass, so this needs its own
    # except clause (real CodeRabbit review finding on this PR).
    (tmp_path / "2026-08-10.json").write_text(json.dumps({"date": "2026-08-10", "trades": [], "errors": []}))
    (tmp_path / "2026-08-11.json").write_bytes(b'{"date": "2026-08-11"}\xff')
    reports = load_daily_reports(tmp_path)
    assert [r["date"] for r in reports] == ["2026-08-10"]


def _raising_read_text(_path: Path, *_args: object, **_kwargs: object) -> str:
    """Shared `Path.read_text` monkeypatch target simulating a TOCTOU read
    failure (e.g. the file was rotated/deleted between an earlier
    `is_file()` check and this read)."""
    raise OSError


def test_latest_signal_decision_read_error_returns_none(tmp_path, monkeypatch):
    log = tmp_path / "cron.log"
    log.write_text("2026-08-10 09:05:06,610 INFO no signal today (no sign-category change) -- signal file left untouched\n")
    monkeypatch.setattr(Path, "read_text", _raising_read_text)
    assert latest_signal_decision(log) is None


def test_tail_lines_read_error_returns_empty(tmp_path, monkeypatch):
    log = tmp_path / "watchdog.log"
    log.write_text("line 1\n")
    monkeypatch.setattr(Path, "read_text", _raising_read_text)
    assert tail_lines(log) == []


def test_format_dashboard_does_not_overclaim_about_pending_order_intent(tmp_path, monkeypatch):
    # A missing signal file only proves there's no file at that path -- it
    # does NOT prove no live order intent is pending anywhere in the system
    # (it could already have been consumed by the Java FileSignalSource and
    # be in flight through OMS/RiskGateway/Execution, which this script has
    # no visibility into). Real CodeRabbit review finding on this PR.
    monkeypatch.setattr(dashboard, "SIGNAL_FILE", tmp_path / "does-not-exist.json")
    monkeypatch.setattr(dashboard, "CRON_LOG", tmp_path / "no-cron.log")
    monkeypatch.setattr(dashboard, "WATCHDOG_LOG", tmp_path / "no-watchdog.log")
    text = format_dashboard([])
    assert "no live order intent" not in text.lower()
    assert "none present at" in text


def test_current_equity_prefers_live_tick_over_daily_report():
    status = LoopStatus(
        key="vst",
        display_name="x",
        session="x",
        alive=True,
        tick=TickStatus("t", Decimal("100420"), True, None),
        kill_switch_mentioned=False,
        daily_reports=[{"date": "2026-08-10", "ending_equity": "99000"}],
    )
    assert current_equity(status) == Decimal("100420")


def test_current_equity_falls_back_to_last_daily_report():
    status = LoopStatus(
        key="vst",
        display_name="x",
        session="x",
        alive=False,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[
            {"date": "2026-08-10", "ending_equity": "99000"},
            {"date": "2026-08-11", "ending_equity": "99500"},
        ],
    )
    assert current_equity(status) == Decimal("99500")


def test_current_equity_none_when_no_data():
    status = LoopStatus(
        key="vst", display_name="x", session="x", alive=False, tick=None, kill_switch_mentioned=False, daily_reports=[]
    )
    assert current_equity(status) is None


def test_recent_trades_flattens_across_days_newest_first():
    status = LoopStatus(
        key="vst",
        display_name="x",
        session="x",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[
            {
                "date": "2026-08-10",
                "trades": [
                    {"filled_at": "2026-08-10T01:00:00Z", "price": "60000", "quantity": "0.001", "notional": "60", "fee": "0.03"}
                ],
            },
            {
                "date": "2026-08-11",
                "trades": [
                    {"filled_at": "2026-08-11T03:00:00Z", "price": "61000", "quantity": "0.001", "notional": "61", "fee": "0.03"},
                    {"filled_at": "2026-08-11T01:00:00Z", "price": "60500", "quantity": "0.001", "notional": "60.5", "fee": "0.03"},
                ],
            },
        ],
    )
    trades = recent_trades(status)
    assert [t["filled_at"] for t in trades] == [
        "2026-08-11T03:00:00Z",
        "2026-08-11T01:00:00Z",
        "2026-08-10T01:00:00Z",
    ]


def test_recent_trades_respects_limit():
    status = LoopStatus(
        key="vst",
        display_name="x",
        session="x",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[
            {
                "date": "2026-08-11",
                "trades": [{"filled_at": f"2026-08-11T0{i}:00:00Z", "price": "1", "quantity": "1", "notional": "1", "fee": "0"} for i in range(5)],
            }
        ],
    )
    assert len(recent_trades(status, limit=2)) == 2


def test_recent_trades_empty_when_no_trades():
    status = LoopStatus(
        key="vst",
        display_name="x",
        session="x",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[{"date": "2026-08-11", "trades": []}],
    )
    assert recent_trades(status) == []


def test_format_loop_section_surfaces_tick_errors():
    status = LoopStatus(
        key="simulated",
        display_name="SIMULATED LOOP",
        session="paper-trading",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[
            {
                "date": "2026-08-10",
                "starting_equity": "100000",
                "ending_equity": "99900",
                "trades": [],
                "errors": [
                    {"occurred_at": "2026-08-10T05:00:00Z", "message": "price feed timeout"},
                    {"occurred_at": "2026-08-10T06:00:00Z", "message": "second failure"},
                ],
                "kill_switch_tripped": False,
                "ticks_attempted": 288,
                "ticks_succeeded": 286,
            }
        ],
    )
    text = format_loop_section(status)
    assert "Tick errors" in text
    assert "2 error(s)" in text
    assert "second failure" in text


def test_format_loop_section_no_error_section_when_clean():
    status = LoopStatus(
        key="simulated",
        display_name="SIMULATED LOOP",
        session="paper-trading",
        alive=True,
        tick=None,
        kill_switch_mentioned=False,
        daily_reports=[
            {
                "date": "2026-08-10",
                "starting_equity": "100000",
                "ending_equity": "100100",
                "trades": [],
                "errors": [],
                "kill_switch_tripped": False,
                "ticks_attempted": 288,
                "ticks_succeeded": 288,
            }
        ],
    )
    text = format_loop_section(status)
    assert "Tick errors" not in text


def test_latest_signal_decision_hold(tmp_path):
    log = tmp_path / "cron.log"
    log.write_text("2026-08-10 09:05:06,610 INFO no signal today (no sign-category change) -- signal file left untouched\n")
    decision = latest_signal_decision(log)
    assert decision == {"timestamp": "2026-08-10 09:05:06,610", "decision": "hold", "detail": "no sign-category change"}


def test_latest_signal_decision_signal_wins_when_it_is_the_last_line(tmp_path):
    log = tmp_path / "cron.log"
    log.write_text(
        "2026-08-09 09:05:06,000 INFO no signal today (no sign-category change) -- signal file left untouched\n"
        "2026-08-10 09:05:06,610 INFO wrote signal: side=LONG quantity=0.01 symbol=BTC-USDT intent_id=abc -> path\n"
    )
    decision = latest_signal_decision(log)
    assert decision is not None
    assert decision["decision"] == "signal"
    assert decision["timestamp"] == "2026-08-10 09:05:06,610"


def test_latest_signal_decision_missing_file_returns_none(tmp_path):
    assert latest_signal_decision(tmp_path / "does-not-exist.log") is None


def test_tail_lines_returns_last_n(tmp_path):
    log = tmp_path / "watchdog.log"
    log.write_text("\n".join(f"line {i}" for i in range(20)) + "\n")
    assert tail_lines(log, 3) == ["line 17", "line 18", "line 19"]


def test_tail_lines_missing_file_returns_empty(tmp_path):
    assert tail_lines(tmp_path / "does-not-exist.log") == []


def test_decimal_default_serializes_as_string():
    assert _decimal_default(Decimal("100.5")) == "100.5"


def test_to_json_dict_is_json_serializable_with_decimal_default():
    status = LoopStatus(
        key="simulated",
        display_name="SIMULATED LOOP",
        session="paper-trading",
        alive=True,
        tick=TickStatus("2026-08-11T09:10:16Z", Decimal("100420"), True, None),
        kill_switch_mentioned=False,
        daily_reports=[],
    )
    payload = to_json_dict([status])
    # Must round-trip through json.dumps without raising -- the real point
    # of this test, since Decimal isn't natively JSON-serializable.
    text = json.dumps(payload, default=_decimal_default)
    parsed = json.loads(text)
    assert parsed["loops"]["simulated"]["current_equity"] == "100420"
    assert parsed["loops"]["simulated"]["return_pct"] == "0.4200"
