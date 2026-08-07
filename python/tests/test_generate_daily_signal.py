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
def _no_real_sleep(monkeypatch):
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
    assert not (tmp_path / "latest.json.tmp").exists()


def test_write_signal_atomically_uses_os_replace_for_the_final_move(tmp_path, monkeypatch):
    target = tmp_path / "latest.json"
    calls = []
    real_replace = os.replace

    def spy_replace(src, dst):
        calls.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr("live.generate_daily_signal.os.replace", spy_replace)

    write_signal_atomically(_order_intent(), target)

    assert len(calls) == 1
    src, dst = calls[0]
    assert dst == str(target)
    assert src == str(target) + ".tmp"


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
    with pytest.raises(SystemExit):
        main([])


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
