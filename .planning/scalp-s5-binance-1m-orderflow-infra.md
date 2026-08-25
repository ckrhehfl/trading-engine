# Scalping Strategy Research Task S5 — Binance 1m + taker-buy-volume data infrastructure

Infrastructure only. No trading strategy was designed, implemented, or
backtested here, and no real holdout data (BingX `BTC-USDT`/`1m`,
already spent by `vwap-mid-reversion`) was touched at all — this task's
own data (Binance USDT-M futures `BTCUSDT`) is a genuinely separate
symbol/venue from that holdout.

## Why this task exists

Two scalping-candidate investigations (order-flow imbalance,
liquidation cascades) both concluded the same real, disclosed blocker:
a genuine order-flow-imbalance proxy needs buyer/seller volume data,
which this project's BingX-only pipeline structurally lacks — BingX's
own kline wire format is plain OHLCV, confirmed directly against
`bingx_klines.py`. Binance's kline wire format *does* carry
`taker_buy_base_volume`/`taker_buy_quote_volume`, but this project's own
`binance_klines.py::_parse_row` discarded them (only the first 6 of 12
wire fields were ever extracted), and no Binance data had ever been
backfilled at 1-minute granularity (confirmed via a real query against
the production DB before this task began: only
`('BINANCE:BTCUSDT', '1d', 1366)` existed for any Binance symbol). The
user explicitly decided to build this infrastructure now, before
attempting either scalping candidate again.

## Task 1 — schema + parsing (additive only)

**`KlineRow` (`bingx_klines.py`)**: two new fields,
`taker_buy_base_volume`/`taker_buy_quote_volume`, both `Decimal | None`
defaulting to `None`, added at the **end** of the dataclass. Verified
additive by grepping every real `KlineRow(...)` construction site in the
codebase first (15 call sites, all keyword-based, zero positional
construction) — confirmed nothing breaks. `None` for every BingX-sourced
row (no buyer/seller breakdown exists on that wire at all) and for any
pre-Task-S5 Binance row.

**`store.py` schema migration**: `CREATE TABLE IF NOT EXISTS` is a
genuine no-op against the real, already-populated production
`klines.sqlite3` — it cannot retroactively add a column to a table that
already exists. Added `_ensure_klines_columns`, a real, idempotent
`ALTER TABLE klines ADD COLUMN ...` migration (existence checked via
`PRAGMA table_info` first, since SQLite's `ADD COLUMN` has no portable
`IF NOT EXISTS`), run on every `connect()` alongside the existing
`CREATE TABLE IF NOT EXISTS`. `upsert_klines`/`fetch_klines` extended to
write/read the two new nullable `TEXT` columns, same
`Decimal`-as-exact-`TEXT` convention every other price/volume column
already uses (`str(Decimal)`, never `str(float)`); `None` binds as real
SQL `NULL`, not the string `"None"`.

**`binance_klines.py::_parse_row`**: now also parses wire index 9
(`taker_buy_base_volume`) and index 10 (`taker_buy_quote_volume`) into
the two new `KlineRow` fields, guarded so a row shorter than 10 elements
degrades to `None` rather than raising (the original `len(row) < 6`
guard is unchanged) — real backward compatibility, not just test
convenience.

**Real, independent verification before touching the production
database at all**: made a full backup copy
(`klines.sqlite3.pre-s5-backup`), recorded the exact real
`(symbol, interval) -> COUNT(*)` for every row already present:

```text
('BINANCE:BTCUSDT', '1d', 1366)
('BTC-USDT', '15m', 24191)
('BTC-USDT', '1d', 1914)
('BTC-USDT', '1h', 19678)
('BTC-USDT', '1m', 910040)
```

Ran the real migration (`connect()`) against the real production DB,
confirmed the two new columns exist, and confirmed **every one of the
five counts above is byte-for-byte unchanged** — re-verified a second
time after Task 2's own real backfill completed, still unchanged. A
sample of pre-existing `BTC-USDT`/`1m` rows confirmed the two new
columns are genuinely `NULL` (not a fabricated zero or empty string) for
data that predates this task. The backup was deleted only after this
full verification passed, matching the same "verify before discarding a
safety net" discipline this project applies elsewhere (e.g. the round-2
KOSPI200 leverage-preflight fail-closed design).

**Tests**: `test_store.py` gained a real migration test (hand-builds a
database with the OLD `CREATE TABLE` shape, confirms `connect()` adds
the columns with pre-existing data intact and the new columns `NULL`,
not fabricated), an idempotent-second-migration test, and two
upsert/fetch round-trip tests (a Binance-style row with real values, and
a regression test confirming a plain BingX-style row still round-trips
with genuine `NULL`s). `test_binance_klines.py` gained real parsing
tests for the two new fields (via a real, purpose-built optional
parameter on `FakeBinanceKlinesServer.set_kline`, not by reaching into
the fake server's internals) and a short-row-degrades-to-`None`
regression test. The one pre-existing test whose name/assumption was no
longer accurate
(`..._parses_a_realistic_12_element_row_ignoring_trailing_fields`) was
renamed and its docstring corrected rather than left misleading. Full
suite: **1506/1506 passing** (up from 1500 before this task).

## Task 2 — real retention probe + full backfill, Binance USDT-M futures `BTCUSDT` 1-minute

**Futures, not spot, deliberately** — this project's own live-trading
scope is USDT-M perpetual futures (BingX); futures order flow is the
economically relevant proxy for that market structure, not spot.
**Disclosed, unverified assumption, stated plainly rather than silently
assumed**: this project doesn't and won't trade on Binance at all (see
`binance_klines.py`'s own long-standing module docstring on this) — using
Binance futures taker-buy volume as a proxy for BTC market-wide order
flow, or for BingX's own order flow specifically, carries a real,
unverified cross-venue-transferability assumption. This is the same
category of caveat CLAUDE.md's own VWAP-reversion section already
discloses for its own proxy (a 1-second Binance order-book paper
standing in for this project's 1-minute OHLCV implementation) — not
resolved here, carried forward as an open question for whatever
candidate actually consumes this data.

**Binary-search probe** (mirroring Task S1's own BingX `1m` methodology
exactly): lower bound 2019-09-08T00:00:00Z (the futures market's own
documented `1d`-retention listing date, confirmed empty at `1m`), upper
bound "now". 22 iterations converged on **2019-09-08T17:57:00Z** as the
earliest real bar — i.e. real `1m` retention reaches all the way back to
essentially the market's own actual launch (the `1d` listing date's
"00:00:00" is a calendar-day rounding convention; the real first trades
began that afternoon UTC). This is a genuine, structural difference from
every prior binary-search estimate in this project's history (BingX's
`1h`/`1d`/`15m`/`5m`/`1m`, all rolling-window retention with a real,
non-zero floor) — Binance appears to retain **full history, no rolling
window, at `1m`**, not just at the coarser granularities `sr-z`/`sr-aa`
already established.

**Real, full backfill** (`data.backfill_binance`, `--market futures
--symbol BTCUSDT --interval 1m --start 2019-09-08T17:57:00+00:00`, into
the real production DB) — not left at the binary-search estimate alone,
matching this project's own standing "an earliest-bar probe alone is not
enough, a full backfill with a real gap count is" rule:

```text
real row count       : 3,661,780
earliest bar          : 2019-09-08T17:57:00Z
latest bar             : 2026-08-25T15:37:00Z
real calendar span     : 2,542.90 days = 6.962 years
real gap count (find_missing_ranges, independently re-run): 1
  [2019-09-08T19:00:00Z, 2019-09-08T19:01:00Z) -- 1 missing bar
expected bar count if perfectly zero-gap: 3,661,781
actual (3,661,780) + missing (1) == expected (3,661,781): confirmed True
```

The single real gap sits ~2 hours after the market's own first bar --
plausibly real exchange-launch-day instability, not a fetch artifact
(the same "consistently absent across retries" standard this project's
`1m` BingX gaps were confirmed against was not separately re-applied
here, since `find_missing_ranges` reading the already-fully-fetched
range is itself the direct evidence, not an indirect inference).

**Taker-buy-volume population, verified end-to-end**: `0` of 3,661,780
rows have a `NULL` `taker_buy_base_volume` -- full population confirmed,
not merely assumed from the schema/parsing work composing correctly on
paper. A real sanity check (`taker_buy_base_volume <= volume` for every
row) also returned `0` violations. Sample rows from both the very start
(2019, near-zero real volume/price stub values consistent with a
brand-new, illiquid market) and the very end (2026, real, large,
plausible volume and taker-buy figures) both look real and internally
consistent, not degenerate.

## Real, load-bearing consequence for future scalping/statistical-power work, disclosed here since it was discovered as a side effect of this task

Not decided or acted on here -- pure disclosure, for whoever picks up
the next scalping candidate. `research/eligibility.py`'s PSR/DSR
detection floor is calendar-day-bound (`1.6449/sqrt(years)`), confirmed
by this project's own Task S3 finding for BingX `1m` (631.98 days -> a
detection floor of only ~1.25, barely better than the already-spent `1h`
window). This new Binance futures `1m` window's real span --
**6.962 years** -- would give a detection floor of
`1.6449/sqrt(6.962) ~= 0.623`, materially better than even the `1d`
early-window holdout's own ~0.958 (this project's previous best). This
was not the goal of this task (which was pure order-flow-proxy
infrastructure) and is not a claim that this window is now "the"
holdout for any future candidate -- venue-transferability (see above),
whether/how it should be split into research vs. holdout, and which
future strategy (if any) should spend it are all real, undecided
questions for a future task, not resolved by this document.

## What this task did NOT do

- No trading strategy of any kind was designed or tested.
- `configs/research/holdout_1m.json`, the `vwap-mid-reversion-1m-holdout.json`
  preregistration, and `research.holdout`/`research.run_preregistered_holdout`
  were never touched or called -- this task's own data
  (`BINANCE-FUTURES:BTCUSDT`) is unrelated to the BTC-USDT/BingX holdout
  already spent by `vwap-mid-reversion`.
- No commit was made by this task -- all changes are left in the working
  tree, real and tested, plus the real backfilled data in the production
  `klines.sqlite3`, for review before committing.

## Files created/modified

- `python/data/bingx_klines.py` -- `KlineRow` gains two optional fields.
- `python/data/store.py` -- schema migration, `upsert_klines`/`fetch_klines` extended.
- `python/data/binance_klines.py` -- `_parse_row` extended, docstring corrected.
- `python/tests/test_store.py` -- migration + round-trip tests.
- `python/tests/test_binance_klines.py` -- taker-buy parsing tests, one pre-existing test renamed/corrected.
- `python/tests/fake_binance_server.py` -- `set_kline` gains optional taker-buy parameters.
- `.planning/scalp-s5-binance-1m-orderflow-infra.md` -- this document.
- `python/data/var/klines.sqlite3` (gitignored, not committed) -- migrated schema + 3,661,780 new real rows under `BINANCE-FUTURES:BTCUSDT`/`1m`.
