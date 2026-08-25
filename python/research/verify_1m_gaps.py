"""Standalone `BTC-USDT`/`1m` gap-detection diagnostic -- Scalping
Strategy Research Task S4.

## What this is now, after a real design correction

This module's first version was meant to be the enforcement mechanism
gating the real `vwap-mid-reversion` 1m holdout access, invoked through
a dedicated wrapper script. A real CodeRabbit review finding on that
PR caught that the wrapper was only a *convention*: nothing stopped a
caller from invoking `research.run_preregistered_holdout` directly
(the same command every other holdout confirmation in this project
already uses) and bypassing the gap check entirely.

**The real enforcement now lives inside
`research/run_preregistered_holdout.py` itself**
(`verify_known_gaps`/`UnexpectedKnownGapsError`), gated on an optional,
opt-in `data.known_gaps` field a registration can declare -- generic
to any interval that ever turns out to have known gaps, not hardcoded
to `1m`, and a no-op (zero behavior change) for every registration that
doesn't declare it, including every one already committed before this
field existed. That is the one real execution path for *every*
holdout confirmation in this project, so there is no longer a second,
bypassable command to remember.

This module survives as a convenient, standalone, `1m`-specific CLI for
manually checking the gap set at any time -- e.g. after a future
backfill re-run, to see whether a new gap appeared, independent of and
without needing a preregistration file at hand. It reads
`find_missing_ranges`' own *metadata* (which timestamps are present/
absent), never the real price/volume content of any bar, and is safe
to run any number of times: it never calls
`research.holdout.load_holdout_klines` and never touches
`runs/experiments.jsonl`'s holdout-access tracking.
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
