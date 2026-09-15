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
from collections import defaultdict
from pathlib import Path

from research.experiment_log import DEFAULT_RUNS_PATH, read_records

#: Where the committed ledger lives. `runs/*` is gitignored with explicit
#: `!` exceptions, and this is one of them, alongside
#: `runs/live_signals.jsonl`.
DEFAULT_LEDGER_PATH = (
    Path(__file__).resolve().parents[2] / "runs" / "spent_windows.json"
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
    for rec in read_records(runs_path):
        if rec.get("record_type") != "holdout_access":
            continue
        symbol, interval = rec.get("symbol"), rec.get("interval")
        if not symbol or not interval:
            raise ValueError(
                f"holdout_access record without symbol/interval: {rec!r}. "
                f"A window that cannot be identified cannot be marked spent."
            )
        by_window[(symbol, interval)].append(
            rec.get("accessed_at") or rec.get("logged_at") or ""
        )

    return {
        "note": (
            "Derived from runs/experiments.jsonl by research.spent_windows. "
            "Do not hand-edit; regenerate with "
            "`python -m research.spent_windows --write`."
        ),
        "windows": [
            {
                "symbol": symbol,
                "interval": interval,
                "accesses": len(times),
                "first_access": min(t for t in times) if any(times) else None,
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--runs-path", default=str(DEFAULT_RUNS_PATH))
    ap.add_argument("--ledger-path", default=str(DEFAULT_LEDGER_PATH))
    ap.add_argument("--write", action="store_true", help="write the ledger")
    args = ap.parse_args(argv)

    ledger = build(args.runs_path)
    text = json.dumps(ledger, indent=2, ensure_ascii=False) + "\n"
    if args.write:
        Path(args.ledger_path).write_text(text, encoding="utf-8")
        print(f"wrote {args.ledger_path}: {len(ledger['windows'])} spent window(s)")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
