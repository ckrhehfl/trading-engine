"""The one canonical location of the local market-data cache.

**This exists because two conventions were silently coexisting**, and on
2026-09-14 they produced a second, parallel database nobody asked for.
`backfill.py` and `collect_positioning.py` defaulted to
`"python/data/var/klines.sqlite3"`, which is correct only when the process
starts at the repo root; `backfill_kis.py` and `run_kr10_portfolio.py`
defaulted to `"data/var/klines.sqlite3"`, correct only when it starts in
`python/`. Both are *relative*, so neither fails — whichever one is wrong
for the current working directory quietly creates an empty database and
writes to that instead.

The failure is invisible at the point it happens: the collector reports
rows written, the exit status is 0, the log looks perfect. It surfaced
only because a re-run that should have written zero rows wrote 3,600.

Resolving from `__file__` removes the whole class: there is one path, it
is absolute, and it does not depend on where the process was launched
from. This is the same lesson `python/tests/conftest.py` already records
for `runs/experiments.jsonl` — *"a relative path resolves against whatever
directory pytest started in"* — applied to the data store.

Callers keep their `--db-path` argument. An explicitly supplied path is
always honoured exactly, relative or not; only the *default* is pinned.
"""

from __future__ import annotations

from pathlib import Path

#: `python/data/var/klines.sqlite3`, resolved from this file rather than
#: from the working directory.
DEFAULT_DB_PATH = str((Path(__file__).resolve().parent / "var" / "klines.sqlite3"))
