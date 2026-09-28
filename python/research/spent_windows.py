"""Which data windows have been spent, as a committed, derived record.

**Why this file exists at all.** CLAUDE.md names which windows are still
available for a confirmation run, and `runs/experiments.jsonl` knows which
have actually been accessed — but that log is **gitignored** (it is a
local research artifact; see `experiment_log`'s own docstring). So a check
comparing the two passes vacuously in CI, where the log does not exist,
which is precisely the inert guard this project keeps rediscovering.

The fix is the pattern `runs/live_signals.jsonl` already establishes: a
**committed artifact derived from an uncommitted one**. `build()` reduces
the log to the small set of windows that have been spent;
`runs/spent_windows.json` carries it into CI; and
`test_planning_index.py` checks **both** directions —

* CLAUDE.md's claim against the ledger, everywhere including CI;
* the ledger against the log, wherever the log actually exists, so the
  ledger cannot quietly drift from the thing it summarises.

**It is derived, never hand-edited.** Regenerate with:

    python -m research.spent_windows --write

A window here is spent **permanently**. The single-holdout-access rule
means there is no path back, so this file only ever grows.
"""

from __future__ import annotations

import argparse
import json
import re
import datetime as dt
from collections import defaultdict
from pathlib import Path

from research.experiment_log import DEFAULT_RUNS_PATH, read_records

#: Where the committed ledger lives. `runs/*` is gitignored with explicit
#: `!` exceptions, and this is one of them, alongside
#: `runs/live_signals.jsonl`.
DEFAULT_LEDGER_PATH = (
    Path(__file__).resolve().parents[2] / "runs" / "spent_windows.json"
)


#: `YYYY-MM-DD..YYYY-MM-DD`, and nothing else counts as a readable era.
_SPAN_RE = re.compile(r"(\d{4}-\d{2}-\d{2})\.\.(\d{4}-\d{2}-\d{2})")


def _span(start_ms: int | None, end_ms: int | None) -> str | None:
    """`YYYY-MM-DD..YYYY-MM-DD` for the dates a window's accesses covered.

    `None` when the records carry no range, which is honest rather than
    convenient: a window whose era is unknown must not read as bounded, or a
    caller would conclude some other era is free.
    """
    if start_ms is None or end_ms is None:
        return None
    return (
        f"{dt.datetime.fromtimestamp(start_ms / 1000, dt.timezone.utc).date()}"
        f"..{dt.datetime.fromtimestamp(end_ms / 1000, dt.timezone.utc).date()}"
    )


def build(runs_path: str | Path = DEFAULT_RUNS_PATH) -> dict:
    """Reduce the experiment log to the windows a holdout has touched.

    Uses `read_records`, so it inherits that function's JSONL integrity
    contract: a truncated **final** line is tolerated, and any earlier
    malformed line raises rather than silently dropping whatever record it
    held. A dropped record here would be a *missing* holdout access, which
    is the direction that makes a spent window look available.
    """
    by_window: dict[tuple[str, str], list[str]] = defaultdict(list)
    bounds: dict[tuple[str, str], tuple[int | None, int | None]] = {}
    unbounded: set[tuple[str, str]] = set()
    for rec in read_records(runs_path):
        if rec.get("record_type") != "holdout_access":
            continue
        symbol, interval = rec.get("symbol"), rec.get("interval")
        if not symbol or not interval:
            raise ValueError(
                f"holdout_access record without symbol/interval: {rec!r}. "
                f"A window that cannot be identified cannot be marked spent."
            )
        key = (symbol, interval)
        by_window[key].append(rec.get("accessed_at") or rec.get("logged_at") or "")
        start, end = rec.get("start_ms"), rec.get("end_ms")
        if not isinstance(start, int) or not isinstance(end, int):
            # **One access with no range makes the whole window's era unknown.**
            # Taking the bounds of the records that DO carry one reports a finite
            # span covering less than was actually accessed, and `_row_touches`
            # then reads an era outside it as unspent -- a reserved window that
            # is not. Unknown is the conservative answer and `_row_touches`
            # already treats it as touching every era. Reported on review of
            # PR #210.
            unbounded.add(key)
            continue
        if key in unbounded:
            continue
        lo, hi = bounds.get(key, (None, None))
        lo = start if lo is None else min(lo, start)
        hi = end if hi is None else max(hi, end)
        bounds[key] = (lo, hi)

    return {
        "note": (
            "Derived from runs/experiments.jsonl by research.spent_windows. "
            "Do not hand-edit; regenerate from the REPOSITORY ROOT with "
            "`PYTHONPATH=python python -m research.spent_windows --write`. The "
            "working directory is part of the command: the runs path is "
            "relative (so tests stay out of the real log) while this ledger's "
            "path is absolute, and running it from python/ once read an absent "
            "log and emptied this file. build() now refuses instead."
        ),
        "windows": [
            {
                "symbol": symbol,
                "interval": interval,
                "accesses": len(times),
                "first_access": min(t for t in times) if any(times) else None,
                # **The era, not just the instrument.** Without it the ledger
                # cannot express what this project already does: `sr-t`
                # reserved the EARLY 1d window because every trial had touched
                # only the later one, and KRX daily is now the same shape --
                # 2019-2026 spent, everything before it untouched. A ledger
                # keyed on (symbol, interval) alone reports the whole symbol
                # spent and makes the reserved era unnameable.
                "span": None
                if (symbol, interval) in unbounded
                else _span(*bounds.get((symbol, interval), (None, None))),
            }
            for (symbol, interval), times in sorted(by_window.items())
        ],
    }


def load(ledger_path: str | Path = DEFAULT_LEDGER_PATH) -> list[dict]:
    """The committed ledger's window rows.

    Raises on a missing ledger rather than returning `[]`. An empty result
    would let every check built on this pass vacuously, which is the exact
    failure this module was written to remove.
    """
    path = Path(ledger_path)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. It is a committed artifact; regenerate it "
            f"with `python -m research.spent_windows --write`."
        )
    windows = json.loads(path.read_text(encoding="utf-8")).get("windows")
    if not windows:
        raise ValueError(
            f"{path} lists no spent windows. This project has spent several, "
            f"so an empty ledger means the generator or the log is broken — "
            f"not that every window is available."
        )
    return windows


def _refuse_to_unspend(ledger_path: str, fresh: dict, runs_path: str) -> None:
    """Never write a ledger that forgets a window or narrows an era.

    **A spend is permanent**, so regeneration may only ever add. Any run that
    would drop a `(symbol, interval)` or shrink its span read a log that was
    absent or partial, and writing it hands every check built on this file a
    fresh-looking window that is gone.

    **Counting rows was not enough**, reported on review of PR #210: dropping one
    window while adding another leaves the count equal, and a narrowed span does
    not change it at all. Both erase a real spend. So each existing window must
    still be present and its era still covered.

    An existing span of `None` means the era was unknown, which is the widest
    claim there is -- a finite replacement narrows it and is refused.

    Observed 2026-09-27, the incident this began as: `DEFAULT_RUNS_PATH` is the
    relative `runs/experiments.jsonl` -- deliberately, so `conftest.py` can
    redirect it and keep tests out of the real log -- while this ledger's path is
    absolute. The documented regeneration command, run from `python/` rather than
    the repository root, read nothing and emptied the committed ledger. `load`
    refuses an empty ledger; nothing refused writing one.

    The guard is here rather than in `build`, which legitimately answers `[]` for
    an absent log: before the first run that is an unremarkable state, and a test
    pins it. What is never unremarkable is *overwriting* a populated ledger with
    a poorer one.
    """
    path = Path(ledger_path)
    if not path.exists():
        return
    try:
        before = json.loads(path.read_text(encoding="utf-8")).get("windows") or []
    except (OSError, ValueError):
        raise ValueError(
            f"{path} exists but could not be read, so it cannot be checked "
            f"against the {len(fresh['windows'])} window(s) just built. Repair "
            f"or remove it deliberately before regenerating."
        ) from None

    after = {(w["symbol"], w["interval"]): w.get("span") for w in fresh["windows"]}
    counts = {(w["symbol"], w["interval"]): w.get("accesses", 0) for w in fresh["windows"]}
    lost, narrowed = [], []
    for old in before:
        key = (old["symbol"], old["interval"])
        if key not in after:
            lost.append(key)
            continue
        # **The access count is evidence too, and it can fall while the window
        # and its era stay identical.** CLAUDE.md cites `ms-f`'s "three recorded
        # `holdout_access` entries" as what shows the single-access discipline
        # held; a partial log reducing that to one erases the evidence while
        # every other field matches. Reported on review of PR #210.
        if counts[key] < old.get("accesses", 0):
            narrowed.append(
                (key, f"accesses {old.get('accesses')}", f"accesses {counts[key]}")
            )
        was, now = old.get("span"), after[key]
        if was is None:
            if now is not None:
                narrowed.append((key, was, now))
            continue
        if now is None:
            continue  # wider than before: unknown covers every era
        # **Both eras are parsed strictly, and an unreadable one refuses.** The
        # first version compared `partition("..")` results directly, so a
        # hand-edited `"within 2010-01-02..."` gave `was_a = "within 2010-01-02"`
        # -- and `"w"` sorts above every digit, making `now_a > was_a` always
        # False, so a genuinely narrowed era was accepted. `_row_touches` already
        # treats an unparseable era as unknown; this follows the same rule, and
        # refuses a finite replacement for one. Reported on review of PR #210.
        mw = _SPAN_RE.fullmatch(str(was).strip())
        mn = _SPAN_RE.fullmatch(str(now).strip())
        if not mw or not mn:
            narrowed.append((key, was, now))
            continue
        if mn.group(1) > mw.group(1) or mn.group(2) < mw.group(2):
            narrowed.append((key, was, now))

    if lost or narrowed:
        raise ValueError(
            f"regenerating from {runs_path} would erase spends from {path}: "
            f"missing {sorted(lost)}, narrowed {narrowed}. A spend is permanent, "
            f"so this means the log was absent or partial -- most often because "
            f"the command ran from python/ instead of the repository root, where "
            f"that relative path does not resolve. Nothing was written."
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-path", default=str(DEFAULT_RUNS_PATH))
    ap.add_argument("--ledger-path", default=str(DEFAULT_LEDGER_PATH))
    ap.add_argument("--write", action="store_true", help="write the ledger")
    args = ap.parse_args(argv)

    ledger = build(args.runs_path)
    text = json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"
    if args.write:
        _refuse_to_unspend(args.ledger_path, ledger, args.runs_path)
        Path(args.ledger_path).write_text(text, encoding="utf-8")
        print(f"wrote {args.ledger_path}: {len(ledger['windows'])} spent window(s)")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
