"""Standalone gap-detection preflight for a `BTC-USDT`/`1m` holdout
access -- Scalping Strategy Research Task S4.

## Why this exists, and why it is not inside `run_preregistered_holdout.py`

Task S1 confirmed real BingX `BTC-USDT`/`1m` kline retention has **2
known, real, permanent gaps** (see CLAUDE.md's "Exchange API Facts --
BingX" section) -- unlike every other interval this project has ever
run a holdout confirmation against (`15m`/`1h`/`1d`, all confirmed
zero-gap). Task S3 found that neither `research/walkforward.py`'s fold
generation nor `python/backtest/`'s bar-by-bar iteration detects a
timestamp gap in the underlying kline sequence -- both are pure
positional/bar-count arithmetic -- and, because the 1m holdout design
(Task S3) uses the *entire* retention window rather than a chosen
sub-range, there is no window-selection step that could dodge the 2
known gaps even if desired.

This module is the "fail closed on an *unexpected* gap" mechanism
CLAUDE.md's Task S3 design requires before any real 1m holdout access:
**not** "fail on any gap" (the 2 known gaps are real, permanent, and
unfixable at the source -- a bare "fail on any gap" rule would block
the whole 1m holdout design outright), but "fail on a gap set that
differs from the 2 already-disclosed ones" -- fewer, more, or relocated
gaps, e.g. from a future backfill re-run that turns up something new.

Deliberately a **separate, standalone** module, not a change to
`research/run_preregistered_holdout.py`: that runner is shared,
already-proven infrastructure (it has already executed `sr-v`/`sr-ab`'s
real holdout confirmations), and this gap check is specific to the one
interval (`1m`) that currently has any known gaps at all -- every other
interval's own holdout confirmation would trivially pass a check that
requires "zero gaps, exactly," so folding this into the shared runner
would add an interval-specific concern to a generic module, unasked by
this task's own scope ("touch only what the task requires").

## What this does NOT do

Does not call `research.holdout.load_holdout_klines` or touch
`runs/experiments.jsonl`'s holdout-access tracking in any way -- this
reads `find_missing_ranges`' own *metadata* (which timestamps are
present/absent), never the real price/volume content of any bar, and
is safe to run any number of times without spending the single,
enforced-once holdout access.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path

from data.backfill import DEFAULT_DB_PATH
from data.store import connect, find_missing_ranges

logger = logging.getLogger(__name__)

SYMBOL = "BTC-USDT"
INTERVAL = "1m"

# The 2 known, real, disclosed gaps (CLAUDE.md's "Exchange API Facts --
# BingX" section), confirmed via a real backfill.py run (Scalping
# Strategy Research Task S1) and re-confirmed directly against the real
# database while building this module. Computed from the real ISO
# timestamps via `datetime`, not hand-typed millisecond values.


def _ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


KNOWN_GAPS: tuple[tuple[int, int], ...] = (
    (_ms("2025-04-25T06:54:00+00:00"), _ms("2025-04-25T06:57:00+00:00")),
    (_ms("2026-02-13T20:32:00+00:00"), _ms("2026-02-13T20:36:00+00:00")),
)


class UnexpectedGapError(RuntimeError):
    """Raised when the real gap set for the registered `BTC-USDT`/`1m`
    range does not match `KNOWN_GAPS` exactly -- fewer, more, or
    relocated gaps. Fail-closed: an undetermined/changed gap situation
    must never be silently treated as "safe to proceed," matching this
    project's established fail-closed discipline elsewhere (e.g.
    `KrxMarketCalendar`'s holiday-lookup rule).
    """


def verify_1m_gaps(
    start_ms: int,
    end_ms: int,
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> list[tuple[int, int]]:
    """Diff the real `BTC-USDT`/`1m` kline sequence in `[start_ms, end_ms)`
    against `find_missing_ranges` and confirm the result matches
    `KNOWN_GAPS` exactly.

    Returns the real gap list (equal to `KNOWN_GAPS`, as a fresh list) on
    success. Raises `UnexpectedGapError` if the real gap set differs in
    any way -- this is the fail-closed gate that must run before a real
    1m holdout access.
    """
    conn = connect(db_path)
    try:
        gaps = find_missing_ranges(conn, SYMBOL, INTERVAL, start_ms, end_ms)
    finally:
        conn.close()

    if tuple(gaps) != KNOWN_GAPS:
        raise UnexpectedGapError(
            f"real gap set for {SYMBOL}/{INTERVAL} in [{start_ms}, {end_ms}) is {gaps!r}, which "
            f"does not exactly match the 2 known, disclosed gaps {KNOWN_GAPS!r} (see CLAUDE.md's "
            "'Exchange API Facts -- BingX' section). This could mean a future backfill re-run "
            "surfaced a new gap, or the requested range differs from what those 2 known gaps were "
            "confirmed against -- refusing to proceed with a real holdout access until this is "
            "understood and, if genuinely new, disclosed."
        )

    logger.info(
        "verify_1m_gaps: real gap set for %s/%s in [%d, %d) matches the 2 known, disclosed gaps "
        "exactly -- safe to proceed",
        SYMBOL,
        INTERVAL,
        start_ms,
        end_ms,
    )
    return list(gaps)


def _build_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Preflight gap check for a BTC-USDT/1m holdout access (Scalping Strategy Research "
            "Task S4). Fails closed (non-zero exit) unless the real gap set for the given range "
            "matches the 2 known, disclosed gaps exactly. Read-only -- never touches the "
            "holdout-access claim mechanism."
        )
    )
    parser.add_argument("--start-ms", type=int, required=True)
    parser.add_argument("--end-ms", type=int, required=True)
    parser.add_argument("--db-path", default=str(DEFAULT_DB_PATH))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        gaps = verify_1m_gaps(args.start_ms, args.end_ms, db_path=args.db_path)
    except UnexpectedGapError as exc:
        print(f"FAIL: {exc}")
        return 1
    print(f"OK: real gap set matches the {len(gaps)} known, disclosed gap(s) exactly: {gaps}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
