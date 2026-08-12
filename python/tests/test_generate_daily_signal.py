"""Tests for `python/live/generate_daily_signal.py` -- Paper-trading
bridge Task B. See `.planning/paper-trading-b-signal-runner.md`.

Three things this file exists to prove, beyond ordinary fetch/convert/
emit correctness:

1. **`runs_path` isolation is real, not just documented.** A daily
   production `fit()` call must NEVER log into
   `research.experiment_log.DEFAULT_RUNS_PATH` -- doing so would silently
   inflate the research selection-trial count `N` this project's Deflated
   Sharpe Ratio math depends on everywhere else. See
   `test_generate_signal_never_touches_the_default_research_runs_path`.
2. **A real signal is written atomically**, and a **`None` decision
   leaves the signal file completely untouched** -- the downstream Java
   `FileSignalSource` (a parallel task) treats "no new file content" as
   "nothing new to act on," which only works if this script never writes
   a sentinel/empty value for "hold."
3. **The fetch/convert path uses the shared cache correctly** and never
   includes today's still-forming daily bar.

Uses the project's existing `FakeBingXKlinesServer` (stdlib
`http.server`, same fixture `test_backfill.py` uses) -- not a mocking
framework, matching this codebase's established Python test philosophy.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from backtest.kline import Kline
from data.bingx_klines import KlineRow
from data.store import connect
from live.generate_daily_signal import (
    DEFAULT_FETCH_BARS,
    LIVE_RUNS_PATH,
    MIN_WARMUP_BARS,
    STRATEGY_ID,
    STRATEGY_VERSION,
    _deterministic_intent_id,
    _kline_row_to_kline,
    fetch_live_klines,
    generate_signal,
    main,
    write_signal_atomically,
)
from research import experiment_log
from schemas.order_intent import OrderIntent, OrderType, Side
from tests.fake_bingx_server import FakeBingXKlinesServer

DAY_STEP = 86_400_000
BASE_DAY_MS = (1_735_689_600_000 // DAY_STEP) * DAY_STEP  # 2025-01-01T00:00:00Z, UTC-midnight aligned


@pytest.fixture
def server():
    srv = FakeBingXKlinesServer()
    yield srv
    srv.close()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch) -> None:
    monkeypatch.setattr("data.bingx_klines.time.sleep", lambda _s: None)


def _flat_klines_with_final_jump(count: int, *, jump_price: str | None = None) -> list[Kline]:
    """`count` daily `Kline`s, flat at 100 except (optionally) the very
    last one, which jumps to `jump_price`. With the strategy's default
    253-bar warmup and `count >= 253`, a flat series alone produces zero
    signal at every bar (every lookback sign is 0 the whole way through --
    same price compared to the same price); a jump ONLY on the final bar
    means the ensemble is non-None and non-zero for the very first time
    exactly on that last bar, so the strategy's LAST call (and only that
    call) returns a real `OrderIntent`. Mirrors
    `test_daily_tsmom_ensemble.py`'s own hand-derived-scenario approach.
    """
    base_time = datetime(2025, 1, 1, tzinfo=timezone.utc)
    closes = ["100"] * count
    if jump_price is not None:
        closes[-1] = jump_price
    return [
        Kline(
            open_time=base_time + timedelta(days=i),
            open=(c := Decimal(price)),
            high=c,
            low=c,
            close=c,
            volume=Decimal("1"),
        )
        for i, price in enumerate(closes)
    ]


# ---------------------------------------------------------------------------
# _kline_row_to_kline
# ---------------------------------------------------------------------------


def test_kline_row_to_kline_converts_fields_and_ms_timestamp_exactly():
    row = KlineRow(
        open_time_ms=BASE_DAY_MS,
        open=Decimal("100.5"),
        high=Decimal("101"),
        low=Decimal("99"),
        close=Decimal("100.75"),
        volume=Decimal("12.3"),
    )
    kline = _kline_row_to_kline(row)
    assert kline.open_time == datetime.fromtimestamp(BASE_DAY_MS // 1000, tz=timezone.utc)
    assert kline.open == Decimal("100.5")
    assert kline.high == Decimal("101")
    assert kline.low == Decimal("99")
    assert kline.close == Decimal("100.75")
    assert kline.volume == Decimal("12.3")


# ---------------------------------------------------------------------------
# fetch_live_klines
# ---------------------------------------------------------------------------


def test_fetch_live_klines_returns_ascending_klines_via_the_shared_cache(server, tmp_path):
    times = [BASE_DAY_MS + i * DAY_STEP for i in range(5)]
    server.set_klines(times)
    db_path = tmp_path / "klines.sqlite3"
    now_ms = times[-1] + DAY_STEP  # "now" is exactly the start of the day after the last seeded bar

    klines = fetch_live_klines(
        base_url=server.base_url,
        db_path=db_path,
        fetch_bars=5,
        now_ms=now_ms,
    )

    assert [k.open_time for k in klines] == [
        datetime.fromtimestamp(t // 1000, tz=timezone.utc) for t in times
    ]
    assert all(k.close == Decimal("100") for k in klines)

    # And the shared cache actually persisted them -- a second read directly
    # from the store (bypassing the fetch) sees the same rows.
    conn = connect(db_path)
    stored = [row[0] for row in conn.execute("SELECT open_time_ms FROM klines ORDER BY open_time_ms")]
    conn.close()
    assert stored == times


def test_fetch_live_klines_excludes_todays_still_forming_bar(server, tmp_path):
    yesterday = BASE_DAY_MS
    today = BASE_DAY_MS + DAY_STEP
    server.set_kline(yesterday, "100", "100", "100", "100", "1")
    server.set_kline(today, "999", "999", "999", "999", "1")  # would stand out if leaked in
    db_path = tmp_path / "klines.sqlite3"
    now_ms = today + 12 * 3_600_000  # midday "today" -- today's daily bar is still forming

    klines = fetch_live_klines(base_url=server.base_url, db_path=db_path, fetch_bars=1, now_ms=now_ms)

    assert len(klines) == 1
    assert klines[0].open_time == datetime.fromtimestamp(yesterday // 1000, tz=timezone.utc)
    assert klines[0].close == Decimal("100")


def test_fetch_live_klines_is_cache_first_on_a_rerun(server, tmp_path):
    times = [BASE_DAY_MS + i * DAY_STEP for i in range(3)]
    server.set_klines(times)
    db_path = tmp_path / "klines.sqlite3"
    now_ms = times[-1] + DAY_STEP

    fetch_live_klines(base_url=server.base_url, db_path=db_path, fetch_bars=3, now_ms=now_ms)
    requests_after_first_call = len(server.requests)
    assert requests_after_first_call > 0

    fetch_live_klines(base_url=server.base_url, db_path=db_path, fetch_bars=3, now_ms=now_ms)

    assert len(server.requests) == requests_after_first_call  # fully cached -- no new network calls


# ---------------------------------------------------------------------------
# generate_signal -- strategy plumbing + the runs_path isolation guarantee
# ---------------------------------------------------------------------------


def test_generate_signal_returns_none_for_a_flat_series_no_signal_today(tmp_path):
    klines = _flat_klines_with_final_jump(MIN_WARMUP_BARS, jump_price=None)
    decision = generate_signal(klines, parent_run_id="test-flat", runs_path=str(tmp_path / "live_signals.jsonl"))
    assert decision is None


def test_generate_signal_returns_a_real_order_intent_on_a_sign_change(tmp_path):
    klines = _flat_klines_with_final_jump(MIN_WARMUP_BARS, jump_price="200")
    runs_path = tmp_path / "live_signals.jsonl"

    decision = generate_signal(klines, parent_run_id="test-jump", runs_path=str(runs_path))

    assert decision is not None
    assert isinstance(decision, OrderIntent)
    assert decision.symbol == "BTC-USDT"
    assert decision.side == Side.LONG  # a bullish jump after a flat, undefined ensemble -> +1
    assert decision.order_type == OrderType.GUARDED_MARKET
    assert decision.quantity > 0


def test_generate_signal_never_touches_the_default_research_runs_path(tmp_path, monkeypatch):
    """The critical correctness requirement: a daily production fit() call
    must land in the isolated live-signals log, and the default research
    trial log (research.experiment_log.DEFAULT_RUNS_PATH) must not even
    be CREATED by this call -- not merely "not the file we happened to
    check."
    """
    monkeypatch.chdir(tmp_path)
    klines = _flat_klines_with_final_jump(MIN_WARMUP_BARS, jump_price="200")

    generate_signal(klines, parent_run_id="test-isolation", runs_path=LIVE_RUNS_PATH)

    live_log = tmp_path / LIVE_RUNS_PATH
    default_log = tmp_path / experiment_log.DEFAULT_RUNS_PATH
    assert live_log.exists()
    assert not default_log.exists()

    records = list(experiment_log.read_records(str(live_log)))
    assert len(records) == 1
    record = records[0]
    assert record["record_type"] == "backtest_run"
    assert record["strategy_id"] == STRATEGY_ID
    assert record["strategy_version"] == STRATEGY_VERSION
    assert record["fee_bps"] == "5"
    assert record["slippage_bps"] == "2"


def test_generate_signal_default_runs_path_is_the_isolated_live_log(tmp_path, monkeypatch):
    """Calling generate_signal without an explicit runs_path (the real
    call shape main() uses when a caller doesn't override --runs-path)
    must still land in the isolated file, never the research default --
    proves the isolation holds even when a caller forgets to pass
    runs_path explicitly.
    """
    monkeypatch.chdir(tmp_path)
    klines = _flat_klines_with_final_jump(MIN_WARMUP_BARS, jump_price="200")

    generate_signal(klines, parent_run_id="test-default")

    assert (tmp_path / LIVE_RUNS_PATH).exists()
    assert not (tmp_path / experiment_log.DEFAULT_RUNS_PATH).exists()


def test_generate_signal_returns_none_for_empty_klines_without_logging_anything(tmp_path, monkeypatch):
    """An empty klines list must short-circuit before DailyTsmomEnsembleTrainable
    is even constructed -- no fit() call, no degenerate backtest_run record.
    """
    monkeypatch.chdir(tmp_path)

    decision = generate_signal([], parent_run_id="test-empty", runs_path=LIVE_RUNS_PATH)

    assert decision is None
    assert not (tmp_path / LIVE_RUNS_PATH).exists()  # fit() was never called -- nothing was logged


# ---------------------------------------------------------------------------
# _deterministic_intent_id -- retry-safety for the live production path
# ---------------------------------------------------------------------------


def test_deterministic_intent_id_is_stable_for_the_same_symbol_and_bar():
    created_at = datetime(2026, 8, 11, tzinfo=timezone.utc)
    first = _deterministic_intent_id(symbol="BTC-USDT", created_at=created_at)
    second = _deterministic_intent_id(symbol="BTC-USDT", created_at=created_at)
    assert first == second


def test_deterministic_intent_id_differs_across_symbols():
    created_at = datetime(2026, 8, 11, tzinfo=timezone.utc)
    btc = _deterministic_intent_id(symbol="BTC-USDT", created_at=created_at)
    eth = _deterministic_intent_id(symbol="ETH-USDT", created_at=created_at)
    assert btc != eth


def test_deterministic_intent_id_differs_across_decision_bars():
    day1 = _deterministic_intent_id(symbol="BTC-USDT", created_at=datetime(2026, 8, 11, tzinfo=timezone.utc))
    day2 = _deterministic_intent_id(symbol="BTC-USDT", created_at=datetime(2026, 8, 12, tzinfo=timezone.utc))
    assert day1 != day2


# ---------------------------------------------------------------------------
# write_signal_atomically
# ---------------------------------------------------------------------------


def _order_intent(quantity: str = "1.5") -> OrderIntent:
    return OrderIntent(
        intent_id=uuid4(),
        symbol="BTC-USDT",
        side=Side.LONG,
        order_type=OrderType.GUARDED_MARKET,
        quantity=Decimal(quantity),
        limit_price=None,
        signal_timeframe="1d",
        created_at=datetime.now(timezone.utc),
    )


def test_write_signal_atomically_creates_parent_dirs_and_writes_valid_json(tmp_path):
    target = tmp_path / "signals" / "BTC-USDT" / "daily-tsmom-ensemble" / "latest.json"
    intent = _order_intent()

    write_signal_atomically(intent, target)

    assert target.exists()
    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["symbol"] == "BTC-USDT"
    assert payload["side"] == "LONG"
    assert payload["quantity"] == "1.5"
    assert payload["intent_id"] == str(intent.intent_id)


def test_write_signal_atomically_fully_replaces_existing_content(tmp_path):
    target = tmp_path / "latest.json"
    target.write_text("stale content that must not survive", encoding="utf-8")

    write_signal_atomically(_order_intent("2.0"), target)

    payload = json.loads(target.read_text(encoding="utf-8"))
    assert payload["quantity"] == "2.0"
    assert "stale content" not in target.read_text(encoding="utf-8")


def test_write_signal_atomically_leaves_no_leftover_tmp_file(tmp_path):
    target = tmp_path / "latest.json"
    write_signal_atomically(_order_intent(), target)
    # The temp filename is process-unique (PID + random UUID), not a fixed
    # "<name>.tmp" -- glob for any leftover ".*<name>.*.tmp" instead of
    # checking one exact path.
    assert not list(tmp_path.glob(".latest.json.*.tmp"))


def test_write_signal_atomically_uses_os_replace_for_the_final_move(tmp_path, monkeypatch):
    target = tmp_path / "latest.json"
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst) -> None:
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("live.generate_daily_signal.os.replace", spy_replace)

    write_signal_atomically(_order_intent(), target)

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    # Process-unique temp name: ".<final-name>.<pid>.<uuid-hex>.tmp",
    # written into the SAME directory as the final target.
    assert src.startswith(str(tmp_path / ".latest.json."))
    assert src.endswith(".tmp")
    assert src != str(target) + ".tmp"  # not the old fixed-name scheme


def test_write_signal_atomically_removes_the_tmp_file_on_a_failed_write(tmp_path, monkeypatch):
    target = tmp_path / "latest.json"

    def failing_replace(src, dst) -> None:
        raise OSError("simulated failure during the final atomic rename")

    monkeypatch.setattr("live.generate_daily_signal.os.replace", failing_replace)

    with pytest.raises(OSError, match="simulated failure"):
        write_signal_atomically(_order_intent(), target)

    assert not target.exists()  # the final path was never created
    assert not list(tmp_path.glob(".latest.json.*.tmp"))  # and the temp file was cleaned up, not left behind


def test_write_signal_atomically_two_invocations_use_distinct_tmp_paths(tmp_path, monkeypatch):
    target = tmp_path / "latest.json"
    seen_tmp_paths = []
    real_replace = os.replace

    def capturing_replace(src, dst) -> None:
        seen_tmp_paths.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr("live.generate_daily_signal.os.replace", capturing_replace)

    write_signal_atomically(_order_intent("1"), target)
    write_signal_atomically(_order_intent("2"), target)

    assert len(seen_tmp_paths) == 2
    assert seen_tmp_paths[0] != seen_tmp_paths[1]  # two invocations never reuse the same temp path


# ---------------------------------------------------------------------------
# main() -- full end-to-end wiring
# ---------------------------------------------------------------------------


def _seed_warmup_server(server, *, now_ms: int, fetch_bars: int, jump_price: str | None) -> None:
    step = DAY_STEP
    end_ms = (now_ms // step) * step
    start_ms = end_ms - fetch_bars * step
    times = [start_ms + i * step for i in range(fetch_bars)]
    server.set_klines(times)
    if jump_price is not None:
        server.set_kline(times[-1], jump_price, jump_price, jump_price, jump_price, "1")


def test_main_writes_a_real_signal_file_end_to_end(server, tmp_path, monkeypatch):
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    now_ms = BASE_DAY_MS + DEFAULT_FETCH_BARS * DAY_STEP
    _seed_warmup_server(server, now_ms=now_ms, fetch_bars=DEFAULT_FETCH_BARS, jump_price="200")
    monkeypatch.setattr("live.generate_daily_signal._current_time_ms", lambda: now_ms)

    signal_path = tmp_path / "signal.json"
    runs_path = tmp_path / "live_signals.jsonl"
    db_path = tmp_path / "klines.sqlite3"

    exit_code = main(
        [
            "--db-path",
            str(db_path),
            "--signal-path",
            str(signal_path),
            "--runs-path",
            str(runs_path),
        ]
    )

    assert exit_code == 0
    assert signal_path.exists()
    payload = json.loads(signal_path.read_text(encoding="utf-8"))
    assert payload["side"] == "LONG"
    assert payload["symbol"] == "BTC-USDT"
    records = list(experiment_log.read_records(str(runs_path)))
    assert len(records) == 1
    assert records[0]["strategy_id"] == STRATEGY_ID


def test_main_leaves_existing_signal_file_untouched_when_no_signal_today(server, tmp_path, monkeypatch):
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    now_ms = BASE_DAY_MS + DEFAULT_FETCH_BARS * DAY_STEP
    _seed_warmup_server(server, now_ms=now_ms, fetch_bars=DEFAULT_FETCH_BARS, jump_price=None)
    monkeypatch.setattr("live.generate_daily_signal._current_time_ms", lambda: now_ms)

    signal_path = tmp_path / "signal.json"
    signal_path.write_text("PRE-EXISTING SIGNAL CONTENT", encoding="utf-8")
    runs_path = tmp_path / "live_signals.jsonl"
    db_path = tmp_path / "klines.sqlite3"

    exit_code = main(
        [
            "--db-path",
            str(db_path),
            "--signal-path",
            str(signal_path),
            "--runs-path",
            str(runs_path),
        ]
    )

    assert exit_code == 0
    assert signal_path.read_text(encoding="utf-8") == "PRE-EXISTING SIGNAL CONTENT"


def test_main_exits_with_error_when_base_url_is_missing(monkeypatch):
    monkeypatch.delenv("BINGX_BASE_URL", raising=False)
    with pytest.raises(SystemExit) as exc_info:
        main([])
    assert exc_info.value.code != 0  # a real error exit, not a clean/successful 0


def test_main_warns_when_fetch_bars_is_below_the_warmup_floor(server, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    now_ms = BASE_DAY_MS + 10 * DAY_STEP
    _seed_warmup_server(server, now_ms=now_ms, fetch_bars=10, jump_price=None)
    monkeypatch.setattr("live.generate_daily_signal._current_time_ms", lambda: now_ms)

    with caplog.at_level("WARNING"):
        exit_code = main(
            [
                "--fetch-bars",
                "10",
                "--db-path",
                str(tmp_path / "klines.sqlite3"),
                "--signal-path",
                str(tmp_path / "signal.json"),
                "--runs-path",
                str(tmp_path / "live_signals.jsonl"),
            ]
        )

    assert exit_code == 0
    assert any("below the strategy's own" in message for message in caplog.messages)


def test_main_warns_distinctly_when_fetched_bars_fall_short_of_the_request(server, tmp_path, monkeypatch, caplog):
    """Different scenario from the --fetch-bars-too-low case above: here
    the REQUEST is fine (default 300), but the exchange itself only has
    fewer bars actually available in that range (e.g. a real retention
    boundary or a genuine gap) -- main() must warn with a message that
    distinguishes this from both a bad --fetch-bars choice and a genuine
    'no signal today' decision, and must skip signal generation entirely
    (a real CodeRabbit review finding on this task's PR).
    """
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    now_ms = BASE_DAY_MS + DEFAULT_FETCH_BARS * DAY_STEP
    end_ms = (now_ms // DAY_STEP) * DAY_STEP
    # Only 50 of the 300 requested trailing days actually have data on the
    # (fake) exchange -- the rest is genuinely absent, not merely uncached.
    short_times = [end_ms - i * DAY_STEP for i in range(1, 51)]
    server.set_klines(short_times)
    monkeypatch.setattr("live.generate_daily_signal._current_time_ms", lambda: now_ms)

    runs_path = tmp_path / "live_signals.jsonl"

    with caplog.at_level("WARNING"):
        exit_code = main(
            [
                "--db-path",
                str(tmp_path / "klines.sqlite3"),
                "--signal-path",
                str(tmp_path / "signal.json"),
                "--runs-path",
                str(runs_path),
            ]
        )

    assert exit_code == 0
    assert not runs_path.exists()  # generate_signal() (and fit()) never ran for this short-data run
    assert any("only 50 klines available" in message for message in caplog.messages)
    # And the OTHER warning (bad --fetch-bars choice) must NOT have fired --
    # the request itself (default 300) was fine, only the exchange's real
    # data came up short.
    assert not any("--fetch-bars=" in message for message in caplog.messages)


def test_main_retry_reproduces_the_identical_intent_id(server, tmp_path, monkeypatch):
    """The real scenario scripts/paper-trading-daily-signal.sh's own
    retry logic depends on: two independent main() invocations for the
    SAME UTC day's real decision must write the identical intent_id, not
    a fresh random one each time -- otherwise a retry after an
    interruption (e.g. between a successful write and that script's own
    completion-marker update -- see its header comment) could look like
    a genuinely new order intent to the downstream Java
    FileSignalSource, risking a real duplicate (VST) order for what is
    actually the same underlying decision. A real CodeRabbit review
    finding on this task's PR.
    """
    monkeypatch.setenv("BINGX_BASE_URL", server.base_url)
    now_ms = BASE_DAY_MS + DEFAULT_FETCH_BARS * DAY_STEP
    _seed_warmup_server(server, now_ms=now_ms, fetch_bars=DEFAULT_FETCH_BARS, jump_price="200")
    monkeypatch.setattr("live.generate_daily_signal._current_time_ms", lambda: now_ms)

    signal_path = tmp_path / "signal.json"
    db_path = tmp_path / "klines.sqlite3"
    common_args = [
        "--db-path",
        str(db_path),
        "--signal-path",
        str(signal_path),
    ]

    exit_code = main(common_args + ["--runs-path", str(tmp_path / "live_signals_1.jsonl")])
    assert exit_code == 0
    first_intent_id = json.loads(signal_path.read_text(encoding="utf-8"))["intent_id"]

    # A second, fully independent invocation simulating a retry -- same
    # real "today," same underlying (cached) kline data.
    exit_code2 = main(common_args + ["--runs-path", str(tmp_path / "live_signals_2.jsonl")])
    assert exit_code2 == 0
    second_intent_id = json.loads(signal_path.read_text(encoding="utf-8"))["intent_id"]

    assert first_intent_id == second_intent_id
