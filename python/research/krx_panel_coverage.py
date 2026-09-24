"""Is the full-universe panel complete enough to select on, per day?

**Why this has to run before anything reads the panel.** CLAUDE.md's
survivorship rules say membership comes from the price series and that
*"an absent bar is not evidence of absence unless the fetch that produced
it is known complete."* A per-day selection rule asks "who was in the
pool on date D" for every D, so a missing bar is not a detail there — it
is the answer to the question.

**The reference is an index series, never an arithmetic calendar.**
`store.find_missing_ranges` diffs against a fixed step and reports every
weekend and holiday as a gap — ~116 false positives per symbol per year
on a market trading ~245 days. An index prints on exactly the days the
market is open, so it needs no holiday table, it covers the moving lunar
holidays `KrxMarketCalendar` still lists as unresolved, and it separates
a market closure from a stock-specific halt. `kis_klines
.missing_trading_days` implements the comparison and **fails closed in
both directions**: a symbol printing on a day the reference lacks proves
the reference is truncated, not that the symbol is special.

**The three classes of absence, which must not be collapsed.** For each
symbol the reference days split at that symbol's own first and last bar:

- **before the first bar** — not yet listed, or listed before the panel
  starts. Not a gap.
- **after the last bar** — delisted, or suspended to the end. Not a gap,
  and CLAUDE.md's rule 2 applies: the exit price is that last bar, never
  a booked −100%.
- **between them** — a real interior gap, and this is the one that
  matters. It resolves to **UNKNOWN**, never to "not in the pool".

That last line is the whole point. A halted name still prints a bar
(`O == H == L == C`, zero turnover — 신라젠 carries 604 consecutive such
sessions), so an interior gap is *not* the ordinary way a halt shows up.
It is either something rarer or an incomplete fetch, and dropping the
name would be the exact survivorship bias these rules exist to prevent.

**Frozen bars are reported beside the gaps, because they are the
opposite failure**: present, and not evidence the name was tradeable.
CLAUDE.md's decided half — a frozen bar may never make a name eligible —
is a rule for the selection step, not for this one; here they are counted
so a later reader can subtract them rather than discover them.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass, field

from data.kis_klines import (
    ReferenceCalendarError,
    index_storage_symbol,
    missing_trading_days,
    ms_to_trading_date,
    trading_date_to_ms,
)

#: KOSPI. An index is a calendar here, not a price series, so which index
#: matters only in that it must print on every day the market is open.
DEFAULT_INDEX = "0001"


class PanelCoverageError(RuntimeError):
    """The panel could not be judged, so no verdict is given."""


@dataclass(frozen=True)
class SymbolCoverage:
    """One symbol's answer, with the three classes kept apart."""

    code: str
    first_date: str
    last_date: str
    bars: int
    frozen: int
    #: Reference days inside `[first_date, last_date]` with no bar. The
    #: only class that is a real gap; **UNKNOWN, not "not listed"**.
    interior_gaps: tuple[str, ...] = field(default=())
    #: Reference days before the first bar. Not a gap.
    before_first: int = 0
    #: Reference days after the last bar. Not a gap.
    after_last: int = 0

    @property
    def is_complete(self) -> bool:
        return not self.interior_gaps

    @property
    def span_days(self) -> int:
        return self.bars + len(self.interior_gaps)


def reference_days(conn: sqlite3.Connection, index_code: str = DEFAULT_INDEX) -> set[int]:
    """Trading days from the stored index series, as epoch-ms.

    **Fails closed on an empty reference.** A calendar with no days makes
    every symbol look complete, which is the single most dangerous way
    this check could go wrong: it would report a verified panel having
    verified nothing.
    """
    symbol = index_storage_symbol(index_code)
    rows = conn.execute(
        "SELECT open_time_ms FROM klines WHERE symbol = ? AND interval = '1d'",
        (symbol,),
    ).fetchall()
    days = {int(r[0]) for r in rows}
    if not days:
        raise PanelCoverageError(
            f"the reference index {symbol} has no stored bars. Backfill it "
            f"first -- an empty calendar makes every symbol look complete, "
            f"so a verdict against it would be meaningless."
        )
    return days


def coverage_for(
    scan: sqlite3.Connection, code: str, reference: set[int]
) -> SymbolCoverage:
    """One symbol's coverage against the reference calendar.

    Raises `ReferenceCalendarError` — through `missing_trading_days` —
    when the symbol prints on a day the reference lacks. That is evidence
    about the *reference*, so it aborts rather than being recorded as a
    property of the symbol.
    """
    row = scan.execute(
        "SELECT first_date, last_date, bars, frozen FROM scan_progress "
        "WHERE code = ? AND status = 'done'",
        (code,),
    ).fetchone()
    if row is None:
        raise PanelCoverageError(f"{code} has no completed scan_progress row")
    first, last, bars, frozen = row

    present = {
        trading_date_to_ms(r[0])
        for r in scan.execute(
            "SELECT bsop_date FROM scan_bars WHERE code = ?", (code,)
        )
    }
    lo, hi = trading_date_to_ms(first), trading_date_to_ms(last)
    inside = {d for d in reference if lo <= d <= hi}

    # Both directions, and the wrong-direction one raises: a bar on a day
    # the market was shut is a statement about the calendar.
    gaps = missing_trading_days(inside, present)

    return SymbolCoverage(
        code=code,
        first_date=first,
        last_date=last,
        bars=int(bars),
        frozen=int(frozen),
        interior_gaps=tuple(ms_to_trading_date(m) for m in gaps),
        before_first=sum(1 for d in reference if d < lo),
        after_last=sum(1 for d in reference if d > hi),
    )


def panel_coverage(
    scan: sqlite3.Connection, reference: set[int], limit: int | None = None
) -> list[SymbolCoverage]:
    codes = [
        r[0]
        for r in scan.execute(
            "SELECT code FROM scan_progress WHERE status = 'done' ORDER BY code"
        )
    ]
    if limit:
        codes = codes[:limit]
    return [coverage_for(scan, c, reference) for c in codes]


def report(rows: list[SymbolCoverage], reference: set[int]) -> None:
    complete = [r for r in rows if r.is_complete]
    gapped = sorted(rows, key=lambda r: -len(r.interior_gaps))
    gapped = [r for r in gapped if not r.is_complete]
    total_gaps = sum(len(r.interior_gaps) for r in rows)
    total_bars = sum(r.bars for r in rows)
    total_frozen = sum(r.frozen for r in rows)

    print(f"reference calendar      {len(reference):,} trading days")
    print(f"symbols judged          {len(rows):,}")
    print(f"  complete              {len(complete):,} "
          f"({100.0 * len(complete) / max(1, len(rows)):.1f}%)")
    print(f"  with interior gaps    {len(gapped):,}")
    print(f"interior gap-days       {total_gaps:,} of {total_gaps + total_bars:,} "
          f"symbol-days inside span")
    print(f"frozen bars             {total_frozen:,} "
          f"({100.0 * total_frozen / max(1, total_bars):.2f}% of stored bars)")
    if gapped:
        print("\nworst by interior gaps:")
        for r in gapped[:10]:
            print(f"  {r.code}  {len(r.interior_gaps):>5} gaps  "
                  f"{r.first_date}..{r.last_date}  e.g. {list(r.interior_gaps[:3])}")
    print(
        "\nAn interior gap is UNKNOWN, never 'not in the pool' -- a halted "
        "name still prints a bar, so this is not how a halt shows up."
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scan-db", required=True, help="the krx_scan database")
    ap.add_argument("--reference-db", required=True,
                    help="a klines database holding the reference index series")
    ap.add_argument("--index", default=DEFAULT_INDEX)
    ap.add_argument("--limit", type=int, default=0, help="first N symbols (probe)")
    args = ap.parse_args(argv)

    ref_conn = sqlite3.connect(f"file:{args.reference_db}?mode=ro", uri=True)
    scan_conn = sqlite3.connect(f"file:{args.scan_db}?mode=ro", uri=True)
    try:
        reference = reference_days(ref_conn, args.index)
        rows = panel_coverage(scan_conn, reference, limit=args.limit or None)
        if not rows:
            # **The symmetric case to an empty reference, and it was
            # missed.** `reference_days` already refuses an empty calendar
            # because it would make every symbol look complete; an empty
            # symbol list makes the *panel* look complete for the same
            # reason -- zero gaps out of zero judged, reported as success.
            # A verification that cannot fail is not evidence.
            raise PanelCoverageError(
                "no symbol has status 'done' in this scan database, so "
                "nothing was judged. A coverage report over zero symbols "
                "is not a verified panel."
            )
    except (PanelCoverageError, ReferenceCalendarError) as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    finally:
        ref_conn.close()
        scan_conn.close()

    report(rows, reference)
    # Reported, not failed: an interior gap is a disclosed limitation of
    # the panel, not a broken run. What fails closed is a truncated
    # reference or an empty one, both handled above.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
