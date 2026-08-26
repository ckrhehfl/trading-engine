from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, Sequence

from backtest.fill import Fill, simulate_fill
from backtest.kline import Kline
from backtest.kline_window import KlineWindow
from metrics.position import PositionTracker
from schemas.order_intent import OrderIntent

Strategy = Callable[[Sequence[Kline]], OrderIntent | None]


@dataclass(frozen=True)
class BacktestResult:
    fills: list[Fill] = field(default_factory=list)
    # Index-aligned with `fills`: filled_intents[i] is the OrderIntent that
    # produced fills[i]. Additive — `Fill` itself has no `side` field, so a
    # portfolio/metrics layer consuming this result needs the originating
    # intent to know direction (see python/metrics/).
    filled_intents: list[OrderIntent] = field(default_factory=list)
    # Additive (Scalping Strategy Research Task S7, see .planning/scalp-s7-
    # backtest-insolvency-floor.md). The 0-based index into the `klines`
    # list passed to `run_backtest` at which mark-to-market equity first
    # reached the zero insolvency floor, or `None` if it never did --
    # including whenever the insolvency-tracking feature wasn't enabled
    # (i.e. `starting_equity` was omitted). A hand-built `BacktestResult(...)`
    # that doesn't name this field (every pre-existing test in this
    # codebase) keeps constructing successfully with `insolvent_at_index=
    # None`, unaffected by this field's addition.
    insolvent_at_index: int | None = None


def run_backtest(
    klines: list[Kline],
    strategy: Strategy,
    fee_bps: Decimal,
    slippage_bps: Decimal,
    *,
    starting_equity: Decimal | None = None,
) -> BacktestResult:
    """Deterministic fill simulator — not a P&L/portfolio backtester (see
    schemas/README.md-adjacent design notes in the PR that added this).

    `strategy` is called at each bar with only a `KlineWindow(klines, i + 1)`
    — bars after the current one are never observable through it, so
    lookahead bias is structurally impossible rather than merely avoided by
    convention. `KlineWindow` is an O(1) bounds-checked view (see
    `backtest/kline_window.py`), not a copy — `klines[: i + 1]` was an O(n)
    copy every iteration (O(n^2) over a full run), which mattered once real
    multi-year datasets and walk-forward runs started exercising this loop.

    **Optional hard zero-equity floor (Scalping Strategy Research Task S7)**:
    pass `starting_equity` to have this function additionally track
    mark-to-market equity bar by bar and permanently stop producing new fills
    once equity reaches `Decimal("0")`. This does **not** turn `run_backtest`
    into a portfolio/metrics layer — Sharpe, drawdown, profit factor, and the
    full equity *curve* remain exclusively `metrics.metrics`'s job, computed
    downstream and unchanged by this feature. It is a narrow, opt-in circuit
    breaker only: this function still never has access to a strategy's own
    dynamic position sizing, leverage, or a real margin model; it only
    refuses to keep filling orders once a simple, non-compounding
    reconstruction of equity against a fixed `starting_equity` would already
    be non-positive. See `.planning/scalp-s7-backtest-insolvency-floor.md`
    for the full motivation and design.

    Enabling this **inverts this module's previously clean layering**: with
    `starting_equity` supplied, `backtest.engine` imports and uses
    `metrics.position.PositionTracker` to do the mark-to-market
    reconstruction, rather than reusing a second, hand-rolled tracker — so
    `backtest/` now optionally depends on `metrics/` for this one feature,
    where historically the dependency only ever ran the other way
    (`metrics/` consuming `backtest/`'s output). No circular import results
    (`metrics.position` imports only from `backtest.fill` and
    `schemas.order_intent`, never from `backtest.engine`), but the boundary
    itself has moved, and that is worth knowing before assuming `backtest/`
    stays import-independent of `metrics/` in every configuration.

    Omitting `starting_equity` (the default, `None`) is byte-for-byte
    identical to this function's behavior before this feature existed — every
    existing caller in this codebase is unaffected, and `starting_equity` is
    keyword-only specifically so it can never collide with a positional
    `fee_bps`/`slippage_bps` argument at an existing call site.

    Algorithm, mirroring `metrics.metrics.build_equity_curve`'s own per-bar
    catch-up + mark-to-market pattern (same formula, same ordering — not a
    reinvented one): at the top of each bar `i` (before calling `strategy`),
    every not-yet-applied fill with `fill.fill_time <= klines[i].open_time`
    is applied to a `metrics.position.PositionTracker`, then

        unrealized = 0 if tracker.position_qty == 0 else
                     tracker.position_qty * (klines[i].close - tracker.avg_entry_price)
        equity = starting_equity + tracker.realized_pnl - tracker.cumulative_fees + unrealized

    is compared against `Decimal("0")`. The floor is **hardcoded at exactly
    zero**, not a tunable or margin-aware parameter — a non-zero
    (maintenance-margin-based) floor would need a real margin-rate input this
    engine has no source for, and turning that into a knob here would open a
    margin-modeling question this feature does not attempt to answer.
    Reaching the floor sets a **permanent** insolvency flag (a real liquidated
    account does not un-liquidate itself on a later bar's favorable
    mark-to-market) recorded once, on first crossing, as
    `BacktestResult.insolvent_at_index`. `strategy` is still called every
    remaining bar after insolvency, so a stateful strategy's own internal
    state machine keeps advancing normally — only the resulting `OrderIntent`
    is discarded (never passed to `simulate_fill`, never appended to `fills`/
    `filled_intents`) once insolvent. No liquidation fill or forced close is
    synthesized at the insolvency point — the already-open position (if any)
    is simply left alone, exactly as any other still-open-at-the-end
    position; `metrics.metrics.build_equity_curve`'s existing final-bar
    force-close logic handles it downstream, unchanged. Funding P&L is never
    threaded into the internal tracker here (it is constructed with no
    `funding_rates`) — `run_backtest` has never been funding-aware, and this
    feature does not change that.

    Raises `ValueError` if `starting_equity` is supplied and not strictly
    positive, matching `metrics.metrics.compute_metrics`'s own identical
    check and message.
    """
    if starting_equity is not None and starting_equity <= 0:
        raise ValueError(f"starting_equity must be positive, got {starting_equity}")

    fills: list[Fill] = []
    filled_intents: list[OrderIntent] = []

    # `tracker is None` is the single guard that makes every insolvency-
    # tracking step below a structural no-op when starting_equity isn't
    # supplied — not a value this function otherwise branches on, so the
    # `starting_equity is None` behavior can't subtly drift from what this
    # function did before this feature existed.
    tracker = PositionTracker() if starting_equity is not None else None
    fill_cursor = 0
    insolvent = False
    insolvent_at_index: int | None = None

    for i in range(len(klines)):
        if tracker is not None and not insolvent:
            kline = klines[i]
            while fill_cursor < len(fills) and fills[fill_cursor].fill_time <= kline.open_time:
                tracker.apply(filled_intents[fill_cursor], fills[fill_cursor])
                fill_cursor += 1

            if tracker.position_qty == 0:
                unrealized = Decimal(0)
            else:
                unrealized = tracker.position_qty * (kline.close - tracker.avg_entry_price)
            equity = starting_equity + tracker.realized_pnl - tracker.cumulative_fees + unrealized

            if equity <= 0:
                insolvent = True
                if insolvent_at_index is None:
                    insolvent_at_index = i

        visible = KlineWindow(klines, i + 1)
        intent = strategy(visible)
        if intent is None:
            continue
        if insolvent:
            continue
        fill = simulate_fill(intent, klines, i, fee_bps, slippage_bps)
        if fill is not None:
            fills.append(fill)
            filled_intents.append(intent)

    return BacktestResult(fills=fills, filled_intents=filled_intents, insolvent_at_index=insolvent_at_index)
