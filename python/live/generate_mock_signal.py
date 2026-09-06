"""`python -m live.generate_mock_signal` — order events for Gate A, and
nothing else.

    PYTHONPATH=python python/.venv/bin/python -m live.generate_mock_signal

## Why this exists

Gate A of the Paper Trading Pass Criteria requires **≥ 200 order events**
through the full `OrderIntent → OrderPipeline → RiskGateway → Order →
OrderExecutor` path, across 15 consecutive days.

`daily-tsmom-ensemble` trades **9-17 times per year**. Over 15 days that
is 0-1 events. **The gate is therefore unreachable by waiting**, no
matter how perfectly the loops run — which is a property of the strategy's
frequency, not a fault in the system Gate A is trying to measure.

CLAUDE.md anticipated exactly this and grants standing permission:

> Gate A — Operational readiness. Proves the system, not the strategy.
> **Signal source is irrelevant here** and a dedicated mock generator is
> explicitly allowed, per this file's own standing permission to exercise
> the paper broker, `ExchangeAdapter` and supervision loop "with
> dummy/mock signals independently of a validated strategy."

This is that generator. It is the *only* thing in this repository whose
purpose is to manufacture order flow.

## What it is emphatically NOT

**Not a strategy.** It has no signal, reads no market data, and its
output carries no information about price. Nothing it produces may ever
be read as evidence about anything except whether the plumbing works.

**Not logged as research.** It never touches `runs/experiments.jsonl`,
never calls `experiment_log`, and contributes nothing to the DSR trial
count `N`. Counting a coin-flip generator as a research trial would
inflate `N` and make every real strategy's DSR worse for no reason.

## The safety property that matters most

**Both paper loops read the same signal file by default.** Verified on
the real deployment 2026-09-05: `simulated` and `bingx-vst` were both
constructed with

    signalPath=var/live/signals/BTC-USDT/daily-tsmom-ensemble/latest.json

So a generator writing there at one signal per tick would drive **both**
— and the `bingx-vst` loop submits real orders to a real (demo) exchange.
288 unexplained orders a day against a venue account is not what Gate A
asks for, and is precisely the kind of uncontrolled order flow this
project exists to prevent.

Therefore:

1. This module writes to its **own** path, `MOCK_SIGNAL_PATH`, which no
   loop reads unless it is explicitly pointed there via
   `PAPER_TRADING_SIGNAL_PATH`.
2. It **refuses to write** anywhere under a real strategy's signal
   directory — see `_reject_real_strategy_path`. That check is not a
   convenience; it is the thing standing between a mock generator and a
   venue.
3. Only the `simulated` loop is ever pointed at it. `simulated` uses the
   internal `PaperBroker` and has no venue at all, so its orders cannot
   leave the machine.

Every order still passes through the Java `RiskGateway` in full, exactly
as a real one would. That is the point — it is the path being measured.

## What it emits

One `GUARDED_MARKET` `OrderIntent` per invocation, **alternating side**
so the simulated position oscillates around flat rather than
accumulating a position the loop then has to carry. Quantity is a small
fixed constant, far under the canary tier's own notional limit, so the
`RiskGateway` approves on the merits rather than being exercised only on
its rejection path.

Driven from the same 5-minute cron as the watchdog: 288 invocations a
day, so Gate A's 200-event floor is cleared inside one day and the
remaining 14 are about *uptime*, which is what they are for.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

from schemas.order_intent import OrderIntent, OrderType, Side

logger = logging.getLogger(__name__)

DEFAULT_SYMBOL = "BTC-USDT"

# Deliberately NOT under `var/live/signals/<symbol>/<strategy>/`. That
# tree is where real strategies publish, and both paper loops default to
# reading one of its paths. Keeping mock output in a sibling directory
# means pointing a loop at it has to be a deliberate act
# (`PAPER_TRADING_SIGNAL_PATH`), never an accident of defaults.
MOCK_SIGNAL_DIR = Path("var/live/signals/_mock")
MOCK_SIGNAL_PATH = MOCK_SIGNAL_DIR / "latest.json"

# The directory every real strategy publishes under. Writing anywhere
# below it is refused outright.
REAL_SIGNAL_ROOT = Path("var/live/signals")

# 0.001 BTC. At ~$80k that is ~$80 of notional against a canary tier
# allowing 2% of equity, so the RiskGateway approves it on the merits.
# A quantity large enough to be rejected would exercise only the refusal
# path and prove nothing about the approval path Gate A is counting.
MOCK_QUANTITY = Decimal("0.001")

# Alternating side, persisted so it survives process restarts. Without
# this every invocation is a fresh process and would emit the same side
# forever, walking the simulated position steadily in one direction until
# it dominates the equity curve — which would make the daily reports
# harder to read for no benefit.
SIDE_STATE_PATH = MOCK_SIGNAL_DIR / ".last-side"


def _reject_real_strategy_path(target: Path) -> None:
    """Allow only `MOCK_SIGNAL_DIR`; refuse everything else.

    The one guard that matters. Both paper loops default to a path under
    `var/live/signals/<symbol>/<strategy>/`, and the `bingx-vst` loop
    submits to a real demo venue — so a mock generator that could write
    there would put 288 manufactured orders a day onto an exchange
    account.

    An **allowlist, not a blocklist**. An earlier version only rejected
    paths inside `REAL_SIGNAL_ROOT`, which let anything outside it
    through -- so a mistyped `--signal-path` could create or overwrite an
    unrelated file anywhere on the box. Enumerating what must not be
    written is a losing game; there is exactly one directory this module
    has business writing to.

    Compares resolved paths, so `..` cannot walk out of the mock
    directory and back into the real tree. Raises rather than warns:
    there is no sensible way to continue.
    """
    resolved = target.resolve()
    allowed = MOCK_SIGNAL_DIR.resolve()
    if resolved.is_relative_to(allowed):
        return
    inside_real = resolved.is_relative_to(REAL_SIGNAL_ROOT.resolve())
    why = (
        f"that is inside {REAL_SIGNAL_ROOT}/, where real strategies publish and "
        f"where the bingx-vst loop reads -- a mock signal there would submit "
        f"manufactured orders to a real exchange account"
        if inside_real else
        f"this module only ever writes inside {MOCK_SIGNAL_DIR}/"
    )
    raise ValueError(
        f"refusing to write a mock signal to {target} -- {why}. Write to "
        f"{MOCK_SIGNAL_PATH} (the default) instead."
    )


def _peek_side(state_path: Path) -> Side:
    """What `_next_side` would return, without writing anything."""
    try:
        previous = state_path.read_text(encoding="utf-8").strip()
    except (OSError, ValueError):
        previous = None
    return Side.SHORT if previous == Side.LONG.value else Side.LONG


def _next_side(state_path: Path) -> Side:
    """Alternate BUY/SELL across invocations, surviving restarts.

    A missing or unreadable state file yields `LONG`, which is a fine
    starting point and never an error: losing the alternation costs
    nothing but a slightly one-sided position, and failing the run would
    cost a Gate A order event.
    """
    side = _peek_side(state_path)
    try:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(side.value, encoding="utf-8")
    except OSError as exc:
        logger.warning("could not persist side state to %s: %s", state_path, exc)
    return side


def build_mock_intent(symbol: str = DEFAULT_SYMBOL, *, side: Side | None = None) -> OrderIntent:
    """One `GUARDED_MARKET` intent carrying no market information.

    `signal_timeframe` is set to `"mock"` so anything downstream reading
    these records can tell at a glance that they are not a strategy's
    output — the field is free-form and no real strategy uses that value.
    """
    return OrderIntent(
        intent_id=uuid4(),
        symbol=symbol,
        side=side if side is not None else Side.LONG,
        order_type=OrderType.GUARDED_MARKET,
        quantity=str(MOCK_QUANTITY),
        limit_price=None,
        signal_timeframe="mock",
        created_at=datetime.now(timezone.utc),
    )


def write_signal_atomically(intent: OrderIntent, path: str | Path) -> None:
    """Write `intent` to `path` atomically, refusing a real strategy path.

    Same discipline as `generate_daily_signal.write_signal_atomically`: a
    process-unique temp sibling, flushed and fsynced, then `os.replace`
    (atomic on POSIX) so the Java `FileSignalSource` can never read a
    half-written file. The temp file is removed if anything fails before
    the rename.
    """
    target = Path(path)
    _reject_real_strategy_path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(intent.model_dump_json())
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbol", default=DEFAULT_SYMBOL)
    parser.add_argument(
        "--signal-path", default=str(MOCK_SIGNAL_PATH),
        help="where to write. Refused if inside a real strategy's signal tree.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="build and print the intent without writing it",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if args.dry_run:
        # Peek without persisting: a dry run that advanced the side state
        # would change the next real signal, which is the one thing a
        # "does not write" flag must not do.
        print(build_mock_intent(args.symbol, side=_peek_side(SIDE_STATE_PATH)).model_dump_json())
        return 0

    intent = build_mock_intent(args.symbol, side=_next_side(SIDE_STATE_PATH))

    try:
        write_signal_atomically(intent, args.signal_path)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    logger.info(
        "mock signal written: %s %s %s -> %s",
        intent.side.value, intent.quantity, intent.symbol, args.signal_path,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
