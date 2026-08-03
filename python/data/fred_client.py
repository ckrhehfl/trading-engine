"""FRED (Federal Reserve Economic Data) observations client -- public,
API-key-authenticated `GET /fred/series/observations`. Stdlib-only
(`urllib`), no `requests`/`httpx` -- same discipline as `bingx_klines.py`
and `bingx_funding.py` (see CLAUDE.md's "Strategy Research Operational
Design" data-pipeline section and Non-negotiable Rules on credential
handling).

This is infrastructure only, built ahead of any macro-conditioned
strategy -- see `.planning/sr-w-macro-data-pipeline.md`. A later task
designs and tests an actual signal on top of what this module and
`store.py`/`backfill_macro.py` build; nothing here evaluates or selects
a strategy.

`base_url` is always a caller-supplied argument, never a literal used
directly inside a request -- mirrors `bingx_klines.py`'s "caller decides
the host" pattern, though for a different reason here: FRED has no
live/demo host distinction the way BingX does (no
`bingx-hostname-guard`-style workflow guards this host; see
`backfill_macro.py`, which is the only place a real default FRED
hostname literal actually lives), the sole motivation for threading it
through as a parameter here is testability against a local fake server.
`api_key` is always a caller-supplied argument too, read from the
`FRED_API_KEY` environment variable only at the CLI layer
(`backfill_macro.py`), never inside this module. This module never
prints, logs, or includes the key value in an exception message --
every `FredClientError` raised here names the endpoint, not the request
URL (which does carry the key in its query string, the same place it
would appear in a browser address bar for a manual test).

Real API facts verified directly against the live production endpoint
this session (2026-08-03; three candidate series probed --
`DTWEXBGS`/`SP500`/`DGS10` -- full investigation in
`.planning/sr-w-macro-data-pipeline.md`):

- Envelope on success: `{"count", "offset", "limit", "observations":
  [...], ...other metadata (realtime_start/end, units, sort_order,
  etc.)...}`. Each observation: `{"date": "YYYY-MM-DD", "value": "...",
  "realtime_start": "...", "realtime_end": "..."}`. `value` is **always
  a quoted JSON string**, confirmed via `type()` on every field checked
  across all three series -- `json.loads(..., parse_float=Decimal)` is
  used anyway, same defensive-in-depth reasoning as `bingx_klines.py`'s
  module docstring (a genuinely inert precaution here, since
  `parse_float` only affects bare/unquoted JSON floats and none appear
  in this response shape, but cheap to keep consistent with the rest of
  this pipeline, and a real guard against FRED ever changing that).
- **Range is INCLUSIVE on both ends**
  (`observation_start <= date <= observation_end`) -- a genuine,
  deliberate difference from this project's usual half-open
  `[start, end)` convention (`bingx_klines.py`/`bingx_funding.py`).
  Preserved as-is here rather than translated to half-open: translating
  would require this module to itself decide what "the next calendar
  day" means for a range that can legitimately skip weekends/holidays,
  which is exactly the kind of calendar judgment call
  `store.find_missing_macro_ranges` -- not this module -- should own.
  `start_date`/`end_date` here are `YYYY-MM-DD` strings, not epoch
  milliseconds -- FRED's own unit, kept as-is for the same
  "don't invent a translation" reasoning.
- **Ordering is oldest-first (ascending `date`)** -- confirmed, the
  opposite of both BingX endpoints already in this pipeline (both
  newest-first). No reordering needed before storage.
- **Pagination**: real `offset`/`limit` paging, confirmed by requesting
  `limit=5&offset=0` then `limit=5&offset=5` against `DGS10` and getting
  the next 5 rows with no overlap or gap. Default `limit` (when
  omitted) is **100000**; the hard server-side max is also **100000**
  -- `limit=100001` returns a real `HTTP 400`
  (`"Variable limit is not between 1 and 100000."`). **Not actually
  needed for any of the three candidate series in practice**: the
  largest, `DGS10`, is 16,848 rows as of this session, comfortably
  under the 100000 default, so a real single-call fetch already returns
  every row FRED has for any of them. `iter_observations` below still
  implements a genuine `offset`-driven pagination loop rather than
  assuming one call always suffices -- exercised in tests against a
  fake server with an artificially small `limit` to force real
  multi-page traversal, since "not needed for these three series today"
  is not the same guarantee as "will never be needed" (a future series,
  or enough calendar time passing, could exceed it).
- **The missing-observation marker is the literal string `"."`,
  confirmed identically across all three candidate series** on a real
  US market holiday that falls on a weekday (2026-07-03, the weekday
  before the observed 2026-07-04 Independence Day holiday; also
  2026-01-01 New Year's Day) -- FRED still emits a row for that
  calendar date, with `"value": "."`, rather than omitting it. This
  module parses `"."` to Python `None` (see `ObservationRow.value`);
  `store.py`'s `macro_series.value` column is nullable for the same
  reason, and still stores the row (not skips it) -- see that module's
  docstring for why the distinction matters for gap detection.
- **Weekends have no row at all** -- confirmed absent (not `"."`) for
  every Saturday/Sunday checked, across all three series. This is the
  naive-gap-detection trap this task was built to avoid: a caller that
  assumed "one row per calendar day" would see every single weekend as
  a false gap, forever. `store.find_missing_macro_ranges` is where this
  is actually handled (see its docstring) -- this module only ever
  reports what FRED actually returned, it does not itself reason about
  calendars.
- **Dates beyond a series' current publication lag are also entirely
  absent, the same as a weekend, NOT `"."`** -- confirmed by requesting
  through "today" for all three series and finding the response simply
  stops at each series' own real `observation_end` (verified via the
  `series` metadata endpoint) with no trailing `"."` placeholder rows.
  This is actually convenient for resumability: a not-yet-published
  weekday reads as a genuine gap today and a later backfill rerun will
  retry it and eventually get a real value, rather than this module
  needing to special-case "recent" dates differently from "holiday"
  dates -- both produce an absent row today, and only the holiday case
  is permanent.
- **A date range starting before a series' true `observation_start` is
  not an error and is not padded** -- FRED silently returns only the
  rows that actually exist (confirmed: requesting `DTWEXBGS` from 1990
  returns the same 7 rows as requesting from its real 2006-01-02 start,
  for a 2006-01-02..2006-01-10 window). Callers backfilling full
  history should seed `start_date` from each series' own real, verified
  `observation_start` (see `backfill_macro.py`'s `SERIES_START_DATE`)
  rather than an arbitrary ancient sentinel the way `bingx_klines.py`
  originally had to probe BingX's unknown retention -- FRED already
  publishes each series' real start per-series via the `series`
  metadata endpoint, there's nothing to discover by probing blindly.
- **A malformed request (bad `series_id`, inverted date range, `limit`
  out of `1..100000`, or a missing/invalid API key) is a real HTTP 400
  with a JSON error body** (`{"error_code", "error_message"}` -- a
  different shape from the success envelope), not a `200` with an error
  code embedded in the body the way every BingX endpoint in this
  pipeline works. This module validates client-side first (inverted
  range, out-of-bounds `limit`/`offset`) for the same fail-fast reason
  `bingx_klines.py` does, so this HTTP-400 path is only actually hit in
  practice for a genuinely bad `series_id` or a bad/missing key.
- **Rate limits are not stated anywhere on FRED's own official docs
  pages** (a real, disclosed gap -- direct `WebFetch` against
  `fred.stlouisfed.org/docs/api/fred/` returned `HTTP 403` this
  session, so "undocumented" here means "could not confirm the docs say
  nothing", not "confirmed the docs say nothing"). Third-party sources
  (the `fredr` R package's changelog, a community FRED MCP server's
  FAQ) consistently report an observed/effective limit around **120
  requests/minute per key**, with one source additionally citing a
  stricter **40/minute specifically for `series/observations`**.
  Exactly like `bingx_klines.py`'s own undocumented-rate-limit
  handling, the mitigation is a conservative fixed inter-request delay
  (`INTER_REQUEST_DELAY_S`) rather than a precisely-tuned number -- and,
  same as BingX, this pipeline's real usage (3 series, 1 request each,
  no pagination actually needed) never comes close to testing either
  cited figure.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Iterator

MAX_OBSERVATIONS_PER_REQUEST = 100_000

# No documented rate limit exists to tune against (see module docstring)
# -- a conservative fixed delay between page requests, same mitigation
# `bingx_klines.py`/`bingx_funding.py` use for the same reason.
INTER_REQUEST_DELAY_S = 0.25

_RETRYABLE_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5
_RETRY_BASE_DELAY_S = 1.0

_OBSERVATIONS_PATH = "/fred/series/observations"

# FRED's own literal marker for "no observation published for this
# date" -- e.g. a US market holiday that still gets a row. See module
# docstring's "missing-observation marker" finding.
_MISSING_VALUE_MARKER = "."


class FredClientError(RuntimeError):
    """Raised on a malformed/unparseable response body or a
    non-retryable HTTP error -- including FRED's real HTTP 400 for a bad
    `series_id`, an out-of-bounds `limit`/`offset` that somehow reached
    the server, or a bad/missing API key.
    """


@dataclass(frozen=True)
class ObservationRow:
    """One daily macro observation as returned by FRED -- date-keyed
    (FRED's own `YYYY-MM-DD` string), matching the wire format, same
    "row carries no context the request already supplies" shape as
    `bingx_klines.KlineRow`/`bingx_funding.FundingRow` (neither of which
    carries `symbol`/`interval` either -- `series_id` is likewise a
    caller-supplied argument to the fetch/store functions here, not a
    field on this row).

    `value` is `None` for FRED's own "no observation for this date"
    marker (`"."`) -- e.g. a US market holiday that still gets a row --
    not for a date FRED never returned a row for at all (a weekend, or a
    not-yet-published recent date; see module docstring). Distinguishing
    these two is exactly why this module never silently drops a `"."`
    row: `store.upsert_macro_observations` still writes it (with `value
    IS NULL`), so `store.find_missing_macro_ranges` can tell "FRED told
    us there's nothing here" apart from "we haven't asked FRED about
    this date yet".
    """

    observation_date: str  # "YYYY-MM-DD", FRED's own format
    value: Decimal | None


def _validate_date_range(start_date: str, end_date: str) -> None:
    # FRED's own range is inclusive on both ends (see module docstring),
    # so `start_date > end_date` is the only structurally-invalid input
    # -- `start_date == end_date` is a legitimate single-day request.
    # `date.fromisoformat` doubles as format validation (raises
    # ValueError on anything not YYYY-MM-DD).
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError(f"start_date ({start_date}) must be <= end_date ({end_date})")


def fetch_observations_page(
    base_url: str,
    series_id: str,
    api_key: str,
    start_date: str,
    end_date: str,
    *,
    limit: int = MAX_OBSERVATIONS_PER_REQUEST,
    offset: int = 0,
) -> tuple[list[ObservationRow], int]:
    """Fetch one page of observations for the inclusive range
    `[start_date, end_date]` (see module docstring -- FRED's own
    convention, deliberately not translated to this pipeline's usual
    half-open convention).

    Returns `(rows, total_count)` -- `total_count` is FRED's own
    `count` field (the number of observations matching the full
    requested range across all pages), which `iter_observations` uses to
    know when it has seen everything. This is a real difference from
    `bingx_klines.fetch_klines_page`, which deliberately returns only
    rows (no count) because BingX's envelope has no equivalent total and
    silently caps instead -- FRED gives a real total, so there is no
    silent-capping risk here to design around.

    Raises `ValueError` for an inverted range or an out-of-range
    `limit`/`offset` (fails loud, checked before any request is sent).
    Raises `FredClientError` for a malformed body or a non-retryable
    HTTP error (including FRED's real HTTP 400 for a bad `series_id` or
    a bad/missing API key).
    """
    _validate_date_range(start_date, end_date)
    if not (1 <= limit <= MAX_OBSERVATIONS_PER_REQUEST):
        raise ValueError(f"limit must be in 1..{MAX_OBSERVATIONS_PER_REQUEST}, got {limit}")
    if offset < 0:
        raise ValueError(f"offset must be >= 0, got {offset}")

    query = urllib.parse.urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start_date,
            "observation_end": end_date,
            "limit": limit,
            "offset": offset,
        }
    )
    url = f"{base_url}{_OBSERVATIONS_PATH}?{query}"

    body = _get_with_retry(url)

    try:
        payload = json.loads(body, parse_float=Decimal)
    except json.JSONDecodeError as exc:
        raise FredClientError(f"malformed JSON response from FRED series/observations: {exc}") from exc

    if not isinstance(payload, dict):
        raise FredClientError(f"FRED response body is not a JSON object: {payload!r}")

    raw_rows = payload.get("observations")
    if not isinstance(raw_rows, list):
        raise FredClientError(f"FRED response 'observations' is not a list: {raw_rows!r}")

    total_count = payload.get("count")
    if not isinstance(total_count, int):
        raise FredClientError(f"FRED response 'count' is not an int: {total_count!r}")

    rows = [_parse_row(row) for row in raw_rows]
    return rows, total_count


def iter_observations(
    base_url: str,
    series_id: str,
    api_key: str,
    start_date: str,
    end_date: str,
    *,
    limit: int = MAX_OBSERVATIONS_PER_REQUEST,
) -> Iterator[ObservationRow]:
    """Walk the inclusive range `[start_date, end_date]` in `<= limit`-row
    pages via real `offset` pagination, yielding every `ObservationRow`
    FRED actually has -- oldest-first, matching FRED's own ordering (see
    module docstring; no reordering needed the way BingX's newest-first
    responses need sorting elsewhere in this pipeline).

    In practice, for every series this pipeline currently backfills, a
    single page (`offset=0`) already covers the full range (`count <=
    limit`) -- see module docstring's "not actually needed... in
    practice" note. This is still a real loop, not a single call, so a
    future series or enough calendar time growing past `limit` doesn't
    silently truncate.
    """
    cursor = 0
    first_request = True
    while True:
        if not first_request:
            time.sleep(INTER_REQUEST_DELAY_S)
        first_request = False

        rows, total_count = fetch_observations_page(
            base_url, series_id, api_key, start_date, end_date, limit=limit, offset=cursor
        )
        yield from rows
        cursor += len(rows)
        if cursor >= total_count or not rows:
            break


def _parse_row(row: object) -> ObservationRow:
    if not isinstance(row, dict):
        raise FredClientError(f"malformed observation row (not an object): {row!r}")
    try:
        raw_date = row["date"]
        raw_value = row["value"]
    except KeyError as exc:
        raise FredClientError(f"malformed observation row: {row!r}") from exc

    if not isinstance(raw_date, str):
        raise FredClientError(f"malformed observation row (date is not a string): {row!r}")

    if raw_value == _MISSING_VALUE_MARKER:
        value: Decimal | None = None
    else:
        try:
            value = Decimal(raw_value)
        except (TypeError, ValueError, ArithmeticError) as exc:
            raise FredClientError(f"malformed observation row (unparseable value): {row!r}") from exc
        if not value.is_finite():
            # Decimal("NaN")/Decimal("Infinity") parse without raising --
            # FRED is not known to ever send these (only a real number or
            # the "." marker above), but a non-finite value silently
            # stored as text and read back later would compare unequal to
            # itself and be indistinguishable from a genuine value at a
            # glance. Fail loud here rather than downstream.
            raise FredClientError(f"malformed observation row (non-finite value): {row!r}")

    return ObservationRow(observation_date=raw_date, value=value)


def _get_with_retry(url: str) -> str:
    # Byte-for-byte the same HTTP-transport retry logic as
    # bingx_klines.py's/bingx_funding.py's _get_with_retry -- duplicated
    # deliberately rather than factored into a shared helper, matching
    # bingx_funding.py's own stated reasoning (a private helper isn't
    # meant for cross-module reuse, and each module's docstring already
    # documents the mirroring relationship instead of a real dependency).
    #
    # Error messages here deliberately never include `url` (which
    # carries `api_key` in its query string) -- see module docstring's
    # credential-handling paragraph.
    last_exc: BaseException | None = None
    for attempt in range(_MAX_RETRIES):
        if attempt > 0:
            time.sleep(_RETRY_BASE_DELAY_S * (2 ** (attempt - 1)))
        request = urllib.request.Request(url, headers={"User-Agent": "trading-engine-research/0.1"})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            last_exc = exc
            if exc.code not in _RETRYABLE_STATUSES:
                raise FredClientError(
                    f"FRED returned non-retryable HTTP {exc.code} for series/observations"
                ) from exc
            continue
        except urllib.error.URLError as exc:
            last_exc = exc
            continue
        except OSError as exc:
            last_exc = exc
            continue

    raise FredClientError(
        f"exceeded {_MAX_RETRIES} retries fetching FRED series/observations: {last_exc}"
    ) from last_exc
