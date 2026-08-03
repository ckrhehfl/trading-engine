# Strategy Research Task W: the FRED macro-data pipeline

## Scope note

This task builds **only the data path** to FRED (Federal Reserve
Economic Data) — a client, a storage table, and a resumable backfill —
for three candidate macroeconomic series. **No macro-conditioned signal
or strategy was written, run, or evaluated here**, exactly the same
"infrastructure first, strategy is a separate later task" split
`sr-a` (`bingx_klines.py`) established for BTC klines. Nothing here
touches the Eligibility Bar, walk-forward, or holdout machinery — this
is pure data plumbing feeding into whatever a future task builds on top
of it.

Prerequisites read in full first: CLAUDE.md ("Strategy Research
Methodology", "Strategy Research Operational Design", "Non-negotiable
Rules" on credential handling, and "Exchange API Facts — BingX" as the
documentation-rigor bar to match for FRED), `.planning/sr-a-data-
pipeline.md` (the BingX pipeline this project's data layer conventions
originate from), `.planning/sr-m-funding-rate-pipeline.md` (the closest
prior precedent for a second, structurally-different data source living
in the same cache).

## Why this task exists

After `sr-v` closed out the BTC-only price-signal research line as
INCONCLUSIVE, CLAUDE.md names two live remedies: multi-symbol expansion,
or a genuinely different data source. A prior research pass concluded
macroeconomic data (via FRED) is the best next candidate — a genuinely
separate generating system (Fed/Treasury/equity markets) rather than
definitionally price-derived the way on-chain metrics are, and FRED is
free/unlimited/well-documented with much lower integration friction than
any on-chain vendor. This task is the infrastructure that decision
requires before any macro-conditioned signal can be designed.

Three candidate series, chosen because their real history (verified
below) comfortably predates this project's earliest BTC data
(2021-05-14), so none of them constrain the eventual macro+BTC joint
window:

- `DTWEXBGS` — Nominal Broad U.S. Dollar Index
- `SP500` — S&P 500 daily close
- `DGS10` — 10-Year Treasury Constant Maturity Yield

## What was built

1. **`python/data/fred_client.py`** — stdlib-only (`urllib`) client for
   `GET /fred/series/observations`, mirroring `bingx_klines.py`'s
   discipline (exact `Decimal` parsing, retry/backoff, fail-loud
   validation) with the real differences FRED's own API actually has
   (see "Real API findings" below).
2. **`macro_series` table in `python/data/store.py`** — `(series_id,
   observation_date, value, fetched_at)`, `value` nullable. New
   functions `upsert_macro_observations`, `find_missing_macro_ranges`,
   `fetch_macro_observations`, mirroring the klines/funding functions'
   shape but with a genuinely different gap-detection algorithm (see
   below).
3. **`python/data/backfill_macro.py`** — resumable CLI backfill,
   structurally mirroring `backfill.py`/`backfill_funding.py`.
4. **`python/tests/fake_fred_server.py`** — stdlib `http.server` fake,
   mirroring `fake_bingx_funding_server.py`'s testing philosophy.
5. **`.env.example`** — `FRED_API_KEY=` added alongside the existing
   `BINGX_*` entries.

**57 new tests** (`test_fred_client.py` 20, `test_backfill_macro.py` 10,
`test_store.py` +27). Full suite: **1,237 passed**, up from 1,180.
Nothing regressed.

## Real API findings — FRED `series/observations`

Verified directly against the live production endpoint this session
(2026-08-03), against `DTWEXBGS`/`SP500`/`DGS10`. Full rigor matching
CLAUDE.md's "Exchange API Facts — BingX" bar; the durable version of
these findings lives in `fred_client.py`'s own module docstring (per
`.planning/README.md`'s convention that a design/finding a future reader
needs lives with the code, not only here) — summarized here with the
supporting evidence.

### Values are always quoted JSON strings

Confirmed via `type()` on every field checked across all three series —
`{"date": "2026-01-02", "value": "4.06"}`, never a bare number.
`json.loads(..., parse_float=Decimal)` is used anyway, same
defensive-in-depth reasoning `bingx_klines.py` states for its own
(verified-string) case: a real guard against FRED ever changing this,
inert today.

### Range is inclusive on both ends — a genuine, deliberate divergence

`observation_start <= date <= observation_end`, confirmed by requesting
`limit=5&offset=0` then `limit=5&offset=5` against `DGS10` and getting
exactly the next 5 rows with no overlap or gap, and by a single-day
request (`start == end`) returning exactly one row. This is the opposite
of `bingx_klines.py`/`bingx_funding.py`'s half-open `[start, end)`
convention. **Deliberately not translated** to half-open anywhere in
`fred_client.py` — `store.find_missing_macro_ranges` is where calendar
judgment calls (what does "the next day" mean when weekends/holidays can
legitimately be absent) belong, not the wire client.

### Pagination is real, but not needed for these three series today

`count`/`offset`/`limit` are all real: default `limit` (when omitted) is
**100000**, and the hard server-side max is also **100000** —
`limit=100001` returns a real `HTTP 400`
(`"Variable limit is not between 1 and 100000."`, confirmed live). The
largest of the three candidates, `DGS10`, is 16,848 rows as of this
session — comfortably under 100000, so a real single-call fetch already
returns everything FRED has for any of the three series. `iter_observations`
still implements a genuine `offset`-driven loop (not a single-call
assumption) and is exercised in tests against a fake server configured
with a small `limit` to force real multi-page traversal — "not needed
today" isn't "will never be needed" (a future series, or years passing,
could exceed it).

### The missing-observation marker: literal `"."`, confirmed identically across all three series

A real US market holiday falling on a weekday still gets a row from
FRED, with `"value": "."` — not omitted. Confirmed on two independent
2026 holidays, identically across all three series:

| Date | Holiday | DTWEXBGS | SP500 | DGS10 |
|---|---|---|---|---|
| 2026-01-01 | New Year's Day | `"."` | `"."` | `"."` |
| 2026-07-03 | July 4th observed | `"."` | `"."` | `"."` |

`fred_client._parse_row` maps `"."` to Python `None`
(`ObservationRow.value: Decimal | None`).

### Weekends have no row at all — the naive-gap-detection trap this task was warned about

Confirmed absent (not `"."`) for every Saturday/Sunday checked, across
all three series — e.g. requesting `2026-07-01`..`2026-07-10` returns
exactly 8 rows (the 8 weekdays), skipping 07-04/07-05 entirely. A
"one row per calendar day" gap check would flag every single weekend as
a permanent false gap. This is the reason `find_missing_macro_ranges`
exists as a genuinely different algorithm from
`find_missing_ranges`/`find_missing_funding_ranges` — see "Schema and
gap-detection design" below.

### Not-yet-published recent dates are also absent, the same as a weekend — not `"."`

Requesting through "today" (2026-08-03) for all three series, the
response simply stops at each series' own real `observation_end`
(`DTWEXBGS` 2026-07-24, `SP500` 2026-07-31, `DGS10` 2026-07-30 — all
independently confirmed via the `/fred/series` metadata endpoint), with
no trailing `"."` rows for the days since. This turned out convenient
rather than requiring special-casing: a not-yet-published weekday reads
as a genuine gap today, and a later backfill rerun retries it and
eventually gets a real value once FRED publishes — proven for real
below ("Idempotent rerun" section), not just asserted.

### A range starting before a series' true `observation_start` is silently truncated, not an error

Requesting `DTWEXBGS` from 1990-01-01 through 2006-01-10 returns the
same 7 rows as requesting from its real 2006-01-02 start — no error, no
padding. This is why `backfill_macro.py`'s `SERIES_START_DATE` hardcodes
each series' own real, verified start (fetched via the `/fred/series`
metadata endpoint this session) rather than probing an unknown retention
edge the way `bingx_klines.py` originally had to for BingX — FRED
already publishes the answer per-series.

### Malformed requests are a real HTTP 400 with a JSON error body — a different shape from BingX

`{"error_code": 400, "error_message": "..."}`, not a `200` with an error
code embedded in the body the way every BingX endpoint in this pipeline
works. Confirmed for: a malformed `series_id` (`"Invalid value for
variable series_id. Series IDs should be 25 or less alphanumeric
characters."`), an inverted date range (`"observation_start can not be
after ... observation_end"`), `limit > 100000`, and a missing/empty
`api_key` (`"a keyless request returns HTTP 400 with a clear error"` —
this task's own pre-verified fact, reproduced). `fetch_observations_page`
validates the first three client-side before ever sending a request
(same fail-fast discipline as `bingx_klines.py`), so this HTTP-400 path
is only actually reachable in practice for a genuinely bad `series_id`
or a bad/missing key.

### Rate limits: not confirmable on FRED's own docs, third-party estimates only

A direct `WebFetch` against `https://fred.stlouisfed.org/docs/api/fred/`
returned `HTTP 403` this session — so "undocumented" here honestly means
"could not confirm the docs say nothing", not "confirmed the docs say
nothing". A `WebSearch` pass found consistent third-party reports (the
`fredr` R package's changelog; a community FRED MCP server's FAQ) citing
an observed/effective **~120 requests/minute per key**, with one source
additionally citing a stricter **40/minute specifically for
`series/observations`**. Same mitigation as `bingx_klines.py`'s own
undocumented-limit handling: a conservative fixed inter-request delay
(`INTER_REQUEST_DELAY_S = 0.25`), not a precisely-tuned number. This
pipeline's real usage (3 series, 1 request each, no pagination needed)
never comes close to testing either cited figure.

## Schema and gap-detection design

### `macro_series` table

```sql
CREATE TABLE IF NOT EXISTS macro_series (
  series_id TEXT NOT NULL,
  observation_date TEXT NOT NULL,
  value TEXT,
  fetched_at TEXT NOT NULL,
  PRIMARY KEY (series_id, observation_date)
);
```

`observation_date` is FRED's own `YYYY-MM-DD` string, not an `INTEGER`
ms timestamp — the other two tables use ms because BingX's own wire
format does; FRED's is a date, and translating it to a timestamp would
invent a timezone/time-of-day this data has no real one for. `value` is
nullable **and deliberately still populated as a real row** when FRED
returns `"."` (stored as SQL `NULL`, not skipped) — this is the
load-bearing decision the whole gap-detection design depends on: storing
the row (with `NULL`) records "FRED told us there is nothing here" as a
fetched fact, distinct from "we have never asked". Skipping it instead
would make `find_missing_macro_ranges` re-request that date forever,
since it would never stop looking missing.

### Why gap detection needed a genuinely different algorithm, not a parameter tweak

`find_missing_ranges`/`find_missing_funding_ranges` both diff stored
timestamps against an **arithmetic step sequence** — valid because
klines have a truly fixed grid and funding is *typically* fixed (with a
documented, accepted residual-risk exception). Daily macro data has no
such grid: FRED never returns a row at all for a Saturday or Sunday, so
"expect one row every `step_ms`" is simply false twice a week, forever.
`find_missing_macro_ranges` instead diffs stored dates against
`_expected_weekdays(start_date, end_date)` — every Monday-Friday
calendar date in the inclusive range, built by walking day-by-day and
filtering `date.weekday() < 5`. This is **not** a full market-holiday-
aware trading calendar (it doesn't know July 4th is a holiday) —
deliberately: a real holiday still gets a real row from FRED (`"."`),
so once fetched once it is never a gap again regardless of whether this
module "knows" it's a holiday. The only thing that must never be
misclassified as a gap is an *ordinary weekend*, which this handles
exactly.

A gap's `(start, end)` may itself span a weekend internally (e.g. a
missing Thursday immediately followed by a present Monday still produces
`(Thursday, Thursday)`, not an accidental extension into the weekend —
verified by `test_find_missing_macro_ranges_detects_a_gap_that_spans_a_weekend`).
Re-requesting an inclusive range that happens to include a weekend is
harmless: FRED just won't return rows for it, same as any other request.

## Idempotent rerun against real production data — not just asserted, reproduced live

After the real backfill (numbers below), the same command was run a
second time immediately. Real output, not a fake-server test:

```
fetching missing macro range [2026-07-31, 2026-08-03] for DGS10
range [2026-07-31, 2026-08-03]: fetched 0 rows (total newly inserted so far: 0)
fetching missing macro range [2026-07-27, 2026-08-03] for DTWEXBGS
range [2026-07-27, 2026-08-03]: fetched 0 rows (total newly inserted so far: 0)
fetching missing macro range [2026-08-03, 2026-08-03] for SP500
range [2026-08-03, 2026-08-03]: fetched 0 rows (total newly inserted so far: 0)
```

This is exactly the "not-yet-published weekday reads as a genuine gap,
retried on rerun" behavior predicted above, reproduced against the real
API rather than only the fake server: each series' own current
publication lag (0-7 days as of 2026-08-03) is correctly re-identified
as still-missing and re-requested, and FRED correctly still has nothing
there yet. Zero new rows inserted, confirming resumability end-to-end
against production.

## Real backfill: the actual numbers

Run against the live, public, API-key-authenticated production endpoint
(`GET https://api.stlouisfed.org/fred/series/observations`) on
**2026-08-03**, each series' own verified `observation_start` (see
`SERIES_START_DATE`) through "today" (2026-08-03), writing to the shared
cache at `/mnt/c/Dev/trading-engine/python/data/var/klines.sqlite3`.
Confirmed by querying the shared SQLite file directly afterward (not
trusting the backfill script's own exit code):

| Series | Rows | Date range | `value IS NULL` rows (holidays) |
|---|---|---|---|
| `DGS10` | 16,848 | 1962-01-02 → 2026-07-30 | 719 |
| `DTWEXBGS` | 5,365 | 2006-01-02 → 2026-07-24 | 211 |
| `SP500` | 2,610 | 2016-08-01 → 2026-07-31 | 96 |
| **Total** | **24,823** | | **1,026** |

Wall clock: ~1.4 seconds, 3 requests (one per series, no pagination
triggered — consistent with "not needed for these three series today"
above). Each series' `MAX(observation_date)` matches its own real
`observation_end` from the `/fred/series` metadata endpoint exactly, and
each series' earliest row matches its hardcoded `SERIES_START_DATE`
exactly — both spot-checked directly against the live cache, not
inferred from the backfill log.

## Credential handling

A real, working `FRED_API_KEY` exists in `/mnt/c/Dev/trading-engine/.env`
(outside this worktree — worktree isolation means `.env` is not copied
in). Per CLAUDE.md's Non-negotiable Rules and this task's explicit
instructions: the key was read via a minimal `.env` parser into
`os.environ`, used only to authenticate the real backfill request, and
never printed, logged, echoed, or included in any committed file, test
fixture, or exception message. `fred_client.py`'s own error paths
deliberately never include the request URL in any `FredClientError`
message (unlike `bingx_klines.py`, which does include its own URL in
error text — safe there only because BingX's URLs carry no secret) —
pinned by `test_fetch_observations_page_error_message_never_contains_the_api_key`,
which forces a real error path with a distinctive dummy key and asserts
it never appears in the resulting exception's text. Presence of the
`FRED_API_KEY` variable name in `.env` was confirmed via
`grep -o '^FRED_API_KEY=' .env` (matches the variable name only, never
its value) before use.

## Zero new dependencies

Confirmed before writing any code: `python/pyproject.toml` has no
`requests`/`httpx`/`python-dotenv` (or any HTTP/env-loading library) —
`fred_client.py` uses stdlib `urllib` exactly like `bingx_klines.py`/
`bingx_funding.py`, and the one place a `.env` file needed parsing (the
real-verification step, and this project's first-ever need to actually
load `.env` programmatically in Python — BingX's own klines/funding
endpoints are public and unauthenticated, so no prior Python code here
has ever needed a credential from `.env`) used a ~10-line inline parser
rather than adding `python-dotenv`.

## Judgment calls resolved without asking

- **`ObservationRow` carries no `series_id` field**, matching
  `KlineRow`/`FundingRow`'s established shape (neither carries
  `symbol`/`interval` either) — `series_id` is a caller-supplied
  argument to the fetch/store functions, not row data, since FRED's own
  observation JSON doesn't include it per-row either (it's implied by
  the request).
- **`fetch_observations_page` returns `(rows, total_count)`**, a real
  divergence from `fetch_klines_page`'s plain `list[KlineRow]` return.
  Justified because FRED's `count` field is a genuine total (unlike
  BingX's envelope, which has no equivalent and instead silently caps) —
  using it makes `iter_observations` simple and correct without
  BingX-style defensive cursor derivation.
- **`DEFAULT_FRED_BASE_URL` is a real hardcoded default in
  `backfill_macro.py`**, unlike `BINGX_BASE_URL`'s mandatory env-var-only
  pattern. FRED has no live/demo host split for `bingx-hostname-guard.yml`-
  style safety reasons to guard against — the only reason `base_url` is
  threaded through as a parameter at all is test injection against the
  fake server.
- **`SERIES_START_DATE` is hardcoded**, not probed. FRED publishes each
  series' real start via its own metadata endpoint; there is nothing to
  discover empirically the way BingX's undocumented retention required.

## Deliberately out of scope

- **Any macro-conditioned signal or strategy.** See Scope note — a
  separate, later task, same split as `sr-a` → the strategy tasks that
  followed it.
- **Fetching the `/fred/series` metadata endpoint from `fred_client.py`
  itself.** `SERIES_START_DATE` was populated once, by hand, from a real
  live call this session (see table in the task instructions and
  cross-checked against the real backfill's own `MIN(observation_date)`
  above) — no code path in this PR calls the metadata endpoint
  programmatically. Worth adding if a future series' start date needs
  discovering without a manual step.
- **A holdout split for macro data.** No strategy exists yet to need
  one; belongs with whichever task first designs a macro-conditioned
  signal, per the same principle `sr-t`/`sr-u` applied to `1d` BTC data.
- **More than three series.** Scoped to the three named in this task's
  instructions; adding a fourth is a `SERIES_START_DATE` entry plus a
  verified `observation_start`, not a design change.
