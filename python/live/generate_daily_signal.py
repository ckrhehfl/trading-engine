"""`python -m live.generate_daily_signal` -- the daily production signal
runner for `daily-tsmom-ensemble` (Paper-trading bridge Task B). See
`.planning/paper-trading-b-signal-runner.md` and CLAUDE.md's "Paper
Trading Policy Exception -- `daily-tsmom-ensemble`" for the full context
this script operates under.

Intended to run once per day, shortly after the UTC daily candle closes,
via OS cron (installing the actual crontab entry is out of scope for this
task -- see the module-level docstring's "Scheduling" section below for
the suggested line). Safe to invoke manually or re-run: fetching is
idempotent (`data.backfill.sync_range`), and the strategy itself is only
ever driven by the real, current kline history each time -- there is no
persisted strategy state between invocations (see "No cross-invocation
state" below).

## Pipeline, in order

1. **Fetch** (`fetch_live_klines`): pull the most recent `fetch_bars`
   (default 300) trailing BTC-USDT `1d` klines directly from the live
   BingX API via `data.bingx_klines.iter_klines_range`, base URL from the
   `BINGX_BASE_URL` environment variable (never hardcoded -- see
   `.github/workflows/bingx-hostname-guard.yml`), cached into the shared
   SQLite store via `data.backfill.sync_range`'s real idiom (idempotent,
   only genuinely missing sub-ranges are ever fetched). This is a real
   *live* fetch against current data -- deliberately NOT
   `research.holdout.load_research_klines`/`load_holdout_klines`, which
   only ever serve historical backtest/holdout splits and would refuse or
   silently clamp away exactly the current data this script needs.
2. **Convert** (`_kline_row_to_kline`): `data.bingx_klines.KlineRow`
   (ms-timestamp-keyed, the wire/storage format) -> `backtest.kline.Kline`
   (datetime-keyed, the backtest engine's internal format). This module
   owns its own small conversion rather than importing
   `research.holdout`'s private, underscore-prefixed
   `_kline_row_to_kline` -- matching this codebase's established "each
   module owns its own small conversion" precedent (e.g.
   `research/holdout.py`'s own `_funding_row_to_rate` next to
   `_kline_row_to_kline`, or `bingx_funding.py` vs `store.py`).
3. **Run the strategy** (`generate_signal`): build a fresh
   `DailyTsmomEnsembleTrainable` -- same `strategy_id`/`strategy_version`/
   `fee_bps`/`slippage_bps` as this strategy's two pre-registered holdout
   confirmations (`sr-v`, `sr-ab`), for consistency with the
   backtested/holdout-confirmed configuration -- call `fit()` once (its
   own in-sample scoring pass logs exactly one `backtest_run` record),
   then feed the returned, freshly-bound `Strategy` one kline at a time,
   oldest to newest (`strategy([kline])` -- it is stateful and only ever
   reads `window[-1]`, confirmed by reading
   `DailyTsmomEnsembleStrategy.__call__` directly). The LAST call's return
   value is today's real decision: a real `OrderIntent` on a sign-category
   change ("Option B" emission), or `None` on "hold, nothing changed".
4. **Emit** (`write_signal_atomically`): a real decision is serialized
   (`OrderIntent.model_dump_json()`) and written ATOMICALLY -- a
   process-unique `.tmp` sibling written first, then `os.replace()`'d
   onto the final path (atomic on POSIX) -- to
   `var/live/signals/<symbol>/daily-tsmom-ensemble/latest.json`
   (`var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json` for this
   script's own `DEFAULT_SYMBOL`; see `default_signal_path()`). A `None`
   decision leaves that file completely untouched: this is deliberate, and
   matches the downstream Java `FileSignalSource`'s (a parallel task) "no
   new file content observed = nothing new to act on" semantics for
   "hold, no new order" -- there is no empty/sentinel write for "nothing
   happened today."

## The one correctness rule this entire module exists to protect

`DailyTsmomEnsembleTrainable.fit()` unconditionally logs a `backtest_run`
record via `research.experiment_log.log_run` -- every one of this
project's 100+ research trials got counted this way, and that count (`N`)
is load-bearing for the Deflated Sharpe Ratio math this project's
Eligibility Bar depends on everywhere else. A daily production run
calling `fit()` every day forever, logging into the DEFAULT path
(`research.experiment_log.DEFAULT_RUNS_PATH`, `"runs/experiments.jsonl"`),
would silently and permanently corrupt that count. This module NEVER
passes that default: `generate_signal`'s own `runs_path` parameter
defaults to `LIVE_RUNS_PATH` (`"runs/live_signals.jsonl"`) below, a
separate, git-committed (see `.gitignore`'s `!runs/live_signals.jsonl`
exception to its blanket `runs/*` rule) operational audit trail that is
never scanned by `research.overfitting_check`'s trial-counting logic
(that module only ever reads whatever path it is explicitly pointed at --
by default `research.experiment_log.DEFAULT_RUNS_PATH`, which this file
is not). See `.planning/paper-trading-b-signal-runner.md`'s "runs_path
isolation" section for the real, tested evidence this rests on, and
`test_generate_daily_signal.py::test_generate_signal_never_touches_the_default_research_runs_path`
for the test that proves it directly against `DailyTsmomEnsembleTrainable`
(not merely against this module's own plumbing).

## No cross-invocation state

Every invocation re-fetches (from cache where possible) the full trailing
`fetch_bars` window and replays the ENTIRE strategy from a cold, flat
start -- there is no persisted `DailyTsmomEnsembleStrategy` instance (with
its internal `_position_sign`/`_position_quantity`) between daily runs.
This is safe *only* because `fetch_bars` (300) comfortably exceeds the
strategy's own `warmup_bars` (253, see `MIN_WARMUP_BARS` below): every
invocation reconstructs the exact same trailing sign-history the strategy
would have accumulated had it been running continuously, so a fresh
replay and a hypothetical always-running process agree on today's
decision. Reducing `--fetch-bars` below `MIN_WARMUP_BARS` would silently
break this equivalence (the strategy would never warm up at all -- see
the warning `main()` logs in that case).

## Scheduling (not installed by this script)

    5 0 * * *  cd /path/to/trading-engine && PYTHONPATH=python python/.venv/bin/python -m live.generate_daily_signal

Shortly after the UTC daily candle closes, so the most recently CLOSED
daily bar is available. No new scheduling framework -- matches this
project's existing "no cron/systemd pattern exists yet, keep it minimal"
posture (see the governing plan doc,
`.planning/paper-trading-b-signal-runner.md`'s "Scheduling" section).
Installing the actual crontab entry is explicitly out of scope for this
task.
"""

import argparse
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from backtest.kline import Kline
from data.backfill import DEFAULT_DB_PATH, sync_range
from data.bingx_klines import KlineRow
from data.store import connect, fetch_klines
from research.strategies.daily_tsmom_ensemble import DailyTsmomEnsembleTrainable
from schemas.order_intent import OrderIntent

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "BTC-USDT"
DEFAULT_INTERVAL = "1d"
_DAY_MS = 86_400_000

STRATEGY_ID = "daily-tsmom-ensemble"

# Same `strategy_version` this strategy's two pre-registered holdout
# confirmations used (`sr-v`'s BingX 2021-2024 run, `sr-ab`'s Binance
# 2017-2021 replication) -- both logged `"strategy_version": "v1"` against
# `research/strategies/daily_tsmom_ensemble.py`, unchanged since either
# run. Kept in sync with that file; bump here if that module's own
# version concept is ever formalized/incremented.
STRATEGY_VERSION = "v1"

# Same fee/slippage the registered configuration
# (`configs/research/preregistrations/daily-tsmom-ensemble-1d-holdout.json`'s
# `procedure` section) and both real holdout runs used -- reused here for
# consistency with the backtested/holdout-confirmed configuration, not
# independently chosen for production. See
# `.planning/paper-trading-b-signal-runner.md` for the citation.
FEE_BPS = Decimal("5")
SLIPPAGE_BPS = Decimal("2")

# `runs/live_signals.jsonl` -- deliberately NOT
# `research.experiment_log.DEFAULT_RUNS_PATH` ("runs/experiments.jsonl").
# See this module's docstring, "The one correctness rule this entire
# module exists to protect."
LIVE_RUNS_PATH = "runs/live_signals.jsonl"

# `DailyTsmomEnsembleStrategy.__init__` computes
# `self._warmup_bars = max(lookbacks) + 1`; with the strategy's own
# `DEFAULT_LOOKBACKS = (21, 63, 126, 252)` (unmodified, this module never
# overrides it), that's `252 + 1 = 253` -- confirmed by reading
# `research/strategies/daily_tsmom_ensemble.py` directly, not assumed.
# The separate volatility estimator
# (`RollingRealizedVolatility`, `vol_period=20` by default) warms up after
# only 21 closes -- well before the ensemble's own 253-bar gate -- so 253
# remains the true, binding minimum regardless of the vol estimator.
MIN_WARMUP_BARS = 253

# Fetched (not 253) for a safety margin against e.g. a single-day gap in
# BingX's own retained history right at the warmup boundary -- see the
# module docstring's "No cross-invocation state" section for why this
# must stay >= MIN_WARMUP_BARS.
DEFAULT_FETCH_BARS = 300

# New, gitignored tree (see .gitignore's `var/live/` entry), mirroring
# `python/data/var/`'s existing gitignore treatment for locally-generated,
# non-source runtime data.


def default_signal_path(symbol: str = DEFAULT_SYMBOL) -> str:
    """`var/live/signals/{symbol}/{STRATEGY_ID}/latest.json`, computed
    from the actual `symbol` in play rather than always the import-time
    `DEFAULT_SYMBOL` constant -- so `main()`'s own default (when
    `--signal-path` isn't given) tracks a caller-supplied `--symbol`
    instead of silently pointing at the BTC-USDT path regardless (a real
    CodeRabbit review finding on this task's PR).
    """
    return f"var/live/signals/{symbol}/{STRATEGY_ID}/latest.json"


DEFAULT_SIGNAL_PATH = default_signal_path()


def _current_time_ms() -> int:
    """The real current instant, as epoch ms -- broken out as its own
    function purely so `main()`'s notion of "now" is a single,
    monkeypatchable seam for tests, rather than requiring a test to
    replace the whole `datetime` module. Production behavior is
    unchanged: this always returns the real current time.
    """
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _kline_row_to_kline(row: KlineRow) -> Kline:
    """`KlineRow` (ms-timestamp-keyed, `data/`'s wire/storage format) ->
    `Kline` (datetime-keyed, `backtest/`'s internal simulation format).

    This module's own small conversion -- deliberately NOT an import of
    `research.holdout`'s private `_kline_row_to_kline`, matching this
    codebase's established "each module owns its own small conversion"
    precedent (see `research/holdout.py`'s own `_funding_row_to_rate`
    docstring, which names this exact precedent for itself). Integer
    floor-division (not `/1000`) avoids any float round-trip on the
    timestamp, same reasoning as the function this mirrors.
    """
    return Kline(
        open_time=datetime.fromtimestamp(row.open_time_ms // 1000, tz=timezone.utc),
        open=row.open,
        high=row.high,
        low=row.low,
        close=row.close,
        volume=row.volume,
    )


def fetch_live_klines(
    *,
    symbol: str = DEFAULT_SYMBOL,
    interval: str = DEFAULT_INTERVAL,
    base_url: str,
    db_path: str | Path = DEFAULT_DB_PATH,
    fetch_bars: int = DEFAULT_FETCH_BARS,
    now_ms: int | None = None,
) -> list[Kline]:
    """Fetch (idempotently, via the shared cache) and return the most
    recent `fetch_bars` trailing daily klines up to (NOT including)
    today's still-forming daily bar, ascending (oldest -> newest).

    Reuses `data.backfill.sync_range`'s real idiom directly -- the same
    idempotent, cache-first, only-fetch-genuinely-missing-sub-ranges path
    `backfill.py`'s own CLI drives -- against the SAME shared SQLite store
    (`data.backfill.DEFAULT_DB_PATH`) `backfill.py`/`research.holdout` use
    elsewhere in this project, so a kline this script fetches today is
    available to any other tool reading that same cache tomorrow (and vice
    versa). This is a genuine live fetch: whatever isn't already cached is
    requested from the real BingX API at `base_url`.

    `now_ms` defaults to the real current time; overridable for tests so
    "today" is deterministic. `end_ms` is `now_ms` floored to the
    interval's own grid -- for `1d` that means "the most recent UTC
    midnight," which excludes the current, still-forming daily bar, same
    rounding rule `data.backfill.main()`'s own "now" default already uses
    for this same reason.
    """
    step = _DAY_MS
    if now_ms is None:
        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    end_ms = (now_ms // step) * step
    start_ms = end_ms - fetch_bars * step

    conn = connect(db_path)
    try:
        sync_range(symbol, interval, start_ms, end_ms, conn=conn, base_url=base_url)
        rows = fetch_klines(conn, symbol, interval, start_ms, end_ms)
    finally:
        conn.close()

    return [_kline_row_to_kline(row) for row in rows]


def generate_signal(
    klines: list[Kline],
    *,
    parent_run_id: str,
    symbol: str = DEFAULT_SYMBOL,
    runs_path: str = LIVE_RUNS_PATH,
) -> OrderIntent | None:
    """Run `DailyTsmomEnsembleTrainable` (unmodified) against `klines`
    (ascending oldest -> newest) and return today's real decision: the
    LAST kline's call result, or `None` if the ensemble's sign category
    did not change on the most recent bar (Option B emission -- see
    `research/strategies/daily_tsmom_ensemble.py`'s module docstring).

    `runs_path` defaults to `LIVE_RUNS_PATH`, never
    `research.experiment_log.DEFAULT_RUNS_PATH` -- see this module's
    docstring, "The one correctness rule this entire module exists to
    protect." A caller can still override it (e.g. for tests), but never
    to the research default; nothing in this module ever constructs a
    `DailyTsmomEnsembleTrainable` without passing `runs_path` explicitly.

    An empty `klines` list returns `None` immediately, WITHOUT
    constructing a `DailyTsmomEnsembleTrainable` or calling `fit()` --
    there is nothing to train or score, and calling `fit()` on an empty
    window would still log a degenerate `backtest_run` record for no
    real work done. A caller (`main()`) still logs this case itself with
    its own more specific message, since a genuinely empty fetch usually
    indicates a problem worth surfacing distinctly from "no signal
    today".
    """
    if not klines:
        return None

    trainable = DailyTsmomEnsembleTrainable(
        strategy_id=STRATEGY_ID,
        strategy_version=STRATEGY_VERSION,
        fee_bps=FEE_BPS,
        slippage_bps=SLIPPAGE_BPS,
        runs_path=runs_path,
    )
    strategy = trainable.fit(klines, {"symbol": symbol}, parent_run_id=parent_run_id)

    decision: OrderIntent | None = None
    for kline in klines:
        decision = strategy([kline])
    return decision


def write_signal_atomically(intent: OrderIntent, path: str | Path = DEFAULT_SIGNAL_PATH) -> None:
    """Serialize `intent` (`model_dump_json()`) and write it to `path`
    ATOMICALLY: a process-unique `.tmp` sibling is written first (and
    fully flushed to disk before being renamed), then `os.replace()`'d
    onto the final path. `os.replace()` is atomic on POSIX -- a
    concurrent reader (the downstream Java `FileSignalSource`, built in a
    parallel task) can never observe a partially-written file, only the
    complete previous content or the complete new content.

    The temp filename includes the PID and a random UUID (not a fixed
    `<name>.tmp`) so two overlapping invocations (e.g. a stuck cron job
    racing a manual re-run) never write through the same temp path and
    clobber each other's in-flight write -- each still ends by atomically
    replacing the SAME final `target`, so the last `os.replace()` to run
    still wins cleanly, but neither can corrupt the other's temp file
    content first. If anything fails before the rename completes, the
    temp file is removed rather than left behind (a real CodeRabbit
    review finding on this task's PR).

    After a successful rename, the parent directory's own fd is fsync'd
    too -- best-effort, never raises -- mirroring
    `research.experiment_log._append_record`'s identical two-tier
    durability pattern: `os.fsync` on the temp file's own fd (above)
    only guarantees the file's *data*, not that the directory entry
    pointing at it has itself reached disk (the well-known POSIX
    "fsync doesn't sync the containing directory" gap).

    Creates `path`'s parent directory tree if it doesn't exist yet (the
    first real signal this process ever writes).
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.parent / f".{target.name}.{os.getpid()}.{uuid4().hex}.tmp"
    payload = intent.model_dump_json()

    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, target)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    try:
        dir_fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument("--interval", default=DEFAULT_INTERVAL)
    parser.add_argument("--fetch-bars", type=int, default=DEFAULT_FETCH_BARS)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    parser.add_argument(
        "--signal-path",
        default=None,
        help=(
            "default: var/live/signals/<--symbol>/daily-tsmom-ensemble/latest.json, "
            "computed from --symbol (see default_signal_path())"
        ),
    )
    parser.add_argument("--runs-path", default=LIVE_RUNS_PATH)
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BINGX_BASE_URL"),
        help="BingX API base URL (default: $BINGX_BASE_URL env var)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = _parse_args(argv)
    signal_path = args.signal_path if args.signal_path is not None else default_signal_path(args.symbol)

    if not args.base_url:
        raise SystemExit(
            "base URL is required: pass --base-url or set BINGX_BASE_URL "
            "(never hardcoded in source -- see bingx-hostname-guard.yml)"
        )

    if args.fetch_bars < MIN_WARMUP_BARS:
        logger.warning(
            "generate_daily_signal: --fetch-bars=%d is below the strategy's own %d-bar warmup "
            "requirement -- every call will return None (warmup only, never a real signal)",
            args.fetch_bars,
            MIN_WARMUP_BARS,
        )

    klines = fetch_live_klines(
        symbol=args.symbol,
        interval=args.interval,
        base_url=args.base_url,
        db_path=args.db_path,
        fetch_bars=args.fetch_bars,
        now_ms=_current_time_ms(),
    )
    logger.info(
        "fetched %d klines for %s/%s%s",
        len(klines),
        args.symbol,
        args.interval,
        f" (most recent open_time={klines[-1].open_time.isoformat()})" if klines else "",
    )
    # Below MIN_WARMUP_BARS (which includes the zero-klines case) is a
    # DATA problem (an empty/short fetch -- retention limits, a real gap,
    # a network hiccup that still returned partial cached data), not a
    # real "the strategy looked and found no signal today" decision.
    # Distinguished with its own warning and an early return, rather than
    # calling generate_signal() and letting it silently return None for a
    # reason that has nothing to do with today's actual sign -- a real
    # CodeRabbit review finding on this task's PR: conflating the two
    # would make an operator reading the logs unable to tell "nothing to
    # trade today" from "this run couldn't get enough data to decide."
    if len(klines) < MIN_WARMUP_BARS:
        logger.warning(
            "generate_daily_signal: only %d klines available (fetched from a %d-bar request), below "
            "the strategy's %d-bar warmup requirement -- skipping signal generation this run (this is "
            "NOT a real 'no signal today' decision, just insufficient data)",
            len(klines),
            args.fetch_bars,
            MIN_WARMUP_BARS,
        )
        return 0

    parent_run_id = f"live-{datetime.now(timezone.utc):%Y-%m-%d}-{uuid4()}"
    decision = generate_signal(
        klines,
        parent_run_id=parent_run_id,
        symbol=args.symbol,
        runs_path=args.runs_path,
    )

    if decision is None:
        logger.info("no signal today (no sign-category change) -- signal file left untouched")
        return 0

    write_signal_atomically(decision, signal_path)
    logger.info(
        "wrote signal: side=%s quantity=%s symbol=%s intent_id=%s -> %s",
        decision.side,
        decision.quantity,
        decision.symbol,
        decision.intent_id,
        signal_path,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
