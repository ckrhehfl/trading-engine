"""A universe selected on day one, with the delisted names left in.

**What this exists to remove.** `rd-v` measured the `rd-r` ten returning
**+694% equal-weight, +54%/yr** against KOSPI's +19% -- a 3.4x selection
premium -- because `rd-r` ranked them on **2026Q1** futures turnover, a
date *inside* the window they are then measured over. A name became liquid
enough to carry a listed future *because* it had gone up 10-25x, so the
selection criterion and the return are the same fact, and every long-side
figure in `rd-t`, `rd-u` and `tm-e` sits on that drift.

**The fix is not a bigger scan, it is an earlier one.** Rank on a window at
the *start* of the panel, using only what was knowable then, and keep every
name that was listed on that date including the ones since delisted. The
second half was impossible before `rd-w`; the first half is `ms-e`'s own
day-one rule, which CLAUDE.md already records as the survivorship-safe
construction.

Cost: ~4,640 calls, about 40 minutes. A per-day full-universe scan is 20x
that and is a separate step -- this one is cheap because the ranking window
is one month, not seven years.

**Three properties this has to get right, each a rule from CLAUDE.md that
the naive version breaks:**

1. **A zero-row answer is not "not listed".** A live name and a code that
   never existed both return `rt_cd=0` with zero rows. So a negative
   control runs in the same pass, and a symbol resolves to
   `ABSENT` only once that control has shown the request shape works;
   otherwise the whole run is refused. Dropping a real name is the exact
   bias this module exists to remove.
2. **The ranking window is under the 100-row cap**, deliberately. One
   month is ~21 trading days. A capped response here would silently be a
   different window than the one declared.
3. **The selection is frozen.** Members leave on delisting -- which the
   price series itself shows, since KIS serves a dead name to its final
   session -- never on "it got less liquid later". Re-ranking on a later
   window is what produced the drift in the first place.

**It does not write to the KRX record.** `--db-path` defaults to a scratch
file, because CLAUDE.md's "exactly one writer per series" makes the
instance the only writer of `KRX:` klines, and `sync-krx-from-instance.sh`
is `INSERT OR IGNORE` -- a locally-written row would never be corrected by
a later sync.

**The committed artifact is the ranking JSON**, and for a different
reason than `runs/krx_futures_liquidity.json`'s. That one is the only
copy of decaying data; the 2019-01 bars here are stable and KIS serves
them indefinitely. What cannot be reconstructed is the **candidate
pool** -- today's live universe plus today's delisted list, and the
delisted list only grows -- so a re-run in a year ranks a larger pool and
may return a different thirty. `.gitignore` carries a matching
`!runs/krx_dayone_universe.json`, because `runs/*` is an allowlist and a
docstring claiming a file is committed does not commit it. That is how
this one was wrong for a commit: the claim was written and never checked
against `git status`.

**Discovery mode.** KRX daily was spent by `ms-f` on 2026-09-13. Nothing
produced here may be promoted, quoted as evidence of an edge, or reported
as a pass; the output is a universe definition.

Run:

    python -m research.krx_dayone_universe --rank
    python -m research.krx_dayone_universe --show
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
import urllib.parse
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path

from data.kis_klines import (
    ADJUSTED,
    DAILY_ITEM_PATH,
    PAPER_HOST,
    TR_DAILY_ITEM,
    KisKlinesError,
    KisSession,
    _get_with_retry,
)
from data.krx_instrument import is_common_stock
from data.store import connect, fetch_krx_delisted, fetch_krx_universe

#: The ranking window: January 2019, at the start of the panel `ms-f`,
#: `rd-t`, `rd-u` and `tm-e` all sit on. ~21 trading days, comfortably
#: under the 100-row cap. Declared here rather than passed, because a
#: window chosen after seeing a ranking is not a day-one rule.
RANKING_START = "20190102"
RANKING_END = "20190131"

#: How many names the universe holds. Thirty rather than ten so a
#: cross-sectional statistic has something to work with, and rather than
#: the full pool so the thing stays a *selection* -- `rd-c` §2's finding
#: is that the filter is the strategy.
UNIVERSE_SIZE = 30

#: Codes that cannot exist, asked in the same pass as everything else.
#: Without them a zero-row answer is unreadable -- see the module
#: docstring and CLAUDE.md's KIS section.
NEGATIVE_CONTROLS = ("999999", "ZZZZZZ", "000000")

#: A name must actually trade through the window to be rankable. One or
#: two prints in a month is a halted or barely-listed name, and its
#: median turnover is not a liquidity measure.
MIN_RANKING_BARS = 15

DEFAULT_OUT = Path(__file__).resolve().parents[2] / "runs" / "krx_dayone_universe.json"
#: Deliberately NOT `data/var/klines.sqlite3` -- see the module docstring.
DEFAULT_SCRATCH_DB = Path(
    os.environ.get("TMPDIR", "/tmp")
) / "krx_dayone_scratch.sqlite3"

_REQUEST_SPACING_S = 0.05


class Outcome(Enum):
    """What the ranking pass learned about one candidate."""

    RANKED = "ranked"
    #: Traded too few sessions in the window to rank on.
    THIN = "thin"
    #: No bars at all, and the negative control confirms the request works,
    #: so this really was not listed in the window.
    ABSENT = "absent"
    #: The request itself failed. NOT the same as `ABSENT`, and never
    #: merged into it.
    ERROR = "error"


@dataclass(frozen=True)
class Candidate:
    code: str
    name: str
    market: str
    listed_now: bool
    outcome: str
    bars: int = 0
    median_turnover: float = 0.0


class DayOneUniverseError(RuntimeError):
    """The ranking could not be trusted, so it was refused."""


def candidates(conn, snapshot_date: str | None = None) -> list[tuple[str, str, str, bool]]:
    """Every common-stock code that could have been listed in the window.

    Live names and delisted ones together, which is the whole point: a
    pool built from currently-listed names alone is biased upward by
    construction, and `ms-e`'s own draft made exactly that mistake before
    review caught it.
    """
    if snapshot_date is None:
        row = conn.execute("SELECT MAX(snapshot_date) FROM krx_universe").fetchone()
        snapshot_date = row[0] if row and row[0] else None
    if snapshot_date is None:
        raise DayOneUniverseError(
            "no krx_universe snapshot; run `python -m data.krx_universe --snapshot`"
        )
    live = [
        (code, name, market, True)
        for code, market, name, _, _ in fetch_krx_universe(
            conn, snapshot_date, common_stock_only=True
        )
    ]
    dead_date = conn.execute("SELECT MAX(snapshot_date) FROM krx_delisted").fetchone()
    dead_date = dead_date[0] if dead_date and dead_date[0] else None
    if dead_date is None:
        raise DayOneUniverseError(
            "no krx_delisted snapshot; a universe built from listed names "
            "alone is survivorship-contaminated by construction, which is "
            "the one thing this module exists to avoid. Run "
            "`python -m data.krx_delisted --snapshot`"
        )
    dead = [
        (code, name, market, False)
        for code, market, name, isin in fetch_krx_delisted(conn, dead_date)
        if len(code) == 6 and code.isdigit() and is_common_stock(isin)
    ]
    return live + dead


def _fetch_window(
    session: KisSession, code: str, start: str = RANKING_START, end: str = RANKING_END
) -> list[dict]:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": code,
        "FID_INPUT_DATE_1": start,
        "FID_INPUT_DATE_2": end,
        "FID_PERIOD_DIV_CODE": "D",
        "FID_ORG_ADJ_PRC": ADJUSTED,
    }
    url = f"{PAPER_HOST}{DAILY_ITEM_PATH}?{urllib.parse.urlencode(params)}"
    payload = _get_with_retry(url, session.headers(TR_DAILY_ITEM))
    if payload.get("rt_cd") != "0":
        raise KisKlinesError(f"{code}: rt_cd={payload.get('rt_cd')}")
    rows = [
        r
        for r in (payload.get("output2") or [])
        if isinstance(r, dict) and r.get("stck_bsop_date")
    ]
    if len(rows) >= 100:
        # The declared window is one month. A capped response means the
        # request did not ask what this module says it asks.
        raise KisKlinesError(
            f"{code}: {len(rows)} rows, at the 100-row cap -- the ranking "
            f"window is not what it claims to be"
        )
    return rows


def verify_negative_controls(
    session: KisSession, start: str = RANKING_START, end: str = RANKING_END
) -> None:
    """Refuse the whole run unless a nonsense code really answers empty.

    **Without this, `ABSENT` is unreadable.** If the request shape were
    wrong, every candidate would come back empty and the run would report
    a universe of nothing while looking like a clean pass -- the silent
    failure shape this project has hit on `rt_cd=0` three times.
    """
    for code in NEGATIVE_CONTROLS:
        time.sleep(_REQUEST_SPACING_S)
        try:
            rows = _fetch_window(session, code)
        except Exception as exc:  # noqa: BLE001
            # **Deliberately every exception, not just `KisKlinesError`.**
            # The control's whole job is to prove the request shape works;
            # a control that cannot be asked -- for any reason -- leaves
            # every `ABSENT` in the run unreadable, and refusing is the
            # only honest response.
            raise DayOneUniverseError(
                f"negative control {code} failed to answer at all "
                f"({type(exc).__name__}: {exc}); a zero-row result from a "
                f"real candidate could not be distinguished from a broken "
                f"request"
            ) from None
        if rows:
            raise DayOneUniverseError(
                f"negative control {code} returned {len(rows)} bars. It "
                f"cannot be a real listing, so the request is reaching "
                f"something other than what this module thinks"
            )


def rank(
    session: KisSession,
    pool,
    *,
    progress=None,
    start: str = RANKING_START,
    end: str = RANKING_END,
) -> list[Candidate]:
    """Median 거래대금 over the ranking window, per candidate.

    Median rather than mean: one block trade in a thin name moves a mean
    by an order of magnitude, and this is a liquidity ranking.
    """
    out: list[Candidate] = []
    for i, (code, name, market, listed_now) in enumerate(pool):
        time.sleep(_REQUEST_SPACING_S)
        try:
            rows = _fetch_window(session, code, start, end)
        except Exception as exc:  # noqa: BLE001 - recorded, never merged into ABSENT
            out.append(Candidate(code, name, market, listed_now, Outcome.ERROR.value))
            if progress:
                progress(i, code, f"ERROR {type(exc).__name__}")
            continue
        if not rows:
            out.append(Candidate(code, name, market, listed_now, Outcome.ABSENT.value))
        elif len(rows) < MIN_RANKING_BARS:
            out.append(
                Candidate(code, name, market, listed_now, Outcome.THIN.value, len(rows))
            )
        else:
            # **Every value validated, and counted against the same floor
            # as the bars.** `len(rows) >= MIN_RANKING_BARS` with one
            # usable 거래대금 would rank a candidate on a single number.
            # A non-numeric, NaN, infinite or negative turnover is a data
            # error rather than a small value, so it is excluded from the
            # count rather than coerced.
            turnovers = []
            for r in rows:
                raw = r.get("acml_tr_pbmn")
                if raw in (None, ""):
                    continue
                try:
                    value = float(raw)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(value) or value < 0:
                    continue
                turnovers.append(value)
            if len(turnovers) < MIN_RANKING_BARS:
                out.append(
                    Candidate(code, name, market, listed_now, Outcome.THIN.value, len(rows))
                )
            else:
                out.append(
                    Candidate(
                        code, name, market, listed_now, Outcome.RANKED.value,
                        len(rows), statistics.median(turnovers),
                    )
                )
        if progress and i % 200 == 0:
            progress(i, code, out[-1].outcome)
    return out


def select(ranked: list[Candidate], size: int = UNIVERSE_SIZE) -> list[Candidate]:
    """The top `size` by median turnover, ties broken by code for
    determinism. Frozen from here -- see the module docstring."""
    usable = [c for c in ranked if c.outcome == Outcome.RANKED.value]
    usable.sort(key=lambda c: (-c.median_turnover, c.code))
    return usable[:size]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db-path", default=str(DEFAULT_SCRATCH_DB))
    ap.add_argument("--universe-db", default=None,
                    help="where the krx_universe/krx_delisted snapshots live")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--rank", action="store_true")
    group.add_argument("--show", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="rank only the first N (probe)")
    ap.add_argument(
        "--window",
        default=f"{RANKING_START}..{RANKING_END}",
        help="the ranking window, YYYYMMDD..YYYYMMDD. Overriding it needs an "
        "explicit --out: a second arm is a different universe, not a new "
        "version of the canonical one.",
    )
    args = ap.parse_args(argv)

    default_window = f"{RANKING_START}..{RANKING_END}"
    try:
        win_start, win_end = args.window.split("..")
    except ValueError:
        print("--window must be YYYYMMDD..YYYYMMDD", file=sys.stderr)
        return 2

    # **A probe or a second arm must not overwrite the canonical artifact.**
    # `--limit` ranks a slice of the pool and `--window` ranks a different
    # question; either landing in runs/krx_dayone_universe.json would hand
    # the drift measurement a corrupted universe that looks canonical.
    if (args.limit or args.window != default_window) and args.out == str(DEFAULT_OUT):
        print(
            "--limit and --window produce a non-canonical universe; pass an "
            "explicit --out so it cannot overwrite "
            f"{DEFAULT_OUT.name}",
            file=sys.stderr,
        )
        return 2

    if args.show:
        data = json.loads(Path(args.out).read_text(encoding="utf-8"))
        print(f"ranked {data['window']}  selected {len(data['universe'])}")
        for i, row in enumerate(data["universe"], 1):
            flag = "" if row["listed_now"] else "  [DELISTED SINCE]"
            print(f"  {i:>2}. {row['code']}  {row['name']:<20} "
                  f"{row['median_turnover']/1e8:>10,.0f}억{flag}")
        print(f"\n  outcomes: {data['outcomes']}")
        return 0

    key, secret = os.environ.get("KIS_APP_KEY"), os.environ.get("KIS_APP_SECRET")
    if not key or not secret:
        print("KIS_APP_KEY / KIS_APP_SECRET must both be set", file=sys.stderr)
        return 2

    conn = connect(args.universe_db or args.db_path)
    pool = candidates(conn)
    conn.close()
    if args.limit:
        pool = pool[: args.limit]
    print(f"{len(pool):,} candidates ({sum(1 for p in pool if p[3]):,} listed now, "
          f"{sum(1 for p in pool if not p[3]):,} delisted since)", flush=True)

    session = KisSession(key, secret, host=PAPER_HOST)
    verify_negative_controls(session, win_start, win_end)
    print("negative controls pass: an empty answer really means empty", flush=True)

    started = time.monotonic()

    def progress(i, code, outcome):
        done = i + 1
        rate = done / max(1e-9, time.monotonic() - started)
        print(f"  [{done:>5}/{len(pool)}] {code} {outcome}  "
              f"{rate:.1f}/s  eta {(len(pool)-done)/max(rate,1e-9)/60:.0f}m", flush=True)

    ranked = rank(session, pool, progress=progress, start=win_start, end=win_end)
    chosen = select(ranked)
    outcomes: dict[str, int] = {}
    for c in ranked:
        outcomes[c.outcome] = outcomes.get(c.outcome, 0) + 1

    # **An incomplete ranking is not a universe.** An `ERROR` is a
    # candidate whose turnover was never read, so it could have belonged in
    # the thirty and nothing here can say. Writing the artifact anyway
    # makes a partial pass indistinguishable from a complete one to every
    # downstream reader -- the `check_reported_from_actual` shape.
    errors = outcomes.get(Outcome.ERROR.value, 0)
    if errors:
        raise DayOneUniverseError(
            f"{errors} candidate(s) failed to rank. A candidate whose "
            f"turnover was never read might have belonged in the {UNIVERSE_SIZE}, "
            f"so no artifact is written; re-run rather than accept a partial "
            f"universe"
        )
    if len(chosen) < UNIVERSE_SIZE:
        raise DayOneUniverseError(
            f"only {len(chosen)} of {UNIVERSE_SIZE} places filled "
            f"(outcomes {outcomes}); the pool or the window is wrong, and a "
            f"short universe written as canonical would be read as complete"
        )

    payload = {
        "window": f"{win_start}..{win_end}",
        "universe_size": UNIVERSE_SIZE,
        "min_ranking_bars": MIN_RANKING_BARS,
        "candidates": len(pool),
        "outcomes": outcomes,
        "universe": [asdict(c) for c in chosen],
        "all_ranked": [asdict(c) for c in ranked if c.outcome == Outcome.RANKED.value],
    }
    Path(args.out).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nwrote {args.out}: {len(chosen)} selected, outcomes {outcomes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
