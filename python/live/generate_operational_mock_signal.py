"""Emits mock order intents to exercise the paper-trading plumbing.

    PYTHONPATH=python python -m live.generate_operational_mock_signal

**This is not a trading strategy and its output must never be read as
one.** It exists to satisfy Gate A of CLAUDE.md's Paper Trading Pass
Criteria (revised 2026-08-29), which asks whether the system works --
order placement, reconciliation, duplicate rejection, the kill switch,
reporting, uptime -- and explicitly says the signal source is irrelevant
to that question. CLAUDE.md's Implementation Priority section has
permitted exactly this since Priority #6: the paper broker,
`ExchangeAdapter` and supervision loop "can and should be built and
tested with dummy/mock signals independently of a validated strategy."

Gate B, which asks whether a strategy makes money, gets **nothing** from
this file.

## Why a separate generator rather than reusing DummySignalSource

`engine.runtime.DummySignalSource` already exists in Java for this
purpose, but `PaperTradingApp` in `bingx-vst` mode reads a
`FileSignalSource`. Wiring the dummy into that mode would mean changing
`PaperTradingApp` -- trading-plane code -- to add an operational testing
feature. Writing signal files from Python instead needs **no Java change
at all**: the existing `PAPER_TRADING_SIGNAL_PATH`,
`PAPER_TRADING_REPORTS_DIR` and `PAPER_TRADING_TICK_INTERVAL_SECONDS`
environment variables already make a third, fully isolated loop possible.

## Contamination guards, because mixing this with real strategy evidence
## would be the expensive failure

- The signal path is **hardcoded to a `operational-mock` directory** and
  this module refuses to write anywhere else. It cannot be pointed at a
  strategy's signal path by argument or environment.
- `strategy_id` in the emitted intent's `signal_timeframe` is the literal
  string `MOCK-NOT-A-STRATEGY`, so a record produced by it is
  self-identifying wherever it ends up.
- It writes to its own reports directory (configured on the loop, not
  here), so `daily-tsmom-ensemble`'s trade record is never touched.

## What it emits

Alternating LONG/SHORT `GUARDED_MARKET` intents at a fixed small size, so
positions genuinely open and close and the reconciliation path is
exercised rather than a one-way accumulation. Direction alternates from a
counter persisted beside the signal, so a restart does not re-emit the
same side forever.

Size is deliberately tiny (`0.001 BTC`, ~$100 notional against a $100k
paper account, i.e. ~0.1%), well inside the canary tier's 2% max order
notional -- the point is to exercise the path, not to move an account.

## `GUARDED_MARKET` exposure, named rather than left implicit

These intents are `GUARDED_MARKET` with a null `limit_price`, and
CLAUDE.md records that this currently maps to a plain unprotected
`"MARKET"` order on the wire -- "the guard is a name only today" -- which
it gates behind a `Discuss` **before `GUARDED_MARKET` is used against a
real account**.

This loop is not a real account. It runs against BingX **VST**: virtual
funds, demo host, a hardcoded base-URL constant with no environment or
argument override able to point it elsewhere. It is also the same order
type the existing `daily-tsmom-ensemble` VST loop already emits, so this
adds volume on an already-running path rather than opening a new one.

What does change is the count -- roughly 200 orders over Gate A's 15 days
against that strategy's ~1. That is disclosed here deliberately: if the
wire-level guard is ever built, this loop is where its absence would show
up first, and its logs are the natural place to look.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from schemas.order_intent import OrderIntent, OrderType, Side  # noqa: E402

# Hardcoded, with no argument or environment override. See the module
# docstring's contamination guards -- an operational mock that could be
# aimed at a strategy's signal path is one misconfiguration away from
# corrupting that strategy's evidence.
SIGNAL_DIR = Path("var/live/signals/BTC-USDT/operational-mock")
SIGNAL_PATH = SIGNAL_DIR / "latest.json"
COUNTER_PATH = SIGNAL_DIR / "counter.json"

SYMBOL = "BTC-USDT"
QUANTITY = Decimal("0.001")

# Stamped into `signal_timeframe` so any downstream record carries its own
# disclaimer. Not a timeframe, deliberately -- this field is free-form and
# a mock intent should be identifiable from the intent alone.
MOCK_MARKER = "MOCK-NOT-A-STRATEGY"


def _read_counter(path: Path) -> int:
    """The number of intents emitted so far, or 0.

    Persisted so a restart continues alternating rather than re-emitting
    the same side forever -- a loop stuck on one side would accumulate a
    position instead of opening and closing, and would exercise neither
    the flattening path nor reconciliation.
    """
    try:
        return int(json.loads(path.read_text())["emitted"])
    except (OSError, ValueError, KeyError, TypeError):
        return 0


def _write_counter(path: Path, emitted: int) -> None:
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps({"emitted": emitted}))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def build_intent(emitted: int, *, now: datetime | None = None) -> OrderIntent:
    """The `emitted`-th mock intent. Even -> LONG, odd -> SHORT."""
    return OrderIntent(
        intent_id=uuid4(),
        symbol=SYMBOL,
        side=Side.LONG if emitted % 2 == 0 else Side.SHORT,
        order_type=OrderType.GUARDED_MARKET,
        quantity=QUANTITY,
        limit_price=None,
        signal_timeframe=MOCK_MARKER,
        created_at=now or datetime.now(timezone.utc),
    )


def write_atomically(intent: OrderIntent, path: Path) -> None:
    """Same atomic write/rename/fsync discipline as
    `live.generate_daily_signal.write_signal_atomically`, so a reader can
    never observe a partially written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(intent.model_dump_json())
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    try:
        fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass  # best effort, matching the sibling writer


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dry-run", action="store_true",
                    help="print the intent that would be written and exit")
    args = ap.parse_args(argv)

    emitted = _read_counter(COUNTER_PATH)
    intent = build_intent(emitted)

    if args.dry_run:
        print(intent.model_dump_json(indent=2))
        print(f"\n(dry run: would be intent #{emitted} -> {SIGNAL_PATH})", file=sys.stderr)
        return 0

    write_atomically(intent, SIGNAL_PATH)
    _write_counter(COUNTER_PATH, emitted + 1)
    print(f"mock intent #{emitted} {intent.side.value} {intent.quantity} {intent.symbol} "
          f"-> {SIGNAL_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
